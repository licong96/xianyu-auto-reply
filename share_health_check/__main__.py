"""网盘分享内容存活巡检 - 子进程入口。

scheduler 的 share_health_check 任务调用方式：
    paper_crawler/.venv/bin/python -m share_health_check check --batch-size 50

退出前最后一行输出 RESULT_JSON: 前缀的 JSON 摘要，供父进程解析。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

from loguru import logger


def _config_logger() -> None:
    logger.remove()
    logger.add(sys.stderr, level="INFO", format="<green>{time:MM-DD HH:mm:ss}</green> | <level>{message}</level>")


def main_check(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="share_health_check check", description="网盘分享内容存活巡检")
    parser.add_argument("--batch-size", type=int, default=50, help="本批最多检查条数（默认 50）")
    parser.add_argument("--port", type=int, default=None, help="Chromium 调试端口（默认 9145）")
    parser.add_argument("--browser-path", default=None, help="Chrome/Chromium 可执行文件路径")
    args = parser.parse_args(argv)

    from common.db.session import async_session_maker

    from .browser import DEFAULT_PORT, get_checker_browser
    from .runner import run_check_batch

    port = args.port or DEFAULT_PORT
    browser = None
    try:
        browser = get_checker_browser(port=port, browser_path=args.browser_path)
        tab = browser.latest_tab

        async def _run() -> dict:
            async with async_session_maker() as session:
                return await run_check_batch(session, lambda: tab, batch_size=args.batch_size)

        stats = asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001 - 子进程失败也要输出 RESULT_JSON
        print(f"RESULT_JSON:{json.dumps({'success': False, 'error': str(exc)}, ensure_ascii=False)}")
        return 1
    finally:
        if browser is not None:
            try:
                browser.quit()
            except Exception:
                pass

    summary = {"success": True, **stats}
    print(f"RESULT_JSON:{json.dumps(summary, ensure_ascii=False)}")
    return 0


def main() -> None:
    _config_logger()
    argv = sys.argv[1:]
    if not argv or argv[0] != "check":
        print("用法：python -m share_health_check check [--batch-size 50] [--port 9145]")
        sys.exit(2)
    sys.exit(main_check(argv[1:]))


if __name__ == "__main__":
    main()
