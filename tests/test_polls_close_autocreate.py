"""`/poll close` 對時段投票（kind=time_slot）自動建立活動的行為。

比照 test_confirm_view.py 的拆分理由：直接呼叫 `Polls._close_impl`，繞過
`@app_commands.command` 裝飾器，不需要真的啟動 gateway 連線。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

from src.bot.cogs.polls import Polls
from src.db import repo
from src.lib.ids import new_id

GUILD_ID = 111111111111111111
CHANNEL_ID = 222222222222222222
USER_ID = 333333333333333333


async def _create_poll(db, *, options=None, kind: str = "time_slot", **kwargs) -> str:
    poll_id = new_id()
    if options is None:
        options = [("<t:100:F>", "100"), ("<t:200:F>", "200")]
    await repo.create_poll(
        poll_id=poll_id,
        guild_id=GUILD_ID,
        channel_id=CHANNEL_ID,
        creator_id=USER_ID,
        question="下次團練訂哪天",
        options=options,
        kind=kind,
        **kwargs,
    )
    return poll_id


def _make_channel() -> MagicMock:
    channel = MagicMock()
    sent_message = MagicMock()
    sent_message.id = 999999
    sent_message.jump_url = f"https://discord.com/channels/{GUILD_ID}/{CHANNEL_ID}/999999"
    channel.send = AsyncMock(return_value=sent_message)
    channel.fetch_message = AsyncMock(return_value=AsyncMock())
    return channel


def _make_bot(channel: MagicMock | None) -> MagicMock:
    bot = MagicMock()
    bot.get_channel.return_value = channel
    return bot


def _make_interaction() -> MagicMock:
    interaction = MagicMock()
    interaction.guild_id = GUILD_ID
    interaction.user = SimpleNamespace(id=USER_ID)
    interaction.response = AsyncMock()
    interaction.followup = AsyncMock()
    interaction.guild = MagicMock(id=GUILD_ID, members=[])
    interaction.guild.get_role.return_value = None
    return interaction


class TestUniqueWinnerCreatesEvent:
    async def test_creates_event_with_winning_slot_and_title(self, db) -> None:
        poll_id = await _create_poll(db)
        options = await repo.list_poll_options(poll_id, GUILD_ID)
        # option[1]（epoch 200）拿到 2 票，option[0] 只有 1 票 —— 應該獲勝
        await repo.cast_vote(poll_id, GUILD_ID, 111, [options[0]["id"]], allow_change=True)
        await repo.cast_vote(poll_id, GUILD_ID, 222, [options[1]["id"]], allow_change=True)
        await repo.cast_vote(poll_id, GUILD_ID, 333, [options[1]["id"]], allow_change=True)

        channel = _make_channel()
        cog = Polls(_make_bot(channel))
        interaction = _make_interaction()

        await cog._close_impl(interaction, poll_id)

        events = await db.query_all("SELECT * FROM events")
        assert len(events) == 1
        assert events[0]["starts_at_utc"] == 200
        assert events[0]["title"] == "下次團練訂哪天"

    async def test_all_voters_become_invitees_not_just_winning_option(self, db) -> None:
        poll_id = await _create_poll(db)
        options = await repo.list_poll_options(poll_id, GUILD_ID)
        await repo.cast_vote(poll_id, GUILD_ID, 111, [options[0]["id"]], allow_change=True)
        await repo.cast_vote(poll_id, GUILD_ID, 222, [options[1]["id"]], allow_change=True)
        await repo.cast_vote(poll_id, GUILD_ID, 333, [options[1]["id"]], allow_change=True)

        cog = Polls(_make_bot(_make_channel()))
        interaction = _make_interaction()

        await cog._close_impl(interaction, poll_id)

        events = await db.query_all("SELECT * FROM events")
        invitees = await repo.list_event_invitees(events[0]["id"], GUILD_ID)
        invited_users = {int(i["target_id"]) for i in invitees if i["target_type"] == "user"}
        assert invited_users == {111, 222, 333}

    async def test_announces_to_polls_channel_and_confirms_with_jump_url(self, db) -> None:
        poll_id = await _create_poll(db)
        options = await repo.list_poll_options(poll_id, GUILD_ID)
        await repo.cast_vote(poll_id, GUILD_ID, 111, [options[1]["id"]], allow_change=True)

        channel = _make_channel()
        cog = Polls(_make_bot(channel))
        interaction = _make_interaction()

        await cog._close_impl(interaction, poll_id)

        channel.send.assert_awaited_once()
        args, _ = interaction.followup.send.call_args
        assert "已自動建立活動" in args[0]
        assert "999999" in args[0]


class TestNoAutoCreateCases:
    async def test_generic_poll_never_triggers_auto_create(self, db) -> None:
        poll_id = await _create_poll(db, options=[("火鍋", None), ("燒肉", None)], kind="generic")
        options = await repo.list_poll_options(poll_id, GUILD_ID)
        await repo.cast_vote(poll_id, GUILD_ID, 111, [options[0]["id"]], allow_change=True)

        channel = _make_channel()
        cog = Polls(_make_bot(channel))
        interaction = _make_interaction()

        await cog._close_impl(interaction, poll_id)

        assert await db.query_all("SELECT * FROM events") == []
        channel.send.assert_not_awaited()

    async def test_tie_does_not_create_event(self, db) -> None:
        poll_id = await _create_poll(db)
        options = await repo.list_poll_options(poll_id, GUILD_ID)
        await repo.cast_vote(poll_id, GUILD_ID, 111, [options[0]["id"]], allow_change=True)
        await repo.cast_vote(poll_id, GUILD_ID, 222, [options[1]["id"]], allow_change=True)

        cog = Polls(_make_bot(_make_channel()))
        interaction = _make_interaction()

        await cog._close_impl(interaction, poll_id)

        assert await db.query_all("SELECT * FROM events") == []
        args, _ = interaction.followup.send.call_args
        assert "平手" in args[0]

    async def test_no_votes_does_not_create_event(self, db) -> None:
        poll_id = await _create_poll(db)

        cog = Polls(_make_bot(_make_channel()))
        interaction = _make_interaction()

        await cog._close_impl(interaction, poll_id)

        assert await db.query_all("SELECT * FROM events") == []
        args, _ = interaction.followup.send.call_args
        assert "沒有人投票" in args[0]

    async def test_corrupted_meta_on_winning_option_does_not_create_event(self, db) -> None:
        poll_id = await _create_poll(db, options=[("A", "not-a-number"), ("B", None)])
        options = await repo.list_poll_options(poll_id, GUILD_ID)
        await repo.cast_vote(poll_id, GUILD_ID, 111, [options[0]["id"]], allow_change=True)

        cog = Polls(_make_bot(_make_channel()))
        interaction = _make_interaction()

        await cog._close_impl(interaction, poll_id)  # 不應拋例外

        assert await db.query_all("SELECT * FROM events") == []
        args, _ = interaction.followup.send.call_args
        assert "損毀" in args[0]


class TestChannelResolutionFailure:
    async def test_event_still_created_when_channel_cannot_be_resolved(self, db) -> None:
        poll_id = await _create_poll(db)
        options = await repo.list_poll_options(poll_id, GUILD_ID)
        await repo.cast_vote(poll_id, GUILD_ID, 111, [options[1]["id"]], allow_change=True)

        bot = _make_bot(None)
        bot.fetch_channel = AsyncMock(
            side_effect=discord.HTTPException(MagicMock(status=404), "not found")
        )
        cog = Polls(bot)
        interaction = _make_interaction()

        await cog._close_impl(interaction, poll_id)  # 不應拋例外

        events = await db.query_all("SELECT * FROM events")
        assert len(events) == 1
        args, _ = interaction.followup.send.call_args
        assert "找不到頻道" in args[0]
        assert events[0]["id"] in args[0]
