"""内容存活巡检（share_health_check 包）的单测。

用假 tab 驱动 check_link 状态机，固化判定语义：
- 错误容器/失效文案命中 → invalid（高置信，永不误判为 error）
- 有效容器命中 → valid；列表为空 → empty
- 预算耗尽无容器 → 风控特征 risk / 否则 error（error 不等于失效）
- run_check_batch：回写 last_check_status/last_check_at，invalid/empty/risk 进通知清单，
  risk 提前结束本批，所有结果落 ShareCheckLog

运行：cd backend-web && .venv/bin/python -m pytest tests -q
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from share_health_check import checker
from share_health_check.checker import CheckOutcome, check_link
from share_health_check.runner import run_check_batch
from common.models.share_check_log import ShareCheckLog


def _run(coro):
    loop = asyncio.get_event_loop_policy().new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture(autouse=True)
def _no_settle_wait(monkeypatch):
    """测试里静置等待置零，避免每条用例真实 sleep 3 秒。"""
    monkeypatch.setattr(checker, "PAGE_SETTLE_SECONDS", 0)


class _Container:
    """有效容器替身：quark 场景需要数行。"""

    def __init__(self, rows):
        self._rows = rows

    def eles(self, selector):
        return self._rows


class FakeTab:
    """check_link 的最小 tab 实现：按场景返回元素/文本。"""

    def __init__(self, *, valid=None, text="", title="分享页", raise_on_get=None):
        self._valid = valid      # 有效容器（_Container 或任意非 None）
        self._text = text
        self._title = title
        self._raise_on_get = raise_on_get
        self.opened: list = []

    def get(self, url, timeout=None):
        if self._raise_on_get:
            raise self._raise_on_get
        self.opened.append((url, timeout))

    def ele(self, selector, timeout=None):
        if selector in (checker.QUARK_VALID_SELECTOR, checker.BAIDU_VALID_SELECTOR):
            return self._valid
        return None

    @property
    def text(self):
        return self._text

    @property
    def title(self):
        return self._title


class TestBuildCheckUrl:
    def test_baidu_appends_pwd(self):
        url = checker.build_check_url("baidu", "https://pan.baidu.com/s/1mI34KpEa9eGbD-sFA8ixxQ", "7njh")
        assert url == "https://pan.baidu.com/s/1mI34KpEa9eGbD-sFA8ixxQ?pwd=7njh"

    def test_baidu_appends_with_amp_when_query_exists(self):
        url = checker.build_check_url("baidu", "https://pan.baidu.com/s/abc?from=123", "7njh")
        assert url == "https://pan.baidu.com/s/abc?from=123&pwd=7njh"

    def test_baidu_keeps_existing_pwd(self):
        url = "https://pan.baidu.com/s/abc?pwd=7njh"
        assert checker.build_check_url("baidu", url, "7njh") == url

    def test_quark_appends_pwd(self):
        url = checker.build_check_url("quark", "https://pan.quark.cn/s/6fc3dd9b56ab", "8J3J")
        assert url == "https://pan.quark.cn/s/6fc3dd9b56ab?pwd=8J3J"

    def test_quark_keeps_existing_pwd(self):
        url = "https://pan.quark.cn/s/6fc3dd9b56ab?pwd=8J3J"
        assert checker.build_check_url("quark", url, "8J3J") == url

    def test_unsupported_pan_unchanged(self):
        url = "https://example.com/s/abc"
        assert checker.build_check_url("aliyun", url, "9999") == url

    def test_no_code_unchanged(self):
        url = "https://pan.baidu.com/s/abc"
        assert checker.build_check_url("baidu", url, None) == url
        assert checker.build_check_url("baidu", url, "  ") == url


class TestCheckLink:
    def test_baidu_no_container_within_budget_is_invalid(self):
        """百度：规定时间内拿不到 file-name 容器即视为失败。"""
        tab = FakeTab(text="啊哦，你来晚了，分享的文件已经被删除了")
        out = check_link(tab, "baidu", "https://pan.baidu.com/s/x", budget=1)
        assert out.status == checker.INVALID
        assert "未获取到文件列表容器" in out.detail

    def test_quark_invalid_keyword(self):
        tab = FakeTab(text="啊哦，你来晚了，分享的文件已经被删除了，下次要早点哟")
        out = check_link(tab, "quark", "https://pan.quark.cn/s/x", budget=3)
        assert out.status == checker.INVALID

    def test_quark_valid_with_rows(self):
        tab = FakeTab(valid=_Container(rows=[object(), object()]))
        out = check_link(tab, "quark", "https://pan.quark.cn/s/x", budget=3)
        assert out.status == checker.VALID
        assert checker.QUARK_VALID_SELECTOR in out.detail

    def test_quark_empty_container_no_rows(self):
        tab = FakeTab(valid=_Container(rows=[]))
        out = check_link(tab, "quark", "https://pan.quark.cn/s/x", budget=3)
        assert out.status == checker.EMPTY

    def test_baidu_valid_with_files(self):
        tab = FakeTab(valid=object(), text="奥威尔作品全集 共 4 个文件 2026-11-06")
        out = check_link(tab, "baidu", "https://pan.baidu.com/s/x", budget=3)
        assert out.status == checker.VALID

    def test_baidu_empty_zero_files(self):
        tab = FakeTab(valid=object(), text="共 0 个文件")
        out = check_link(tab, "baidu", "https://pan.baidu.com/s/x", budget=3)
        assert out.status == checker.EMPTY

    def test_error_when_nothing_found(self):
        tab = FakeTab(text="一些无关内容")
        out = check_link(tab, "quark", "https://pan.quark.cn/s/x", budget=1)
        assert out.status == checker.ERROR
        assert "未出现有效容器或失效文案" in out.detail

    def test_risk_when_captcha_text(self):
        tab = FakeTab(text="请完成验证后继续 拖动滑块")
        out = check_link(tab, "quark", "https://pan.quark.cn/s/x", budget=1)
        assert out.status == checker.RISK

    def test_open_failure_is_error_not_invalid(self):
        tab = FakeTab(raise_on_get=RuntimeError("连接重置"))
        out = check_link(tab, "quark", "https://pan.quark.cn/s/x", budget=3)
        assert out.status == checker.ERROR
        assert "页面打开失败" in out.detail


class _FakeResult:
    def __init__(self, scalars_list=None):
        self._scalars = scalars_list or []

    def scalars(self):
        return SimpleNamespace(all=lambda: self._scalars)


class _FakeSession:
    def __init__(self, links):
        self._links = links
        self.added: list = []
        self.commit_count = 0

    async def execute(self, stmt):
        return _FakeResult(scalars_list=self._links)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commit_count += 1


def _link(url, pan_type="quark", pool_id=1, pan_code=None):
    return SimpleNamespace(
        pool_id=pool_id, pan_type=pan_type, pan_url=url, pan_code=pan_code,
        last_check_status=None, last_check_at=None, check_priority=1,
    )


def _tab_for(url: str):
    if "invalid" in url:
        return FakeTab(text="你来晚了，分享的文件已经被删除了")
    if "empty" in url:
        return FakeTab(valid=_Container(rows=[]))
    if "risk" in url:
        return FakeTab(text="请完成验证")
    if "error" in url:
        return FakeTab(text="无关内容", )
    return FakeTab(valid=_Container(rows=[object()]))


class TestRunCheckBatch:
    def test_batch_updates_links_logs_and_notify(self, monkeypatch):
        # error 用极小预算，避免测试里真的等 15 秒
        monkeypatch.setattr(checker, "PAGE_BUDGET_SECONDS", 1)
        links = [
            _link("https://pan.quark.cn/s/ok"),
            _link("https://pan.quark.cn/s/invalid1"),
            _link("https://pan.quark.cn/s/empty1"),
            _link("https://pan.quark.cn/s/error1"),
        ]
        session = _FakeSession(links)

        stats = _run(run_check_batch(
            session, lambda url: _tab_for(url), batch_size=50, sleep_between=lambda: None,
        ))

        assert stats["checked"] == 4
        assert stats["valid"] == 1
        assert stats["invalid"] == 1
        assert stats["empty"] == 1
        assert stats["error"] == 1
        # invalid/empty 进通知清单，valid/error 不进
        notify_statuses = {n["status"] for n in stats["notify"]}
        assert notify_statuses == {"invalid", "empty"}
        # 回写字段：valid/invalid/empty 消费插队标记；error 保留待复查
        by_url = {l.pan_url: l for l in links}
        assert by_url["https://pan.quark.cn/s/ok"].check_priority == 0
        assert by_url["https://pan.quark.cn/s/error1"].check_priority == 1
        assert by_url["https://pan.quark.cn/s/ok"].last_check_status == "valid"
        assert by_url["https://pan.quark.cn/s/invalid1"].last_check_status == "invalid"
        assert by_url["https://pan.quark.cn/s/ok"].last_check_at is not None
        # 每条结果都落了日志
        logs = [o for o in session.added if isinstance(o, ShareCheckLog)]
        assert len(logs) == 4
        assert all(l.task_type == "check" for l in logs)
        assert session.commit_count == 1

    def test_risk_breaks_batch_early(self, monkeypatch):
        monkeypatch.setattr(checker, "PAGE_BUDGET_SECONDS", 1)
        links = [
            _link("https://pan.quark.cn/s/risk1"),
            _link("https://pan.quark.cn/s/never"),
        ]
        session = _FakeSession(links)

        stats = _run(run_check_batch(
            session, lambda url: _tab_for(url), batch_size=50, sleep_between=lambda: None,
        ))

        assert stats["risk"] == 1
        assert stats["checked"] == 1  # 风控后不再继续检查
        assert links[1].last_check_at is None
        assert stats["notify"][0]["status"] == "risk"

    def test_baidu_link_opens_with_pwd(self, monkeypatch):
        """表里有提取码时，实际打开的地址必须拼上 pwd 参数。"""
        monkeypatch.setattr(checker, "PAGE_BUDGET_SECONDS", 1)
        links = [_link("https://pan.baidu.com/s/abc", pan_type="baidu", pan_code="7njh")]
        session = _FakeSession(links)
        opened: list = []

        def tab_factory(url):
            opened.append(url)
            return FakeTab(valid=object(), text="共 2 个文件")

        stats = _run(run_check_batch(session, tab_factory, sleep_between=lambda: None))

        assert stats["valid"] == 1
        assert opened == ["https://pan.baidu.com/s/abc?pwd=7njh"]
        # 日志与通知记录的是实际打开的完整地址
        logs = [o for o in session.added if isinstance(o, ShareCheckLog)]
        assert logs[0].pan_url == "https://pan.baidu.com/s/abc?pwd=7njh"

    def test_empty_batch(self):
        session = _FakeSession([])
        stats = _run(run_check_batch(session, lambda url: None, sleep_between=lambda: None))
        assert stats["checked"] == 0
        assert session.commit_count == 0
