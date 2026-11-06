"""网盘分享续期与存活巡检日志路由。

功能：
1. 分页查询续期/巡检历史（xy_share_check_logs，日志管理页用）
2. 清空日志（管理员工具）

数据来源：
- task_type=renew：到期续期与手动重新分享的逐条结果（share_renew_service 落库）
- task_type=check：存活巡检逐条结果（share_health_check 子进程落库）

# CUSTOM: 网盘分享自动续期与内容存活巡检（新增文件，合并作者更新零冲突）
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import delete, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_admin_user, get_db_session
from common.models.share_check_log import ShareCheckLog
from common.models.user import User
from common.schemas.common import ApiResponse

router = APIRouter(tags=["分享续期巡检日志"])

_PAGE_SIZES = {10, 20, 50, 100}
_TASK_TYPES = {"renew", "check"}
_RESULTS = {"done", "failed", "valid", "invalid", "empty", "error", "risk"}
_PAN_TYPES = {"baidu", "quark"}


def _log_to_dict(log: ShareCheckLog) -> Dict[str, Any]:
    return {
        "id": log.id,
        "task_type": log.task_type,
        "pool_id": log.pool_id,
        "pan_type": log.pan_type,
        "pan_url": log.pan_url,
        "old_url": log.old_url,
        "result": log.result,
        "detail": log.detail,
        "fail_reason": log.fail_reason,
        "duration_seconds": log.duration_seconds,
        "created_at": log.created_at.isoformat() if log.created_at else None,
    }


@router.get("/share-check-logs", response_model=ApiResponse)
async def list_share_check_logs(
    task_type: Optional[str] = Query(default=None, description="按任务类型筛选：renew/check"),
    result: Optional[str] = Query(default=None, description="按结果筛选：done/failed/valid/invalid/empty/error/risk"),
    pan_type: Optional[str] = Query(default=None, description="按网盘筛选：baidu/quark"),
    pool_id: Optional[int] = Query(default=None, description="按资料池记录 ID 筛选"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1),
    current_user: User = Depends(get_current_admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """分页查询分享续期/巡检日志，按创建时间倒序。"""
    if task_type and task_type not in _TASK_TYPES:
        return ApiResponse(success=False, message="task_type 仅支持 renew/check", data=None)
    if result and result not in _RESULTS:
        return ApiResponse(success=False, message="result 取值不合法", data=None)
    if pan_type and pan_type not in _PAN_TYPES:
        return ApiResponse(success=False, message="pan_type 仅支持 baidu/quark", data=None)
    if page_size not in _PAGE_SIZES:
        return ApiResponse(success=False, message="每页数量仅支持 10、20、50、100", data=None)

    conditions = []
    if task_type:
        conditions.append(ShareCheckLog.task_type == task_type)
    if result:
        conditions.append(ShareCheckLog.result == result)
    if pan_type:
        conditions.append(ShareCheckLog.pan_type == pan_type)
    if pool_id:
        conditions.append(ShareCheckLog.pool_id == pool_id)

    total_count = (
        await session.execute(select(func.count()).select_from(ShareCheckLog).where(*conditions))
    ).scalar() or 0
    rows = (
        await session.execute(
            select(ShareCheckLog)
            .where(*conditions)
            .order_by(desc(ShareCheckLog.id))
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


@router.delete("/share-check-logs/clear", response_model=ApiResponse)
async def clear_share_check_logs(
    current_user: User = Depends(get_current_admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """清空全部分享续期/巡检日志。"""
    result = await session.execute(delete(ShareCheckLog))
    await session.commit()
    return ApiResponse(success=True, message=f"已清空 {result.rowcount} 条日志", data=None)
