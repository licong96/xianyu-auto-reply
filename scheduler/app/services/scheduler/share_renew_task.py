"""网盘分享到期续期定时任务。

功能：
1. 默认每 12 小时触发一次，调用 backend-web 内部接口
   POST /internal/material-pool/reshare-expiring（X-Internal-Token 鉴权）
2. backend-web 侧扫描 7 天内到期的分享链接（限在售商品），重建分享、
   重建卡券正文、落 xy_share_check_logs，并把新地址置为巡检优先
3. 本任务只负责触发与结果汇总日志，网盘操作全部在 backend-web 内完成
   （pan client 只在 backend-web 进程可用）

# CUSTOM: 网盘分享自动续期与内容存活巡检（新增文件，合并作者更新零冲突）
"""
from __future__ import annotations

from datetime import datetime

from loguru import logger

from app.core.config import get_settings
from app.core.http_client import get_http_client


class ShareRenewTaskService:
    """网盘分享到期续期任务服务"""

    def __init__(self):
        self.task_name = "网盘分享到期续期"

    async def execute(self) -> dict:
        """执行一轮到期续期触发。"""
        logger.info(f"【{self.task_name}】开始执行")
        start_time = datetime.now()

        settings = get_settings()
        url = f"{settings.backend_web_service_url}/internal/material-pool/reshare-expiring"
        try:
            # 续期可能涉及多个网盘 CLI 调用，放宽超时；副作用幂等（重建分享），重试安全
            resp = await get_http_client().post(url, json={}, timeout=600)
        except Exception as exc:  # noqa: BLE001 - 触发失败只记日志，下轮再试
            logger.error(f"【{self.task_name}】触发 backend-web 续期接口失败：{exc}")
            return {"success": False, "error": str(exc)}

        elapsed = (datetime.now() - start_time).total_seconds()
        if not resp.get("success"):
            logger.error(
                f"【{self.task_name}】续期接口返回失败：{resp.get('message')}，耗时 {elapsed:.2f} 秒"
            )
            return {"success": False, "error": str(resp.get("message"))}

        data = resp.get("data") or {}
        logger.info(
            f"【{self.task_name}】执行完成：{data.get('pools', 0)} 个资料池 / "
            f"{data.get('links', 0)} 条链接，成功 {data.get('success_count', 0)}，"
            f"截断={data.get('truncated', False)}，耗时 {elapsed:.2f} 秒"
        )
        return {"success": True, **data}


# 全局实例
share_renew_task_service = ShareRenewTaskService()
