"""Backend-Web 内部 API 路由（scheduler 等内部服务调用）。

当前接口：
- POST /internal/material-pool/reshare-expiring  网盘分享到期续期
  （扫描 7 天内到期的分享地址，重建分享 + 重建卡券正文 + 落日志，
   由 scheduler 的 share_renew 定时任务触发；也可手动 POST 触发）

全部接口要求 X-Internal-Token（与 scheduler/websocket 同一套服务间令牌）。

# CUSTOM: 网盘分享自动续期与内容存活巡检（新增文件，合并作者更新零冲突）
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session, require_internal_auth
from app.services.share_renew_service import (
    DEFAULT_RENEW_BEFORE_DAYS,
    DEFAULT_RENEW_LIMIT,
    renew_expiring_shares,
)

router = APIRouter(
    prefix="/internal",
    tags=["internal"],
    dependencies=[Depends(require_internal_auth)],
)


@router.post("/material-pool/reshare-expiring")
async def reshare_expiring(
    days: int = DEFAULT_RENEW_BEFORE_DAYS,
    limit: int = DEFAULT_RENEW_LIMIT,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """到期续期入口：扫描即将到期的分享链接并逐池重建分享、重建卡券。"""
    try:
        data = await renew_expiring_shares(session, days=days, limit=limit)
    except Exception as exc:  # noqa: BLE001 - 内部接口返回结构化错误
        logger.error(f"[internal] 到期续期执行失败：{exc}")
        return {"success": False, "code": 500, "message": f"到期续期执行失败：{exc}", "data": None}
    return {
        "success": True,
        "code": 200,
        "message": f"到期续期完成：{data['pools']} 个资料池 / {data['links']} 条链接，成功 {data['success_count']}",
        "data": data,
    }
