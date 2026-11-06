"""网盘分享续期与内容存活巡检日志模型。

功能：
1. 记录每次自动续期（task_type=renew）与页面存活巡检（task_type=check）的结果。
2. 供日志管理页事后排查：每条 link 的历史检查/续期轨迹。
3. detail 记录命中的页面选择器、页面标题、错误文案，便于网盘改版后定位。

# CUSTOM: 网盘分享自动续期与内容存活巡检（新增文件，合并作者更新零冲突）
"""
from __future__ import annotations

from sqlalchemy import BigInteger, Float, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from common.db.base_class import Base, TimestampMixin

# 巡检结果
CHECK_VALID = "valid"
CHECK_INVALID = "invalid"
CHECK_EMPTY = "empty"      # 页面可打开但内容为空
CHECK_ERROR = "error"      # 打不开/超时/风控，不等于链接失效
# 续期结果
RENEW_DONE = "done"
RENEW_FAILED = "failed"


class ShareCheckLog(TimestampMixin, Base):
    """网盘分享续期与存活巡检日志表。"""

    __tablename__ = "xy_share_check_logs"
    __table_args__ = (
        Index("idx_sc_pool", "pool_id"),
        Index("idx_sc_type_time", "task_type", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="主键ID")
    task_type: Mapped[str] = mapped_column(String(20), nullable=False, comment="任务类型：renew续期/check巡检")
    pool_id: Mapped[int | None] = mapped_column(BigInteger, comment="资料池记录 ID（xy_material_pool.id）")
    pan_type: Mapped[str] = mapped_column(String(20), nullable=False, comment="网盘类型：quark/baidu")
    pan_url: Mapped[str | None] = mapped_column(String(500), comment="检查/续期涉及的地址（续期记新地址）")
    old_url: Mapped[str | None] = mapped_column(String(500), comment="仅续期：旧地址")
    result: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="结果：renew=done/failed；check=valid/invalid/empty/error"
    )
    detail: Mapped[str | None] = mapped_column(Text, comment="命中选择器、页面标题、错误文案等排查信息")
    fail_reason: Mapped[str | None] = mapped_column(String(500), comment="失败原因")
    duration_seconds: Mapped[float | None] = mapped_column(Float, comment="单条耗时（秒）")


__all__ = [
    "ShareCheckLog",
    "CHECK_VALID",
    "CHECK_INVALID",
    "CHECK_EMPTY",
    "CHECK_ERROR",
    "RENEW_DONE",
    "RENEW_FAILED",
]
