"""巡检浏览器实例管理（共享启动/接管逻辑）。

与 paper_crawler 的浏览器独立：独立调试端口 + 独立用户数据目录，
不影响爬虫、滑块等其他浏览器实例。实现参考 quark-模拟代码/browser.py。
"""
from __future__ import annotations

import os
import subprocess
import time
from typing import Optional
from urllib.error import URLError
from urllib.request import urlopen

BROWSER_STARTUP_DELAY = 3
EXTERNAL_BROWSER_CONNECT_TIMEOUT = 15

# 巡检专用实例：独立端口与用户数据目录
DEFAULT_PORT = 9145
USER_DATA_DIR_NAME = "chrome_user_data_share_checker"


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _browser_debugger_alive(port: int) -> bool:
    try:
        with urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1) as resp:
            return resp.status == 200
    except (OSError, URLError):
        return False


def _launch_detached_chrome(port: int, user_data_dir: str, browser_path: Optional[str] = None) -> None:
    """独立启动 Chrome，使 Python/终端停止后浏览器仍保持运行。"""
    path = browser_path or _default_chrome_path()
    args = [
        path,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--disable-popup-blocking",
    ]
    subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=True,
    )


def _default_chrome_path() -> str:
    try:
        from DrissionPage._functions.browser import get_chrome_path

        return get_chrome_path(None) or "chrome"
    except Exception:
        return "chrome"


def _ensure_detached_browser(port: int, user_data_dir: str, browser_path: Optional[str] = None) -> None:
    if not _browser_debugger_alive(port):
        _launch_detached_chrome(port, user_data_dir, browser_path=browser_path)

    end_at = time.time() + EXTERNAL_BROWSER_CONNECT_TIMEOUT
    while time.time() < end_at:
        if _browser_debugger_alive(port):
            return
        time.sleep(0.5)
    raise RuntimeError(f"Chrome 启动后无法连接调试端口：127.0.0.1:{port}")


def get_checker_browser(port: int = DEFAULT_PORT, browser_path: Optional[str] = None):
    """启动或接管巡检专用 Chromium，使用独立用户数据目录。"""
    from DrissionPage import Chromium, ChromiumOptions

    user_data_dir = os.path.join(_repo_root(), "share_health_check", USER_DATA_DIR_NAME)
    os.makedirs(user_data_dir, exist_ok=True)
    _ensure_detached_browser(port, user_data_dir, browser_path=browser_path)

    co = (
        ChromiumOptions()
        .set_local_port(port)
        .set_user_data_path(user_data_dir)
        .set_argument("--no-first-run")
        .set_argument("--disable-popup-blocking")
        .existing_only()
    )
    browser = Chromium(addr_or_opts=co)
    print(f"巡检 Chromium 已启动或已接管，端口：{port}")
    time.sleep(BROWSER_STARTUP_DELAY)
    return browser
