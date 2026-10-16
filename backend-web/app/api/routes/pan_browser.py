"""
网盘浏览 API 路由 - 多网盘并列查询与合并列表方案

接口：
1. GET  /pan/status    每个已启用网盘各查一次连接，返回 [{pan_type, ok, message}]
2. GET  /pan/entries   并列列出各盘该路径，内存合并（NFC+lower 文件名），返回 drives + list
3. POST /pan/share     body 带 locations，逐盘分享（资料池/子表/按 fid 反查三重查重）
4. POST /pan/import    按路径命中或新建资料池记录，成功的分享写入 xy_material_pool_pan_links 子表

设计要点：
- 网盘客户端走 app.services.pan.base 的 PanClient 协议与注册表，启用的盘由 PAN_ENABLED_PANS 决定
- 某一盘整层查询失败只标 drives，不当作「没有文件」；全部失败才返回错误
- 合并只发生在当前这一层；同名文件大小不同标 size_mismatch（前端默认不勾选双盘）
- 主记录旧 pan_* 字段过渡期仍代表单盘链接；子表为空时卡券回退旧字段

# CUSTOM: 网盘浏览与直选上架（新增文件）
# CUSTOM: 多网盘并列查询与合并列表方案（改造）
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import or_

from app.api.deps import get_current_active_user, get_db_session
from app.services.material_pool_service import _row_to_dict
from app.services.pan.base import (
    PanError,
    enabled_pan_types,
    make_client,
    merge_pan_entries,
)
from common.models.material_pool import (
    STATUS_CRAWLED,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_UPLOADED,
    MaterialPool,
)
from common.models.material_pool_link import MaterialPoolPanLink
from common.models.user import User
from common.schemas.common import ApiResponse

SOURCE_TYPE_QUARK_MANUAL = "quark-manual"
SOURCE_TYPE_PAN_MANUAL = "pan-manual"

router = APIRouter(prefix="/pan", tags=["网盘"])


def _share_days() -> int:
    return int(os.environ.get("KUAKE_SHARE_DAYS", "30"))


def _source_url_variants(pan_type: str, pan_path: str) -> List[str]:
    """一条网盘路径可能对应的历史 source_url 形态（命中旧记录用）。"""
    return [f"{pan_type}://{pan_path}", f"pan://{pan_path}", pan_path]


async def _find_pool_by_pan_path(
    session: AsyncSession, owner_id: int, pan_type: str, pan_path: str
) -> Optional[MaterialPool]:
    """按网盘路径命中资料池记录：source_url（各形态）或 pan_path 比对。"""
    variants = _source_url_variants(pan_type, pan_path)
    stmt = (
        select(MaterialPool)
        .where(
            MaterialPool.owner_id == owner_id,
            MaterialPool.is_deleted == False,  # noqa: E712
            or_(MaterialPool.source_url.in_(variants), MaterialPool.pan_path == pan_path),
        )
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def _find_link_by_pan_path(
    session: AsyncSession, owner_id: int, pan_type: str, pan_path: str
) -> Optional[MaterialPoolPanLink]:
    """按网盘路径命中子表链接（join 主表过滤 owner），用于跨记录复用分享。"""
    stmt = (
        select(MaterialPoolPanLink)
        .join(MaterialPool, MaterialPoolPanLink.pool_id == MaterialPool.id)
        .where(
            MaterialPool.owner_id == owner_id,
            MaterialPool.is_deleted == False,  # noqa: E712
            MaterialPoolPanLink.pan_type == pan_type,
            MaterialPoolPanLink.pan_path == pan_path,
            MaterialPoolPanLink.pan_url.isnot(None),
        )
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


def _share_to_dict(pan_type: str, share: Dict[str, Any]) -> Dict[str, Any]:
    expire_at = share.get("share_expire_at")
    return {
        "pan_type": pan_type,
        "share_url": share.get("share_url"),
        "pan_code": share.get("pan_code"),
        "share_id": share.get("share_id"),
        "share_expire_at": expire_at.isoformat() if isinstance(expire_at, datetime) else expire_at,
    }


async def _ensure_share_for_location(
    session: AsyncSession, owner_id: int, location: Dict[str, Any]
) -> Dict[str, Any]:
    """确保某个 location 有分享链接。查重顺序：子表/主记录 → 按 fid 反查 → 新建。"""
    pan_type = location["pan_type"]
    path = location["path"]
    fid = location.get("fid")

    # 1) 资料池主记录或子表已有该路径的分享 → 直接复用
    row = await _find_pool_by_pan_path(session, owner_id, pan_type, path)
    if row and row.pan_url:
        return _share_to_dict(pan_type, {
            "share_url": row.pan_url,
            "pan_code": row.pan_code,
            "share_id": row.share_id,
            "share_expire_at": row.share_expire_at,
        })
    link = await _find_link_by_pan_path(session, owner_id, pan_type, path)
    if link:
        return _share_to_dict(pan_type, {
            "share_url": link.pan_url,
            "pan_code": link.pan_code,
            "share_id": link.share_id,
            "share_expire_at": link.share_expire_at,
        })

    client = make_client(pan_type)

    # 2) 按 fid 反查已有分享
    if fid:
        existing = await asyncio.to_thread(client.find_share, fid=fid, path=path)
        if existing and existing.get("share_url"):
            return _share_to_dict(pan_type, existing)

    # 3) 新建分享
    share = await asyncio.to_thread(
        lambda: client.create_share(path, days=_share_days(), passcode=False)
    )
    return _share_to_dict(pan_type, share)


# ---------- 连接状态 ----------

@router.get("/status", response_model=ApiResponse)
async def pan_status(current_user: User = Depends(get_current_active_user)) -> Dict[str, Any]:
    """每个已启用网盘各查一次连接。某盘失败不影响其他盘。"""

    async def _check(pan_type: str) -> Dict[str, Any]:
        try:
            client = make_client(pan_type)
            await asyncio.to_thread(client.status_check)
            return {"pan_type": pan_type, "ok": True, "message": ""}
        except Exception as exc:  # noqa: BLE001 - 凭证失效/二进制缺失统一提示
            return {"pan_type": pan_type, "ok": False, "message": str(exc)}

    drives = await asyncio.gather(*[_check(p) for p in enabled_pan_types()])
    return ApiResponse(success=True, message="ok", data={"drives": list(drives)})


# ---------- 目录列举与合并 ----------

@router.get("/entries", response_model=ApiResponse)
async def pan_entries(
    path: str = Query("/", max_length=1000),
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """并列列出各盘该路径并内存合并；某盘整层失败标 drives，不当作「没有文件」。"""

    async def _list(pan_type: str) -> Dict[str, Any]:
        try:
            client = make_client(pan_type)
            entries = await asyncio.to_thread(client.list_entries, path)
            return {"pan_type": pan_type, "ok": True, "entries": entries, "error": None}
        except Exception as exc:  # noqa: BLE001
            return {"pan_type": pan_type, "ok": False, "entries": [], "error": str(exc)}

    results = await asyncio.gather(*[_list(p) for p in enabled_pan_types()])
    drives = [
        {"pan_type": r["pan_type"], "ok": r["ok"], **({"error": r["error"]} if r["error"] else {})}
        for r in results
    ]
    if not any(r["ok"] for r in results):
        first_err = next((r["error"] for r in results if r["error"]), "未知错误")
        return ApiResponse(success=False, message=f"所有网盘查询失败：{first_err}", data=None)

    per_pan = {r["pan_type"]: r["entries"] for r in results if r["ok"]}
    merged = merge_pan_entries(per_pan)

    # 批量比对资料池：每个 location 按路径命中（pan://、<pan_type>://、pan_path）
    status_map: Dict[str, Dict[str, Any]] = {}
    seen_paths = set()
    for row in merged:
        for loc in row["locations"]:
            key = (loc["pan_type"], loc["path"])
            if key in seen_paths:
                continue
            seen_paths.add(key)
            pool = await _find_pool_by_pan_path(session, current_user.id, loc["pan_type"], loc["path"])
            if pool:
                status_map[f"{loc['pan_type']}://{loc['path']}"] = {"pool_id": pool.id, "status": pool.status}
    for row in merged:
        for loc in row["locations"]:
            loc["pool"] = status_map.get(f"{loc['pan_type']}://{loc['path']}") or None

    return ApiResponse(success=True, message="ok", data={"path": path, "drives": drives, "list": merged})


# ---------- 分享 ----------

class LocationItem(BaseModel):
    pan_type: str = Field(..., min_length=1, max_length=20, description="网盘类型：quark/baidu")
    path: str = Field(..., min_length=1, max_length=1000, description="该盘上的完整路径")
    fid: Optional[str] = Field(None, max_length=100, description="该盘条目 id（查重用）")


class ShareRequest(BaseModel):
    locations: List[LocationItem] = Field(default_factory=list, description="要分享的位置列表")
    # 兼容旧调用：单盘单路径直传
    path: Optional[str] = Field(None, max_length=1000)
    fid: Optional[str] = Field(None, max_length=100)


@router.post("/share", response_model=ApiResponse)
async def pan_share(
    req: ShareRequest,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """逐盘创建分享：成功的返回链接，失败的单独标出可重试。查重不重创。"""
    locations = [loc.model_dump() for loc in req.locations]
    if not locations and req.path:
        # 旧版调用兼容：默认夸克
        locations = [{"pan_type": "quark", "path": req.path, "fid": req.fid}]
    if not locations:
        return ApiResponse(success=False, message="locations 不能为空", data=None)

    shares = []
    for loc in locations:
        try:
            shares.append({"pan_type": loc["pan_type"], "ok": True,
                           **await _ensure_share_for_location(session, current_user.id, loc)})
        except Exception as exc:  # noqa: BLE001 - 单侧失败不拖垮其他盘
            logger.warning(f"[pan] 分享失败 {loc['pan_type']}:{loc['path']}：{exc}")
            shares.append({"pan_type": loc["pan_type"], "ok": False, "error": str(exc)})

    ok_count = sum(1 for s in shares if s["ok"])
    if ok_count == 0:
        return ApiResponse(success=False, message=f"所有网盘分享失败：{shares[0].get('error')}", data={"shares": shares})
    return ApiResponse(success=True, message=f"{ok_count}/{len(shares)} 个网盘分享成功", data={"shares": shares})


# ---------- 导入资料池 ----------

class PanImportRequest(BaseModel):
    file_name: str = Field(..., min_length=1, max_length=300, description="文件名（写入 paper_name）")
    file_size: Optional[int] = Field(None, ge=0, description="文件大小（字节）")
    is_dir: bool = Field(False, description="导入的是整个文件夹：分享链接为文件夹链接")
    create_share: bool = Field(True, description="导入时自动创建分享链接")
    locations: List[LocationItem] = Field(..., min_length=1, description="该文件实际存在的网盘位置")
    # 兼容旧调用：单盘单路径直传
    pan_path: Optional[str] = Field(None, max_length=1000)
    fid: Optional[str] = Field(None, max_length=100)


def _loc_to_link(pool_id: int, loc: Dict[str, Any], share: Optional[Dict[str, Any]]) -> MaterialPoolPanLink:
    """把 location + 分享结果合成子表行（share 为 None 时只落位置信息）。"""
    expire_raw = (share or {}).get("share_expire_at")
    expire_at = datetime.fromisoformat(expire_raw) if isinstance(expire_raw, str) else expire_raw
    return MaterialPoolPanLink(
        pool_id=pool_id,
        pan_type=loc["pan_type"],
        pan_path=loc["path"],
        fid=loc.get("fid"),
        file_size=loc.get("size"),
        pan_url=(share or {}).get("share_url"),
        pan_code=(share or {}).get("pan_code"),
        share_id=(share or {}).get("share_id"),
        share_expire_at=expire_at,
    )


@router.post("/import", response_model=ApiResponse)
async def pan_import(
    req: PanImportRequest,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """把网盘文件导入资料池：按任一位置路径命中已有记录则补链，否则新建；
    成功的分享写入子表。全部失败不落库，避免无链接的半截记录。"""
    locations = [loc.model_dump() for loc in req.locations]
    if req.pan_path:
        locations = [{"pan_type": "quark", "path": req.pan_path,
                      "fid": req.fid, "size": req.file_size}] + [
            l for l in locations if l["path"] != req.pan_path
        ]
    if not locations:
        return ApiResponse(success=False, message="locations 不能为空", data=None)

    # 提前取 id：commit/rollback 后 user 属性过期，异步会话不能懒加载
    owner_id = current_user.id

    # 逐盘确保分享（create_share=False 时跳过，沿用已有链接）
    shares: Dict[str, Dict[str, Any]] = {}
    if req.create_share:
        for loc in locations:
            try:
                shares[loc["pan_type"]] = await _ensure_share_for_location(session, owner_id, loc)
            except Exception as exc:  # noqa: BLE001 - 单侧失败保留其他盘
                logger.warning(f"[pan] import 分享失败 {loc['pan_type']}:{loc['path']}：{exc}")
        if not shares:
            return ApiResponse(success=False, message="所有网盘创建分享失败，未导入", data=None)

    # 命中已有记录：任一位置路径能命中即同一商品
    existing: Optional[MaterialPool] = None
    for loc in locations:
        existing = await _find_pool_by_pan_path(session, owner_id, loc["pan_type"], loc["path"])
        if existing:
            break

    existed = existing is not None
    if not existing:
        first_ok = next(iter(shares.values()), None)
        first_loc = locations[0]
        source_type = (
            f"{first_loc['pan_type']}-manual" if len(locations) == 1 else SOURCE_TYPE_PAN_MANUAL
        )
        existing = MaterialPool(
            owner_id=owner_id,
            paper_name=req.file_name,
            source_url=f"pan://{first_loc['path']}",
            source_type=source_type,
            file_size=req.file_size,
            is_dir=req.is_dir,
            pan_type=first_loc["pan_type"],
            pan_path=first_loc["path"],
            pan_url=(first_ok or {}).get("share_url"),
            pan_code=(first_ok or {}).get("pan_code"),
            share_id=(first_ok or {}).get("share_id"),
            share_expire_at=datetime.fromisoformat(first_ok["share_expire_at"])
            if first_ok and isinstance(first_ok.get("share_expire_at"), str) else None,
            status=STATUS_PENDING,
        )
        session.add(existing)
        await session.flush()  # 先取主键，子表写入时 pool_id 才有值

    # 子表落链：已有 (pool_id, pan_type) 则更新，否则插入
    stmt = select(MaterialPoolPanLink).where(MaterialPoolPanLink.pool_id == existing.id)
    link_rows = (await session.execute(stmt)).scalars().all() if existed else []
    link_by_type = {l.pan_type: l for l in link_rows}
    for loc in locations:
        share = shares.get(loc["pan_type"])
        if share is None and not existed:
            continue  # 新建记录且该盘无分享：不写入
        link = link_by_type.get(loc["pan_type"])
        if link:
            if share:
                link.fid = loc.get("fid") or link.fid
                link.file_size = loc.get("size") or link.file_size
                link.pan_url = share.get("share_url")
                link.pan_code = share.get("pan_code")
                link.share_id = share.get("share_id")
                if isinstance(share.get("share_expire_at"), str):
                    link.share_expire_at = datetime.fromisoformat(share["share_expire_at"])
        else:
            session.add(_loc_to_link(existing.id, loc, share))

    # 旧记录收敛：主记录还没有链接且新分享成功 → 补齐旧字段并置待审核
    if existed and not existing.pan_url:
        first_ok = next(iter(shares.values()), None)
        if first_ok:
            first_loc = next(l for l in locations if l["pan_type"] == next(iter(shares)))
            existing.pan_url = first_ok.get("share_url")
            existing.pan_code = first_ok.get("pan_code")
            existing.share_id = first_ok.get("share_id")
            if isinstance(first_ok.get("share_expire_at"), str):
                existing.share_expire_at = datetime.fromisoformat(first_ok["share_expire_at"])
            existing.pan_type = first_loc["pan_type"]
            existing.pan_path = first_loc["path"]
            if existing.status in (STATUS_CRAWLED, STATUS_UPLOADED, STATUS_FAILED):
                existing.status = STATUS_PENDING
            existing.error_message = None

    try:
        await session.commit()
    except IntegrityError:
        # 并发兜底：唯一键冲突 → 返回已存在记录
        await session.rollback()
        existing = None
        for loc in locations:
            existing = await _find_pool_by_pan_path(session, owner_id, loc["pan_type"], loc["path"])
            if existing:
                break
        logger.info(f"[pan] import 并发冲突，返回已存在记录：{req.file_name}")
        existed = True
    if existing:
        await session.refresh(existing)

    message = "该文件已在资料池中" if existed else "已导入资料池"
    logger.info(f"[pan] 网盘文件导入资料池：{req.file_name} -> pool_id={existing.id}（{message}）")
    link_stmt = (
        select(MaterialPoolPanLink)
        .where(MaterialPoolPanLink.pool_id == existing.id)
        .order_by(MaterialPoolPanLink.id)
    )
    pan_links = [
        {
            "pan_type": l.pan_type,
            "pan_path": l.pan_path,
            "pan_url": l.pan_url,
            "pan_code": l.pan_code,
            "share_id": l.share_id,
            "share_expire_at": l.share_expire_at.isoformat() if l.share_expire_at else None,
        }
        for l in (await session.execute(link_stmt)).scalars().all()
    ]
    return ApiResponse(success=True, message=message, data=_row_to_dict(existing, pan_links))
