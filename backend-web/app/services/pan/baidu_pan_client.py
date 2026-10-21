"""
百度网盘客户端 - BaiduPCS-Go 的 Python 封装（多网盘并列查询与合并列表方案）

BaiduPCS-Go 为开源百度网盘 CLI（github.com/qjfoidnh/BaiduPCS-Go，实测 v4.0.2 通过）。

设计要点：
- subprocess 调用 BaiduPCS-Go 二进制；输出为人类可读表格（无 JSON 模式），按固定列解析
- 登录：BaiduPCS-Go login -cookies="BDUSS=..; STOKEN=.."（一次性，登录态持久化在
  ~/.config/BaiduPCS-Go，后续子进程调用免登录）。cookie 从 pan.baidu.com 的
  开发者工具中获取，配置走项目 .env：BAIDUPCS_BIN
- 分享查重按路径：share list 的「特征路径」列即完整网盘路径（百度 ls 不输出 fs_id，
  find_share 因此以 path 为准，fid 仅作参考）
- 分享有效期：share set --period 天数（0=永久，默认 30 天对齐夸克的 KUAKE_SHARE_DAYS）

# CUSTOM: 多网盘并列查询与合并列表方案（新增文件，合并作者更新零冲突）
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

from loguru import logger

from app.services.pan.quark_pan_client import PanError, PanPathNotFoundError

_SIZE_UNITS = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}

# ls 表格行：序号  大小  修改日期  名称（目录名以 / 结尾）
_LS_ROW_RE = re.compile(
    r"^\s*\d+\s+(?P<size>\S+)\s+(?P<date>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s+(?P<name>.+?)\s*$"
)
# share set 输出：shareID: 16063199351, 链接: https://pan.baidu.com/s/xxx, 密码: 4d3e
_SHARE_SET_RE = re.compile(
    r"shareID:\s*(?P<sid>\d+),\s*链接:\s*(?P<url>https?://[^\s,]+)(?:,\s*密码:\s*(?P<pwd>\S+))?"
)
# download 输出：「下载完成, 保存位置: /xxx/yyy/文件名」（savedir 下会再套一层 用户ID_用户名 目录）
_DOWNLOAD_SAVED_RE = re.compile(r"保存位置:\s*(?P<path>\S+)")
# upload 输出：「上传文件成功, 保存到网盘路径: /xxx/文件名」
_UPLOAD_SAVED_RE = re.compile(r"保存到网盘路径:\s*(?P<path>\S+)")


def _human_size_to_bytes(text: str) -> int | None:
    """'24.26MB' / '17518712' / '-' → 字节数。"""
    text = text.strip()
    if text == "-":
        return None
    m = re.match(r"^([\d.]+)([A-Za-z]+)$", text)
    if m and m.group(2).upper() in _SIZE_UNITS:
        return int(float(m.group(1)) * _SIZE_UNITS[m.group(2).upper()])
    if text.isdigit():
        return int(text)
    return None


class BaiduPanClient:
    """BaiduPCS-Go 封装。所有 remote 路径以 / 开头。实现 PanClient 协议。"""

    PAN_TYPE = "baidu"

    def __init__(self, bin_path: str | None = None, timeout: int = 300):
        self.bin = bin_path or os.environ.get("BAIDUPCS_BIN")
        if not self.bin:
            raise PanError("未配置 BAIDUPCS_BIN（BaiduPCS-Go 二进制路径）")
        if not Path(self.bin).exists():
            raise PanError(f"BaiduPCS-Go 二进制不存在：{self.bin}（在 .env 配置 BAIDUPCS_BIN）")
        self.timeout = timeout

    # ---------- 底层调用 ----------

    def _run(self, *args: str, timeout: int | None = None) -> str:
        cmd = [self.bin, *[str(a) for a in args]]
        logger.info(f"[pan:baidu] {' '.join(cmd)}")
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True,
                encoding="utf-8", errors="replace",
                timeout=timeout or self.timeout,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired as e:
            raise PanError(f"BaiduPCS-Go 超时（{timeout or self.timeout}s）：{' '.join(cmd)}") from e
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        # BaiduPCS-Go 失败时仍可能返回 0，错误信息混在 stdout/stderr
        combined = out + "\n" + err
        # 代码 -9 = 文件不存在：多网盘允许文件只存在一个盘，另一盘路径不存在
        # 不是错误（分享/列目录遇到都按「该盘无此项」处理）
        if re.search(r"代码:\s*-9\b", combined) or "消息: 文件不存在" in combined:
            raise PanPathNotFoundError(f"BaiduPCS-Go 路径不存在：{combined.strip()[:300]}")
        if "遇到错误" in combined or "请尝试重新登录" in combined or "user not exists" in combined:
            raise PanError(f"BaiduPCS-Go 失败：{combined.strip()[:300]}")
        if not out:
            raise PanError(f"BaiduPCS-Go 无输出（stderr: {err[:300]}）")
        return out

    # ---------- PanClient 协议方法 ----------

    def status_check(self) -> None:
        """连接状态：quota 能取到用户名即登录有效。"""
        out = self._run("quota", timeout=60)
        if "用户名:" not in out:
            raise PanError(f"百度网盘状态异常：{out[:300]}")

    def list_entries(self, remote: str = "/") -> list[dict]:
        """列出当前目录（单层），返回统一形状条目。"""
        out = self._run("ls", remote, timeout=120)
        entries = []
        for line in out.splitlines():
            m = _LS_ROW_RE.match(line)
            if not m:
                continue
            name = m.group("name")
            is_dir = name.endswith("/")
            if is_dir:
                name = name.rstrip("/")
            if not name:
                continue
            base = remote.rstrip("/")
            path = f"{base}/{name}" if base else f"/{name}"
            try:
                dt = datetime.strptime(m.group("date"), "%Y-%m-%d %H:%M:%S")
                mtime = int(time.mktime(dt.timetuple()))
            except ValueError:
                mtime = None
            entries.append({
                "name": name,
                "path": path,
                "is_dir": is_dir,
                "fid": None,  # 百度 ls 不输出 fs_id，查重按路径
                "size": None if is_dir else _human_size_to_bytes(m.group("size")),
                "mtime": mtime,
            })
        return entries

    def create_share(self, remote_path: str, days: int = 30, passcode: bool = False) -> dict:
        """创建分享（--period 天数，0=永久）。返回统一形状。"""
        args = ["share", "set", remote_path, "--period", str(days)]
        if passcode:
            args.append("-f")  # 带密码完整链接格式（密码由百度生成）
        out = self._run(*args, timeout=120)
        m = _SHARE_SET_RE.search(out)
        if not m:
            raise PanError(f"分享成功但未解析到链接，原始返回：{out[:300]}")
        pwd = m.group("pwd")
        if pwd == ".":
            pwd = None
        return {
            "share_url": m.group("url"),
            "pan_code": pwd,
            "share_id": m.group("sid"),
            "share_expire_at": datetime.now() + timedelta(days=days) if days > 0 else None,
        }

    def find_share(self, fid: str | None = None, path: str | None = None) -> dict | None:
        """share list 按「特征路径」反查已有分享（查重）。查不到返回 None。"""
        if not path:
            return None
        try:
            out = self._run("share", "list", timeout=120)
        except PanError as e:
            logger.warning(f"[pan:baidu] share list 查重失败：{e}")
            return None
        for line in out.splitlines():
            # 列按 2+ 空格分隔：# SHAREID 链接 密码 特征目录 特征路径 过期时间 浏览次数
            fields = [f.strip() for f in re.split(r"\s{2,}", line.strip()) if f.strip()]
            if path not in fields:
                continue
            sid = url = pwd = None
            expire_at = None
            for f in fields:
                if f.isdigit() and len(f) >= 8 and not sid:
                    sid = f
                elif f.startswith("http"):
                    url = f
                elif re.fullmatch(r"[0-9a-zA-Z]{4}", f):
                    pwd = f
                elif re.fullmatch(r"\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}", f):
                    try:
                        dt = datetime.strptime(f, "%Y/%m/%d %H:%M:%S")
                        expire_at = datetime.fromtimestamp(time.mktime(dt.timetuple()))
                    except ValueError:
                        pass
            if url:
                return {
                    "share_url": url,
                    "pan_code": pwd,
                    "share_id": sid,
                    "share_expire_at": expire_at,
                }
        return None

    # ---------- 附加能力（巡检/清理用，协议外） ----------

    def cancel_share(self, share_id: str) -> None:
        """取消分享（share cancel <shareid>）。"""
        self._run("share", "cancel", str(share_id), timeout=60)

    # ---------- 同步能力（网盘互传用，协议外） ----------

    def set_savedir(self, local_dir: str) -> None:
        """设置 download 命令的本地保存目录（BaiduPCS-Go 为全局配置，每次任务前设置）。"""
        self._run("config", "set", "-savedir", str(local_dir), timeout=60)

    def download(self, remote_path: str, local_dir: str, timeout: int = 7200) -> str:
        """下载单个文件到 local_dir，返回实际落盘的本地文件路径。

        BaiduPCS-Go 会在 savedir 下再套一层「用户ID_用户名」目录，
        实际路径从「保存位置:」行解析，调用方不要自行拼路径。
        """
        local_dir = str(local_dir)
        self.set_savedir(local_dir)
        out = self._run("download", remote_path, timeout=timeout)
        m = _DOWNLOAD_SAVED_RE.search(out)
        if not m:
            raise PanError(f"下载成功但未解析到保存位置，原始返回：{out[:300]}")
        local_path = m.group("path")
        if not Path(local_path).is_file():
            raise PanError(f"下载输出路径不存在：{local_path}")
        return local_path

    def make_dir(self, remote: str) -> None:
        """创建目录（mkdir <path>）。已存在时 CLI 报错，由 ensure_dir 吞掉。"""
        self._run("mkdir", remote, timeout=120)

    def ensure_dir(self, remote_dir: str) -> None:
        """逐级创建目录（/a/b/c），已存在则忽略报错。"""
        parts = [p for p in remote_dir.split("/") if p]
        current = ""
        for part in parts:
            current = f"{current}/{part}"
            try:
                self.make_dir(current)
            except PanError:
                pass  # 已存在

    def upload(self, local_path: str, remote_dir: str, timeout: int = 7200) -> str:
        """上传本地文件到远端目录，返回远端完整路径（remote_dir/文件名）。"""
        local_path = str(local_path)
        if not Path(local_path).is_file():
            raise PanError(f"本地文件不存在：{local_path}")
        out = self._run("upload", local_path, remote_dir, timeout=timeout)
        m = _UPLOAD_SAVED_RE.search(out)
        if not m:
            raise PanError(f"上传成功但未解析到网盘路径，原始返回：{out[:300]}")
        return m.group("path")

    def delete(self, remote_path: str) -> None:
        """删除文件/目录（rm <path>），进回收站可找回。"""
        out = self._run("rm", remote_path, timeout=300)
        if "操作成功" not in out and "已删除" not in out:
            raise PanError(f"删除失败：{out[:300]}")
