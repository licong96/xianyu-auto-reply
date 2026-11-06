"""
资料池商品实际在售状态核对（卡券链路目录比对版）

判定依据（不用调用闲鱼接口，纯数据库比对）：
- 资料池记录绑定的卡券（pool.card_id）在卡券关系表 xy_card_item_relations
  中的所有 item_id，至少有一个仍存在于商品目录 xy_catalog_items → 在售
- 全部不在目录（商品管理中删除 / 擦亮任务清理已下架 / 续售重发前的空窗）→ 不在售

为什么走卡券链路而不是 pool.item_id：
- 续售是「重新发布新商品」：卖出后新 item_id 由续售任务生成，关联迁移会把新
  item_id 追加到卡券关系（CardItemRelation），但没有任何环节回写 pool.item_id
  （续售迁移服务明确不碰资料池）。若直接用 pool.item_id 比对目录，续售后旧商品
  被清理会导致资料池永远误报「已下架」。
- 卡券关系是续售唯一主动维护的资料池侧关联（含失败重试，最终一致），
  素材库 current_item_id 同样不被续售回写，不能作为判据。

说明：
- 目录只增量同步在售商品、不主动清理下架数据，"在售"为近似判定；资料池状态
  定位为运营参考，不追求 100% 准确，对账幂等，下一轮会自然纠正。
- 「不在售」涵盖售出待续售、手动下架、删除三种情况（虚拟商品默认续售，售出是
  过渡态，续售重发后新商品入目录，自动回到在售，不单独区分）。

两个回写通道共用本模块：
- 定时对账（scheduler 任务 material_pool_live，默认每小时）
- 资料池页手动刷新（backend-web 接口）
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from common.models.card_item_relation import CardItemRelation
from common.models.material_pool import (
    LIVE_OFF_SALE,
    LIVE_ON_SALE,
    LIVE_UNKNOWN,
    STATUS_LISTED,
    MaterialPool,
)
from common.models.xy_catalog_item import XYCatalogItem


async def _load_live_item_ids(session: AsyncSession) -> set:
    """商品目录中现存的商品 ID 集合（目录是全局表，item_id 全局唯一）。"""
    stmt = select(XYCatalogItem.item_id)
    return {str(r) for r in (await session.execute(stmt)).scalars().all()}


async def _load_card_item_ids(session: AsyncSession, pool: MaterialPool) -> List[str]:
    """取资料池绑定卡券当前关联的全部商品 ID（续售迁移维护，指向最新在售商品）。

    卡券无关联记录时退回 pool.item_id（兼容旧数据）。
    """
    if pool.card_id:
        stmt = select(CardItemRelation.item_id).where(
            CardItemRelation.user_id == pool.owner_id,
            CardItemRelation.card_id == pool.card_id,
        )
        item_ids = [str(r) for r in (await session.execute(stmt)).scalars().all()]
        if item_ids:
            return item_ids
    return [str(pool.item_id)] if pool.item_id else []


async def check_pool_item_live_status(
    session: AsyncSession, pool: MaterialPool, *, log_prefix: str = "[material-pool]"
) -> Dict[str, Any]:
    """核对单条资料池记录的商品实际在售状态并回写（单条手动刷新用）。

    Returns:
        {"status": "on_sale"/"off_sale"/"unknown", "error": str|None}
    """
    candidate_ids = await _load_card_item_ids(session, pool)
    if not candidate_ids:
        return {"status": LIVE_UNKNOWN, "error": "未绑定商品 ID"}

    live_ids = await _load_live_item_ids(session)
    if any(i in live_ids for i in candidate_ids):
        _apply_result(session, pool, LIVE_ON_SALE, None)
        await session.commit()
        logger.info(f"{log_prefix} 在售核对：pool={pool.id} 在售（目录命中：{candidate_ids}）")
        return {"status": LIVE_ON_SALE, "error": None}

    _apply_result(session, pool, LIVE_OFF_SALE, "关联商品已从商品目录删除（下架/删除/待续售）")
    await session.commit()
    logger.info(f"{log_prefix} 在售核对：pool={pool.id} 不在售（目录无：{candidate_ids}）")
    return {"status": LIVE_OFF_SALE, "error": "关联商品已从商品目录删除（下架/删除/待续售）"}


async def sync_pools_live_status(session: AsyncSession, limit: Optional[int] = None) -> Dict[str, int]:
    """批量对账：目录一次查询 + 逐条回写（定时任务用）。

    Returns:
        {"checked": n, "on_sale": n, "off_sale": n}
    """
    live_ids = await _load_live_item_ids(session)
    pools = await fetch_pools_to_check(session, limit=limit)
    stats = {"checked": 0, "on_sale": 0, "off_sale": 0}
    for pool in pools:
        candidate_ids = await _load_card_item_ids(session, pool)
        if not candidate_ids:
            continue  # 未绑定商品/卡券，不改动状态
        if any(i in live_ids for i in candidate_ids):
            _apply_result(session, pool, LIVE_ON_SALE, None)
            stats["on_sale"] += 1
        else:
            _apply_result(session, pool, LIVE_OFF_SALE, "关联商品已从商品目录删除（下架/删除/待续售）")
            stats["off_sale"] += 1
        stats["checked"] += 1
    if stats["checked"]:
        await session.commit()
    return stats


def _apply_result(session: AsyncSession, pool: MaterialPool, status: str, error: Optional[str]) -> None:
    pool.item_live_status = status
    pool.item_live_checked_at = datetime.now()
    pool.item_live_error = error


async def fetch_pools_to_check(session: AsyncSession, limit: Optional[int] = None) -> List[MaterialPool]:
    """取待核对的资料池记录：已上架成功且绑定了商品 ID 或卡券。"""
    stmt = (
        select(MaterialPool)
        .where(
            MaterialPool.is_deleted == False,  # noqa: E712
            MaterialPool.status == STATUS_LISTED,
            (MaterialPool.item_id.isnot(None)) | (MaterialPool.card_id.isnot(None)),
        )
        .order_by(MaterialPool.id)
    )
    if limit:
        stmt = stmt.limit(limit)
    return list((await session.execute(stmt)).scalars().all())


__all__ = [
    "check_pool_item_live_status",
    "sync_pools_live_status",
    "fetch_pools_to_check",
    "LIVE_ON_SALE",
    "LIVE_OFF_SALE",
    "LIVE_UNKNOWN",
]
