"""资料池卡券正文生成与刷新。

从 backend-web MaterialPoolService._build_card_text 抽取到 common 层：
scheduler 的分享续期任务和 backend-web 的资料池服务共用同一份渲染逻辑，
保证"重新分享后重建卡券"与"一键上架时建卡券"产出的正文完全一致。

分层设计（便于单测）：
- render_card_text(pool, links)：纯函数，只依赖已加载的 ORM 对象；
- build_card_text(session, pool)：查库包装，渲染该资料池当前的有效链接；
- refresh_card_for_pool(session, pool, old_urls)：重新分享后刷新卡券正文，
  仅当卡券正文仍引用旧地址时才重建（人工改过的卡券不覆盖）。

# CUSTOM: 网盘分享自动续期与内容存活巡检（新增文件，合并作者更新零冲突）
"""
from __future__ import annotations

from typing import Any, List, Optional, Sequence

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from common.models.card import Card
from common.models.material_pool_link import MaterialPoolPanLink

# 盘显示名。backend-web/app/services/pan/base.py 有一份同名映射，
# 卡券正文以本文件为准，避免 common 反向依赖 backend-web 的 app 包。
PAN_DISPLAY_NAMES = {
    "quark": "夸克网盘",
    "baidu": "百度网盘",
}


def pan_display_name(pan_type: str) -> str:
    return PAN_DISPLAY_NAMES.get(pan_type, pan_type)


def render_card_text(pool: Any, links: Sequence[Any]) -> str:
    """渲染卡券正文（纯函数）。子表有链接记录按盘逐条输出；为空回退主记录旧单盘字段。

    夸克/百度地址自动拼上提取码（?pwd=），买家点开链接即可直达文件列表；
    提取码行保留，方便手动输入场景。
    """
    from common.utils.share_url import build_check_url

    if links:
        lines: List[str] = []
        for i, link in enumerate(links):
            if i > 0:
                lines.append("")  # 多个网盘链接之间空一行，便于买家阅读
            display_url = build_check_url(link.pan_type, link.pan_url, getattr(link, "pan_code", None))
            lines.append(f"{pan_display_name(link.pan_type)}：{display_url}")
            if link.pan_code:
                lines.append(f"提取码：{link.pan_code}")
        lines.append("")
        lines.append(f"资料：{getattr(pool, 'paper_name', None) or ''}")
        lines.append("链接失效请联系客服补发。")
        return "\n".join(lines)
    # 旧单盘记录（未迁子表）：保持原格式
    legacy_url = build_check_url(
        getattr(pool, "pan_type", "") or "",
        getattr(pool, "pan_url", None),
        getattr(pool, "pan_code", None),
    )
    lines = [f"网盘链接：{legacy_url}"]
    pan_code = getattr(pool, "pan_code", None)
    if pan_code:
        lines.append(f"提取码：{pan_code}")
    lines.append("")
    lines.append(f"资料：{getattr(pool, 'paper_name', None) or ''}")
    lines.append("链接失效请联系客服补发。")
    return "\n".join(lines)


async def build_card_text(session: AsyncSession, pool: Any) -> str:
    """查询资料池当前的有效分享链接并渲染卡券正文。"""
    stmt = (
        select(MaterialPoolPanLink)
        .where(MaterialPoolPanLink.pool_id == pool.id, MaterialPoolPanLink.pan_url.isnot(None))
        .order_by(MaterialPoolPanLink.id)
    )
    links = (await session.execute(stmt)).scalars().all()
    return render_card_text(pool, links)


def card_references_any(card_text: Optional[str], urls: Sequence[str]) -> bool:
    """卡券正文是否仍引用任一旧地址（判断是否需要重建的安全条件）。"""
    if not card_text:
        return False
    return any(u and u in card_text for u in urls)


async def refresh_card_for_pool(
    session: AsyncSession,
    pool: Any,
    old_urls: Sequence[str],
    *,
    log_prefix: str = "[share-renew]",
) -> bool:
    """重新分享后刷新绑定的卡券正文。

    仅当卡券存在、类型为 text、且正文仍引用旧地址时才重建——
    人工编辑过的卡券（不再含旧地址）不覆盖，避免丢人工内容。

    Args:
        pool: 资料池 ORM 行（需已加载 card_id / owner_id / paper_name）
        old_urls: 本次续期前的旧分享地址列表，用于安全匹配

    Returns:
        是否实际更新了卡券正文
    """
    card_id = getattr(pool, "card_id", None)
    if not card_id:
        return False
    stmt = select(Card).where(Card.id == card_id, Card.user_id == pool.owner_id)
    card = (await session.execute(stmt)).scalar_one_or_none()
    if not card or card.type != "text":
        return False
    if not card_references_any(card.text_content, old_urls):
        logger.info(
            f"{log_prefix} 卡券 {card_id} 正文未引用旧地址（可能人工编辑过），跳过重建 pool={pool.id}"
        )
        return False
    card.text_content = await build_card_text(session, pool)
    logger.info(f"{log_prefix} 卡券 {card_id} 正文已重建（{len(old_urls)} 个旧地址）pool={pool.id}")
    return True


__all__ = [
    "PAN_DISPLAY_NAMES",
    "pan_display_name",
    "render_card_text",
    "build_card_text",
    "card_references_any",
    "refresh_card_for_pool",
]
