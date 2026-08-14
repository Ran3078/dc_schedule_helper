"""PollVoteSelect 測試 —— 持久化投票下拉選單（DynamicItem）。

比照 test_rsvp_button.py 的風格，重點測試：
・custom_id 格式與 from_custom_id 的往返正確性（持久化能不能運作的關鍵）
・投票後資料庫正確寫入、使用者拿到 ephemeral 確認、公告訊息即時更新
・改票允許/拒絕、對已關閉/不存在投票的防呆
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

from src.bot.views_poll import _VOTE_TEMPLATE, PollVoteSelect, build_poll_vote_view
from src.db import repo
from src.lib.ids import new_id

GUILD_ID = 111111111111111111
CHANNEL_ID = 222222222222222222
USER_ID = 333333333333333333


async def _create_poll(db, *, options=None, **kwargs) -> str:
    poll_id = new_id()
    options = options or [("A", None), ("B", None)]
    await repo.create_poll(
        poll_id=poll_id,
        guild_id=GUILD_ID,
        channel_id=CHANNEL_ID,
        creator_id=USER_ID,
        question="晚餐吃什麼",
        options=options,
        **kwargs,
    )
    return poll_id


def _make_interaction() -> MagicMock:
    interaction = MagicMock()
    interaction.guild_id = GUILD_ID
    interaction.user = SimpleNamespace(id=USER_ID)
    interaction.response = AsyncMock()
    interaction.message = AsyncMock()
    return interaction


def _select_with_values(poll_id: str, values: list[str]) -> PollVoteSelect:
    """比照真實流程：discord.py 在 callback 前用 interaction payload 幫
    Select 灌好 `.values`，這裡直接寫 `_values`（`.values` property 的
    fallback 來源，沒有 contextvar 時就是讀它）模擬同樣的效果，不用真的
    跑一次 gateway 派發。"""
    select = PollVoteSelect(poll_id=poll_id, options=[], multi=True)
    select.item._values = values
    return select


class TestCustomIdRoundTrip:
    def test_custom_id_matches_template(self) -> None:
        select = PollVoteSelect(poll_id="abc123XYZ0", options=[], multi=False)
        assert select.item.custom_id == "poll:vote:abc123XYZ0"
        assert _VOTE_TEMPLATE.match(select.item.custom_id)

    def test_template_parses_poll_id(self) -> None:
        match = _VOTE_TEMPLATE.match("poll:vote:abc123XYZ0")
        assert match is not None
        assert match["poll_id"] == "abc123XYZ0"

    async def test_from_custom_id_reconstructs_select(self) -> None:
        match = _VOTE_TEMPLATE.match("poll:vote:abc123XYZ0")
        assert match is not None
        select = await PollVoteSelect.from_custom_id(MagicMock(), MagicMock(), match)
        assert select.poll_id == "abc123XYZ0"


class TestBuildPollVoteView:
    def test_view_is_persistent_with_one_select(self) -> None:
        view = build_poll_vote_view("p1", [{"id": "o1", "label": "A"}], multi=False)
        assert view.timeout is None
        assert len(view.children) == 1

    def test_disabled_flag_disables_the_select(self) -> None:
        view = build_poll_vote_view(
            "p1", [{"id": "o1", "label": "A"}], multi=False, disabled=True
        )
        assert view.children[0].item.disabled is True


class TestCallback:
    async def test_records_vote_in_database(self, db) -> None:
        poll_id = await _create_poll(db)
        options = await repo.list_poll_options(poll_id, GUILD_ID)
        interaction = _make_interaction()

        await _select_with_values(poll_id, [options[0]["id"]]).callback(interaction)

        votes = await repo.list_poll_votes(poll_id, GUILD_ID)
        assert votes[0]["option_id"] == options[0]["id"]
        assert votes[0]["user_id"] == str(USER_ID)

    async def test_sends_ephemeral_confirmation(self, db) -> None:
        poll_id = await _create_poll(db)
        options = await repo.list_poll_options(poll_id, GUILD_ID)
        interaction = _make_interaction()

        await _select_with_values(poll_id, [options[0]["id"]]).callback(interaction)

        interaction.response.send_message.assert_awaited_once()
        args, kwargs = interaction.response.send_message.call_args
        assert kwargs["ephemeral"] is True
        assert "已記錄" in args[0]

    async def test_refreshes_the_public_announcement(self, db) -> None:
        poll_id = await _create_poll(db)
        options = await repo.list_poll_options(poll_id, GUILD_ID)
        interaction = _make_interaction()

        await _select_with_values(poll_id, [options[0]["id"]]).callback(interaction)

        interaction.message.edit.assert_awaited_once()
        _, kwargs = interaction.message.edit.call_args
        embed = kwargs["embed"]
        assert any(f.name.startswith(options[0]["label"]) for f in embed.fields)

    async def test_change_allowed_overwrites_vote(self, db) -> None:
        poll_id = await _create_poll(db, allow_change=True)
        options = await repo.list_poll_options(poll_id, GUILD_ID)
        interaction = _make_interaction()

        await _select_with_values(poll_id, [options[0]["id"]]).callback(interaction)
        await _select_with_values(poll_id, [options[1]["id"]]).callback(interaction)

        votes = await repo.list_poll_votes(poll_id, GUILD_ID)
        assert [v["option_id"] for v in votes] == [options[1]["id"]]

    async def test_change_disallowed_is_rejected(self, db) -> None:
        poll_id = await _create_poll(db, allow_change=False)
        options = await repo.list_poll_options(poll_id, GUILD_ID)
        interaction = _make_interaction()

        await _select_with_values(poll_id, [options[0]["id"]]).callback(interaction)
        await _select_with_values(poll_id, [options[1]["id"]]).callback(interaction)

        args, _ = interaction.response.send_message.call_args
        assert "不能改票" in args[0]
        votes = await repo.list_poll_votes(poll_id, GUILD_ID)
        assert [v["option_id"] for v in votes] == [options[0]["id"]]

    async def test_closed_poll_rejects_vote(self, db) -> None:
        poll_id = await _create_poll(db)
        options = await repo.list_poll_options(poll_id, GUILD_ID)
        await repo.close_poll(poll_id, GUILD_ID)
        interaction = _make_interaction()

        await _select_with_values(poll_id, [options[0]["id"]]).callback(interaction)

        args, _ = interaction.response.send_message.call_args
        assert "截止" in args[0]
        assert await repo.list_poll_votes(poll_id, GUILD_ID) == []
        interaction.message.edit.assert_not_awaited()

    async def test_nonexistent_poll_shows_error(self, db) -> None:
        interaction = _make_interaction()

        await _select_with_values("does-not-exist", ["x"]).callback(interaction)

        args, _ = interaction.response.send_message.call_args
        assert "找不到" in args[0]
        interaction.message.edit.assert_not_awaited()

    async def test_no_guild_id_is_a_silent_noop(self, db) -> None:
        interaction = _make_interaction()
        interaction.guild_id = None

        await _select_with_values("p1", ["x"]).callback(interaction)

        interaction.response.send_message.assert_not_awaited()


class TestRefreshResilience:
    async def test_message_edit_failure_does_not_raise(self, db) -> None:
        poll_id = await _create_poll(db)
        options = await repo.list_poll_options(poll_id, GUILD_ID)
        interaction = _make_interaction()
        interaction.message.edit = AsyncMock(
            side_effect=discord.HTTPException(MagicMock(status=404), "not found")
        )

        await _select_with_values(poll_id, [options[0]["id"]]).callback(interaction)
