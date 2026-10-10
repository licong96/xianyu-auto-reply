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

import asyncio
import re
import sys
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

    def wait_for_login(self, timeout: int = 600) -> None:
        """未登录则等待人工在浏览器中登录。轮询登录标记，最多等待 timeout 秒。"""
        browser_cfg = self.cfg["browser"]
        selector = browser_cfg.get("logged_in_selector", "")
        self.tab.get(browser_cfg["login_url"])
        time.sleep(3)
        if selector and self.tab.ele(selector):
            logger.info("检测到已登录状态，直接开始采集")
            return
        logger.warning("未检测到登录状态——请在打开的浏览器窗口中完成学科网会员登录")
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(5)
            self.tab.get(browser_cfg["login_url"])
            time.sleep(2)
            if selector and self.tab.ele(selector):
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
        """在详情页点击下载，等待文件落盘。失败不抛异常，留待后续补下载。"""
        download_dir = Path(self.cfg["crawl"]["download_dir"])
        timeout = int(self.cfg["crawl"].get("download_timeout", 120))
        before = {p.name for p in download_dir.iterdir() if p.is_file()}

        clicked = False
        # 候选下载入口：优先「本地下载」，其次包含「下载」的按钮/链接
        for pattern in ('text=本地下载', 'text=立即下载', 'text=下载资料'):
            ele = self.tab.ele(pattern)
            if ele:
                try:
                    ele.click()
                    clicked = True
                    logger.info(f"点击下载入口：{pattern}")
                    break
                except Exception as e:
                    logger.warning(f"点击 {pattern} 失败：{e}")
        if not clicked:
            logger.warning(f"soft_id={item.soft_id} 未找到下载入口（可能未登录或页面结构变化），跳过下载")
            return

        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(2)
            new_files = [p for p in download_dir.iterdir()
                         if p.is_file() and p.name not in before and not p.name.endswith(".crdownload")]
            if new_files:
                f = max(new_files, key=lambda p: p.stat().st_mtime)
                item.local_path = str(f)
                item.file_size = f.stat().st_size
                item.file_hash = db.md5_file(f)
                logger.info(f"下载完成：{f.name}（{item.file_size} 字节）")
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

    def run(self, keyword: str, max_pages: int | None = None, download: bool | None = None) -> dict:
        crawl_cfg = self.cfg["crawl"]
        max_pages = max_pages or int(crawl_cfg.get("max_pages", 3))
        download = bool(crawl_cfg.get("download", True)) if download is None else download
        interval = float(crawl_cfg.get("interval_seconds", 2))

        stats = {"new": 0, "skipped": 0, "failed": 0, "downloaded": 0}
        self.wait_for_login()

        for page in range(1, max_pages + 1):
            items = self.parse_list_page(keyword, page)
            if not items:
                logger.info("没有更多结果，结束翻页")
                break

            # 断点续爬：批量预取已入库的 source_url
            existing = asyncio.run(db.fetch_existing_urls([it.source_url for it in items]))

            for item in items:
                if item.source_url in existing:
                    stats["skipped"] += 1
                    continue
                try:
                    self.fill_detail(item)   # 详情页已停留，下载直接复用该页面
                    if download:
                        self.download(item)
                        if item.local_path:
                            stats["downloaded"] += 1
                    row_id = asyncio.run(db.insert_item(self.normalize(item)))
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
