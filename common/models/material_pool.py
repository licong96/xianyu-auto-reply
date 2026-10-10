"""
资料池模型 - 资料池自动化流水线

功能：
1. 定义资料池表结构（xy_material_pool）
2. 存储爬虫采集的试卷资料元数据、本地文件信息、网盘分享信息
3. 状态机驱动上架流水线，各阶段回填关联 ID
4. 采集源起步：学科网（zxxk.com），交付物为试卷 PDF 单文件

状态机：
    crawled（已采集）→ uploaded（已传网盘）→ pending（待审核）
    → approved（审核通过）→ listing（上架中）→ listed（在售）
    分支：rejected（不售）/ failed（失败，可原地重试）
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, CHAR, DateTime, Index, Integer, JSON, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from common.db.base_class import Base, TimestampMixin

# 状态机常量
STATUS_CRAWLED = "crawled"
STATUS_UPLOADED = "uploaded"
STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_LISTING = "listing"
STATUS_LISTED = "listed"
STATUS_REJECTED = "rejected"
STATUS_FAILED = "failed"

VALID_STATUS_VALUES = {
    STATUS_CRAWLED,
    STATUS_UPLOADED,
    STATUS_PENDING,
    STATUS_APPROVED,
    STATUS_LISTING,
    STATUS_LISTED,
    STATUS_REJECTED,
    STATUS_FAILED,
}


class MaterialPool(TimestampMixin, Base):
    """资料池表 - 爬虫采集的试卷资料，经网盘上传、审核后一键上架"""

    __tablename__ = "xy_material_pool"
    __table_args__ = (
        UniqueConstraint("source_url", name="uq_material_pool_source_url"),
        Index("idx_mp_owner_status", "owner_id", "status"),
        Index("idx_mp_school_year_grade", "school", "year", "grade"),
        Index("idx_mp_file_hash", "file_hash"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="主键ID")

    # ---- 采集字段（爬虫写入）----
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="所属用户ID（爬虫默认写管理员）")
    paper_name: Mapped[str] = mapped_column(String(300), nullable=False, comment="试卷名字")
    school: Mapped[str | None] = mapped_column(String(200), comment="学校")
    year: Mapped[str | None] = mapped_column(String(20), comment="年份（如 2025、2025-2026）")
    grade: Mapped[str | None] = mapped_column(String(50), comment="年级")
    tags: Mapped[list | None] = mapped_column(JSON, comment="自定义标签数组，如 [数学, 期中, 含答案]")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="源站发布时间")
    view_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", comment="源站浏览量（采集时快照）")
    download_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", comment="源站下载量（采集时快照）")
    source_url: Mapped[str] = mapped_column(String(1000), nullable=False, comment="来源链接（唯一，防重）")
    source_type: Mapped[str] = mapped_column(String(50), default="zxxk", server_default="zxxk", comment="采集渠道：zxxk-学科网")

    # ---- 本地文件 ----
    local_path: Mapped[str | None] = mapped_column(String(500), comment="本地文件路径")
    file_size: Mapped[int | None] = mapped_column(BigInteger, comment="文件大小（字节）")
    file_hash: Mapped[str | None] = mapped_column(CHAR(32), comment="文件 md5（二次去重）")
    redownload_url: Mapped[str | None] = mapped_column(Text, comment="源站重新下载地址（预留字段，暂未启用；补下载走下载列表页）")

    # ---- 网盘信息（pan_client 回写）----
    pan_type: Mapped[str] = mapped_column(String(20), default="quark", server_default="quark", comment="网盘类型")
    pan_path: Mapped[str | None] = mapped_column(String(500), comment="网盘远端路径，如 /资料池/<学校>/<年份>/xx.pdf")
    pan_url: Mapped[str | None] = mapped_column(String(500), comment="网盘分享链接")
    pan_code: Mapped[str | None] = mapped_column(String(20), comment="提取码（无则空）")
    share_id: Mapped[str | None] = mapped_column(String(100), comment="分享 ID（撤销/巡检用）")
    share_expire_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="分享有效期")

    # ---- 商品信息（审核/上架阶段补全）----
    title: Mapped[str | None] = mapped_column(String(200), comment="商品标题（上架生成：学校+年份+年级+试卷名）")
    description: Mapped[str | None] = mapped_column(Text, comment="商品描述")
    price: Mapped[float | None] = mapped_column(Numeric(10, 2), comment="售价（审核时人工填写）")
    cover_image: Mapped[str | None] = mapped_column(String(500), comment="封面图（PDF 首页转图）")

    # ---- 流水线状态 ----
    status: Mapped[str] = mapped_column(
        String(20),
        default=STATUS_CRAWLED,
        server_default=STATUS_CRAWLED,
        nullable=False,
        index=True,
        comment="状态机：crawled/uploaded/pending/approved/listing/listed/rejected/failed",
    )
    account_id: Mapped[str | None] = mapped_column(String(80), comment="上架目标闲鱼账号（xy_accounts.account_id）")
    material_id: Mapped[int | None] = mapped_column(BigInteger, comment="回填：素材库 ID（xy_product_materials）")
    item_id: Mapped[str | None] = mapped_column(String(64), comment="回填：闲鱼商品 ID")
    card_id: Mapped[int | None] = mapped_column(BigInteger, comment="回填：卡券 ID")
    relist_rule_id: Mapped[int | None] = mapped_column(BigInteger, comment="回填：自动续售规则 ID")
    error_message: Mapped[str | None] = mapped_column(Text, comment="失败原因")
    retry_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", comment="重试次数")
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False, comment="软删除")

    # 关系定义 - 无外键约束
    owner: Mapped["User"] = relationship(
        "User",
        primaryjoin="MaterialPool.owner_id == User.id",
        foreign_keys="[MaterialPool.owner_id]",
        viewonly=True,
    )
