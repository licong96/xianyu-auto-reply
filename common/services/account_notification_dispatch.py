"""按账号绑定的通知渠道发送系统通知。

供 scheduler 等非 WebSocket 服务使用：读取账号在「消息通知」里绑定的
启用渠道，复用 common.utils.notification_utils 中的渠道发送函数。
自带按 (账号, 通知类型) 的进程内冷却，避免任务循环刷屏。
"""
from __future__ import annotations

import time

from loguru import logger
from sqlalchemy import select

from common.db.session import async_session_maker
from common.models.message_notification import MessageNotification
from common.models.notification_channel import NotificationChannel
from common.utils.notification_utils import (
    parse_notification_config,
    send_bark_notification,
    send_dingtalk_notification,
    send_email_notification,
    send_feishu_notification,
    send_pushplus_notification,
    send_telegram_notification,
    send_webhook_notification,
    send_wechat_notification,
)
from common.utils.time_utils import get_beijing_now_naive

DEFAULT_NOTIFY_COOLDOWN_SECONDS = 3600

# (account_identifier, notification_type) -> 上次发送的 epoch 秒
_notify_cooldown: dict[str, float] = {}


async def _load_enabled_channels(account_identifier: str) -> list[dict]:
    """查询账号绑定的、账号侧与渠道侧均已启用的通知渠道。"""
    async with async_session_maker() as session:
        rows = (
            await session.execute(
                select(MessageNotification, NotificationChannel)
                .join(
                    NotificationChannel,
                    MessageNotification.channel_id == NotificationChannel.id,
                )
                .where(
                    MessageNotification.account_identifier == account_identifier,
                    MessageNotification.enabled == True,  # noqa: E712
                    NotificationChannel.enabled == True,  # noqa: E712
                )
                .order_by(MessageNotification.id)
            )
        ).all()
    return [
        {
            "channel_name": channel.name,
            "channel_type": channel.channel_type,
            "channel_config": channel.config_payload,
        }
        for _, channel in rows
    ]


async def send_account_notification(
    account_identifier: str,
    title: str,
    detail: str,
    *,
    notification_type: str = "system",
    cooldown_seconds: int = DEFAULT_NOTIFY_COOLDOWN_SECONDS,
) -> bool:
    """向账号绑定的所有启用渠道发送一条通知。

    Args:
        account_identifier: 闲鱼账号 ID（xy_message_notifications.account_identifier）
        title: 通知标题（如 "❌ Token续期失败"）
        detail: 详情文本
        notification_type: 通知类型标识，用于冷却去重
        cooldown_seconds: 同一账号同一类型的发送冷却秒数，0 表示不冷却

    Returns:
        是否至少成功发送到一个渠道；冷却期内或未绑定渠道时返回 False。
    """
    key = f"{account_identifier}:{notification_type}"
    try:
        now = time.time()
        last = _notify_cooldown.get(key, 0.0)
        if cooldown_seconds > 0 and now - last < cooldown_seconds:
            return False

        channels = await _load_enabled_channels(account_identifier)
        if not channels:
            logger.info(
                f"【账号通知】{account_identifier} 未绑定启用中的通知渠道，跳过发送"
            )
            return False

        now_str = get_beijing_now_naive().strftime("%Y-%m-%d %H:%M:%S")
        message = (
            f"{title}\n\n"
            f"闲鱼账号: {account_identifier}\n"
            f"时间: {now_str}\n"
            f"详情: {detail}\n"
        )

        sent = False
        for channel in channels:
            channel_name = channel.get("channel_name", "Unknown")
            try:
                config_data = parse_notification_config(channel.get("channel_config"))
                channel_type = channel.get("channel_type")
                if channel_type in ("ding_talk", "dingtalk"):
                    await send_dingtalk_notification(config_data, message)
                elif channel_type in ("feishu", "lark"):
                    await send_feishu_notification(config_data, message)
                elif channel_type == "bark":
                    await send_bark_notification(config_data, message)
                elif channel_type == "email":
                    await send_email_notification(config_data, message, None)
                elif channel_type == "webhook":
                    await send_webhook_notification(config_data, message)
                elif channel_type in ("wechat", "wechat_work"):
                    await send_wechat_notification(config_data, message)
                elif channel_type == "telegram":
                    await send_telegram_notification(config_data, message)
                elif channel_type == "pushplus":
                    await send_pushplus_notification(config_data, message)
                else:
                    logger.warning(
                        f"【账号通知】不支持的通知渠道类型: {channel_type}"
                    )
                    continue
                sent = True
            except Exception as exc:
                logger.error(f"【账号通知】渠道 {channel_name} 发送失败: {exc}")

        if sent:
            _notify_cooldown[key] = now
            logger.info(
                f"【账号通知】已向 {account_identifier} 发送「{title}」，"
                f"冷却 {cooldown_seconds} 秒"
            )
        return sent
    except Exception as exc:
        logger.error(f"【账号通知】发送异常: {exc}")
        return False
