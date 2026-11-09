"""网盘分享到期续期服务（share_renew_service）的单测。

固化语义：
- reshare_pool_links：成功路径更新 link 字段、捕获旧地址、置 check_priority=1、
  调用卡券重建并提交；失败路径单侧不拖垮其他盘、写失败日志仍提交；
- renew_expiring_shares：无命中也落一条汇总日志；truncated 标记按 limit 判定；
- make_client 走 app.services.pan.base（函数内延迟导入，monkeypatch 该模块属性即可）。

运行：cd backend-web && .venv/bin/python -m pytest tests -q
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest

import app.services.share_renew_service as renew_svc
from app.services.share_renew_service import renew_expiring_shares, reshare_pool_links
from common.models.share_check_log import RENEW_DONE, RENEW_FAILED, ShareCheckLog


def _run(coro):
    loop = asyncio.get_event_loop_policy().new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _pool(**kw):
    base = dict(id=100, owner_id=7, card_id=9, pan_type="quark", pan_path="/x",
                pan_url=None, pan_code=None, share_id=None, share_expire_at=None)
    base.update(kw)
    return SimpleNamespace(**base)


def _link(pan_type="quark", url="https://pan.quark.cn/s/old", path="/x"):
    return SimpleNamespace(
        pan_type=pan_type, pan_path=path, pan_url=url, pan_code="1111",
        share_id="sid-old", share_expire_at=None, last_renew_at=None, check_priority=0,
    )


class _FakeSession:
    """最小 AsyncSession 替身：记录 add/commit/refresh。"""

    def __init__(self):
        self.added: list = []
        self.commit_count = 0
        self.refreshed: list = []

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commit_count += 1

    async def refresh(self, obj):
        self.refreshed.append(obj)


class _FakeClient:
    def __init__(self, result=None, error=None):
        self._result = result
        self._error = error

    def create_share(self, path, days=30, passcode=False):
        if self._error:
            raise self._error
        return self._result


def _patch_client(monkeypatch, client):
    monkeypatch.setattr("app.services.pan.base.make_client", lambda pan_type: client)


class TestResharePoolLinks:
    def test_success_updates_link_and_rebuilds_card(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.pan.base.make_client",
            lambda pan_type: _FakeClient(result={
                "share_url": "https://pan.quark.cn/s/new",
                "pan_code": "2222",
                "share_id": "sid-new",
                "share_expire_at": datetime(2026, 12, 6),
            }),
        )
        card_calls: list = []

        async def fake_refresh(session, pool, old_urls, log_prefix="[share-renew]"):
            card_calls.append(list(old_urls))
            return True

        monkeypatch.setattr(renew_svc, "refresh_card_for_pool", fake_refresh)
        pool = _pool()
        link = _link()
        session = _FakeSession()

        data = _run(reshare_pool_links(session, pool, [link]))

        assert data["success_count"] == 1 and data["total"] == 1
        assert link.pan_url == "https://pan.quark.cn/s/new"
        assert link.pan_code == "2222"
        assert link.share_id == "sid-new"
        assert link.share_expire_at == datetime(2026, 12, 6)
        assert link.check_priority == 1  # 新地址优先巡检验证
        assert link.last_renew_at is not None
        assert card_calls == [["https://pan.quark.cn/s/old"]]  # 旧地址传给卡券安全匹配
        assert session.commit_count == 2  # link 提交 + 卡券提交
        assert session.refreshed == [pool]
        # 成功日志落库（新旧地址都拼接提取码展示，同卡券/巡检口径）
        logs = [o for o in session.added if isinstance(o, ShareCheckLog)]
        assert len(logs) == 1 and logs[0].result == RENEW_DONE
        assert logs[0].old_url == "https://pan.quark.cn/s/old?pwd=1111"
        assert logs[0].pan_url == "https://pan.quark.cn/s/new?pwd=2222"
        # 主行 pan_path/pan_type 匹配时同步刷新
        assert pool.pan_url == "https://pan.quark.cn/s/new"

    def test_failure_keeps_other_pans_and_logs(self, monkeypatch):
        def client_factory(pan_type):
            if pan_type == "quark":
                return _FakeClient(result={"share_url": "https://pan.quark.cn/s/new"})
            return _FakeClient(error=RuntimeError("百度 CLI 挂了"))

        monkeypatch.setattr("app.services.pan.base.make_client", client_factory)

        async def fake_refresh(session, pool, old_urls, log_prefix="[share-renew]"):
            return False

        monkeypatch.setattr(renew_svc, "refresh_card_for_pool", fake_refresh)
        pool = _pool()
        quark = _link("quark", url="https://pan.quark.cn/s/oldq")
        baidu = _link("baidu", url="https://pan.baidu.com/s/oldb", path="/y")
        session = _FakeSession()

        data = _run(reshare_pool_links(session, pool, [quark, baidu]))

        assert data["success_count"] == 1 and data["total"] == 2
        assert quark.pan_url == "https://pan.quark.cn/s/new"
        assert baidu.pan_url == "https://pan.baidu.com/s/oldb"  # 失败侧不被改动
        results = {r["pan_type"]: r for r in data["results"]}
        assert results["baidu"]["ok"] is False and "百度 CLI 挂了" in results["baidu"]["error"]
        logs = [o for o in session.added if isinstance(o, ShareCheckLog)]
        assert {l.result for l in logs} == {RENEW_DONE, RENEW_FAILED}
        failed = next(l for l in logs if l.result == RENEW_FAILED)
        assert failed.fail_reason == "百度 CLI 挂了"

    def test_all_failed_still_commits_logs(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.pan.base.make_client",
            lambda pan_type: _FakeClient(error=RuntimeError("路径不存在")),
        )
        session = _FakeSession()
        data = _run(reshare_pool_links(session, _pool(), [_link()]))

        assert data["success_count"] == 0
        assert session.commit_count == 1  # 失败日志也要落库
        logs = [o for o in session.added if isinstance(o, ShareCheckLog)]
        assert len(logs) == 1 and logs[0].result == RENEW_FAILED


class TestRenewExpiringShares:
    def test_no_expiring_writes_summary_log(self, monkeypatch):
        async def fake_fetch(session, **kw):
            return []

        monkeypatch.setattr(renew_svc, "fetch_expiring_link_groups", fake_fetch)
        session = _FakeSession()
        data = _run(renew_expiring_shares(session))

        assert data["pools"] == 0 and data["links"] == 0 and data["truncated"] is False
        summaries = [o for o in session.added if isinstance(o, ShareCheckLog)]
        assert len(summaries) == 1 and summaries[0].task_type == "renew"
        assert session.commit_count == 1

    def test_truncated_flag_when_hits_limit(self, monkeypatch):
        pool = _pool(id=1)

        async def fake_fetch(session, **kw):
            return [(pool, [_link(url=f"https://pan.quark.cn/s/old{i}")]) for i in range(3)]

        async def fake_reshare(session, p, links, **kw):
            return {"results": [], "success_count": 0, "total": len(links), "old_urls": []}

        monkeypatch.setattr(renew_svc, "fetch_expiring_link_groups", fake_fetch)
        monkeypatch.setattr(renew_svc, "reshare_pool_links", fake_reshare)
        session = _FakeSession()
        data = _run(renew_expiring_shares(session, limit=3))

        assert data["pools"] == 3 and data["links"] == 3
        assert data["truncated"] is True  # 达到 limit，可能还有未处理的
