"""单条分享链接的页面判定状态机（总预算 15 秒）。

判定顺序（每条链接）：
1. tab.get(url, timeout=剩余预算) —— 页面跳转
2. 优先轮询"错误容器"：命中 → invalid（链接确实死了，高置信）
   百度: css:#error-new #shareError
   夸克: 页面文本命中失效关键词（夸克失效页无稳定容器，用文案白名单）
3. 轮询"有效容器"：命中 → valid / empty（列表容器存在但无文件条目）
   夸克: css:tbody.ant-table-tbody（行数 0 → empty）
   百度: css:div.file-name（"共 N 个文件" N=0 → empty）
4. 预算耗尽 → 看 URL/文本特征：
   命中风控/登录墙特征 → risk（暂停巡检 + 通知人工）
   否则 → error（网络慢等，永不升级为 invalid，只进重试）

tab 抽象：任何实现了 get(url, timeout) / ele(selector, timeout) / 属性 text 的对象
（DrissionPage 的 ChromiumTab 天然满足），单测用假对象驱动。
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
POLL_INTERVAL = 0.5

# 各盘选择器与文案（网盘改版后只需改这里）
QUARK_VALID_SELECTOR = "css:tbody.ant-table-tbody"
BAIDU_VALID_SELECTOR = "css:div.file-name"
BAIDU_ERROR_SELECTOR = "css:#error-new #shareError"

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

# 风控/登录墙特征（两个容器都不出现时兜底判 error 还是 risk）
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
# 逻辑实现在 common/utils/share_url.py，卡券正文与巡检共用一份，此处仅转发
from common.utils.share_url import PAN_TYPES_WITH_PWD, build_check_url  # noqa: F401  (re-export)


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
    """包装 tab.ele：任何异常都视为未命中（DrissionPage 页面中途跳转会抛错）。"""
    try:
        return tab.ele(selector, timeout=max(0.1, timeout))
    except Exception:
        return None


def check_link(tab: Any, pan_type: str, url: str, *, budget: float = PAGE_BUDGET_SECONDS) -> CheckOutcome:
    """打开一个分享地址并判定状态（见模块头状态机）。"""
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

    while time.time() < deadline:
        remaining = deadline - time.time()
        # 1) 错误容器优先（高置信 invalid）
        if pan_type == "baidu":
            err = _ele(tab, BAIDU_ERROR_SELECTOR, remaining)
            if err is not None:
                snippet = ""
                try:
                    snippet = " ".join((err.text or "").split())[:200]
                except Exception:
                    pass
                return CheckOutcome(
                    status=INVALID,
                    detail=f"命中错误容器 {BAIDU_ERROR_SELECTOR}：{snippet}",
                    page_title=_page_title(tab),
                    duration_seconds=time.time() - started,
                )
        else:
            text = _page_text(tab)
            for kw in QUARK_INVALID_KEYWORDS:
                if kw in text:
                    return CheckOutcome(
                        status=INVALID,
                        detail=f"命中失效文案「{kw}」",
                        page_title=_page_title(tab),
                        duration_seconds=time.time() - started,
                    )

        # 2) 有效容器
        valid_sel = QUARK_VALID_SELECTOR if pan_type == "quark" else BAIDU_VALID_SELECTOR
        container = _ele(tab, valid_sel, remaining)
        if container is not None:
            if pan_type == "quark":
                try:
                    rows = container.eles("css:tr") or []
                    row_count = len(rows)
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
                # 百度：头部"共 N 个文件"可判空
                try:
                    header_text = " ".join(_page_text(tab).split())
                    import re

                    m = re.search(r"共\s*(\d+)\s*个文件", header_text)
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

        time.sleep(POLL_INTERVAL)

    # 3) 预算耗尽：风控特征 → risk；否则 error
    text = _page_text(tab)
    hit = next((kw for kw in RISK_KEYWORDS if kw in text), None)
    if hit:
        return CheckOutcome(
            status=RISK,
            detail=f"疑似风控/登录墙（命中「{hit}」），建议暂停巡检并人工检查浏览器登录态",
            page_title=_page_title(tab),
            duration_seconds=time.time() - started,
        )
    return CheckOutcome(
        status=ERROR,
        detail=f"{int(budget)} 秒内未出现有效/错误容器（网络慢或页面结构变更）",
        page_title=_page_title(tab),
        duration_seconds=time.time() - started,
    )


__all__ = [
    "VALID", "INVALID", "EMPTY", "ERROR", "RISK",
    "CheckOutcome", "check_link", "build_check_url", "PAN_TYPES_WITH_PWD",
    "QUARK_VALID_SELECTOR", "BAIDU_VALID_SELECTOR", "BAIDU_ERROR_SELECTOR",
    "PAGE_BUDGET_SECONDS",
]
