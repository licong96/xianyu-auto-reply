"""
学科网（zxxk.com）试卷爬虫 - DrissionPage 实现

流程：
1. 启动/接管浏览器（独立端口 + 独立用户目录，登录态持久化）
2. 未登录则等待人工在浏览器中完成登录（会员账号）
3. 搜索关键词翻页，解析列表：标题/属性标签/发布日/下载量
4. 逐条进详情页补全完整日期，已登录态下点击下载 PDF
5. md5 去重后写入 xy_material_pool（status=crawled）

已实测校准（2026-10-10）：
- 列表页 SSR 可直接解析：.mid-tit a.title / .attribute-spot / .feature
- 翻页 URL：/doc/page-N.html?kw=关键词
- 详情页有反爬 JS 挑战（设置 cookie 后自刷新），必须走真实浏览器
- 下载按钮选择器依赖登录态页面结构，如失效请看 README「下载校准」
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

from . import db
from .settings import PROJECT_ROOT

GRADE_PATTERN = re.compile(r"(高[一二三]|[七八九]年级|初[一二三]|小学)")
YEAR_PATTERN = re.compile(r"(20\d\d)")


@dataclass
class PaperItem:
    """一条试卷采集结果（入库前的中间结构）。"""
    soft_id: str
    title: str
    source_url: str
    attributes: list[str] = field(default_factory=list)
    publish_text: str = ""          # 列表上的发布文本（如 9月2日 发布，缺少年份）
    download_count: int = 0
    published_at: str | None = None  # 详情页补全的完整日期 2026-09-02
    local_path: str | None = None
    file_size: int | None = None
    file_hash: str | None = None


class ZxxkCrawler:
    SEARCH_TMPL = "https://search.zxxk.com/doc/page-{page}.html?kw={kw}"
    SEARCH_FIRST = "https://search.zxxk.com/doc/?kw={kw}"

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.browser = None
        self.tab = None
        self.owner_id = int(cfg.get("owner_id", 1))
        self.school_keywords: list[str] = cfg.get("school_keywords", [])

    # ---------- 浏览器 ----------

    def start_browser(self, headless: bool | None = None):
        from DrissionPage import Chromium, ChromiumOptions

        browser_cfg = self.cfg["browser"]
        co = ChromiumOptions(read_file=False)
        co.set_local_port(int(browser_cfg["port"]))
        co.set_user_data_path(browser_cfg["user_data_dir"])
        co.headless(bool(headless if headless is not None else browser_cfg.get("headless", False)))
        # 静默无关通知
        co.set_argument("--disable-features=TranslateUI")
        self.browser = Chromium(addr_or_opts=co)
        self.tab = self.browser.latest_tab
        logger.info(f"浏览器已就绪（端口 {browser_cfg['port']}，用户目录 {browser_cfg['user_data_dir']}）")

    def _loginish_cookie_names(self) -> set[str]:
        """浏览器级读取 zxxk 域登录态 Cookie（不依赖页面 DOM，抗反爬刷新）。"""
        found = set()
        try:
            for c in self.tab.cookies(all_domains=True):
                domain = c.get("domain", "") or ""
                name = c.get("name", "") or ""
                if "zxxk" in domain and re.search(r"passport|ticket|login|auth|token|user", name, re.I):
                    found.add(name)
        except Exception as e:
            logger.debug(f"读取 cookie 失败：{e}")
        return found

    def _page_has(self, text: str) -> bool:
        """页面文本是否包含指定内容（容错页面刷新重连）。"""
        try:
            return bool(text and text in (self.tab.html or ""))
        except Exception:
            return False

    def wait_for_login(self, timeout: int = 600, skip: bool = False) -> None:
        """未登录则等待人工在浏览器中登录。

        判定信号（满足其一即视为已登录）：
        1. zxxk 域出现登录态 Cookie（passport/ticket/login/auth/token/user 命名）
        2. config.yaml 的 login_username 出现在页面中（最可靠，建议填写）
        skip=True 时不检测直接继续（人工确认已登录后的快速重跑）。
        """
        browser_cfg = self.cfg["browser"]
        username = (browser_cfg.get("login_username") or "").strip()
        if skip:
            logger.info("已指定 --no-login-wait，跳过登录检测")
            return
        self.tab.get(browser_cfg["login_url"])
        time.sleep(4)
        if self._loginish_cookie_names() or (username and self._page_has(username)):
            logger.info("检测到已登录状态，直接开始采集")
            return
        print("\n>>> 请在打开的浏览器窗口中完成学科网会员登录，登录后采集将自动开始 <<<\n", flush=True)
        logger.warning("未检测到登录状态——等待人工登录（最多 10 分钟）")
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(5)
            try:
                self.tab.get(browser_cfg["login_url"])
            except Exception:
                pass
            time.sleep(2)
            if self._loginish_cookie_names() or (username and self._page_has(username)):
                logger.info("登录成功，开始采集")
                return
        raise RuntimeError("等待登录超时，请重新运行")

    # ---------- 列表解析 ----------

    def _search_url(self, keyword: str, page: int) -> str:
        kw = keyword  # zxxk 支持中文直出
        if page <= 1:
            return self.SEARCH_FIRST.format(kw=kw)
        return self.SEARCH_TMPL.format(page=page, kw=kw)

    def parse_list_page(self, keyword: str, page: int) -> list[PaperItem]:
        """解析一页搜索结果，返回试卷条目列表（空列表表示没有更多了）。"""
        self.tab.get(self._search_url(keyword, page))
        time.sleep(float(self.cfg["crawl"].get("page_wait_seconds", 3)))
        containers = self.tab.eles("css:.mid-tit")
        items: list[PaperItem] = []
        for box in containers:
            a = box.ele("css:a.title")
            if not a:
                continue
            title = (a.text or "").strip()
            href = (a.attr("href") or "").split("?")[0]
            m = re.search(r"/soft/(\d+)", href)
            if not title or not m:
                continue
            item = PaperItem(soft_id=m.group(1), title=title, source_url=href)

            # 属性标签（年份/年级/学期/地区/考试类型/答案解析等）
            parent = box.parent()
            attr_box = parent.ele("css:.attribute-spot") if parent else None
            if attr_box:
                item.attributes = [s.text.strip() for s in attr_box.eles("tag:span") if s.text and s.text.strip()]

            # 发布日期 + 下载量
            feature = parent.ele("css:.feature") if parent else None
            if feature:
                ftext = re.sub(r"\s+", " ", feature.text or "")
                m_pub = re.search(r"([\d月日\s]+发布)", ftext)
                if m_pub:
                    item.publish_text = m_pub.group(1).strip()
                m_dn = re.search(r"下载量[：:]\s*(\d+)", ftext)
                if m_dn:
                    item.download_count = int(m_dn.group(1))
            items.append(item)
        logger.info(f"第 {page} 页解析到 {len(items)} 条")
        return items

    # ---------- 详情与下载 ----------

    def fill_detail(self, item: PaperItem) -> None:
        """进详情页补全完整发布日期（列表页日期缺少年份）。"""
        self.tab.get(item.source_url)
        time.sleep(4)  # 反爬 JS 挑战执行 + 页面渲染
        html = self.tab.html or ""
        m = re.search(r"(20\d\d)[-年/.](\d{1,2})[-月/.](\d{1,2})", html)
        if m:
            item.published_at = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    def download(self, item: PaperItem) -> None:
        """在详情页点击下载（含 layui 二次确认），文件落盘到 zxxk_download_dir 后归档到 download_dir。

        已实测校准（2026-10-10）：
        1. 主按钮 #btnSoftDownload（VIP会员 免费下载）
        2. 弹出 layui iframe（#layui-layer-iframe*），内有 .balance-payment-btn「确认」
        3. 确认后自动跳转并下载，文件落在 ~/Downloads/zxxk（Chrome 下载目录）
        失败不抛异常，留待后续补下载。
        """
        final_dir = Path(self.cfg["crawl"]["download_dir"])
        watch_dir = Path(self.cfg["crawl"]["zxxk_download_dir"])
        timeout = int(self.cfg["crawl"].get("download_timeout", 120))
        before = {p.name for p in watch_dir.iterdir() if p.is_file()}

        # 详情页加载完成后再多等几秒，确保页面/脚本稳定（学科网有反爬刷新）
        time.sleep(3)

        # 点击主按钮：重试 3 次（反刷新的元素失效场景），滚动到可见 + JS 点击兜底
        clicked = False
        for attempt in range(1, 4):
            try:
                btn = self.tab.ele("css:#btnSoftDownload", timeout=5)
                if not btn:
                    logger.warning(f"第 {attempt} 次未找到 #btnSoftDownload")
                    time.sleep(2)
                    continue
                try:
                    btn.scroll.to_see()
                except Exception:
                    pass
                try:
                    btn.click()
                except Exception:
                    btn.click(by_js=True)
                clicked = True
                logger.info("点击下载主按钮 #btnSoftDownload")
                break
            except Exception as e:
                logger.warning(f"第 {attempt} 次点击下载主按钮失败：{e!r}")
                time.sleep(2)
        if not clicked:
            return

        # 等弹窗完全渲染后再点二次确认
        time.sleep(3)
        try:
            frame = self.tab.get_frame("css:#layui-layer-iframe2") or self.tab.get_frame("css:iframe[id^='layui-layer-iframe']")
            if frame:
                confirm = frame.ele("css:.balance-payment-btn") or frame.ele("text=确认")
                if confirm:
                    confirm.click()
                    logger.info("点击二次确认「确认」")
        except Exception as e:
            logger.debug(f"二次确认处理（可能无需确认）：{e}")

        # 等待 zxxk 下载目录出现新文件，然后归档到 download_dir
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(2)
            new_files = [p for p in watch_dir.iterdir()
                         if p.is_file() and p.name not in before and not p.name.endswith(".crdownload")]
            if new_files:
                f = max(new_files, key=lambda p: p.stat().st_mtime)
                safe_name = re.sub(r'[\\/:*?"<>|\s]+', "_", item.title)[:60]
                target = final_dir / f"{item.soft_id}_{safe_name}{f.suffix}"
                try:
                    f.rename(target)
                except OSError:
                    target = f  # 移动失败则保留原位置
                item.local_path = str(target)
                item.file_size = target.stat().st_size
                item.file_hash = db.md5_file(target)
                logger.info(f"下载完成：{target.name}（{item.file_size} 字节）")
                return
        logger.warning(f"soft_id={item.soft_id} 下载超时（{timeout}s），留待补下载")

    # ---------- 字段归一 ----------

    def normalize(self, item: PaperItem) -> dict:
        """把采集结果映射为 xy_material_pool 字段。"""
        title = item.title
        school = next((k for k in self.school_keywords if k in title), None)
        year = None
        for attr in item.attributes:
            m = YEAR_PATTERN.fullmatch(attr.strip())
            if m:
                year = m.group(1)
                break
        if not year:
            m = YEAR_PATTERN.search(title)
            if m:
                year = m.group(1)
        grade = None
        m = GRADE_PATTERN.search(title)
        if m:
            grade = m.group(1)
        tags = [a for a in item.attributes if a.strip() and not YEAR_PATTERN.fullmatch(a.strip())]
        if grade:
            tags = [a for a in tags if a.strip() != grade]
        for flag in ("答案", "解析"):
            if flag in title and flag not in tags:
                tags.append(flag)

        return {
            "owner_id": self.owner_id,
            "paper_name": title[:300],
            "school": school,
            "year": year,
            "grade": grade,
            "tags": tags or None,
            "published_at": item.published_at,
            "view_count": 0,
            "download_count": item.download_count,
            "source_url": item.source_url,
            "source_type": "zxxk",
            "local_path": item.local_path,
            "file_size": item.file_size,
            "file_hash": item.file_hash,
        }

    # ---------- 主流程 ----------

    def run(self, keyword: str, max_pages: int | None = None, download: bool | None = None,
            limit: int | None = None, skip_login_wait: bool = False) -> dict:
        crawl_cfg = self.cfg["crawl"]
        max_pages = max_pages or int(crawl_cfg.get("max_pages", 3))
        download = bool(crawl_cfg.get("download", True)) if download is None else download
        interval = float(crawl_cfg.get("interval_seconds", 2))

        stats = {"new": 0, "skipped": 0, "failed": 0, "downloaded": 0, "keyword_filtered": 0}
        self.wait_for_login(skip=skip_login_wait)

        for page in range(1, max_pages + 1):
            if limit is not None and stats["new"] >= limit:
                break
            items = self.parse_list_page(keyword, page)
            if not items:
                logger.info("没有更多结果，结束翻页")
                break

            # 关键词过滤：标题必须包含搜索关键词才进详情（排除「名校协作体」等沾边结果）
            before = len(items)
            items = [it for it in items if keyword in it.title]
            stats["keyword_filtered"] += before - len(items)
            if not items:
                continue

            # 断点续爬：批量预取已入库的 source_url
            existing = db.run(db.fetch_existing_urls([it.source_url for it in items]))

            for item in items:
                if limit is not None and stats["new"] >= limit:
                    logger.info(f"已达到采集上限 limit={limit}，停止")
                    break
                if item.source_url in existing:
                    stats["skipped"] += 1
                    continue
                try:
                    self.fill_detail(item)   # 详情页已停留，下载直接复用该页面
                    if download:
                        self.download(item)
                        if item.local_path:
                            stats["downloaded"] += 1
                    row_id = db.run(db.insert_item(self.normalize(item)))
                    if row_id:
                        stats["new"] += 1
                        logger.info(f"入库成功 id={row_id}：{item.title[:40]}")
                    else:
                        stats["skipped"] += 1
                except Exception as e:
                    stats["failed"] += 1
                    logger.exception(f"处理失败 {item.source_url}: {e}")
                time.sleep(interval)
        return stats
