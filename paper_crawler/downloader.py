"""
单条试卷下载 - 管理后台资料池页「下载试卷」按钮触发

设计（2026-10-10 重构）：
- 爬虫采集只入库元数据（含详情页地址 source_url），不下载
- 是否下载由人工在资料池页面判断后点按钮触发：
  后台 API -> 本模块子进程（paper_crawler 独立 venv）-> 接管浏览器 ->
  打开详情页 -> 点击下载 -> 归档 -> 回写 local_path/file_size/file_hash ->
  后台自动接网盘上传（upload_pool_row_to_pan）

已实测校准（2026-10-10）：
1. 主按钮 #btnSoftDownload（VIP会员 免费下载）
2. 弹出 layui iframe（#layui-layer-iframe*），内有 .balance-payment-btn「确认」
3. 确认后自动跳转并下载，文件落在 ~/Downloads/zxxk（Chrome 下载目录）
4. 点击主按钮/二次确认/跳转下载页之间都要留等待时间（反爬刷新）
"""
from __future__ import annotations

import re
import time

from loguru import logger

from . import db
from .settings import load_config
from .zxxk import PaperItem, ZxxkCrawler


def download_by_pool_id(pool_id: int, headless: bool | None = None) -> dict:
    """按资料池记录下载单条试卷并回写本地文件信息。

    成功返回 {"id", "local_path", "file_size", "file_hash"}；
    失败抛 RuntimeError（子进程 exit code 非 0，RESULT_JSON 带 message）。
    """
    cfg = load_config()
    crawler = ZxxkCrawler(cfg)
    crawler.start_browser(headless=headless)

    row = db.run(db.fetch_pool_for_download(pool_id))
    if not row:
        raise RuntimeError(f"资料池记录不存在：id={pool_id}")
    if row.get("local_path"):
        raise RuntimeError(f"记录 id={pool_id} 已有本地文件：{row['local_path']}（无需重复下载）")
    source_url = row.get("source_url") or ""
    if not source_url:
        raise RuntimeError(f"记录 id={pool_id} 缺少详情页地址（source_url）")

    m = re.search(r"/soft/(\d+)", source_url)
    item = PaperItem(
        soft_id=m.group(1) if m else str(pool_id),
        title=row.get("paper_name") or "",
        source_url=source_url,
    )

    # 打开详情页：反爬 JS 挑战需要等待完成后再执行下载交互
    crawler.tab.get(source_url)
    time.sleep(4)
    crawler.download(item)

    if not item.local_path:
        raise RuntimeError("下载超时或未触发：请确认爬虫浏览器已登录学科网会员，且会员可下载该资料")

    db.run(db.set_local_file(pool_id, item.local_path, item.file_size or 0, item.file_hash or ""))
    logger.info(f"资料池记录已回写本地文件信息：id={pool_id} -> {item.local_path}")
    return {
        "id": pool_id,
        "local_path": item.local_path,
        "file_size": item.file_size,
        "file_hash": item.file_hash,
    }
