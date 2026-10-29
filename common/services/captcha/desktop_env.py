"""
环境探测：检测当前 Windows 会话是否处于可交互桌面（未锁屏、RDP 未断开）。

原理：锁屏 / RDP 断开时，系统输入桌面从 "Default" 切换到 "Winlogon"
（屏保场景为 "Screen-saver"）。OpenInputDesktop + GetUserObjectInformation
实时读取输入桌面名称，单次探测成本 <1ms，纯读取无副作用。

约定：任何"探测不了"的情况（非 Windows、权限不足、API 失败）一律
返回可用（None），绝不因探测失败而拦截业务——宁可漏拦，不可误拦。
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

ENV_DESKTOP_OK = "Default"  # 可交互（已登录且未锁屏）的输入桌面名称

_win32_ready = False


def _load_win32() -> bool:
    """声明 Win32 API 原型（仅 Windows 可用）。返回是否可用。"""
    global _win32_ready
    if _win32_ready:
        return True
    if sys.platform != "win32":
        return False
    try:
        user32 = ctypes.windll.user32
        user32.OpenInputDesktop.restype = wintypes.HANDLE
        user32.OpenInputDesktop.argtypes = [
            wintypes.DWORD, wintypes.BOOL, wintypes.DWORD,
        ]
        user32.GetUserObjectInformationW.argtypes = [
            wintypes.HANDLE, wintypes.INT, wintypes.LPVOID,
            wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
        ]
        user32.GetUserObjectInformationW.restype = wintypes.BOOL
        user32.CloseDesktop.argtypes = [wintypes.HANDLE]
        user32.CloseDesktop.restype = wintypes.BOOL
        _win32_ready = True
    except Exception:
        _win32_ready = False
    return _win32_ready


def get_input_desktop_name() -> str | None:
    """返回当前输入桌面名称（"Default"/"Winlogon"/"Screen-saver"）。

    打不开（非 Windows、权限不足等）返回 None，与"探测失败"同口径处理。
    """
    if not _load_win32():
        return None
    user32 = ctypes.windll.user32
    # GENERIC_READ：读取桌面对象信息所需的访问权
    desktop = user32.OpenInputDesktop(0, False, 0x80000000)
    if not desktop:
        return None
    try:
        buf = ctypes.create_unicode_buffer(256)
        needed = wintypes.DWORD(0)
        # nIndex=UOI_NAME(2)：返回对象名称
        if not user32.GetUserObjectInformationW(
            desktop, 2, buf, ctypes.sizeof(buf), ctypes.byref(needed)
        ):
            return None
        return buf.value or None
    finally:
        user32.CloseDesktop(desktop)


def env_block_reason() -> str | None:
    """环境不可交互时返回原因字符串；可用（或无法判断）返回 None。

    返回示例："locked:Winlogon"（锁屏/RDP 断开）、"locked:Screen-saver"（屏保桌面）。
    """
    if sys.platform != "win32":
        return None  # 容器 / 无头环境不拦截
    name = get_input_desktop_name()
    if name is None:
        return None  # 探测失败按可用处理，避免误拦
    if name != ENV_DESKTOP_OK:
        return f"locked:{name}"
    return None
