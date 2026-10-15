"""
网盘客户端协议与注册表 - 多网盘并列查询与合并列表方案

每种网盘一个客户端，对外四件事（同步阻塞，调用方用 asyncio.to_thread 包裹）：
1. status_check()  连接状态（登录失效/二进制不可用时抛 PanError）
2. list_entries(remote)  列出当前目录，返回统一形状的条目列表
3. create_share(remote_path, days, passcode)  创建分享，返回统一形状的分享结果
4. find_share(fid, path)  按文件反查已有分享（查重，防重复创建）

启用方式：环境变量 PAN_ENABLED_PANS（逗号分隔，默认 "quark"）。
新增网盘：实现协议后在 PAN_REGISTRY 注册，列表/合并/卡券无需再改。

合并规则（merge_pan_entries）：
- 只合并当前这一层；合并键 = NFC + lower(文件名)，不区分大小写
- 文件和文件夹不合并；同名文件大小不同标 size_mismatch

# CUSTOM: 多网盘并列查询与合并列表方案（新增文件，合并作者更新零冲突）
"""
from __future__ import annotations

import os
import unicodedata
from typing import Any, Callable, Dict, List, Optional, Protocol, runtime_checkable

from app.services.pan.baidu_pan_client import BaiduPanClient
from app.services.pan.quark_pan_client import PanError, QuarkPanClient  # noqa: F401  (re-export)

# 统一条目形状
# {"name": str, "path": str, "is_dir": bool, "fid": str|None, "size": int|None, "mtime": int|None}
PanEntryDict = Dict[str, Any]

# 统一分享结果形状
# {"share_url": str, "pan_code": str|None, "share_id": str|None, "share_expire_at": datetime|None}
PanShareDict = Dict[str, Any]


@runtime_checkable
class PanClient(Protocol):
    """网盘客户端协议。实现类需提供 class 属性 PAN_TYPE。"""

    PAN_TYPE: str

    def status_check(self) -> None:
        """连接状态检查。失败抛 PanError（登录失效/二进制不可用/CLI 报错）。"""
        ...

    def list_entries(self, remote: str = "/") -> List[PanEntryDict]:
        """列出当前目录（单层），返回统一形状的条目列表。"""
        ...

    def create_share(self, remote_path: str, days: int = 30, passcode: bool = False) -> PanShareDict:
        """创建分享。失败抛 PanError。返回统一形状（含 share_expire_at 为 datetime）。"""
        ...

    def find_share(self, fid: Optional[str] = None, path: Optional[str] = None) -> Optional[PanShareDict]:
        """按文件反查已有分享（查重）。查不到返回 None。"""
        ...


# 懒构造：QuarkPanClient.__init__ 会校验二进制存在，未启用/未配置的盘不应在导入时实例化
PAN_REGISTRY: Dict[str, Callable[[], PanClient]] = {
    "quark": QuarkPanClient,
    "baidu": BaiduPanClient,
}


def enabled_pan_types() -> List[str]:
    """启用的网盘列表（环境变量 PAN_ENABLED_PANS，默认仅夸克）。未知取值忽略。"""
    raw = os.environ.get("PAN_ENABLED_PANS", "quark")
    result = []
    for item in raw.split(","):
        pan_type = item.strip()
        if pan_type and pan_type in PAN_REGISTRY and pan_type not in result:
            result.append(pan_type)
    return result


def make_client(pan_type: str) -> PanClient:
    """构造指定网盘客户端。未注册时抛 PanError。"""
    factory = PAN_REGISTRY.get(pan_type)
    if not factory:
        raise PanError(f"未注册的网盘类型：{pan_type}")
    return factory()


def merge_key(file_name: str) -> str:
    """合并键：NFC 归一化 + 小写（不区分大小写）。"""
    return unicodedata.normalize("NFC", file_name).lower()


def merge_pan_entries(
    per_pan: Dict[str, List[PanEntryDict]],
) -> List[Dict[str, Any]]:
    """把各盘单层条目按合并键合成一份列表。

    per_pan: {pan_type: 该盘条目列表}
    返回: [{"file_name", "dir", "size_mismatch", "locations": [{pan_type, path, fid, size, mtime}]}]
    - 只合并文件与文件、文件夹与文件夹（is_dir 参与键）
    - 同名文件大小不同 → size_mismatch=True。注意百度 ls 只给 2 位小数的人类可读大小
      （MB 级误差约 5KB），比较带 0.5% 容差，避免同文件被误标
    - 展示名取第一个见到的大小写形式（原文）
    """

    def _sizes_differ(sizes: set) -> bool:
        vals = sorted(s for s in sizes if s is not None)
        for i in range(len(vals)):
            for j in range(i + 1, len(vals)):
                tolerance = max(1024, vals[j] * 0.005)
                if vals[j] - vals[i] > tolerance:
                    return True
        return False

    groups: Dict[tuple, Dict[str, Any]] = {}
    order: List[tuple] = []
    for pan_type, entries in per_pan.items():
        for e in entries:
            name = str(e.get("name") or "")
            is_dir = bool(e.get("is_dir"))
            key = (merge_key(name), is_dir)
            if key not in groups:
                groups[key] = {
                    "file_name": name,
                    "dir": is_dir,
                    "size_mismatch": False,
                    "locations": [],
                    "_sizes": set(),
                }
                order.append(key)
            g = groups[key]
            size = e.get("size")
            if not is_dir and size is not None:
                g["_sizes"].add(size)
                g["size_mismatch"] = _sizes_differ(g["_sizes"])
            g["locations"].append({
                "pan_type": pan_type,
                "path": e.get("path") or "",
                "fid": e.get("fid"),
                "size": size,
                "mtime": e.get("mtime"),
            })
    result = []
    for key in order:
        g = groups[key]
        g.pop("_sizes", None)
        result.append(g)
    return result


# 盘显示名（卡券正文、页面标识用；合并与卡券逻辑不写死盘名判断，只查表）
PAN_DISPLAY_NAMES: Dict[str, str] = {
    "quark": "夸克网盘",
    "baidu": "百度网盘",
}


def pan_display_name(pan_type: str) -> str:
    return PAN_DISPLAY_NAMES.get(pan_type, pan_type)
