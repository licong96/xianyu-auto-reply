"""单条分享链接的页面判定（总预算 15 秒）。

百度：页面加载完静置 3 秒（提取码中间页跳转稳定），然后在剩余预算内
轮询文件列表容器 div.file-name —— 拿到即正常（"共 N 个文件" N=0 为空），
规定时间内拿不到就视为失败（invalid），不再单独识别错误页。

夸克：轮询列表容器 tbody.ant-table-tbody（行数 0 为空）；同时用失效
文案关键词判失效（夸克失效页无稳定容器）。

通用：预算耗尽前命中风控/登录墙文案 → risk（暂停巡检人工处理）；
夸克耗尽无容器无文案 → error（网络慢，仅重试）。

tab 抽象：任何实现了 get(url, timeout) / ele(selector, timeout) / 属性 text 的对象
（DrissionPage 的 ChromiumTab 天然满足），单测用假对象驱动。

# CUSTOM: 网盘分享自动续期与内容存活巡检（新增文件，合并作者更新零冲突）
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

# 结果取值
VALID = "valid"
INVALID = "invalid"
EMPTY = "empty"
ERROR = "error"
RISK = "risk"

PAGE_BUDGET_SECONDS = 15.0
# 页面加载完成后、开始查询前的静置时间：等 SPA 渲染与提取码页跳转稳定
PAGE_SETTLE_SECONDS = 3.0
POLL_INTERVAL = 0.5

# 各盘列表容器（网盘改版后只需改这里）
QUARK_VALID_SELECTOR = "css:tbody.ant-table-tbody"
BAIDU_VALID_SELECTOR = "css:div.file-name"

# 夸克失效页文案白名单（命中即 invalid）
QUARK_INVALID_KEYWORDS = (
    "分享已取消",
    "分享已删除",
    "文件已被删除",
    "你来晚了",
    "链接已失效",
    "分享不存在",
    "内容不存在",
)

# 风控/登录墙特征（容器拿不到时区分 risk 与 error/invalid）
RISK_KEYWORDS = (
    "访问过于频繁",
    "操作过于频繁",
    "安全验证",
    "请完成验证",
    "滑块",
    "拖动滑块",
    "登录后继续",
    "请先登录",
)


@dataclass
class CheckOutcome:
    status: str
    detail: str = ""
    page_title: str = ""
    duration_seconds: float = 0.0
    extra: dict = field(default_factory=dict)


# 支持提取码拼接的网盘（夸克/百度分享页都支持 ?pwd= 参数）
# 逻辑实现在 common/share_url.py（刻意不在 common.utils，避免巡检子进程
# 的精简 venv 拉起 PIL 等重依赖），卡券正文与巡检共用一份，此处仅转发
from common.share_url import PAN_TYPES_WITH_PWD, build_check_url  # noqa: F401  (re-export)


def _page_text(tab: Any) -> str:
    try:
        return (tab.text or "")[:2000]
    except Exception:
        return ""


def _page_title(tab: Any) -> str:
    try:
        title = tab.title
        return str(title) if title else ""
    except Exception:
        return ""


def _ele(tab: Any, selector: str, timeout: float):
    """包装 tab.ele：未命中统一返回 None。

    DrissionPage 4.x 找不到元素时返回 NoneElement 占位对象（本身非 None，
    访问属性才抛 ElementNotFoundError）——不能用 is not None 判断命中，
    否则任何页面都会"命中"第一个容器。按类名识别占位对象，
    真实元素与测试假对象原样返回。
    """
    try:
        el = tab.ele(selector, timeout=max(0.1, timeout))
        if el is None or el.__class__.__name__ == "NoneElement":
            return None
        return el
    except Exception:
        return None


def _hit_risk(tab: Any) -> CheckOutcome | None:
    """页面文本命中风控/登录墙特征时返回 RISK 结果，否则 None。"""
    text = _page_text(tab)
    hit = next((kw for kw in RISK_KEYWORDS if kw in text), None)
    if not hit:
        return None
    return CheckOutcome(status=RISK, detail=f"疑似风控/登录墙（命中「{hit}」），建议暂停巡检并人工检查浏览器登录态")


def check_link(tab: Any, pan_type: str, url: str, *, budget: float = PAGE_BUDGET_SECONDS) -> CheckOutcome:
    """打开一个分享地址并判定状态（见模块头）。"""
    started = time.time()
    deadline = started + budget
    try:
        tab.get(url, timeout=max(1, int(deadline - time.time())))
    except TypeError:
        tab.get(url)  # 假对象/旧版签名兼容
    except Exception as exc:
        return CheckOutcome(
            status=ERROR,
            detail=f"页面打开失败：{exc}",
            page_title=_page_title(tab),
            duration_seconds=time.time() - started,
        )

    # 静置：等提取码中间页跳转与 SPA 渲染稳定。常量从模块全局读取（测试可置 0）
    time.sleep(min(PAGE_SETTLE_SECONDS, max(0.0, deadline - time.time())))

    while time.time() < deadline:
        remaining = deadline - time.time()
        valid_sel = QUARK_VALID_SELECTOR if pan_type == "quark" else BAIDU_VALID_SELECTOR
        container = _ele(tab, valid_sel, remaining)
        if container is not None:
            if pan_type == "quark":
                # 夸克：数表格行，0 行判空
                try:
                    row_count = len(container.eles("css:tr") or [])
                except Exception:
                    row_count = -1  # 无法数行时按有内容处理
                if row_count == 0:
                    return CheckOutcome(
                        status=EMPTY,
                        detail=f"列表容器存在但为空（{valid_sel} 行数 0）",
                        page_title=_page_title(tab),
                        duration_seconds=time.time() - started,
                    )
            else:
                # 百度：头部"共 N 个文件"判空
                try:
                    import re

                    m = re.search(r"共\s*(\d+)\s*个文件", " ".join(_page_text(tab).split()))
                    if m and int(m.group(1)) == 0:
                        return CheckOutcome(
                            status=EMPTY,
                            detail="页面可打开但「共 0 个文件」",
                            page_title=_page_title(tab),
                            duration_seconds=time.time() - started,
                        )
                except Exception:
                    pass
            return CheckOutcome(
                status=VALID,
                detail=f"命中有效容器 {valid_sel}",
                page_title=_page_title(tab),
                duration_seconds=time.time() - started,
            )

        # 夸克：列表容器没出来时先用失效文案判死（百度不需要，拿不到容器即失败）
        if pan_type == "quark":
            text = _page_text(tab)
            for kw in QUARK_INVALID_KEYWORDS:
                if kw in text:
                    return CheckOutcome(
                        status=INVALID,
                        detail=f"命中失效文案「{kw}」",
                        page_title=_page_title(tab),
                        duration_seconds=time.time() - started,
                    )

        time.sleep(POLL_INTERVAL)

    # 预算耗尽：风控特征 → risk；百度拿不到容器 → invalid；夸克 → error 重试
    risk = _hit_risk(tab)
    if risk:
        risk.page_title = _page_title(tab)
        risk.duration_seconds = time.time() - started
        return risk
    if pan_type == "baidu":
        return CheckOutcome(
            status=INVALID,
            detail=f"{int(budget)} 秒内未获取到文件列表容器（视为分享失效或不可访问）",
            page_title=_page_title(tab),
            duration_seconds=time.time() - started,
        )
    return CheckOutcome(
        status=ERROR,
        detail=f"{int(budget)} 秒内未出现有效容器或失效文案（网络慢或页面结构变更）",
        page_title=_page_title(tab),
        duration_seconds=time.time() - started,
    )


__all__ = [
    "VALID", "INVALID", "EMPTY", "ERROR", "RISK",
    "CheckOutcome", "check_link", "build_check_url", "PAN_TYPES_WITH_PWD",
    "QUARK_VALID_SELECTOR", "BAIDU_VALID_SELECTOR",
    "PAGE_BUDGET_SECONDS",
]
