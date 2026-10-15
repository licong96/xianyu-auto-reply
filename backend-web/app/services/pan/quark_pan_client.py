"""
夸克网盘客户端 - kuake_cli 的 Python 封装（资料池自动化流水线）

kuake_cli 为非官方夸克网盘 CLI（github.com/zhangjingwei/kuake_cli），
本机已编译实测通过（见飞书文档《夸克网盘 CLI 操作说明》）。

设计要点：
- subprocess 调用 kuake 二进制，cwd 指向 kuake_cli 目录（其 .env 自带 KUAKE_COOKIE）
- stdout 为 JSON（进度在 stderr），统一解析并抛出 PanError
- 所有路径参数引号包裹，根目录为 "/"
- 配置走项目 .env：KUAKE_BIN / KUAKE_WORKDIR / KUAKE_ROOT_DIR / KUAKE_SHARE_DAYS

# CUSTOM: 资料池自动化流水线（新增文件，合并作者更新零冲突）
"""
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from loguru import logger

# 本机（Windows）构建产物的默认路径；也可在根 .env 用 KUAKE_BIN / KUAKE_WORKDIR 覆盖
DEFAULT_BIN = r"C:\Users\Administrator\Desktop\code\xianyu\kuake_cli\dist\kuake-windows-amd64.exe"
DEFAULT_WORKDIR = r"C:\Users\Administrator\Desktop\code\xianyu\kuake_cli"


class PanError(RuntimeError):
    """网盘操作失败（CLI 返回 success=false、输出非 JSON、超时等）。"""


class QuarkPanClient:
    """kuake_cli 封装。所有 remote 路径以 / 开头。"""

    PAN_TYPE = "quark"

    def __init__(self, bin_path: str | None = None, workdir: str | None = None, timeout: int = 600):
        self.bin = bin_path or os.environ.get("KUAKE_BIN") or DEFAULT_BIN
        self.workdir = workdir or os.environ.get("KUAKE_WORKDIR") or DEFAULT_WORKDIR
        self.timeout = timeout
        if not Path(self.bin).exists():
            raise PanError(f"kuake 二进制不存在：{self.bin}（在 .env 配置 KUAKE_BIN）")

    # ---------- 底层调用 ----------

    def _run(self, *args: str, timeout: int | None = None) -> dict:
        cmd = [self.bin, *[str(a) for a in args]]
        logger.info(f"[pan] {' '.join(cmd)}")
        try:
            # text/encoding：kuake 固定输出 UTF-8 JSON（含中文消息），Windows 上
            # locale 编码（cp1252/cp936）会触发 UnicodeDecodeError 导致读取线程失败、
            # stdout 为空；errors=replace 保证坏字节不致命。
            proc = subprocess.run(
                cmd, capture_output=True, text=True,
                encoding="utf-8", errors="replace",
                cwd=self.workdir, timeout=timeout or self.timeout,
                # stdin 固定为 NUL：kuake 对 delete/download/info 等命令在
                # 「stdin 非字符设备」时会进入管道模式并忽略路径参数；
                # Windows 服务/无控制台运行时 stdin 可能是管道，会导致误触发。
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired as e:
            raise PanError(f"kuake 超时（{timeout or self.timeout}s）：{' '.join(cmd)}") from e
        out = (proc.stdout or "").strip()
        if not out:
            raise PanError(f"kuake 无输出（stderr: {proc.stderr[:300]}）")
        try:
            data = json.loads(out)
        except json.JSONDecodeError as e:
            raise PanError(f"kuake 输出非 JSON：{out[:300]}") from e
        if not data.get("success"):
            raise PanError(f"kuake 失败：{data.get('message') or data.get('code') or out[:300]}")
        return data

    # ---------- 目录 ----------

    def list_dir(self, remote: str = "/") -> dict:
        return self._run("list", remote)

    def create_dir(self, name: str, parent: str = "/") -> dict:
        return self._run("create", name, parent)

    def ensure_dir(self, remote_dir: str) -> None:
        """逐级创建目录（/资料池/学校/年份），已存在则忽略报错。"""
        parts = [p for p in remote_dir.split("/") if p]
        current = ""
        for part in parts:
            parent = current or "/"
            current = f"{current}/{part}"
            try:
                self.create_dir(part, parent)
                logger.info(f"[pan] 创建目录 {current}")
            except PanError:
                pass  # 已存在

    # ---------- 文件与分享 ----------

    def upload(self, local_path: str, remote_path: str, timeout: int = 1800) -> dict:
        """上传文件（大文件给足超时，进度在 stderr）。"""
        if not Path(local_path).exists():
            raise PanError(f"本地文件不存在：{local_path}")
        return self._run("upload", local_path, remote_path, timeout=timeout)

    def share(self, remote_path: str, days: int = 30, passcode: bool = False) -> dict:
        """创建分享，返回 data（含分享链接字段，形状以实际输出为准）。"""
        return self._run("share", remote_path, str(days), "true" if passcode else "false")

    def share_list(self) -> dict:
        return self._run("share-list")

    def share_delete(self, remote_path: str) -> dict:
        return self._run("share-delete", remote_path)

    def delete(self, remote_path: str) -> dict:
        return self._run("delete", remote_path)

    def user_info(self) -> dict:
        return self._run("user")

    # ---------- PanClient 协议方法（多网盘并列查询与合并列表方案） ----------

    def status_check(self) -> None:
        """连接状态：失败抛 PanError（登录失效/二进制不可用/CLI 报错）。"""
        self.user_info()

    def list_entries(self, remote: str = "/") -> list[dict]:
        """列出当前目录，返回统一形状条目（name/path/is_dir/fid/size/mtime）。"""
        data = self.list_dir(remote)
        result = []
        for e in (data.get("data") or {}).get("list") or []:
            result.append({
                "name": e.get("file_name") or "",
                "path": e.get("path") or "",
                "is_dir": bool(e.get("dir")),
                "fid": str(e.get("fid")) if e.get("fid") is not None else None,
                "size": e.get("size"),
                "mtime": e.get("mtime"),
            })
        return result

    def create_share(self, remote_path: str, days: int = 30, passcode: bool = False) -> dict:
        """创建分享，返回统一形状（share_url/pan_code/share_id/share_expire_at）。"""
        data = self.share(remote_path, days=days, passcode=passcode)
        url, code, sid = _extract_share_url(data)
        if not url:
            raise PanError(f"分享成功但未解析到链接，原始返回：{json.dumps(data, ensure_ascii=False)[:500]}")
        if not sid:
            sid = _lookup_share_id(self, url)
        return {
            "share_url": url,
            "pan_code": code,
            "share_id": sid,
            "share_expire_at": datetime.now() + timedelta(days=days),
        }

    def find_share(self, fid: str | None = None, path: str | None = None) -> dict | None:
        """share-list 按 first_fid 反查已有分享（查重）。查不到返回 None。"""
        if not fid:
            return None
        try:
            shares = self.share_list()
        except PanError as e:
            logger.warning(f"[pan] share-list 查重失败：{e}")
            return None
        for item in (shares.get("data") or {}).get("list") or []:
            if str(item.get("first_fid") or "") == str(fid):
                expired_at = item.get("expired_at")
                return {
                    "share_url": item.get("share_url"),
                    "pan_code": item.get("pwd_id") or None,
                    "share_id": str(item.get("share_id") or "") or None,
                    "share_expire_at": datetime.fromtimestamp(expired_at / 1000) if expired_at else None,
                }
        return None


# ---------- 资料池行上传 ----------

def build_remote_path(root_dir: str, school: str | None, year: str | None, filename: str) -> str:
    """远端路径约定：{root}/<学校|未分类>/<年份|未知年份>/<文件名>。"""
    school_part = (school or "未分类").strip() or "未分类"
    year_part = (year or "未知年份").strip() or "未知年份"
    return f"{root_dir.rstrip('/')}/{school_part}/{year_part}/{filename}"


def _extract_share_url(data: dict) -> tuple[str | None, str | None, str | None]:
    """从 share 返回的 data 中提取 (url, passcode, share_id)，兼容不同字段形状。"""
    d = data.get("data") or {}
    url = d.get("share_url") or d.get("url") or d.get("link")
    code = d.get("passcode") or d.get("pwd") or d.get("code") or None
    sid = d.get("share_id") or d.get("shareId") or d.get("id") or None
    return url, code, (str(sid) if sid else None)


def _lookup_share_id(client: "QuarkPanClient", share_url: str) -> str | None:
    """share 命令的返回不含 share_id 时，通过 share-list 按链接反查。"""
    try:
        data = client.share_list().get("data") or {}
        for item in data.get("list") or []:
            if item.get("share_url") == share_url:
                return str(item.get("share_id") or "") or None
    except PanError as e:
        logger.warning(f"[pan] share-list 反查 share_id 失败：{e}")
    return None


async def upload_pool_row_to_pan(row_id: int, client: QuarkPanClient | None = None) -> dict:
    """把资料池的一行（必须有本地文件）上传到夸克网盘、创建分享并回写表中。

    成功后状态置为 pending（待审核）。失败抛 PanError，由调用方记录 error_message。
    """
    from datetime import datetime

    from common.db.session import async_session_maker
    from common.models.material_pool import MaterialPool, STATUS_PENDING

    client = client or QuarkPanClient()
    root_dir = os.environ.get("KUAKE_ROOT_DIR", "/资料池")
    share_days = int(os.environ.get("KUAKE_SHARE_DAYS", "30"))

    async with async_session_maker() as s:
        row = await s.get(MaterialPool, row_id)
        if not row or row.is_deleted:
            raise PanError(f"资料池记录不存在：id={row_id}")
        if not row.local_path or not Path(row.local_path).exists():
            raise PanError(f"记录 id={row_id} 没有本地文件（local_path 为空或文件缺失）")
        if row.pan_url:
            raise PanError(f"记录 id={row_id} 已有分享链接（{row.pan_url}），禁止重复上传")

        local = Path(row.local_path)
        remote_path = build_remote_path(root_dir, row.school, row.year, local.name)
        row.pan_type = "quark"
        row.pan_path = remote_path

        # 上传 + 分享（同步阻塞，文件大时较久；后续可挪到后台任务）
        client.ensure_dir(str(Path(remote_path).parent))
        client.upload(str(local), remote_path)
        logger.info(f"[pan] 上传完成：{remote_path}")
        share_data = client.share(remote_path, days=share_days, passcode=False)
        url, code, sid = _extract_share_url(share_data)
        if not url:
            raise PanError(f"分享成功但未解析到链接，原始返回：{json.dumps(share_data, ensure_ascii=False)[:500]}")
        if not sid:
            sid = _lookup_share_id(client, url)

        row.pan_url = url
        row.pan_code = code
        row.share_id = sid
        row.share_expire_at = datetime.now() + timedelta(days=share_days)
        row.status = STATUS_PENDING
        row.error_message = None

        # 多网盘子表：新上传记录同步落一条夸克链接（卡券遍历子表输出）
        from common.models.material_pool_link import MaterialPoolPanLink
        s.add(MaterialPoolPanLink(
            pool_id=row.id,
            pan_type="quark",
            pan_path=remote_path,
            fid=None,
            file_size=local.stat().st_size,
            pan_url=url,
            pan_code=code,
            share_id=sid,
            share_expire_at=row.share_expire_at,
        ))

        await s.commit()
        logger.info(f"[pan] 分享已创建并回写：id={row_id} -> {url}")
        return {"id": row_id, "pan_url": url, "pan_code": code, "share_id": sid}
