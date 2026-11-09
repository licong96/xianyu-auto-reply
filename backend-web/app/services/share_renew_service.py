"""网盘分享到期自动续期服务。

复用资料池现有的重新分享能力（PanClient.create_share），在分享地址到期前
提前重建分享，并同步重建绑定卡券的正文（发货实时读 xy_cards.text_content）。

两个入口共用本模块：
- 手动重新分享（backend-web 路由 /material-pool/{pool_id}/reshare）
- 定时续期（backend-web 内部接口 /internal/material-pool/reshare-expiring，
  由 scheduler 的 share_renew 任务触发）

每个链接的处理结果写入 xy_share_check_logs（task_type=renew），
续期成功的链接置 check_priority=1，供存活巡检任务优先验证新地址。

# CUSTOM: 网盘分享自动续期与内容存活巡检（新增文件，合并作者更新零冲突）
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from common.models.material_pool import STATUS_LISTED, MaterialPool
from common.models.material_pool_link import MaterialPoolPanLink
from common.models.share_check_log import RENEW_DONE, RENEW_FAILED, ShareCheckLog
from common.services.card_share_text import refresh_card_for_pool
from common.share_url import build_check_url

# 到期前提前续期的天数（新地址有效期 30 天，旧地址在过期前仍可访问，无切换空窗）
DEFAULT_RENEW_BEFORE_DAYS = 7
# 单轮处理的链接上限，防止一轮触发过多网盘操作引发风控
DEFAULT_RENEW_LIMIT = 200


async def fetch_expiring_link_groups(
    session: AsyncSession,
    *,
    days: int = DEFAULT_RENEW_BEFORE_DAYS,
    limit: int = DEFAULT_RENEW_LIMIT,
) -> List[Tuple[MaterialPool, List[MaterialPoolPanLink]]]:
    """查询即将到期、且所属资料池有在售商品卡券的链接，按资料池分组。

    到期比较在 SQL 侧用 NOW() + INTERVAL，避免 Python/MySQL 时区不一致问题。
    """
    stmt = (
        select(MaterialPoolPanLink, MaterialPool)
        .join(MaterialPool, MaterialPoolPanLink.pool_id == MaterialPool.id)
        .where(
            MaterialPoolPanLink.share_expire_at.isnot(None),
            MaterialPoolPanLink.share_expire_at <= func.date_add(func.now(), text(f"INTERVAL {int(days)} DAY")),
            MaterialPool.status == STATUS_LISTED,
            MaterialPool.card_id.isnot(None),
            MaterialPool.is_deleted == False,  # noqa: E712
        )
        .order_by(MaterialPoolPanLink.share_expire_at)
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()
    groups: Dict[int, Tuple[MaterialPool, List[MaterialPoolPanLink]]] = {}
    for link, pool in rows:
        if pool.id not in groups:
            groups[pool.id] = (pool, [])
        groups[pool.id][1].append(link)
    return list(groups.values())


async def reshare_pool_links(
    session: AsyncSession,
    pool: MaterialPool,
    links: List[MaterialPoolPanLink],
    *,
    days: Optional[int] = None,
    log_prefix: str = "[share-renew]",
) -> Dict[str, Any]:
    """对一组网盘链接重建分享并刷新卡券（手动 reshare 与定时续期共用）。

    单侧失败不拖垮其他盘；成功的链接重建卡券正文（仅当卡券仍引用旧地址）。
    每条链接的处理结果写入 xy_share_check_logs，成功后置 check_priority=1。

    Returns:
        {"results": [{pan_type, ok, share_url|error}], "success_count", "total", "old_urls"}
    """
    from app.api.routes.pan_browser import _share_days, _validate_share_url
    from app.services.pan.base import make_client

    share_days = days if days is not None else _share_days()

    results: List[Dict[str, Any]] = []
    old_urls: List[str] = []
    ok_count = 0
    for link in links:
        started = datetime.now()
        old_url = link.pan_url
        old_code = link.pan_code
        try:
            client = make_client(link.pan_type)
            share = await asyncio.to_thread(
                lambda: client.create_share(link.pan_path, days=share_days, passcode=False)
            )
            _validate_share_url(link.pan_type, share)
            if old_url:
                old_urls.append(old_url)
            link.pan_url = share["share_url"]
            link.pan_code = share.get("pan_code")
            link.share_id = share.get("share_id")
            expire_at = share.get("share_expire_at")
            link.share_expire_at = expire_at if isinstance(expire_at, datetime) else None
            link.last_renew_at = datetime.now()
            link.check_priority = 1  # 新地址优先巡检验证
            # 主记录旧字段恰好是该盘该路径时一并刷新
            if pool.pan_type == link.pan_type and pool.pan_path == link.pan_path:
                pool.pan_url = link.pan_url
                pool.pan_code = link.pan_code
                pool.share_id = link.share_id
                pool.share_expire_at = link.share_expire_at
            ok_count += 1
            results.append({"pan_type": link.pan_type, "ok": True, "share_url": link.pan_url})
            logger.info(f"{log_prefix} 重新分享成功 pool={pool.id} {link.pan_type} -> {link.pan_url}")
            _log_renew(session, pool.id, link, RENEW_DONE, started, old_url=old_url, old_code=old_code)
        except Exception as exc:  # noqa: BLE001 - 单侧失败不拖垮其他盘
            results.append({"pan_type": link.pan_type, "ok": False, "error": str(exc)})
            logger.warning(f"{log_prefix} 重新分享失败 pool={pool.id} {link.pan_type}：{exc}")
            _log_renew(session, pool.id, link, RENEW_FAILED, started, old_url=old_url, old_code=old_code, fail_reason=str(exc)[:500])

    if ok_count:
        await session.commit()
        # 发货实时读卡券正文：续期成功后同步重建卡券，指向新地址。
        # 仅当卡券正文仍引用旧地址时才重建，人工编辑过的卡券不覆盖。
        # commit 后 ORM 属性过期，先 refresh 再访问
        await session.refresh(pool)
        if await refresh_card_for_pool(session, pool, old_urls, log_prefix=log_prefix):
            await session.commit()
    elif results:
        await session.commit()  # 仅有失败日志也要落库

    return {"results": results, "success_count": ok_count, "total": len(links), "old_urls": old_urls}


def _log_renew(
    session: AsyncSession,
    pool_id: int,
    link: MaterialPoolPanLink,
    result: str,
    started: datetime,
    *,
    old_url: Optional[str] = None,
    old_code: Optional[str] = None,
    fail_reason: Optional[str] = None,
) -> None:
    """单条续期结果落日志表（随外层 commit 一起提交）。

    日志展示用地址统一拼接提取码（build_check_url），与卡券正文、巡检口径一致。
    """
    session.add(ShareCheckLog(
        task_type="renew",
        pool_id=pool_id,
        pan_type=link.pan_type,
        pan_url=build_check_url(link.pan_type, link.pan_url, link.pan_code),
        old_url=build_check_url(link.pan_type, old_url, old_code),
        result=result,
        detail=None,
        fail_reason=fail_reason,
        duration_seconds=(datetime.now() - started).total_seconds(),
    ))


async def renew_expiring_shares(
    session: AsyncSession,
    *,
    days: int = DEFAULT_RENEW_BEFORE_DAYS,
    limit: int = DEFAULT_RENEW_LIMIT,
    log_prefix: str = "[share-renew]",
) -> Dict[str, Any]:
    """定时续期入口：扫描即将到期的链接并逐池续期。内部接口与 scheduler 任务共用。"""
    groups = await fetch_expiring_link_groups(session, days=days, limit=limit)
    pool_results: List[Dict[str, Any]] = []
    total_ok = 0
    for pool, links in groups:
        data = await reshare_pool_links(session, pool, links, log_prefix=log_prefix)
        total_ok += data["success_count"]
        pool_results.append({"pool_id": pool.id, **data})
    summary = (
        f"到期续期：命中 {len(groups)} 个资料池 / {sum(r['total'] for r in pool_results)} 条链接，"
        f"成功 {total_ok}"
    )
    logger.info(f"{log_prefix} {summary}")
    session.add(ShareCheckLog(
        task_type="renew",
        pool_id=None,
        pan_type="-",
        pan_url=None,
        result=RENEW_DONE,
        detail=summary,
        fail_reason=None,
        duration_seconds=None,
    ))
    await session.commit()
    return {
        "pools": len(groups),
        "links": sum(r["total"] for r in pool_results),
        "success_count": total_ok,
        "results": pool_results,
        "truncated": sum(r["total"] for r in pool_results) >= limit,
    }


__all__ = [
    "DEFAULT_RENEW_BEFORE_DAYS",
    "DEFAULT_RENEW_LIMIT",
    "fetch_expiring_link_groups",
    "reshare_pool_links",
    "renew_expiring_shares",
]
