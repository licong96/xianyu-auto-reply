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

import asyncio
import json
import subprocess
from datetime import datetime, timezone
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
# CUSTOM: 多网盘并列查询与合并列表方案
from common.models.material_pool_link import MaterialPoolPanLink
from common.models.user import User, UserRole
from common.models.xy_catalog_item import XYCatalogItem

EDITABLE_FIELDS = ("paper_name", "school", "year", "grade", "tags", "title", "description", "price", "cover_image")

# 一键上架前必须满足的状态
LISTABLE_STATUSES = (STATUS_APPROVED, STATUS_FAILED)

# 正在执行「下载+上传」的记录（防重复点击；本机单人使用，进程内锁足够）
_running_downloads: set[int] = set()


def _run_crawler_download_subprocess(pool_id: int, timeout: int = 420) -> dict:
    """调用 paper_crawler 子进程接管浏览器下载单条试卷，返回下载结果 dict。

    DrissionPage 只装在爬虫独立 venv，backend-web 通过子进程隔离调用；
    子进程最后一行输出 RESULT_JSON: 前缀的结果，过程日志在 stderr。
    """
    root = Path(__file__).resolve().parents[3]  # backend-web/app/services/xxx.py -> 项目根
    py = root / "paper_crawler" / ".venv" / "bin" / "python"
    if not py.exists():
        raise MaterialPoolError(f"爬虫虚拟环境不存在：{py}")
    cmd = [str(py), "-m", "paper_crawler", "download", "--id", str(pool_id)]
    logger.info(f"[material-pool] 触发下载子进程：pool={pool_id}")
    try:
        proc = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise MaterialPoolError(f"下载超时（{timeout // 60} 分钟）：请检查爬虫浏览器状态后重试") from exc

    result_line = next(
        (ln for ln in reversed((proc.stdout or "").splitlines()) if ln.startswith("RESULT_JSON:")),
        None,
    )
    if proc.returncode != 0 or not result_line:
        tail = "\n".join((proc.stderr or "").splitlines()[-15:])
        raise MaterialPoolError(f"下载失败：{tail[-500:] or '子进程无输出（检查爬虫浏览器是否已登录学科网）'}")
    payload = json.loads(result_line[len("RESULT_JSON:"):])
    if not payload.get("success"):
        raise MaterialPoolError(payload.get("message") or "下载失败")
    return payload["data"]


class MaterialPoolError(RuntimeError):
    """资料池业务错误（消息直接展示给用户）。"""


def _is_admin(user: User) -> bool:
    return user.role == UserRole.ADMIN


def _row_to_dict(row: MaterialPool, pan_links: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
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
        # 多网盘子表链接（可为空列表；空时前端回退 pan_url 单链接展示）
        "pan_links": pan_links or [],
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
        "publish_options": row.publish_options or None,
        "is_dir": row.is_dir,
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

    async def _pan_links_map(self, pool_ids: List[int]) -> Dict[int, List[Dict[str, Any]]]:
        """批量取一批资料池记录的多网盘链接子表，按 pool_id 分组（无则为空列表）。"""
        result: Dict[int, List[Dict[str, Any]]] = {pid: [] for pid in pool_ids}
        if not pool_ids:
            return result
        stmt = (
            select(MaterialPoolPanLink)
            .where(MaterialPoolPanLink.pool_id.in_(pool_ids))
            .order_by(MaterialPoolPanLink.id)
        )
        for link in (await self.session.execute(stmt)).scalars().all():
            result.setdefault(link.pool_id, []).append({
                "pan_type": link.pan_type,
                "pan_path": link.pan_path,
                "pan_url": link.pan_url,
                "pan_code": link.pan_code,
                "share_id": link.share_id,
                "share_expire_at": link.share_expire_at.isoformat() if link.share_expire_at else None,
            })
        return result

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
        source_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        page = max(page, 1)
        page_size = page_size if page_size in (10, 20, 50, 100) else 20

        cond = [MaterialPool.is_deleted == False, *self._scope_cond(user)]  # noqa: E712
        if status:
            cond.append(MaterialPool.status == status)
        if source_type:
            cond.append(MaterialPool.source_type == source_type)
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
        links_map = await self._pan_links_map([r.id for r in rows])
        return {
            "list": [_row_to_dict(r, links_map.get(r.id)) for r in rows],
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
        row = await self._get_row(user, pool_id)
        links_map = await self._pan_links_map([row.id])
        return _row_to_dict(row, links_map.get(row.id))

    async def update_pool(self, user: User, pool_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
        row = await self._get_row(user, pool_id)
        for key in EDITABLE_FIELDS:
            if key in data and data[key] is not None:
                setattr(row, key, data[key])
        # 发布选项整体替换（JSON 对象；PUT 用 exclude_none，传了就该生效，含空对象）
        if "publish_options" in data:
            row.publish_options = data["publish_options"]
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

    # ---------- 触发下载（浏览器接管）+ 自动上传网盘 ----------

    async def download_and_upload(self, user: User, pool_id: int) -> Dict[str, Any]:
        """资料池「下载试卷」按钮全链路：接管浏览器下载 -> 自动上传夸克网盘 -> 生成分享链接。

        - 幂等：已有本地文件则跳过下载直接进入上传；已有分享链接则拒绝
        - 并发：同一记录防重复点击（进程内集合）
        - 失败：状态置 failed + error_message，可原地重试（重试时已有文件则跳过下载）
        - 成功：upload_pool_row_to_pan 把状态置 pending（待审核）
        """
        from app.services.pan.quark_pan_client import upload_pool_row_to_pan

        row = await self._get_row(user, pool_id)
        if pool_id in _running_downloads:
            raise MaterialPoolError("该资料正在下载中，请勿重复点击")
        if row.pan_url:
            raise MaterialPoolError(f"已生成网盘分享链接（{row.pan_url}），无需再下载")
        if not row.source_url:
            raise MaterialPoolError("缺少详情页地址（source_url），无法下载")

        _running_downloads.add(pool_id)
        try:
            if not (row.local_path and Path(row.local_path).exists()):
                # 子进程内是 DrissionPage 同步流程（含多次等待），放线程池避免阻塞事件循环
                await asyncio.to_thread(_run_crawler_download_subprocess, pool_id)
            else:
                logger.info(f"[material-pool] 已有本地文件，跳过下载直接上传：pool={pool_id}")

            await upload_pool_row_to_pan(pool_id)
            # 上传在独立会话提交，本会话里的 row 是旧状态；refresh(row) 异步安全地强制重查。
            # 不要 expire_all()：它会把 current_user 等会话内全部对象过期，
            # 之后同步读属性（如 _row_to_dict）会触发 MissingGreenlet
            await self.session.refresh(row)
            return _row_to_dict(row)
        except Exception as exc:  # noqa: BLE001 - 统一标记 failed，保证状态落库后可原地重试
            message = str(exc)[:1000]
            logger.warning(f"[material-pool] 下载/上传失败：pool={pool_id} - {message}")
            try:
                await self.session.execute(
                    update(MaterialPool)
                    .where(MaterialPool.id == pool_id)
                    .values(
                        status=STATUS_FAILED,
                        retry_count=MaterialPool.retry_count + 1,
                        error_message=message,
                    )
                )
                await self.session.commit()
            except Exception:  # noqa: BLE001 - 标记失败本身不阻断主异常
                logger.exception(f"[material-pool] 失败状态落库异常：pool={pool_id}")
            raise MaterialPoolError(message) from exc
        finally:
            _running_downloads.discard(pool_id)

    # ---------- 一键上架 ----------

    def _build_goods_title(self, row: MaterialPool) -> str:
        if row.title:
            return row.title
        # 普通卖家账号标题上限 30 字（鱼小铺 80 字），按最严约束截断；
        # 学科网试卷名常见「精品解析：」等前缀对买家无意义，生成时剥掉
        name = row.paper_name or "试卷资料"
        for prefix in ("精品解析：", "精品解析:", "解析：", "解析:"):
            if name.startswith(prefix):
                name = name[len(prefix):]
                break
        base = "".join(p for p in (row.school, row.year, row.grade) if p)
        title = f"{base}{name}" if base else name
        return title[:30]

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

    async def _build_card_text(self, row: MaterialPool) -> str:
        """发货卡券正文。子表有链接记录按盘逐条输出；子表为空回退主记录旧单盘字段（老商品字节级不变）。"""
        from app.services.pan.base import pan_display_name

        stmt = (
            select(MaterialPoolPanLink)
            .where(MaterialPoolPanLink.pool_id == row.id, MaterialPoolPanLink.pan_url.isnot(None))
            .order_by(MaterialPoolPanLink.id)
        )
        links = (await self.session.execute(stmt)).scalars().all()
        if links:
            lines: List[str] = []
            for link in links:
                lines.append(f"{pan_display_name(link.pan_type)}：{link.pan_url}")
                if link.pan_code:
                    lines.append(f"提取码：{link.pan_code}")
            lines.append("")
            lines.append(f"资料：{row.paper_name or ''}")
            lines.append("链接失效请联系客服补发。")
            return "\n".join(lines)
        # 旧单盘记录（未迁子表）：保持原格式
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

    async def _ensure_catalog_item(
        self, account_pk: int, owner_id: int, item_id: str, title: str, price: float
    ) -> None:
        """把刚发布的商品写入本地商品目录缓存（幂等，已存在则跳过）。

        自动续售规则的启用校验依赖 xy_catalog_items 命中商品；
        该缓存平时靠「获取该账号商品」同步写入，新发布的商品尚未同步时
        如果不这里兜底，启用续售会误报「当前商品不属于所选闲鱼账号」，
        并把已发布成功的一键上架整单误判为失败。
        """
        stmt = select(XYCatalogItem.id).where(
            XYCatalogItem.account_pk == account_pk,
            XYCatalogItem.owner_id == owner_id,
            XYCatalogItem.item_id == item_id,
        )
        if (await self.session.execute(stmt)).scalar_one_or_none():
            return
        self.session.add(XYCatalogItem(
            owner_id=owner_id,
            account_pk=account_pk,
            item_id=item_id,
            title=title,
            price=f"{price:.2f}",
            is_polished=False,
            metadata_json={"description": "", "source": "material-pool-publish"},
            created_at=datetime.now(timezone.utc),
        ))
        await self.session.flush()
        logger.info(f"[material-pool] 商品已写入目录缓存：item={item_id}")

    async def list_to_pan(
        self,
        user: User,
        pool_id: int,
        account_id: str,
        category_data: Optional[dict] = None,
        publish_options: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """一键上架编排。成功返回 listed；失败原地停留 failed + error_message，可重试。

        category_data: 前端选择的类目（一键上架弹窗内推荐/选择），会持久化到行上，
        失败重试时无需重新选择。
        publish_options: 前端选择的发布选项（多图/视频/原价/运费/自提/所在地/数量/
        成色/品牌/规格/标题/描述/售价），与行内已持久化选项 merge（本次传入优先），
        结果持久化到行上，建素材与发布请求都使用 merge 后的值。
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

            # merge 发布选项：行内已持久化 < 本次传入；有传入则持久化 merge 结果（重试复用）
            effective_options: Dict[str, Any] = dict(row.publish_options or {})
            if publish_options:
                effective_options.update(publish_options)
                row.publish_options = effective_options
            # 标题/描述/售价以选项为准并回写行，保证资料池记录与已发布商品一致
            opt_title = str(effective_options.get("title") or "").strip()
            opt_description = str(effective_options.get("description") or "").strip()
            if opt_title:
                row.title = opt_title[:200]
            if opt_description:
                row.description = opt_description[:1500]
            if effective_options.get("price") is not None:
                row.price = float(effective_options["price"])

            platform_category_id = row.platform_category_id
            platform_category_name = row.platform_category_name
            platform_channel_category_id = row.platform_channel_category_id
            platform_channel_category_name = row.platform_channel_category_name
            if not platform_category_id and not platform_channel_category_id:
                raise MaterialPoolError("缺少商品类目，请在上架弹窗中选择类目后再上架")

            # commit 会导致 ORM 属性过期，先把后续要用的值全部取到局部变量
            title = opt_title or self._build_goods_title(row)
            description = opt_description or self._build_goods_description(row)
            card_text = self._build_card_text(row)
            price = float(row.price)
            cover_image = row.cover_image
            material_id = row.material_id
            item_id = row.item_id or None
            card_id = row.card_id
            relist_rule_id = row.relist_rule_id
            platform_attributes = row.platform_attributes or []
            platform_category_path = row.platform_category_path or []

            # merge 后的发布选项（素材库字段全集，空则等同历史默认行为）
            option_images = effective_options.get("images") or [cover_image]
            option_videos = effective_options.get("videos") or []
            option_specifications = effective_options.get("specifications") or []
            option_sku_rows = effective_options.get("sku_rows") or []
            option_shipping_method = effective_options.get("shipping_method") or "free"
            material_payload: Dict[str, Any] = {
                "title": title,
                "description": description,
                "price": price,
                "original_price": effective_options.get("original_price"),
                "images": option_images,
                "videos": option_videos,
                "specifications": option_specifications,
                "sku_rows": option_sku_rows,
                "quantity": int(effective_options.get("quantity") or 1),
                # delivery_method 仅为兼容展示字段，实际发布由 shipping_method 决定
                "delivery_method": "pickup" if option_shipping_method == "none" else "express",
                "shipping_method": option_shipping_method,
                "support_pickup": bool(effective_options.get("support_pickup", False)),
                "postage": float(effective_options.get("postage") or 0),
                "address": effective_options.get("address"),
                "address_expected_text": effective_options.get("address_expected_text"),
                "brand": effective_options.get("brand"),
                "condition": effective_options.get("condition") or "全新",
            }
            # 发布请求可选覆盖项（均为 PublishSingleRequest 合法字段，交给 pydantic 校验）
            publish_req_overrides = {
                key: effective_options[key]
                for key in (
                    "original_price", "videos", "quantity", "stock", "specifications", "sku_rows",
                    "address", "address_expected_text", "delivery_method", "shipping_method",
                    "support_pickup", "postage", "brand", "condition",
                )
                if key in effective_options
            }

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
                    {**material_payload, **material_category_fields},
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
                    images=option_images,
                    **material_category_fields,
                    **publish_req_overrides,
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
                # 4.0 先把刚发布的商品兜底写入本地目录缓存：续售启用校验依赖
                # xy_catalog_items 命中商品，新商品未同步时否则会误报
                # 「当前商品不属于所选闲鱼账号」并把整单误判为失败。
                await self._ensure_catalog_item(account.id, user.id, item_id, title, price)
                await self.session.commit()
                relist_svc = AutoRelistRuleService(self.session)
                try:
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
                except Exception as relist_exc:  # noqa: BLE001
                    # 续售是附加能力：发布和卡券绑定已经成功落库，
                    # 这里失败只告警、不再把整单标为失败；可事后在素材处重新启用续售。
                    logger.warning(
                        f"[material-pool] 续售规则启用失败（不影响已上架结果）：pool={pool_id} err={relist_exc}"
                    )

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
