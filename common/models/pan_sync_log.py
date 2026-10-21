"""
网盘互传同步日志模型。

功能：
1. 定义网盘互传任务日志表结构。
2. 每次同步任务结束（成功/失败）落一条，供日志管理页事后排查。
   任务运行期的进度查询仍走内存 registry（pan_sync_service），本表只做历史记录。
3. errors 为 JSON 字符串（最多 10 条单文件错误），查询后由前端解析展示。
"""
from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from common.db.base_class import Base, TimestampMixin


class PanSyncLog(TimestampMixin, Base):
    """网盘互传同步日志表。"""

    __tablename__ = "xy_pan_sync_logs"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
        comment="主键ID",
    )
    task_id: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        index=True,
        comment="任务ID（pan_sync_service 的内存任务ID）",
    )
    src_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="源网盘：baidu/quark",
    )
    dst_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="目标网盘：baidu/quark",
    )
    remote: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        comment="同步的源盘路径",
    )
    is_dir: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="是否文件夹同步",
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        comment="任务状态：done/failed",
    )
    total: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="文件总数",
    )
    processed: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="已处理文件数",
    )
    uploaded: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="上传文件数",
    )
    skipped: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="查重跳过文件数",
    )
    failed: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="失败文件数",
    )
    errors: Mapped[str | None] = mapped_column(
        Text,
        comment="单文件错误列表（JSON 数组，最多10条）",
    )
    duration_seconds: Mapped[float | None] = mapped_column(
        Float,
        comment="任务耗时（秒）",
    )
