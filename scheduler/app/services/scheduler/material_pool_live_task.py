"""
资料池商品实际在售状态对账定时任务

功能：
1. 默认每小时执行一次（interval_seconds=3600，纯数据库比对，无接口调用风险）
2. 取所有「上架成功且绑定商品 ID」的资料池记录，与商品目录 xy_catalog_items 比对：
   商品 ID 仍在目录 → 在售；已从目录消失（商品管理删除/擦亮清理下架）→ 不在售
3. 回写 item_live_status，一轮一次目录查询，幂等可重复执行
4. 目录不主动清理下架商品，"在售"为近似判定（资料池状态定位为运营参考）

说明：
- 「不在售」涵盖售出待续售、手动下架、删除三种情况（虚拟商品默认续售，
  售出是过渡态，续售重发后商品重新入目录，自动回到在售，不单独区分）
"""
from __future__ import annotations

from datetime import datetime

from loguru import logger

from common.db.session import async_session_maker
from common.services.material_pool_live_status import sync_pools_live_status


class MaterialPoolLiveTaskService:
    """资料池在售状态对账任务服务"""

    def __init__(self):
        self.task_name = "资料池在售状态对账"

    async def execute(self, limit: int = 2000):
        """执行一轮在售状态核对。"""
        logger.info(f"【{self.task_name}】开始执行")
        start_time = datetime.now()

        try:
            async with async_session_maker() as session:
                stats = await sync_pools_live_status(session, limit=limit)

            if stats["checked"] == 0:
                logger.info(f"【{self.task_name}】没有已上架的资料池记录，任务结束")
                return

            elapsed = (datetime.now() - start_time).total_seconds()
            logger.info(
                f"【{self.task_name}】执行完成：核对 {stats['checked']} 条，"
                f"在售 {stats['on_sale']}，不在售 {stats['off_sale']}，耗时 {elapsed:.2f} 秒"
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(f"【{self.task_name}】执行失败: {exc}")
            raise


# 全局实例
material_pool_live_task_service = MaterialPoolLiveTaskService()
