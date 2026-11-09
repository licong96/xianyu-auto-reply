"""网盘分享地址工具：提取码拼接。

夸克/百度分享页都支持 ?pwd= 参数：卡券正文与存活巡检统一用本模块
拼接出"点开即达"的完整地址，避免各写一份拼接逻辑不一致。

注意：本模块刻意放在 common 根包（而非 common.utils 子包）——
share_health_check 巡检子进程跑在 paper_crawler 独立 venv（无 Pillow 等
重依赖），import common.utils.* 会拉起 image_utils 全链而崩溃；
common 根包 __init__ 为空，任意 venv 都可安全导入。

# CUSTOM: 网盘分享自动续期与内容存活巡检（新增文件，合并作者更新零冲突）
"""
from __future__ import annotations

from typing import Optional

# 支持 pwd= 参数的网盘
PAN_TYPES_WITH_PWD = ("quark", "baidu")


def build_check_url(pan_type: str, url: str, pan_code: Optional[str]) -> str:
    """拼接实际打开/展示的地址：带提取码才能直接看到文件列表。

    - 有 pan_code 且地址未带 pwd 参数时追加（已有 query 用 &，否则用 ?）
    - 地址已含 pwd=（分享文案里常已拼好）或网盘不支持提取码参数时不改动
    """
    url = (url or "").strip()
    code = (pan_code or "").strip()
    if not url or pan_type not in PAN_TYPES_WITH_PWD or not code:
        return url
    if "pwd=" in url:
        return url
    sep = "&" if ("?" in url) else "?"
    return f"{url}{sep}pwd={code}"


__all__ = ["PAN_TYPES_WITH_PWD", "build_check_url"]
