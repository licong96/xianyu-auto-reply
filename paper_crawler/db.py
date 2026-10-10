"""
资料池入库层

功能：
1. 复用 common ORM（asyncmy 驱动）直接写项目 MySQL
2. 两道去重：source_url 唯一约束（库级）+ 文件 md5（应用级）
3. 断点续爬：已入库的 source_url 批量预取跳过；local_path 为空的行可后续补下载
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Iterable

from loguru import logger
from sqlalchemy import select

from common.db.session import async_session_maker
from common.models.material_pool import MaterialPool, STATUS_CRAWLED


def md5_file(path: str | Path, chunk_size: int = 1 << 20) -> str:
    """计算文件 md5（流式，避免大文件占内存）。"""
    h = hashlib.md5()
    with open(path, "rb") as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()


async def fetch_existing_urls(urls: Iterable[str]) -> set[str]:
    """批量查询已入库的 source_url，用于采集前跳过（断点续爬）。"""
    urls = list(urls)
    if not urls:
        return set()
    async with async_session_maker() as s:
        result = await s.execute(select(MaterialPool.source_url).where(MaterialPool.source_url.in_(urls)))
        return set(result.scalars().all())


async def find_by_hash(file_hash: str) -> MaterialPool | None:
    """按 md5 查重：同一份文件换了链接也能识别。"""
    async with async_session_maker() as s:
        result = await s.execute(
            select(MaterialPool).where(MaterialPool.file_hash == file_hash, MaterialPool.is_deleted == False)  # noqa: E712
        )
        return result.scalar_one_or_none()


async def insert_item(item: dict) -> int:
    """插入一条资料池记录，返回记录 ID。source_url 冲突时返回 0（已存在）。"""
    async with async_session_maker() as s:
        exists = await s.execute(select(MaterialPool.id).where(MaterialPool.source_url == item["source_url"]))
        if exists.scalar_one_or_none():
            return 0
        row = MaterialPool(status=STATUS_CRAWLED, **item)
        s.add(row)
        await s.commit()
        return row.id


async def set_local_file(pool_id: int, local_path: str, file_size: int, file_hash: str) -> None:
    """补写本地文件信息（先采元数据、后补下载的场景）。"""
    async with async_session_maker() as s:
        row = await s.get(MaterialPool, pool_id)
        if row:
            row.local_path = local_path
            row.file_size = file_size
            row.file_hash = file_hash
            await s.commit()
