"""
资料池 API 路由 - 资料池自动化流水线

接口：
1. GET    /material-pool            分页列表（状态/学校/年份/年级/关键词筛选）
2. GET    /material-pool/{id}       详情
3. PUT    /material-pool/{id}       编辑（标签、售价、标题、描述、封面图等）
4. POST   /material-pool/{id}/approve      审核通过（可顺带定价）
5. POST   /material-pool/{id}/reject       标记不售
6. POST   /material-pool/{id}/upload-pan   触发上传夸克网盘并生成分享链接
7. POST   /material-pool/{id}/download     触发浏览器接管下载，成功后自动上传网盘
8. POST   /material-pool/{id}/list         一键上架（body: account_id）
9. POST   /material-pool/batch-list        批量上架（body: ids + account_id）

# CUSTOM: 资料池自动化流水线（新增文件，合并作者更新零冲突）
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_active_user, get_db_session
from app.services.material_pool_service import MaterialPoolError, MaterialPoolService
from common.models.user import User
from common.schemas.common import ApiResponse

router = APIRouter(prefix="/material-pool", tags=["资料池"])


class PoolUpdateRequest(BaseModel):
    paper_name: Optional[str] = Field(None, max_length=300)
    school: Optional[str] = Field(None, max_length=200)
    year: Optional[str] = Field(None, max_length=20)
    grade: Optional[str] = Field(None, max_length=50)
    tags: Optional[List[str]] = None
    title: Optional[str] = Field(None, max_length=200)
    description: Optional[str] = None
    price: Optional[float] = Field(None, ge=0)
    cover_image: Optional[str] = Field(None, max_length=500)


class ApproveRequest(BaseModel):
    price: Optional[float] = Field(None, ge=0)
    title: Optional[str] = Field(None, max_length=200)
    description: Optional[str] = None


class CategorySelection(BaseModel):
    """一键上架时选择的闲鱼类目（来自类目推荐组件）。"""
    category: Optional[str] = Field(None, max_length=100)
    platform_category_id: Optional[str] = Field(None, max_length=64)
    platform_category_name: Optional[str] = Field(None, max_length=100)
    platform_channel_category_id: Optional[str] = Field(None, max_length=64)
    platform_channel_category_name: Optional[str] = Field(None, max_length=100)
    platform_category_path: List[Dict[str, Any]] = Field(default_factory=list)
    platform_attributes: List[Dict[str, Any]] = Field(default_factory=list)
    category_source: str = Field("manual", pattern="^(manual|recommendation)$")


class ListRequest(BaseModel):
    account_id: str = Field(..., min_length=1, description="闲鱼账号ID")
    category: Optional[CategorySelection] = None


class BatchListRequest(BaseModel):
    ids: List[int] = Field(..., min_length=1, max_length=50)
    account_id: str = Field(..., min_length=1)
    category: Optional[CategorySelection] = None


def _pool_error_resp(exc: MaterialPoolError) -> ApiResponse:
    return ApiResponse(success=False, message=str(exc), data=None)


@router.get("", response_model=ApiResponse)
async def list_pools(
    status: Optional[str] = Query(None),
    school: Optional[str] = Query(None),
    year: Optional[str] = Query(None),
    grade: Optional[str] = Query(None),
    keyword: Optional[str] = Query(None),
    source_type: Optional[str] = Query(None, description="来源筛选：zxxk=学科网爬虫，quark-manual=网盘直选"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    svc = MaterialPoolService(session)
    data = await svc.list_pools(
        current_user,
        page=page,
        page_size=page_size,
        status=status,
        school=school,
        year=year,
        grade=grade,
        keyword=keyword,
        source_type=source_type,
    )
    return ApiResponse(success=True, message="ok", data=data)


@router.get("/{pool_id}", response_model=ApiResponse)
async def get_pool(
    pool_id: int,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    svc = MaterialPoolService(session)
    try:
        data = await svc.get_pool(current_user, pool_id)
    except MaterialPoolError as exc:
        return _pool_error_resp(exc)
    return ApiResponse(success=True, message="ok", data=data)


@router.put("/{pool_id}", response_model=ApiResponse)
async def update_pool(
    pool_id: int,
    req: PoolUpdateRequest,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    svc = MaterialPoolService(session)
    try:
        data = await svc.update_pool(current_user, pool_id, req.model_dump(exclude_none=True))
    except MaterialPoolError as exc:
        return _pool_error_resp(exc)
    return ApiResponse(success=True, message="已保存", data=data)


@router.post("/{pool_id}/approve", response_model=ApiResponse)
async def approve_pool(
    pool_id: int,
    req: ApproveRequest,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    svc = MaterialPoolService(session)
    try:
        data = await svc.approve(current_user, pool_id, req.model_dump(exclude_none=True))
    except MaterialPoolError as exc:
        return _pool_error_resp(exc)
    return ApiResponse(success=True, message="审核通过", data=data)


@router.post("/{pool_id}/reject", response_model=ApiResponse)
async def reject_pool(
    pool_id: int,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    svc = MaterialPoolService(session)
    try:
        data = await svc.reject(current_user, pool_id)
    except MaterialPoolError as exc:
        return _pool_error_resp(exc)
    return ApiResponse(success=True, message="已标记不售", data=data)


@router.post("/{pool_id}/upload-pan", response_model=ApiResponse)
async def upload_pan(
    pool_id: int,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    svc = MaterialPoolService(session)
    try:
        data = await svc.trigger_upload(current_user, pool_id)
    except MaterialPoolError as exc:
        return _pool_error_resp(exc)
    except Exception as exc:  # noqa: BLE001 - PanError 等统一兜底
        return ApiResponse(success=False, message=f"上传网盘失败：{exc}", data=None)
    return ApiResponse(success=True, message="上传成功，分享链接已生成", data=data)


@router.post("/{pool_id}/download", response_model=ApiResponse)
async def download_pool(
    pool_id: int,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """触发浏览器接管下载该资料，成功后自动上传夸克网盘并生成分享链接。

    耗时较长（接管浏览器 + 下载 + 网盘上传，约 1-5 分钟），前端需放大请求超时。
    """
    svc = MaterialPoolService(session)
    try:
        data = await svc.download_and_upload(current_user, pool_id)
    except MaterialPoolError as exc:
        return _pool_error_resp(exc)
    except Exception as exc:  # noqa: BLE001
        return ApiResponse(success=False, message=f"下载失败：{exc}", data=None)
    return ApiResponse(success=True, message="下载完成，已上传网盘并生成分享链接", data=data)


@router.post("/{pool_id}/list", response_model=ApiResponse)
async def list_goods(
    pool_id: int,
    req: ListRequest,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    svc = MaterialPoolService(session)
    try:
        data = await svc.list_to_pan(
            current_user,
            pool_id,
            req.account_id,
            category_data=req.category.model_dump(exclude_none=True) if req.category else None,
        )
    except MaterialPoolError as exc:
        return _pool_error_resp(exc)
    except Exception as exc:  # noqa: BLE001
        return ApiResponse(success=False, message=f"上架失败：{exc}", data=None)
    return ApiResponse(success=True, message="上架完成", data=data)


@router.post("/batch-list", response_model=ApiResponse)
async def batch_list_goods(
    req: BatchListRequest,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """批量上架：逐条执行，单条失败不阻断其余；逐条返回结果。"""
    svc = MaterialPoolService(session)
    results = []
    success_count = 0
    category_data = req.category.model_dump(exclude_none=True) if req.category else None
    for pool_id in req.ids:
        try:
            data = await svc.list_to_pan(current_user, pool_id, req.account_id, category_data=category_data)
            results.append({"id": pool_id, "success": True, "data": data})
            success_count += 1
        except MaterialPoolError as exc:
            results.append({"id": pool_id, "success": False, "message": str(exc)})
        except Exception as exc:  # noqa: BLE001
            results.append({"id": pool_id, "success": False, "message": str(exc)})
    return ApiResponse(
        success=True,
        message=f"批量上架完成：成功 {success_count} / 共 {len(req.ids)}",
        data={"results": results, "success_count": success_count, "total": len(req.ids)},
    )
