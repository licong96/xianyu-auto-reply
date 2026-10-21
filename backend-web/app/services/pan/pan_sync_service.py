"""
网盘互相同步服务 - 百度网盘 ↔ 夸克网盘

在网盘管理页点一个按钮，把文件/文件夹从一个盘同步到对端盘的相同路径。

设计要点：
- 方向无关：sync(src_type, path)，目标盘取 SYNC_PAIRS 对端；路径完全照搬（目标路径 = 源路径）
- 存量查重：目标盘同目录同名文件，大小一致（0.5% 容差，对齐 merge_pan_entries）跳过；
  同名但大小不一致按确认策略直接覆盖（先删目标再传）
- 逐文件「下载 → 上传 → 删本地」：磁盘峰值 ≈ 并发数 × 单文件大小，不整批落盘
- 后台任务：内存 registry，进度可查；服务重启任务丢失，重跑靠查重补齐
- 暂存目录任务级 finally 清理；启动时清理超过 24h 的孤儿目录（防 kill -9 残留）
- 磁盘水位：剩余空间低于 5GB 或不足以容纳下一个文件时拒绝/中止

# CUSTOM: 网盘互传（新增文件）
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List

from loguru import logger

from app.services.pan.base import PanError, PanPathNotFoundError, make_client, merge_key

# 同步方向表：源盘 -> 目标盘
SYNC_PAIRS: Dict[str, str] = {"baidu": "quark", "quark": "baidu"}

DEFAULT_STAGING_ROOT = Path(__file__).resolve().parents[4] / "data" / "pan_sync_staging"
ORPHAN_MAX_AGE_SECONDS = 24 * 3600
MIN_FREE_BYTES = 5 * 1024**3  # 暂存盘剩余空间底线 5GB
MAX_RECORDED_ERRORS = 10
MAX_FINISHED_TASKS = 100  # 内存中保留的已完成任务数（防膨胀）

SYNC_CONCURRENCY = max(1, int(os.environ.get("PAN_SYNC_CONCURRENCY", "2")))


def _staging_root() -> Path:
    raw = os.environ.get("PAN_SYNC_STAGING_DIR")
    return Path(raw) if raw else DEFAULT_STAGING_ROOT


def _sizes_match(a: int | None, b: int | None) -> bool:
    """大小是否视为同一份文件（0.5% 相对容差）。

    注意与 merge_pan_entries 的展示用容差不同：那里带 1024B 绝对下限，
    只影响「大小不一致」的 UI 提示；这里是跳过/覆盖的同步决策，
    小文件也必须严格比对，否则 24B 与 87B 会被误判为同一份而错误跳过。
    0.5% 相对容差已足够覆盖百度 ls 两位小数人类可读大小的舍入误差。
    """
    if a is None or b is None:
        return False
    if a == b:
        return True
    lo, hi = min(a, b), max(a, b)
    return hi - lo <= max(1, int(hi * 0.005))


def _check_disk(need_bytes: int = 0) -> None:
    """磁盘水位保护：剩余空间不足以容纳 need_bytes 且保留底线时抛 PanError。"""
    root = _staging_root()
    root.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(root).free
    if free - need_bytes < MIN_FREE_BYTES:
        raise PanError(
            f"本地磁盘空间不足：剩余 {free / 1024**3:.1f}GB，"
            f"本次同步至少需要 {(need_bytes + MIN_FREE_BYTES) / 1024**3:.1f}GB（含 5GB 保留水位），"
            f"请清理 {root} 或本地磁盘后重试"
        )


# ---------- 任务注册表（内存） ----------

_sync_tasks: Dict[str, Dict[str, Any]] = {}
_running_keys: set[str] = set()  # f"{src_type}:{remote}"，防同一路径并发重入
_orphans_cleaned = False


def _cleanup_orphans_once() -> None:
    """启动后首次同步时清理孤儿暂存目录（上次进程被杀留下的）。"""
    global _orphans_cleaned
    if _orphans_cleaned:
        return
    _orphans_cleaned = True
    root = _staging_root()
    if not root.is_dir():
        return
    now = time.time()
    for child in root.iterdir():
        try:
            if now - child.stat().st_mtime > ORPHAN_MAX_AGE_SECONDS:
                shutil.rmtree(child, ignore_errors=True)
                logger.info(f"[pan-sync] 清理孤儿暂存目录：{child}")
        except OSError as exc:
            logger.warning(f"[pan-sync] 清理孤儿目录失败 {child}：{exc}")


def get_task(task_id: str) -> Dict[str, Any] | None:
    """任务公开视图（不含内部句柄）。"""
    task = _sync_tasks.get(task_id)
    if not task:
        return None
    return {k: v for k, v in task.items() if not k.startswith("_")}


def start_sync(src_type: str, remote: str, is_dir: bool) -> Dict[str, Any]:
    """启动一次同步任务。同一路径已在同步中时抛 PanError。"""
    if src_type not in SYNC_PAIRS:
        raise PanError(f"不支持的同步源网盘：{src_type}（支持：{'、'.join(SYNC_PAIRS)}）")
    dst_type = SYNC_PAIRS[src_type]
    key = f"{src_type}:{remote}"
    if key in _running_keys:
        raise PanError("该路径正在同步中，请等待完成后再操作")

    _cleanup_orphans_once()
    _check_disk()

    task_id = uuid.uuid4().hex[:12]
    staging = _staging_root() / task_id
    staging.mkdir(parents=True, exist_ok=True)

    task: Dict[str, Any] = {
        "task_id": task_id,
        "src_type": src_type,
        "dst_type": dst_type,
        "remote": remote,
        "is_dir": is_dir,
        "status": "queued",  # queued -> running -> done / failed
        "total": 0,
        "processed": 0,
        "uploaded": 0,
        "skipped": 0,
        "failed": 0,
        "errors": [],
        "current": "",
        "staging_dir": str(staging),
        "created_at": time.time(),
        "finished_at": None,
    }
    _sync_tasks[task_id] = task
    _running_keys.add(key)
    task["_handle"] = asyncio.create_task(_run_sync_safely(task, key))
    _prune_finished()
    logger.info(f"[pan-sync] 任务启动 {task_id}：{src_type} -> {dst_type} {remote}")
    return get_task(task_id)


def _prune_finished() -> None:
    """已完成的任务最多保留 MAX_FINISHED_TASKS 条，超出按创建时间淘汰。"""
    finished = [t for t in _sync_tasks.values() if t["status"] in ("done", "failed")]
    if len(finished) <= MAX_FINISHED_TASKS:
        return
    finished.sort(key=lambda t: t["created_at"])
    for t in finished[: len(finished) - MAX_FINISHED_TASKS]:
        _sync_tasks.pop(t["task_id"], None)


# ---------- 同步执行 ----------

async def _list_entries(client, remote: str) -> List[Dict[str, Any]]:
    return await asyncio.to_thread(client.list_entries, remote)


async def _collect_files(client, remote: str, is_dir: bool) -> List[Dict[str, Any]]:
    """收集待同步文件列表：[{"rel": 相对同步根的路径, "remote": 源盘绝对路径, "size": int|None}]。"""
    if not is_dir:
        parent = remote.rsplit("/", 1)[0] or "/"
        name = remote.rsplit("/", 1)[1]
        try:
            entries = await _list_entries(client, parent)
            size = next((e["size"] for e in entries if e["name"] == name), None)
        except PanError:
            size = None  # 列不出父目录不阻塞同步，按大小未知处理（会走覆盖逻辑）
        return [{"rel": name, "remote": remote, "size": size}]

    async def walk(dir_remote: str, rel_prefix: str) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        try:
            entries = await _list_entries(client, dir_remote)
        except PanPathNotFoundError:
            return out
        for e in entries:
            rel = f"{rel_prefix}/{e['name']}" if rel_prefix else e["name"]
            if e["is_dir"]:
                out.extend(await walk(e["path"], rel))
            else:
                out.append({"rel": rel, "remote": e["path"], "size": e.get("size")})
        return out

    return await walk(remote, "")


async def _persist_task_log(task: Dict[str, Any]) -> None:
    """任务结束时把结果落 xy_pan_sync_logs（日志管理页可查）。

    尽力而为：任何异常只记日志，绝不影响同步主流程和任务状态。
    """
    try:
        from common.db.session import async_session_maker
        from common.models.pan_sync_log import PanSyncLog

        async with async_session_maker() as session:
            session.add(PanSyncLog(
                task_id=task["task_id"],
                src_type=task["src_type"],
                dst_type=task["dst_type"],
                remote=task["remote"][:500],
                is_dir=task["is_dir"],
                status=task["status"],
                total=task["total"],
                processed=task["processed"],
                uploaded=task["uploaded"],
                skipped=task["skipped"],
                failed=task["failed"],
                errors=json.dumps(task["errors"], ensure_ascii=False) if task["errors"] else None,
                duration_seconds=(
                    round(task["finished_at"] - task["created_at"], 3)
                    if task.get("finished_at") else None
                ),
            ))
            await session.commit()
    except Exception as exc:  # noqa: BLE001 - 日志落库失败不阻断任务
        logger.warning(f"[pan-sync] 同步日志落库失败（任务 {task['task_id']}）：{exc}")


async def _run_sync_safely(task: Dict[str, Any], key: str) -> None:
    """任务外壳：统一记状态、finally 清理暂存目录与运行标记、落历史日志。"""
    try:
        await _run_sync(task)
        if task["status"] != "failed":
            task["status"] = "done"
    except Exception as exc:  # noqa: BLE001 - 任务级兜底，单文件失败不走到这里
        task["status"] = "failed"
        task["errors"].append(str(exc)[:300])
        logger.exception(f"[pan-sync] 任务失败 {task['task_id']}")
    finally:
        task["finished_at"] = time.time()
        shutil.rmtree(task["staging_dir"], ignore_errors=True)
        _running_keys.discard(key)
        logger.info(
            f"[pan-sync] 任务结束 {task['task_id']}：{task['status']}，"
            f"上传 {task['uploaded']}，跳过 {task['skipped']}，失败 {task['failed']}"
        )
        await _persist_task_log(task)


async def _run_sync(task: Dict[str, Any]) -> None:
    src = make_client(task["src_type"])
    dst = make_client(task["dst_type"])
    remote, is_dir = task["remote"], task["is_dir"]
    task["status"] = "running"

    files = await _collect_files(src, remote, is_dir)
    task["total"] = len(files)
    if not files:
        if is_dir:  # 空文件夹：把目录本身建到目标盘
            await asyncio.to_thread(dst.ensure_dir, remote)
        return

    # 目标盘目录索引缓存：parent 路径 -> {merge_key(文件名): 条目}
    dst_index: Dict[str, Dict[str, Dict[str, Any]]] = {}

    async def ensure_dst_index(parent: str) -> Dict[str, Dict[str, Any]]:
        if parent not in dst_index:
            try:
                entries = await _list_entries(dst, parent)
            except PanPathNotFoundError:
                entries = []
            dst_index[parent] = {merge_key(e["name"]): e for e in entries}
        return dst_index[parent]

    sem = asyncio.Semaphore(SYNC_CONCURRENCY)

    async def process(item: Dict[str, Any]) -> None:
        async with sem:
            rel = item["rel"]
            dst_path = f"{remote}/{rel}" if is_dir else remote
            dst_parent = dst_path.rsplit("/", 1)[0] or "/"
            name = dst_path.rsplit("/", 1)[1]
            local = Path(task["staging_dir"]) / rel
            try:
                index = await ensure_dst_index(dst_parent)
                existing = index.get(merge_key(name))
                if existing and _sizes_match(existing.get("size"), item["size"]):
                    task["skipped"] += 1
                    task["current"] = rel
                    return  # 已存在且大小一致，跳过

                _check_disk(need_bytes=item["size"] or 0)

                # 覆盖策略：先删目标盘同名文件再传（同名不同大小视为旧版本）
                if existing:
                    await asyncio.to_thread(dst.delete, dst_path)
                    dst_index.pop(dst_parent, None)
                    logger.info(f"[pan-sync] 覆盖旧文件：{dst_path}")

                local.parent.mkdir(parents=True, exist_ok=True)
                if task["src_type"] == "baidu":
                    # BaiduPCS-Go 下载落盘路径是 savedir/用户ID_用户名/文件名，需挪到预期位置
                    downloaded = await asyncio.to_thread(
                        src.download, item["remote"], str(local.parent)
                    )
                    if Path(downloaded) != local:
                        shutil.move(downloaded, local)
                else:
                    await asyncio.to_thread(src.download, item["remote"], str(local))

                await asyncio.to_thread(dst.ensure_dir, dst_parent)
                if task["dst_type"] == "quark":
                    await asyncio.to_thread(dst.upload, str(local), dst_path)
                else:
                    await asyncio.to_thread(dst.upload, str(local), dst_parent)
                task["uploaded"] += 1

                # 逐文件清理：传完立即删本地副本，磁盘峰值 ≈ 并发数 × 单文件大小
                try:
                    local.unlink()
                except OSError as exc:
                    logger.warning(f"[pan-sync] 本地副本清理失败 {local}：{exc}")
                dst_index.pop(dst_parent, None)
            except Exception as exc:  # noqa: BLE001 - 单文件失败不拖垮整个任务
                task["failed"] += 1
                if len(task["errors"]) < MAX_RECORDED_ERRORS:
                    task["errors"].append(f"{rel}: {str(exc)[:200]}")
                logger.warning(f"[pan-sync] 文件同步失败 {rel}：{exc}")
            finally:
                task["processed"] += 1
                task["current"] = rel

    await asyncio.gather(*(process(f) for f in files))
