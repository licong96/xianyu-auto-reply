"""
资料池网盘链接子表 - 多网盘并列查询与合并列表方案

功能：
1. 一条资料池记录（xy_material_pool）对应多个网盘链接（夸克、百度……）
2. 同一条记录下 (pool_id, pan_type) 唯一，重复上架同一路径时补链不建重复商品
3. 主记录上的旧 pan_* 字段在过渡期仍代表已有的单盘（夸克）链接：
   读卡券时子表有记录遍历子表，子表为空回退旧字段

# CUSTOM: 多网盘并列查询与合并列表方案（新增表，合并作者更新零冲突）
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from common.db.base_class import Base, TimestampMixin


class MaterialPoolPanLink(TimestampMixin, Base):
    """资料池网盘链接子表 - 每条记录是一个网盘上的一份文件及其分享结果"""

    __tablename__ = "xy_material_pool_pan_links"
    __table_args__ = (
        UniqueConstraint("pool_id", "pan_type", name="uq_mppl_pool_pan"),
        Index("idx_mppl_pool", "pool_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="主键ID")
    pool_id: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="所属资料池记录 ID（xy_material_pool.id）")
    pan_type: Mapped[str] = mapped_column(String(20), nullable=False, comment="网盘类型：quark/baidu，新盘加新取值")
    pan_path: Mapped[str] = mapped_column(String(500), nullable=False, comment="该盘上的完整路径")
    fid: Mapped[str | None] = mapped_column(String(100), comment="该盘条目 id（分享查重用）")
    file_size: Mapped[int | None] = mapped_column(BigInteger, comment="该盘上的文件大小（字节）")
    pan_url: Mapped[str | None] = mapped_column(String(500), comment="该盘分享链接")
    pan_code: Mapped[str | None] = mapped_column(String(20), comment="提取码（无则空）")
    share_id: Mapped[str | None] = mapped_column(String(100), comment="分享 ID（撤销/巡检用）")
    share_expire_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="分享有效期")

    pool: Mapped["MaterialPool"] = relationship(
        "MaterialPool",
        primaryjoin="MaterialPoolPanLink.pool_id == MaterialPool.id",
        foreign_keys="[MaterialPoolPanLink.pool_id]",
        viewonly=True,
    )
