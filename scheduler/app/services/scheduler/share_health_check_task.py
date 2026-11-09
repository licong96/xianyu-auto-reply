"""网盘分享内容存活巡检定时任务。

功能：
1. 默认每天触发一次，以子进程方式运行 share_health_check 包
   （DrissionPage 只装在 paper_crawler 独立 venv，与其他服务隔离的既定模式）：
       paper_crawler/.venv/bin/python -m share_health_check check --batch-size 50
2. 子进程真实浏览器逐个打开分享地址，判定 valid/invalid/empty/error/risk，
   回写 xy_material_pool_pan_links 并落 xy_share_check_logs（只读+记录，不做自动处理）
3. 父进程解析子进程最后一行 RESULT_JSON: 摘要；invalid/empty/risk 按资料池绑定的
   闲鱼账号发通知（人工判断处理），条目间随机等待与单页超时由子进程控制

# CUSTOM: 网盘分享自动续期与内容存活巡检（新增文件，合并作者更新零冲突）
"""
from __future__ import annotations

import asyncio
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from loguru import logger
from sqlalchemy import select

from common.db.session import async_session_maker
from common.models.material_pool import MaterialPool
from common.services.account_notification_dispatch import send_account_notification

# 通知冷却：同一账号同类型 6 小时最多 1 条
NOTIFY_COOLDOWN_SECONDS = 6 * 3600
NOTIFY_TYPE = "share_link_invalid"


def _repo_root() -> Path:
    # scheduler/app/services/scheduler/xxx.py -> 仓库根
    return Path(__file__).resolve().parents[4]


def _checker_python() -> Path:
    return _repo_root() / "paper_crawler" / ".venv" / "bin" / "python"


def _parse_result(stdout: str) -> Dict[str, Any]:
    """取子进程输出最后一行 RESULT_JSON: 前缀的 JSON。"""
    line = next(
        (ln for ln in reversed((stdout or "").splitlines()) if ln.startswith("RESULT_JSON:")),
        None,
    )
    if not line:
        return {"success": False, "error": "子进程无 RESULT_JSON 输出"}
    try:
        return json.loads(line[len("RESULT_JSON:"):])
    except json.JSONDecodeError as exc:
        return {"success": False, "error": f"RESULT_JSON 解析失败：{exc}"}


async def _notify_invalid_pools(entries: List[Dict[str, Any]]) -> int:
    """按资料池绑定的闲鱼账号发送巡检异常通知，返回发送条数。"""
    if not entries:
        return 0
    pool_ids = {int(e["pool_id"]) for e in entries if e.get("pool_id")}
    async with async_session_maker() as session:
        rows = (
            await session.execute(select(MaterialPool).where(MaterialPool.id.in_(pool_ids)))
        ).scalars().all()
        pools = {p.id: p for p in rows}

    sent = 0
    for entry in entries:
        pool = pools.get(int(entry.get("pool_id") or 0))
        account_id = getattr(pool, "account_id", None) if pool else None
        if not account_id:
            logger.info(f"【分享存活巡检】pool={entry.get('pool_id')} 无绑定闲鱼账号，跳过通知")
            continue
        title = {
            "invalid": "❌ 网盘分享链接已失效",
            "empty": "⚠️ 网盘分享内容为空",
            "risk": "🚧 网盘巡检触发风控/登录墙",
        }.get(entry.get("status"), "⚠️ 网盘分享巡检异常")
        name = getattr(pool, "paper_name", None) or f"资料池记录 {pool.id}"
        detail = (
            f"资料：{name}\n"
            f"网盘：{entry.get('pan_type')}  状态：{entry.get('status')}\n"
            f"地址：{entry.get('url')}\n"
            f"说明：{entry.get('detail')}\n"
            f"请到资料池手动点「重新分享」修复（会自动同步更新卡券发货地址）。"
        )
        try:
            if await send_account_notification(
                str(account_id), title, detail,
                notification_type=NOTIFY_TYPE,
                cooldown_seconds=NOTIFY_COOLDOWN_SECONDS,
            ):
                sent += 1
        except Exception as exc:  # noqa: BLE001 - 通知失败不影响任务
            logger.error(f"【分享存活巡检】通知发送失败 account={account_id}：{exc}")
    return sent


class ShareHealthCheckTaskService:
    """网盘分享内容存活巡检任务服务"""

    def __init__(self):
        self.task_name = "网盘分享存活巡检"

    async def execute(self, batch_size: int = 50) -> dict:
        """执行一轮巡检（子进程）。"""
        logger.info(f"【{self.task_name}】开始执行")
        start_time = datetime.now()

        py = _checker_python()
        if not py.exists():
            logger.error(f"【{self.task_name}】巡检解释器不存在：{py}")
            return {"success": False, "error": f"巡检解释器不存在：{py}"}

        cmd = [str(py), "-m", "share_health_check", "check", "--batch-size", str(batch_size)]
        try:
            # 子进程是同步阻塞调用且最长可达 40 分钟：放线程池执行，
            # 避免冻结 scheduler 共享事件循环（补发货等高频任务同循环运行）
            proc = await asyncio.to_thread(
                subprocess.run,
                cmd, cwd=str(_repo_root()), capture_output=True, text=True, timeout=2400,
            )
        except subprocess.TimeoutExpired:
            logger.error("【分享存活巡检】子进程超时（40 分钟）")
            return {"success": False, "error": "子进程超时"}
        except Exception as exc:  # noqa: BLE001
            logger.error(f"【分享存活巡检】子进程启动失败：{exc}")
            return {"success": False, "error": str(exc)}

        result = _parse_result(proc.stdout)
        elapsed = (datetime.now() - start_time).total_seconds()
        if not result.get("success"):
            tail = "\n".join((proc.stderr or "").splitlines()[-10:])
            logger.error(
                f"【分享存活巡检】执行失败：{result.get('error')}，耗时 {elapsed:.1f} 秒\n{tail}"
            )
            return result

        logger.info(
            f"【分享存活巡检】完成：检查 {result.get('checked', 0)}，"
            f"valid {result.get('valid', 0)}，invalid {result.get('invalid', 0)}，"
            f"empty {result.get('empty', 0)}，error {result.get('error', 0)}，"
            f"risk {result.get('risk', 0)}，耗时 {elapsed:.1f} 秒"
        )
        try:
            sent = await _notify_invalid_pools(result.get("notify") or [])
            if sent:
                logger.info(f"【分享存活巡检】已发送 {sent} 条异常通知")
        except Exception as exc:  # noqa: BLE001
            logger.error(f"【分享存活巡检】通知环节异常：{exc}")
        return result


# 全局实例
share_health_check_task_service = ShareHealthCheckTaskService()
