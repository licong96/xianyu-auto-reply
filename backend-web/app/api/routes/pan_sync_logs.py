"""
网盘互传同步日志路由。

功能：
1. 分页查询同步任务历史（pan_sync_service 内存任务的持久化记录，日志管理页用）。
2. 清空日志（管理员工具）。

数据来源：xy_pan_sync_logs（每次同步任务结束由 pan_sync_service._persist_task_log 落库）。
运行中的任务进度仍走 GET /pan/sync-status/{task_id}（内存 registry），本路由只做事后排查。

# CUSTOM: 网盘互传同步日志（新增文件）
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import delete, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_admin_user, get_db_session
from common.models.pan_sync_log import PanSyncLog
from common.models.user import User
from common.schemas.common import ApiResponse

router = APIRouter(tags=["网盘同步日志"])

_PAGE_SIZES = {10, 20, 50, 100}
_STATUSES = {"done", "failed"}
_PAN_TYPES = {"baidu", "quark"}


def _log_to_dict(log: PanSyncLog) -> Dict[str, Any]:
    return {
        "id": log.id,
        "task_id": log.task_id,
        "src_type": log.src_type,
        "dst_type": log.dst_type,
        "remote": log.remote,
        "is_dir": bool(log.is_dir),
        "status": log.status,
        "total": log.total,
        "processed": log.processed,
        "uploaded": log.uploaded,
        "skipped": log.skipped,
        "failed": log.failed,
        "errors": log.errors,  # JSON 字符串，前端解析展示
        "duration_seconds": log.duration_seconds,
        "created_at": log.created_at.isoformat() if log.created_at else None,
    }


@router.get("/pan-sync-logs", response_model=ApiResponse)
async def list_pan_sync_logs(
    status: Optional[str] = Query(default=None, description="按任务状态筛选：done/failed"),
    pan_type: Optional[str] = Query(default=None, description="按网盘筛选（源或目标）：baidu/quark"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1),
    current_user: User = Depends(get_current_admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """分页查询网盘同步任务历史，按创建时间倒序。"""
    if status and status not in _STATUSES:
        return ApiResponse(success=False, message="status 仅支持 done/failed", data=None)
    if pan_type and pan_type not in _PAN_TYPES:
        return ApiResponse(success=False, message="pan_type 仅支持 baidu/quark", data=None)
    if page_size not in _PAGE_SIZES:
        return ApiResponse(success=False, message="每页数量仅支持 10、20、50、100", data=None)

    conditions = []
    if status:
        conditions.append(PanSyncLog.status == status)
    if pan_type:
        conditions.append((PanSyncLog.src_type == pan_type) | (PanSyncLog.dst_type == pan_type))

    total_count = (
        await session.execute(select(func.count()).select_from(PanSyncLog).where(*conditions))
    ).scalar() or 0
    rows = (
        await session.execute(
            select(PanSyncLog)
            .where(*conditions)
            .order_by(desc(PanSyncLog.id))
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()

    return ApiResponse(
        success=True,
        message="ok",
        data={
            "total": total_count,
            "page": page,
            "page_size": page_size,
            "list": [_log_to_dict(log) for log in rows],
        },
    )


@router.delete("/pan-sync-logs/clear", response_model=ApiResponse)
async def clear_pan_sync_logs(
    current_user: User = Depends(get_current_admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """清空全部网盘同步日志。"""
    result = await session.execute(delete(PanSyncLog))
    await session.commit()
    return ApiResponse(success=True, message=f"已清空 {result.rowcount} 条同步日志", data=None)
