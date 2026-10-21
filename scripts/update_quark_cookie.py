# -*- coding: utf-8 -*-
"""
夸克网盘 Cookie 更新脚本

用途：
1. 默认模式：打开浏览器访问夸克网盘，用户扫码/登录后，自动把登录态 Cookie
   写回 kuake_cli 的 .env（KUAKE_COOKIE 行），并做下载鉴权验证。
2. --check 模式：不打开浏览器，仅验证当前 .env 里的 Cookie 是否通过
   夸克下载回调鉴权（上传探针文件 -> 取下载链接 -> 请求 -> 清理）。

用法：
    backend-web/.venv/Scripts/python.exe scripts/update_quark_cookie.py
    backend-web/.venv/Scripts/python.exe scripts/update_quark_cookie.py --check
    backend-web/.venv/Scripts/python.exe scripts/update_quark_cookie.py --kuake-dir "C:\\path\\to\\kuake_cli"

依赖：backend-web 虚拟环境里的 playwright（已随项目安装）。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_KUAKE_DIR = r"C:\Users\Administrator\Desktop\code\xianyu\kuake_cli"
KUAKE_BIN_REL = r"dist\kuake-windows-amd64.exe"
PAN_URL = "https://pan.quark.cn/"
# 下载回调鉴权需要的关键 Cookie（__pus/__puus 是夸克登录态核心令牌）
REQUIRED_COOKIES = ("__pus", "__puus")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36")


# ---------- kuake 调用与 .env 读写 ----------

def find_kuake_bin(kuake_dir: Path) -> Path:
    env_bin = __import__("os").environ.get("KUAKE_BIN")
    candidates = [Path(env_bin)] if env_bin else []
    candidates.append(kuake_dir / KUAKE_BIN_REL)
    for c in candidates:
        if c.exists():
            return c
    raise SystemExit(f"[FAIL] 找不到 kuake 二进制，请用 --kuake-dir 指定 kuake_cli 目录")


def kuake_run(kuake_dir: Path, *args: str, timeout: int = 180) -> dict:
    """调用 kuake CLI 并解析 JSON 输出（等价于生产服务的调用方式）。"""
    proc = subprocess.run(
        [str(find_kuake_bin(kuake_dir)), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(kuake_dir), stdin=subprocess.DEVNULL, timeout=timeout,
    )
    out = (proc.stdout or "").strip()
    if not out:
        raise RuntimeError(f"kuake 无输出（stderr: {(proc.stderr or '')[:200]}）")
    return json.loads(out)


def read_env_cookie(env_path: Path) -> str | None:
    if not env_path.exists():
        return None
    for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"\s*KUAKE_COOKIE\s*=\s*(.+)", line)
        if m:
            return m.group(1).strip().strip('"').strip("'")
    return None


def write_env_cookie(env_path: Path, cookie: str) -> None:
    """替换 .env 里的 KUAKE_COOKIE 行（不存在则追加），先备份。"""
    text = env_path.read_text(encoding="utf-8", errors="replace") if env_path.exists() else ""
    bak = env_path.with_suffix(env_path.suffix + f".bak-{time.strftime('%Y%m%d-%H%M%S')}")
    if env_path.exists():
        bak.write_text(text, encoding="utf-8")

    new_line = f"KUAKE_COOKIE={cookie}"
    lines = text.splitlines()
    replaced = False
    for i, line in enumerate(lines):
        if re.match(r"\s*KUAKE_COOKIE\s*=", line):
            lines[i] = new_line
            replaced = True
    if not replaced:
        if lines and lines[-1].strip():
            lines.append(new_line)
        else:
            lines.append(new_line)
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[OK] 已写入 {env_path}（原文件备份：{bak.name}）" if env_path.exists() else "")


# ---------- 下载鉴权验证（探针法） ----------

def verify_download_auth(kuake_dir: Path, cookie: str | None) -> bool:
    """上传探针文件 -> 取签名 URL -> 请求 -> 删除。返回是否通过鉴权。

    无 Cookie 请求返回 200 才算通过；403 RequestDeniedByCallback
    （auth not found / auth fail / auth expired）均视为未通过。
    """
    probe_remote_dir = "/_cookie_check"
    probe_remote = f"{probe_remote_dir}/probe.txt"
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("quark cookie check " + "x" * 256)
            local = f.name

        r = kuake_run(kuake_dir, "upload", local, probe_remote)
        if not r.get("success"):
            print(f"[WARN] 探针上传失败，跳过验证：{r.get('message', '')[:100]}")
            return False

        r = kuake_run(kuake_dir, "download", probe_remote)
        if not r.get("success"):
            print(f"[WARN] 获取探针下载链接失败，跳过验证：{r.get('message', '')[:100]}")
            return False
        url = r["data"]["download_url"]

        headers = {"User-Agent": UA}
        if cookie:
            headers["Cookie"] = cookie
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                ok = resp.status == 200
                print(f"[VERIFY] 下载探针 status={resp.status} -> {'通过' if ok else '异常'}")
                return ok
        except urllib.error.HTTPError as e:
            body = e.read(300)
            m = re.search(rb"<Code>([^<]+)</Code>", body)
            reason = re.search(rb"reason: ([^<\"]+)", body)
            detail = (reason.group(1).decode() if reason
                      else (m.group(1).decode() if m else body[:80].decode("utf-8", "replace")))
            print(f"[VERIFY] 下载探针 status={e.code} ({detail}) -> 未通过")
            return False
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] 验证过程异常，跳过验证：{e}")
        return False
    finally:
        for path in (probe_remote, probe_remote_dir):
            try:
                kuake_run(kuake_dir, "delete", path, timeout=60)
            except Exception:  # noqa: BLE001
                pass
        try:
            Path(local).unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass


# ---------- 浏览器扫码登录 ----------

def grab_cookie_via_browser(kuake_dir: Path, timeout: int) -> str | None:
    from playwright.sync_api import sync_playwright

    profile_dir = kuake_dir / ".quark_cookie_browser"
    profile_dir.mkdir(exist_ok=True)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=False,
            viewport=None,
            args=["--start-maximized"],
        )
        page = context.new_page()
        page.goto(PAN_URL, wait_until="domcontentloaded")
        print("=" * 50)
        print("已在浏览器打开夸克网盘。")
        print("如未登录：请用手机夸克 App 扫码；如已登录：无需操作。")
        print(f"等待登录完成（最长 {timeout} 秒，可随时 Ctrl+C 取消）...")
        print("=" * 50)

        deadline = time.time() + timeout
        while time.time() < deadline:
            cookies = {c["name"]: c["value"]
                       for c in context.cookies()
                       if c["domain"].endswith("quark.cn")}
            if all(k in cookies for k in REQUIRED_COOKIES):
                cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
                context.close()
                print(f"[OK] 检测到登录态（共 {len(cookies)} 个 Cookie 字段）")
                return cookie_str
            time.sleep(2)

        context.close()
        print("[FAIL] 等待超时，未检测到完整登录态")
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="夸克网盘 Cookie 获取/更新工具")
    parser.add_argument("--check", action="store_true",
                        help="只验证当前 .env 里的 Cookie，不打开浏览器")
    parser.add_argument("--kuake-dir", default=DEFAULT_KUAKE_DIR,
                        help="kuake_cli 目录（默认本机路径）")
    parser.add_argument("--timeout", type=int, default=180,
                        help="扫码登录等待秒数（默认 180）")
    args = parser.parse_args()

    kuake_dir = Path(args.kuake_dir)
    env_path = kuake_dir / ".env"

    if args.check:
        cookie = read_env_cookie(env_path)
        if not cookie:
            raise SystemExit(f"[FAIL] {env_path} 中没有 KUAKE_COOKIE")
        print(f"当前 Cookie 长度 {len(cookie)}，开始下载鉴权验证...")
        ok = verify_download_auth(kuake_dir, cookie)
        raise SystemExit(0 if ok else 1)

    cookie = grab_cookie_via_browser(kuake_dir, args.timeout)
    if not cookie:
        raise SystemExit(1)

    if not env_path.exists():
        raise SystemExit(f"[FAIL] 找不到 {env_path}")
    write_env_cookie(env_path, cookie)

    print("开始下载鉴权验证（上传探针文件...）")
    if verify_download_auth(kuake_dir, cookie):
        print("[DONE] Cookie 已更新并通过验证，网盘同步功能可正常使用。")
    else:
        print("[DONE] Cookie 已写入，但下载鉴权未通过——")
        print("       请确认登录的是正确账号，或稍后重试（新登录态可能有数分钟生效延迟）。")
        sys.exit(1)


if __name__ == "__main__":
    main()
