"""
网盘浏览 API 路由 - 网盘浏览与直选上架

接口：
1. GET  /pan/status    网盘连接状态（页面加载时检测凭证有效性）
2. GET  /pan/entries   列出指定目录内容（附资料池导入状态，辅助人工判断）
3. POST /pan/share     创建分享（资料池记录 + first_fid 双重查重，不重复创建）
4. POST /pan/import    把网盘文件导入资料池（复用 xy_material_pool，source_type=quark-manual）

设计要点：
- 全部复用 QuarkPanClient（子进程调 kuake），同步子进程用 asyncio.to_thread 包裹避免阻塞事件循环
- 网盘来源记录：source_url=quark://<网盘路径>（唯一键，天然防重），status=pending
- 不做本地上传/下载

# CUSTOM: 网盘浏览与直选上架（新增文件）
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Query
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import or_

from app.api.deps import get_current_active_user, get_db_session
from app.services.material_pool_service import _row_to_dict
from app.services.pan.quark_pan_client import (
    PanError,
    QuarkPanClient,
    _extract_share_url,
    _lookup_share_id,
)
from common.models.material_pool import STATUS_PENDING, MaterialPool
from common.models.user import User
from common.schemas.common import ApiResponse

SOURCE_TYPE_QUARK_MANUAL = "quark-manual"

router = APIRouter(prefix="/pan", tags=["网盘"])


def _source_url(pan_path: str) -> str:
    """网盘来源记录的合成 source_url：quark:// + 网盘完整路径（唯一、可识别来源、防重）。"""
    return f"quark://{pan_path}"


def _share_days() -> int:
    return int(os.environ.get("KUAKE_SHARE_DAYS", "30"))


def _make_client() -> QuarkPanClient:
    return QuarkPanClient()


def _find_share_by_fid(shares_data: dict, fid: str) -> Optional[dict]:
    """在 share-list 结果中按 first_fid（首个文件 fid）反查已有分享。"""
    for item in (shares_data.get("data") or {}).get("list") or []:
        if str(item.get("first_fid") or "") == fid:
            return item
    return None


def _share_item_to_result(item: dict, message: str) -> ApiResponse:
    expired_at = item.get("expired_at")
    return ApiResponse(
        success=True,
        message=message,
        data={
            "share_url": item.get("share_url"),
            "pan_code": item.get("pwd_id"),  # 分享提取码（无提取码时该字段为空/无）
            "share_id": item.get("share_id"),
            "share_expire_at": datetime.fromtimestamp(expired_at / 1000).isoformat() if expired_at else None,
        },
    )


@router.get("/status", response_model=ApiResponse)
async def pan_status(current_user: User = Depends(get_current_active_user)) -> Dict[str, Any]:
    """网盘连接状态：调 kuake user，失败说明凭证失效或二进制不可用。"""
    try:
        client = _make_client()
        data = await asyncio.to_thread(client.user_info)
    except Exception as exc:  # noqa: BLE001 - 凭证失效/二进制缺失统一提示
        return ApiResponse(success=False, message=f"网盘连接失败：{exc}", data=None)
    return ApiResponse(success=True, message="ok", data=(data.get("data") or {}))


@router.get("/entries", response_model=ApiResponse)
async def pan_entries(
    path: str = Query("/", max_length=1000),
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """列出指定目录内容，并批量比对资料池导入状态（每行附 pool 字段辅助人工判断）。"""
    try:
        client = _make_client()
        result = await asyncio.to_thread(client.list_dir, path)
    except Exception as exc:  # noqa: BLE001
        return ApiResponse(success=False, message=f"列出网盘目录失败：{exc}", data=None)

    entries = (result.get("data") or {}).get("list") or []

    # 批量比对：quark-manual 行按 source_url=quark://路径，爬虫行按 pan_path，都已导入的行附 {pool_id, status}
    urls = [_source_url(e["path"]) for e in entries if e.get("path")]
    paths = [e["path"] for e in entries if e.get("path")]
    status_map: Dict[str, Dict[str, Any]] = {}
    if urls:
        stmt = select(MaterialPool.id, MaterialPool.source_url, MaterialPool.pan_path, MaterialPool.status).where(
            MaterialPool.owner_id == current_user.id,
            MaterialPool.is_deleted == False,  # noqa: E712
            or_(MaterialPool.source_url.in_(urls), MaterialPool.pan_path.in_(paths)),
        )
        for pool_id, surl, ppath, st in (await session.execute(stmt)).all():
            info = {"pool_id": pool_id, "status": st}
            if surl:
                status_map[surl] = info
            if ppath:
                status_map.setdefault(_source_url(ppath), info)  # 同一行的网盘路径也指向该记录
    for e in entries:
        e["pool"] = status_map.get(_source_url(e["path"])) if e.get("path") else None

    return ApiResponse(success=True, message="ok", data={"path": path, "list": entries})


async def _ensure_share(client: QuarkPanClient, path: str, fid: Optional[str]) -> dict:
    """确保 path 有分享链接：share-list 按 first_fid 反查（带 fid 时）→ 新建。返回统一结果 dict。"""
    if fid:
        shares = await asyncio.to_thread(client.share_list)
        item = _find_share_by_fid(shares, fid)
        if item:
            return _share_item_to_result(item, "已存在该文件的分享，直接复用").data
    days = _share_days()
    data = await asyncio.to_thread(lambda: client.share(path, days=days, passcode=False))
    url, code, sid = _extract_share_url(data)
    if not url:
        raise PanError(f"分享成功但未解析到链接：{str(data)[:300]}")
    if not sid:
        sid = _lookup_share_id(client, url)
    return {
        "share_url": url,
        "pan_code": code,
        "share_id": sid,
        "share_expire_at": (datetime.now() + timedelta(days=days)).isoformat(),
    }


class ShareRequest(BaseModel):
    path: str = Field(..., min_length=1, max_length=1000, description="网盘文件/文件夹完整路径")
    fid: Optional[str] = Field(None, max_length=100, description="目录条目 fid（用于 share-list 反查查重）")


@router.post("/share", response_model=ApiResponse)
async def pan_share(
    req: ShareRequest,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """创建分享。查重顺序：资料池已有记录（带 pan_url）→ share-list 按 first_fid 反查 → 新建。"""
    try:
        # 1) 资料池已有该文件的记录且带分享链接 → 直接复用
        # （quark-manual 行按 source_url 命中，爬虫行按 pan_path 命中）
        stmt = (
            select(MaterialPool)
            .where(
                MaterialPool.owner_id == current_user.id,
                MaterialPool.is_deleted == False,  # noqa: E712
                or_(
                    MaterialPool.source_url == _source_url(req.path),
                    MaterialPool.pan_path == req.path,
                ),
            )
            .limit(1)
        )
        row = (await session.execute(stmt)).scalar_one_or_none()
        if row and row.pan_url:
            return ApiResponse(
                success=True,
                message="资料池已有该文件的分享链接",
                data={
                    "share_url": row.pan_url,
                    "pan_code": row.pan_code,
                    "share_id": row.share_id,
                    "share_expire_at": row.share_expire_at.isoformat() if row.share_expire_at else None,
                },
            )

        # 2) 3) share-list 反查 / 新建
        client = _make_client()
        result = await _ensure_share(client, req.path, req.fid)
        return ApiResponse(success=True, message="分享已就绪", data=result)
    except Exception as exc:  # noqa: BLE001
        return ApiResponse(success=False, message=f"创建分享失败：{exc}", data=None)


class PanImportRequest(BaseModel):
    pan_path: str = Field(..., min_length=1, max_length=1000, description="网盘文件完整路径")
    file_name: str = Field(..., min_length=1, max_length=300, description="文件名（写入 paper_name）")
    file_size: Optional[int] = Field(None, ge=0, description="文件大小（字节）")
    fid: Optional[str] = Field(None, max_length=100, description="目录条目 fid（分享查重用，避免重复创建分享）")
    create_share: bool = Field(True, description="导入时自动创建分享链接")


@router.post("/import", response_model=ApiResponse)
async def pan_import(
    req: PanImportRequest,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """把网盘文件导入资料池：已有记录直接返回（幂等，爬虫行按 pan_path 命中）；
    命中已有记录但无分享链接且要求建分享时，补齐网盘信息并置待审核。create_share 时先建分享再落库，避免半截状态。"""
    surl = _source_url(req.pan_path)
    stmt = (
        select(MaterialPool)
        .where(
            MaterialPool.owner_id == current_user.id,
            MaterialPool.is_deleted == False,  # noqa: E712
            or_(
                MaterialPool.source_url == surl,
                MaterialPool.pan_path == req.pan_path,
            ),
        )
        .limit(1)
    )

    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing:
        if existing.pan_url or not req.create_share:
            return ApiResponse(success=True, message="该文件已在资料池中", data=_row_to_dict(existing))
        # 命中已有记录（如爬虫行）但尚无分享链接：建分享后补齐网盘信息，收敛到待审核
        try:
            client = _make_client()
            result = await _ensure_share(client, req.pan_path, req.fid)
            raw_expire = result.get("share_expire_at")
            existing.pan_url = result.get("share_url")
            existing.pan_code = result.get("pan_code")
            existing.share_id = result.get("share_id")
            existing.share_expire_at = datetime.fromisoformat(raw_expire) if raw_expire else None
            existing.status = STATUS_PENDING
            await session.commit()
            await session.refresh(existing)
            logger.info(f"[pan] 已有记录补齐网盘信息：pool_id={existing.id} path={req.pan_path}")
            return ApiResponse(success=True, message="该文件已在资料池中，已补齐网盘分享信息", data=_row_to_dict(existing))
        except Exception as exc:  # noqa: BLE001
            return ApiResponse(success=False, message=f"创建分享失败：{exc}", data=None)

    share_url = share_code = share_id = None
    expire_at = None
    if req.create_share:
        try:
            client = _make_client()
            result = await _ensure_share(client, req.pan_path, req.fid)
            share_url = result.get("share_url")
            share_code = result.get("pan_code")
            share_id = result.get("share_id")
            raw_expire = result.get("share_expire_at")
            expire_at = datetime.fromisoformat(raw_expire) if raw_expire else None
        except Exception as exc:  # noqa: BLE001 - 分享失败不落库，避免无链接的半截记录
            return ApiResponse(success=False, message=f"创建分享失败：{exc}", data=None)

    row = MaterialPool(
        owner_id=current_user.id,
        paper_name=req.file_name,
        source_url=surl,
        source_type=SOURCE_TYPE_QUARK_MANUAL,
        file_size=req.file_size,
        pan_type="quark",
        pan_path=req.pan_path,
        pan_url=share_url,
        pan_code=share_code,
        share_id=share_id,
        share_expire_at=expire_at,
        status=STATUS_PENDING,
    )
    session.add(row)
    try:
        await session.commit()
    except IntegrityError:
        # 并发兜底：唯一键冲突 → 返回已存在记录
        await session.rollback()
        existing = (await session.execute(stmt)).scalar_one_or_none()
        logger.info(f"[pan] import 并发冲突，返回已存在记录：{surl}")
        return ApiResponse(
            success=True,
            message="该文件已在资料池中",
            data=_row_to_dict(existing) if existing else None,
        )
    await session.refresh(row)
    logger.info(f"[pan] 网盘文件已导入资料池：{surl} -> pool_id={row.id}")
    return ApiResponse(success=True, message="已导入资料池", data=_row_to_dict(row))
