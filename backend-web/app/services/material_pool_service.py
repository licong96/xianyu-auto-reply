"""
资料池服务 - 资料池自动化流水线

功能：
1. 资料池分页列表 / 详情 / 编辑（标签、售价、标题、描述、封面图）
2. 审核流转（通过 / 不售）
3. 触发网盘上传（复用 pan_client.upload_pool_row_to_pan）
4. 一键上架编排：建素材 -> 发布 -> 建卡券(网盘链接) -> 绑定 -> 续售规则 -> 回填
   每一步幂等可重试：失败原地停留并记录 error_message，已完成的步骤跳过。

# CUSTOM: 资料池自动化流水线（新增文件，合并作者更新零冲突）
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import String, desc, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from common.models.material_pool import (
    MaterialPool,
    STATUS_APPROVED,
    STATUS_CRAWLED,
    STATUS_FAILED,
    STATUS_LISTED,
    STATUS_LISTING,
    STATUS_PENDING,
    STATUS_REJECTED,
)
from common.models.user import User, UserRole
from common.models.xy_catalog_item import XYCatalogItem

EDITABLE_FIELDS = ("paper_name", "school", "year", "grade", "tags", "title", "description", "price", "cover_image")

# 一键上架前必须满足的状态
LISTABLE_STATUSES = (STATUS_APPROVED, STATUS_FAILED)


class MaterialPoolError(RuntimeError):
    """资料池业务错误（消息直接展示给用户）。"""


def _is_admin(user: User) -> bool:
    return user.role == UserRole.ADMIN


def _row_to_dict(row: MaterialPool) -> Dict[str, Any]:
    return {
        "id": row.id,
        "paper_name": row.paper_name,
        "school": row.school,
        "year": row.year,
        "grade": row.grade,
        "tags": row.tags or [],
        "published_at": row.published_at.isoformat() if row.published_at else None,
        "view_count": row.view_count,
        "download_count": row.download_count,
        "source_url": row.source_url,
        "source_type": row.source_type,
        "local_path": row.local_path,
        "file_size": row.file_size,
        "pan_url": row.pan_url,
        "pan_code": row.pan_code,
        "share_expire_at": row.share_expire_at.isoformat() if row.share_expire_at else None,
        "title": row.title,
        "description": row.description,
        "price": float(row.price) if row.price is not None else None,
        "cover_image": row.cover_image,
        "platform_category_id": row.platform_category_id,
        "platform_category_name": row.platform_category_name,
        "platform_channel_category_id": row.platform_channel_category_id,
        "platform_channel_category_name": row.platform_channel_category_name,
        "platform_category_path": row.platform_category_path or [],
        "platform_attributes": row.platform_attributes or [],
        "status": row.status,
        "account_id": row.account_id,
        "material_id": row.material_id,
        "item_id": row.item_id,
        "card_id": row.card_id,
        "relist_rule_id": row.relist_rule_id,
        "error_message": row.error_message,
        "retry_count": row.retry_count,
        "owner_id": row.owner_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


class MaterialPoolService:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ---------- 查询 ----------

    def _scope_cond(self, user: User):
        return [] if _is_admin(user) else [MaterialPool.owner_id == user.id]

    async def list_pools(
        self,
        user: User,
        *,
        page: int = 1,
        page_size: int = 20,
        status: Optional[str] = None,
        school: Optional[str] = None,
        year: Optional[str] = None,
        grade: Optional[str] = None,
        keyword: Optional[str] = None,
    ) -> Dict[str, Any]:
        page = max(page, 1)
        page_size = page_size if page_size in (10, 20, 50, 100) else 20

        cond = [MaterialPool.is_deleted == False, *self._scope_cond(user)]  # noqa: E712
        if status:
            cond.append(MaterialPool.status == status)
        if school:
            cond.append(MaterialPool.school.like(f"%{school}%"))
        if year:
            cond.append(MaterialPool.year.like(f"%{year}%"))
        if grade:
            cond.append(MaterialPool.grade.like(f"%{grade}%"))
        if keyword:
            like = f"%{keyword}%"
            cond.append(
                (MaterialPool.paper_name.like(like))
                | (MaterialPool.title.like(like))
                | (MaterialPool.tags.cast(String).like(like))
            )

        count_stmt = select(func.count()).select_from(MaterialPool).where(*cond)
        total = (await self.session.execute(count_stmt)).scalar() or 0

        stmt = (
            select(MaterialPool)
            .where(*cond)
            .order_by(desc(MaterialPool.id))
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return {
            "list": [_row_to_dict(r) for r in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size if total else 0,
        }

    async def _get_row(self, user: User, pool_id: int) -> MaterialPool:
        cond = [MaterialPool.id == pool_id, MaterialPool.is_deleted == False]  # noqa: E712
        if not _is_admin(user):
            cond.append(MaterialPool.owner_id == user.id)
        row = (await self.session.execute(select(MaterialPool).where(*cond))).scalar_one_or_none()
        if not row:
            raise MaterialPoolError(f"资料池记录不存在或无权操作：id={pool_id}")
        return row

    async def get_pool(self, user: User, pool_id: int) -> Dict[str, Any]:
        return _row_to_dict(await self._get_row(user, pool_id))

    async def update_pool(self, user: User, pool_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
        row = await self._get_row(user, pool_id)
        for key in EDITABLE_FIELDS:
            if key in data and data[key] is not None:
                setattr(row, key, data[key])
        await self.session.commit()
        await self.session.refresh(row)
        return _row_to_dict(row)

    # ---------- 审核 ----------

    async def approve(self, user: User, pool_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
        row = await self._get_row(user, pool_id)
        if row.status not in (STATUS_PENDING, STATUS_CRAWLED, STATUS_FAILED, STATUS_REJECTED):
            raise MaterialPoolError(f"当前状态（{row.status}）不允许审核通过")
        if data.get("price") is not None:
            row.price = float(data["price"])
        if data.get("title"):
            row.title = str(data["title"]).strip()
        if data.get("description"):
            row.description = str(data["description"]).strip()
        row.status = STATUS_APPROVED
        row.error_message = None
        await self.session.commit()
        await self.session.refresh(row)
        return _row_to_dict(row)

    async def reject(self, user: User, pool_id: int) -> Dict[str, Any]:
        row = await self._get_row(user, pool_id)
        if row.status == STATUS_LISTED:
            raise MaterialPoolError("已上架的商品不能标记不售")
        row.status = STATUS_REJECTED
        await self.session.commit()
        await self.session.refresh(row)
        return _row_to_dict(row)

    # ---------- 触发网盘上传 ----------

    async def trigger_upload(self, user: User, pool_id: int) -> Dict[str, Any]:
        await self._get_row(user, pool_id)  # 权限校验
        from app.services.pan.quark_pan_client import upload_pool_row_to_pan

        # upload_pool_row_to_pan 自带幂等（已有 pan_url 拒绝重复上传）
        result = await upload_pool_row_to_pan(pool_id)
        return result

    # ---------- 一键上架 ----------

    def _build_goods_title(self, row: MaterialPool) -> str:
        if row.title:
            return row.title
        parts = [p for p in (row.school, row.year, row.grade) if p]
        prefix = " ".join(parts)
        name = row.paper_name or "试卷资料"
        title = f"{prefix} {name}" if prefix else name
        return title[:80]

    def _build_goods_description(self, row: MaterialPool) -> str:
        if row.description:
            return row.description
        lines = [
            f"【资料名称】{row.paper_name or '-'}",
        ]
        if row.school:
            lines.append(f"【学校】{row.school}")
        if row.year:
            lines.append(f"【年份】{row.year}")
        if row.grade:
            lines.append(f"【年级】{row.grade}")
        if row.tags:
            lines.append(f"【标签】{' '.join(str(t) for t in row.tags)}")
        lines.append("")
        lines.append("付款后自动发送网盘下载链接，请尽快保存。")
        return "\n".join(lines)[:1500]

    def _build_card_text(self, row: MaterialPool) -> str:
        lines = [f"网盘链接：{row.pan_url}"]
        if row.pan_code:
            lines.append(f"提取码：{row.pan_code}")
        lines.append("")
        lines.append(f"资料：{row.paper_name or ''}")
        lines.append("链接失效请联系客服补发。")
        return "\n".join(lines)

    async def _find_published_item_id(self, account_pk: int, owner_id: int, title: str) -> Optional[str]:
        """发布成功后按标题在商品目录里反查 item_id（同步有索引延迟时兜底）。"""
        stmt = (
            select(XYCatalogItem)
            .where(
                XYCatalogItem.account_pk == account_pk,
                XYCatalogItem.owner_id == owner_id,
                XYCatalogItem.title == title,
            )
            .order_by(desc(XYCatalogItem.created_at))
            .limit(1)
        )
        item = (await self.session.execute(stmt)).scalar_one_or_none()
        return item.item_id if item else None

    async def list_to_pan(
        self,
        user: User,
        pool_id: int,
        account_id: str,
        category_data: Optional[dict] = None,
    ) -> Dict[str, Any]:
        """一键上架编排。成功返回 listed；失败原地停留 failed + error_message，可重试。

        category_data: 前端选择的类目（一键上架弹窗内推荐/选择），会持久化到行上，
        失败重试时无需重新选择。
        """
        from app.services.auto_relist_rule_service import AutoRelistRuleService
        from app.services.card_service import CardService
        from app.services.product_publish_service import ProductMaterialService
        from app.services.publish_execution_service import PublishExecutorService
        from common.models.xy_account import XYAccount

        row = await self._get_row(user, pool_id)
        try:
            if row.status not in LISTABLE_STATUSES:
                raise MaterialPoolError(f"当前状态（{row.status}）不允许上架，需先审核通过")
            if not row.pan_url:
                raise MaterialPoolError("缺少网盘分享链接，请先上传网盘")
            if row.price is None:
                raise MaterialPoolError("缺少售价，请先编辑售价或审核时填写")
            if not row.cover_image:
                raise MaterialPoolError("缺少封面图，请先上传封面图（编辑资料可上传）")

            account = (
                await self.session.execute(
                    select(XYAccount).where(
                        XYAccount.account_id == account_id,
                        XYAccount.owner_id == user.id,
                    )
                )
            ).scalar_one_or_none()
            if not account:
                raise MaterialPoolError("选择的闲鱼账号不存在或无权使用")

            # 持久化本次选择的类目（重试时复用）
            if category_data:
                row.platform_category_id = category_data.get("platform_category_id") or None
                row.platform_category_name = category_data.get("platform_category_name") or None
                row.platform_channel_category_id = category_data.get("platform_channel_category_id") or None
                row.platform_channel_category_name = category_data.get("platform_channel_category_name") or None
                row.platform_category_path = category_data.get("platform_category_path") or None
                row.platform_attributes = category_data.get("platform_attributes") or None

            platform_category_id = row.platform_category_id
            platform_category_name = row.platform_category_name
            platform_channel_category_id = row.platform_channel_category_id
            platform_channel_category_name = row.platform_channel_category_name
            if not platform_category_id and not platform_channel_category_id:
                raise MaterialPoolError("缺少商品类目，请在上架弹窗中选择类目后再上架")

            # commit 会导致 ORM 属性过期，先把后续要用的值全部取到局部变量
            title = self._build_goods_title(row)
            description = self._build_goods_description(row)
            card_text = self._build_card_text(row)
            price = float(row.price)
            cover_image = row.cover_image
            material_id = row.material_id
            item_id = row.item_id or None
            card_id = row.card_id
            relist_rule_id = row.relist_rule_id
            platform_attributes = row.platform_attributes or []
            platform_category_path = row.platform_category_path or []

            row.status = STATUS_LISTING
            row.account_id = account_id
            row.error_message = None
            await self.session.commit()

            category_name = platform_channel_category_name or platform_category_name or "学习资料"
            material_category_fields = {
                "category": category_name,
                "platform_category_id": platform_category_id,
                "platform_category_name": platform_category_name,
                "platform_channel_category_id": platform_channel_category_id,
                "platform_channel_category_name": platform_channel_category_name,
                "platform_category_path": platform_category_path,
                "platform_attributes": platform_attributes,
                "category_source": "recommendation" if (category_data or {}).get("category_source") != "manual" else "manual",
            }

            # 1) 建商品素材（幂等：已建过则复用）
            if not material_id:
                mat_svc = ProductMaterialService(self.session)
                material = await mat_svc.create(
                    user.id,
                    {
                        "title": title,
                        "description": description,
                        "price": price,
                        "original_price": None,
                        "images": [cover_image],
                        "specifications": [],
                        "sku_rows": [],
                        "videos": [],
                        "delivery_method": "express",
                        "shipping_method": "free",
                        "postage": 0,
                        "quantity": 1,
                        **material_category_fields,
                    },
                )
                material_id = material.id
                await self.session.execute(
                    update(MaterialPool)
                    .where(MaterialPool.id == pool_id)
                    .values(material_id=material_id)
                )
                await self.session.commit()
                logger.info(f"[material-pool] 素材已创建：pool={pool_id} material={material_id}")

            # 2) 发布（幂等：已发布过且有 item_id 则跳过）
            if not item_id:
                from app.api.routes.product_publish import PublishSingleRequest

                req = PublishSingleRequest(
                    account_id=account_id,
                    title=title,
                    description=description,
                    price=price,
                    images=[cover_image],
                    **material_category_fields,
                )
                pub_svc = PublishExecutorService(self.session)
                result = await pub_svc.publish_single(
                    user_id=user.id,
                    account_id=account_id,
                    item_data=req.model_dump(),
                )
                if not result.get("success"):
                    raise MaterialPoolError(result.get("message") or "发布失败，请查看发布日志")
                item_id = str(result.get("item_id") or "").strip() or None
                if not item_id:
                    item_id = await self._find_published_item_id(account.id, user.id, title)
                if not item_id:
                    raise MaterialPoolError("发布成功但未取回商品 ID，请稍后重试（不会重复发布）")
                await self.session.execute(
                    update(MaterialPool)
                    .where(MaterialPool.id == pool_id)
                    .values(item_id=item_id)
                )
                await self.session.commit()
                logger.info(f"[material-pool] 发布成功：pool={pool_id} item={item_id}")

            # 3) 建卡券并绑定（幂等：已有卡券跳过）
            if not card_id:
                card_svc = CardService(self.session)
                card = await card_svc.create_card(
                    user.id,
                    name=f"{title[:40]}",
                    card_type="text",
                    text_content=card_text,
                    delay_seconds=0,
                )
                card_id = card if isinstance(card, int) else card.id
                await self.session.execute(
                    update(MaterialPool)
                    .where(MaterialPool.id == pool_id)
                    .values(card_id=card_id)
                )
                await self.session.commit()
                bind_result = await card_svc.batch_bind_cards_to_items(
                    user.id, [card_id], [item_id]
                )
                logger.info(f"[material-pool] 卡券已创建并绑定：pool={pool_id} card={card_id} -> {bind_result}")

            # 4) 自动续售规则（幂等：material_id 维度 save 即 upsert）
            if not relist_rule_id:
                relist_svc = AutoRelistRuleService(self.session)
                rule = await relist_svc.save(
                    material_id=material_id,
                    user_id=user.id,
                    account_id=account_id,
                    current_item_id=item_id,
                    card_id=card_id,
                    enabled=True,
                    delay_seconds=60,
                )
                relist_rule_id = rule.id
                await self.session.execute(
                    update(MaterialPool)
                    .where(MaterialPool.id == pool_id)
                    .values(relist_rule_id=relist_rule_id)
                )
                await self.session.commit()
                logger.info(f"[material-pool] 续售规则已创建：pool={pool_id} rule={relist_rule_id}")

            row.status = STATUS_LISTED
            row.error_message = None
            await self.session.commit()
            await self.session.refresh(row)
            return _row_to_dict(row)

        except MaterialPoolError as exc:
            await self.session.execute(
                update(MaterialPool)
                .where(MaterialPool.id == pool_id)
                .values(
                    status=STATUS_FAILED,
                    retry_count=MaterialPool.retry_count + 1,
                    error_message=str(exc)[:1000],
                )
            )
            await self.session.commit()
            raise
        except Exception as exc:  # noqa: BLE001 - 编排层统一兜底，保证状态落库
            logger.exception(f"[material-pool] 一键上架异常：pool={pool_id}")
            await self.session.execute(
                update(MaterialPool)
                .where(MaterialPool.id == pool_id)
                .values(
                    status=STATUS_FAILED,
                    retry_count=MaterialPool.retry_count + 1,
                    error_message=str(exc)[:1000],
                )
            )
            await self.session.commit()
            raise MaterialPoolError(f"上架失败：{exc}") from exc


__all__ = ["MaterialPoolService", "MaterialPoolError"]
