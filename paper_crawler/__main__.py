"""
资料池爬虫 - 学科网试卷采集工具（独立可运行）

用法（在项目根目录或 paper_crawler 目录执行均可）：

    # 首次运行：浏览器弹出后，人工完成学科网会员登录，然后自动开始采集
    paper_crawler/.venv/bin/python -m paper_crawler --keyword 学军中学

    # 只采元数据不下载（先囤数据，之后补下载）
    paper_crawler/.venv/bin/python -m paper_crawler --keyword 学军中学 --no-download

    # 指定页数 / 间隔 / 无头模式
    paper_crawler/.venv/bin/python -m paper_crawler --keyword 学军中学 --pages 5 --interval 3

数据写入项目 MySQL 的 xy_material_pool 表（status=crawled），
后续由系统内的网盘上传服务和一键上架流水线接力。
"""
from __future__ import annotations

import argparse
import sys

from loguru import logger

from .settings import load_config
from .zxxk import ZxxkCrawler


def main() -> None:
    parser = argparse.ArgumentParser(description="学科网试卷爬虫（资料池流水线第一步）")
    parser.add_argument("--keyword", help="搜索关键词，默认取 config.yaml 的 default_keyword")
    parser.add_argument("--pages", type=int, help="最多翻页数")
    parser.add_argument("--no-download", action="store_true", help="只采元数据，不下载文件")
    parser.add_argument("--headless", action="store_true", help="无头模式（首次登录不要用）")
    args = parser.parse_args()

    logger.remove()
    logger.add(sys.stderr, level="INFO", format="<green>{time:MM-DD HH:mm:ss}</green> | <level>{message}</level>")

    cfg = load_config()
    keyword = args.keyword or cfg["crawl"].get("default_keyword", "学军中学")
    crawler = ZxxkCrawler(cfg)
    crawler.start_browser(headless=args.headless or None)
    try:
        stats = crawler.run(keyword, max_pages=args.pages, download=not args.no_download)
        logger.info(f"采集完成：新增 {stats['new']}，跳过 {stats['skipped']}，下载 {stats['downloaded']}，失败 {stats['failed']}")
    except KeyboardInterrupt:
        logger.warning("人工中断，已入库数据保留（断点续爬，重跑自动跳过）")


if __name__ == "__main__":
    main()
