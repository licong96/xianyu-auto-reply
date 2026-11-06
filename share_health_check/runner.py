"""巡检批次执行器：取批 → 逐条浏览器检查 → 回写数据库 → 输出摘要。

由 scheduler 的 share_health_check 任务以子进程方式调用（见 __main__.py）。
设计为可单测：run_check_batch 只依赖传入的 session 与 tab 工厂，
浏览器与 DrissionPage 仅在 main() 路径上引入。
"""
from __future__ import annotations

import random
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from common.models.material_pool import STATUS_LISTED, MaterialPool
from common.models.material_pool_link import MaterialPoolPanLink
from common.models.share_check_log import ShareCheckLog

from . import checker

DEFAULT_BATCH_SIZE = 50
# 条目之间的随机等待（秒）：防风控
MIN_GAP_SECONDS = 2.0
MAX_GAP_SECONDS = 3.0

# 单条当天 error 重试上限：达到后当天不再查（由 SQL 侧 last_check_status + last_check_at 控制）
ERROR_RETRY_PER_DAY = 3


async def fetch_links_to_check(session: AsyncSession, batch_size: int = DEFAULT_BATCH_SIZE) -> List[MaterialPoolPanLink]:
    """取一批待巡检链接：续期后的优先链接（check_priority=1）插队，
    其余按最久未查优先；只扫未删除资料池中「在售」商品的链接。"""
    stmt = (
        select(MaterialPoolPanLink)
        .join(MaterialPool, MaterialPoolPanLink.pool_id == MaterialPool.id)
        .where(
            MaterialPoolPanLink.pan_url.isnot(None),
            MaterialPool.is_deleted == False,  # noqa: E712
            (MaterialPool.status == STATUS_LISTED) | (MaterialPoolPanLink.check_priority == 1),
        )
        .order_by(MaterialPoolPanLink.check_priority.desc(), MaterialPoolPanLink.last_check_at.asc())
        .limit(batch_size)
    )
    return list((await session.execute(stmt)).scalars().all())


async def run_check_batch(
    session: AsyncSession,
    open_tab: Callable[[], Any],
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    sleep_between: Optional[Callable[[], None]] = None,
) -> Dict[str, Any]:
    """执行一轮巡检批次。

    Args:
        session: 数据库会话
        open_tab: 按 url 返回 tab 的工厂（真实实现返回同一个可复用 tab）
        batch_size: 本批最多检查条数
        sleep_between: 条目间隔函数（默认随机 2~3 秒，测试可传入 no-op）

    Returns:
        汇总 dict：checked/valid/invalid/empty/error/risk 计数 + notify 清单
    """
    if sleep_between is None:
        def sleep_between():
            time.sleep(random.uniform(MIN_GAP_SECONDS, MAX_GAP_SECONDS))

    links = await fetch_links_to_check(session, batch_size=batch_size)
    stats: Dict[str, Any] = {
        "checked": 0, "valid": 0, "invalid": 0, "empty": 0, "error": 0, "risk": 0,
        "notify": [],  # [{pool_id, pan_type, url, status, detail}] 供调用方发通知
    }
    if not links:
        logger.info("巡检：没有待检查的分享链接")
        return stats

    logger.info(f"巡检：本批待检查 {len(links)} 条链接")
    try:
        for idx, link in enumerate(links):
            # 夸克/百度分享必须带提取码才能直接看到文件列表：表里 pan_code 存在且
            # 地址未带 pwd 参数时拼接上去；日志与通知记录实际打开的完整地址
            check_url = checker.build_check_url(link.pan_type, link.pan_url or "", getattr(link, "pan_code", None))
            # 每个链接都向工厂取 tab：真实实现返回同一个 tab 复用，
            # 测试可按 url 注入不同页面场景；预算从模块常量实时读取（可测试覆盖）
            tab = open_tab(check_url)
            outcome = checker.check_link(
                tab, link.pan_type, check_url,
                budget=checker.PAGE_BUDGET_SECONDS,
            )
            stats["checked"] += 1
            stats[outcome.status] = stats.get(outcome.status, 0) + 1

            link.last_check_status = outcome.status
            link.last_check_at = datetime.now()
            if outcome.status != checker.ERROR:
                link.check_priority = 0  # 非 error 结果消费掉插队标记
            session.add(ShareCheckLog(
                task_type="check",
                pool_id=link.pool_id,
                pan_type=link.pan_type,
                pan_url=check_url,
                result=outcome.status,
                detail=f"[{outcome.detail}] 标题：{outcome.page_title}"[:1000],
                duration_seconds=outcome.duration_seconds,
            ))
            logger.info(
                f"巡检 [{idx + 1}/{len(links)}] pool={link.pool_id} {link.pan_type} "
                f"{check_url} -> {outcome.status}（{outcome.duration_seconds:.1f}s）"
            )
            if outcome.status in (checker.INVALID, checker.EMPTY, checker.RISK):
                stats["notify"].append({
                    "pool_id": link.pool_id,
                    "pan_type": link.pan_type,
                    "url": check_url,
                    "status": outcome.status,
                    "detail": outcome.detail,
                })
            # 风控：立即停止本批，避免继续访问加重风控
            if outcome.status == checker.RISK:
                logger.warning("巡检：命中风控特征，本批次提前结束")
                break
            sleep_between()
    finally:
        await session.commit()
    return stats
