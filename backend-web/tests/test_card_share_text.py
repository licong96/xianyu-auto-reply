"""卡券正文联动（网盘分享自动续期第一步）的单测。

固化语义：
- render_card_text：子表多盘链接按盘逐条输出 + 空行分隔 + 提取码 + 固定尾注；
  子表为空回退主记录旧单盘字段（老商品字节级不变）；
- card_references_any：仅当卡券正文仍引用任一旧地址时才返回 True（人工改过的卡券不覆盖）；
- refresh_card_for_pool：卡券类型非 text / 正文不含旧地址 / 资料池未绑卡券时不动卡券；
  命中时正文被重建为新地址，且 Card 行随 session 一起提交。

运行：cd backend-web && .venv/bin/python -m pytest tests -q
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from common.services.card_share_text import (
    card_references_any,
    pan_display_name,
    refresh_card_for_pool,
    render_card_text,
)


def _pool(**kw):
    base = dict(id=1, paper_name="奥威尔作品全集", pan_url=None, pan_code=None)
    base.update(kw)
    return SimpleNamespace(**base)


def _link(pan_type, url, code=None):
    return SimpleNamespace(pan_type=pan_type, pan_url=url, pan_code=code)


class TestRenderCardText:
    def test_multi_pan_links_with_blank_line_and_code(self):
        text = render_card_text(
            _pool(),
            [_link("quark", "https://pan.quark.cn/s/aaa", "1234"), _link("baidu", "https://pan.baidu.com/s/bbb")],
        )
        lines = text.splitlines()
        # 夸克带提取码：地址自动拼 ?pwd=，买家点开即达
        assert lines[0] == "夸克网盘：https://pan.quark.cn/s/aaa?pwd=1234"
        assert lines[1] == "提取码：1234"
        assert lines[2] == ""  # 多盘之间空一行
        assert lines[3] == "百度网盘：https://pan.baidu.com/s/bbb"
        assert "资料：奥威尔作品全集" in text
        assert text.endswith("链接失效请联系客服补发。")

    def test_url_already_has_pwd_not_duplicated(self):
        text = render_card_text(
            _pool(),
            [_link("quark", "https://pan.quark.cn/s/6fc3dd9b56ab?pwd=8J3J", "8J3J")],
        )
        assert text.count("pwd=8J3J") == 1
        assert "夸克网盘：https://pan.quark.cn/s/6fc3dd9b56ab?pwd=8J3J" in text

    def test_baidu_link_with_code_gets_pwd(self):
        text = render_card_text(
            _pool(),
            [_link("baidu", "https://pan.baidu.com/s/1mI34KpEa9eGbD-sFA8ixxQ", "7njh")],
        )
        assert "夸克网盘" not in text
        assert "百度网盘：https://pan.baidu.com/s/1mI34KpEa9eGbD-sFA8ixxQ?pwd=7njh" in text

    def test_legacy_single_pan_fallback(self):
        text = render_card_text(_pool(pan_url="https://pan.quark.cn/s/old", pan_code="8888"), [])
        lines = text.splitlines()
        assert lines[0] == "网盘链接：https://pan.quark.cn/s/old"
        assert lines[1] == "提取码：8888"
        assert "资料：奥威尔作品全集" in text

    def test_legacy_without_code(self):
        text = render_card_text(_pool(pan_url="https://pan.quark.cn/s/old"), [])
        assert "提取码" not in text

    def test_pan_display_name(self):
        assert pan_display_name("quark") == "夸克网盘"
        assert pan_display_name("baidu") == "百度网盘"
        assert pan_display_name("future_pan") == "future_pan"  # 未注册盘回退原名


class TestCardReferencesAny:
    def test_hits_old_url(self):
        assert card_references_any("夸克网盘：https://pan.quark.cn/s/old", ["https://pan.quark.cn/s/old"]) is True

    def test_multi_urls_any_hit(self):
        text = "百度网盘：https://pan.baidu.com/s/b"
        assert card_references_any(text, ["https://pan.quark.cn/s/a", "https://pan.baidu.com/s/b"]) is True

    def test_no_hit_means_manual_edit(self):
        assert card_references_any("客服手动编辑的内容", ["https://pan.quark.cn/s/old"]) is False

    def test_empty_text(self):
        assert card_references_any(None, ["https://pan.quark.cn/s/old"]) is False
        assert card_references_any("", ["https://pan.quark.cn/s/old"]) is False

    def test_empty_url_list(self):
        assert card_references_any("任意内容", []) is False


class _FakeResult:
    def __init__(self, scalar=None, scalars_list=None):
        self._scalar = scalar
        self._scalars = scalars_list or []

    def scalar_one_or_none(self):
        return self._scalar

    def scalars(self):
        return SimpleNamespace(all=lambda: self._scalars)


class _FakeSession:
    """最小 AsyncSession 替身：只记录 execute 的语句与 add 的对象。"""

    def __init__(self, card=None, links=None):
        self.card = card
        self.links = links or []
        self.committed = False

    async def execute(self, stmt):
        from common.models.card import Card

        if stmt.column_descriptions[0]["entity"] is Card:
            return _FakeResult(scalar=self.card)
        return _FakeResult(scalars_list=self.links)

    async def commit(self):
        self.committed = True


def _run(coro):
    loop = asyncio.get_event_loop_policy().new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestRefreshCardForPool:
    def test_rebuilds_text_card_referencing_old_url(self):
        card = SimpleNamespace(id=9, type="text", text_content="夸克网盘：https://pan.quark.cn/s/old")
        pool = _pool(card_id=9, owner_id=7)
        session = _FakeSession(card=card, links=[_link("quark", "https://pan.quark.cn/s/new", "6666")])

        updated = _run(refresh_card_for_pool(session, pool, ["https://pan.quark.cn/s/old"]))

        assert updated is True
        assert "https://pan.quark.cn/s/new" in card.text_content
        assert "提取码：6666" in card.text_content
        assert "https://pan.quark.cn/s/old" not in card.text_content

    def test_skips_card_without_old_url(self):
        card = SimpleNamespace(id=9, type="text", text_content="人工编辑过的内容")
        pool = _pool(card_id=9, owner_id=7)
        session = _FakeSession(card=card)

        updated = _run(refresh_card_for_pool(session, pool, ["https://pan.quark.cn/s/old"]))

        assert updated is False
        assert card.text_content == "人工编辑过的内容"

    def test_skips_non_text_card(self):
        card = SimpleNamespace(id=9, type="image", text_content=None)
        pool = _pool(card_id=9, owner_id=7)
        session = _FakeSession(card=card)

        updated = _run(refresh_card_for_pool(session, pool, ["https://pan.quark.cn/s/old"]))

        assert updated is False

    def test_skips_pool_without_card(self):
        pool = _pool(card_id=None)
        session = _FakeSession()
        assert _run(refresh_card_for_pool(session, pool, ["https://pan.quark.cn/s/old"])) is False

    def test_skips_missing_card_row(self):
        pool = _pool(card_id=9, owner_id=7)
        session = _FakeSession(card=None)
        assert _run(refresh_card_for_pool(session, pool, ["https://pan.quark.cn/s/old"])) is False
