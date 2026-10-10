"""
资料池爬虫 - 学科网试卷采集/下载工具（独立可运行）

子命令：
1. 采集（默认）：只爬元数据入库，不下载
       paper_crawler/.venv/bin/python -m paper_crawler --keyword 学军中学
       paper_crawler/.venv/bin/python -m paper_crawler crawl --keyword 学军中学 --limit 1

2. 下载：按资料池记录单条下载（后台「下载试卷」按钮走的就是这条链路）
       paper_crawler/.venv/bin/python -m paper_crawler download --id 5

数据写入项目 MySQL 的 xy_material_pool 表（status=crawled），
下载成功 + 网盘上传成功后由 upload_pool_row_to_pan 置为 pending（待审核），
后续由审核 / 一键上架流水线接力。
"""
from __future__ import annotations

import argparse
import json
import sys

from loguru import logger

from . import db
from .settings import load_config
from .zxxk import ZxxkCrawler


def _config_logger() -> None:
    logger.remove()
    logger.add(sys.stderr, level="INFO", format="<green>{time:MM-DD HH:mm:ss}</green> | <level>{message}</level>")


def _dispose_db() -> None:
    try:
        db.run(db.dispose())
    except Exception:
        pass


def main_crawl(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="paper_crawler crawl", description="学科网试卷采集（只采元数据，不下载）")
    parser.add_argument("--keyword", help="搜索关键词，默认取 config.yaml 的 default_keyword")
    parser.add_argument("--pages", type=int, help="最多翻页数")
    parser.add_argument("--headless", action="store_true", help="无头模式（首次登录不要用）")
    parser.add_argument("--limit", type=int, help="最多采集条数（测试阶段用，如 1）")
    parser.add_argument("--no-login-wait", action="store_true",
                        help="跳过登录检测（已确认浏览器登录态有效时用）")
    args = parser.parse_args(argv)

    cfg = load_config()
    keyword = args.keyword or cfg["crawl"].get("default_keyword", "学军中学")
    crawler = ZxxkCrawler(cfg)
    crawler.start_browser(headless=args.headless or None)
    try:
        stats = crawler.run(keyword, max_pages=args.pages,
                            limit=args.limit, skip_login_wait=args.no_login_wait)
        logger.info(f"采集完成：新增 {stats['new']}，跳过 {stats['skipped']}，"
                    f"失败 {stats['failed']}，关键词过滤 {stats['keyword_filtered']}")
    except KeyboardInterrupt:
        logger.warning("人工中断，已入库数据保留（断点续爬，重跑自动跳过）")
    finally:
        _dispose_db()


def main_download(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="paper_crawler download", description="按资料池记录下载单条试卷")
    parser.add_argument("--id", type=int, required=True, help="xy_material_pool 记录 ID")
    parser.add_argument("--headless", action="store_true", help="无头模式（默认有头，便于人工处理验证）")
    args = parser.parse_args(argv)

    # 结果以 RESULT_JSON: 前缀打印到 stdout 最后一行，供后台子进程解析；
    # 过程日志全部走 stderr（loguru），不污染结果行
    try:
        from .downloader import download_by_pool_id

        result = download_by_pool_id(args.id, headless=True if args.headless else None)
        print("RESULT_JSON:" + json.dumps({"success": True, "data": result}, ensure_ascii=False))
    except Exception as e:  # noqa: BLE001 - 结果统一走 RESULT_JSON，后台按 exit code 判断
        print("RESULT_JSON:" + json.dumps({"success": False, "message": str(e)}, ensure_ascii=False))
        sys.exit(1)
    finally:
        _dispose_db()


def main() -> None:
    _config_logger()
    argv = sys.argv[1:]
    if argv and argv[0] == "download":
        main_download(argv[1:])
    elif argv and argv[0] == "crawl":
        main_crawl(argv[1:])
    else:
        main_crawl(argv)


if __name__ == "__main__":
    main()
