"""`/event edit`／`cancel`／`invite`／`ping` 測試——這幾個都拆了 `_impl`，
直接呼叫、不透過 app_commands 裝飾器（比照 polls.py／views.py 的拆法）。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

from src.bot.cogs.events import Events
from src.db import repo
from src.lib.clock import now_ms
from src.lib.ids import new_id

GUILD_ID = 111111111111111111
CHANNEL_ID = 222222222222222222
CREATOR_ID = 333333333333333333
OTHER_USER_ID = 444444444444444444
ORGANIZER_ROLE_ID = 555555555555555555

NOW = now_ms()
HOUR = 3_600_000


async def _restrict_to_organizer_role(db) -> None:
    """讓「非建立者、非管理員身分組」這種測試情境真的有意義——
    `organizer_role_id` 沒設定時人人都算管理員（見 `is_organizer` 的說明），
    要先設定一個跟測試帳號無關的身分組，拒絕才會真的發生。"""
    await repo.ensure_guild(GUILD_ID, "Asia/Taipei")
    await repo.update_guild_settings(GUILD_ID, organizer_role_id=str(ORGANIZER_ROLE_ID))


async def _create_event(db, *, creator_id: int = CREATOR_ID, **kwargs) -> str:
    event_id = new_id()
    await repo.create_event(
        event_id=event_id,
        guild_id=GUILD_ID,
        channel_id=CHANNEL_ID,
        creator_id=creator_id,
        title="週五團練",
        starts_at_utc=NOW + HOUR,
        tz="Asia/Taipei",
        **kwargs,
    )
    return event_id


def _make_member(user_id: int, role_ids: tuple[int, ...] = ()) -> MagicMock:
    """`Events._can_manage` 對非建立者會做 `isinstance(member, discord.Member)`
    檢查——`MagicMock(spec=discord.Member)` 讓這個檢查通過，不用真的建構一個
    需要完整 guild 狀態的 discord.Member。"""
    member = MagicMock(spec=discord.Member)
    member.id = user_id
    member.roles = [SimpleNamespace(id=rid) for rid in role_ids]
    return member


def _make_channel() -> MagicMock:
    channel = MagicMock()
    channel.fetch_message = AsyncMock(return_value=AsyncMock())
    sent_message = MagicMock()
    sent_message.id = 999999
    channel.send = AsyncMock(return_value=sent_message)
    return channel


def _make_bot(channel: MagicMock | None) -> MagicMock:
    bot = MagicMock()
    bot.get_channel.return_value = channel
    bot.fetch_channel = AsyncMock(return_value=channel)
    # guild_tz 的 self-heal 分支會讀 bot.settings.default_tz；不明確設成
    # None 的話，MagicMock 會自動生出一個「看起來有值」的假屬性，讓
    # resolve_user_tz 回傳一個不是字串的東西，之後 ZoneInfo(tz) 就會炸掉。
    bot.settings = None
    return bot


def _make_interaction(*, user: MagicMock, guild: MagicMock | None = None) -> MagicMock:
    interaction = MagicMock()
    interaction.guild_id = GUILD_ID
    interaction.user = user
    interaction.response = AsyncMock()
    interaction.followup = AsyncMock()
    interaction.guild = guild if guild is not None else MagicMock(id=GUILD_ID)
    return interaction


class TestEditPermission:
    async def test_creator_can_open_edit_modal(self, db) -> None:
        event_id = await _create_event(db)
        cog = Events(_make_bot(_make_channel()))
        interaction = _make_interaction(user=_make_member(CREATOR_ID))

        await cog._edit_impl(interaction, event_id)

        interaction.response.send_modal.assert_awaited_once()

    async def test_non_creator_without_role_is_denied(self, db) -> None:
        event_id = await _create_event(db)
        await _restrict_to_organizer_role(db)
        cog = Events(_make_bot(_make_channel()))
        interaction = _make_interaction(user=_make_member(OTHER_USER_ID))

        await cog._edit_impl(interaction, event_id)

        interaction.response.send_modal.assert_not_awaited()
        args, kwargs = interaction.response.send_message.call_args
        assert "只有" in args[0]
        assert kwargs["ephemeral"] is True

    async def test_organizer_role_member_is_allowed(self, db) -> None:
        event_id = await _create_event(db)
        await repo.ensure_guild(GUILD_ID, "Asia/Taipei")
        await repo.update_guild_settings(GUILD_ID, organizer_role_id=str(ORGANIZER_ROLE_ID))
        cog = Events(_make_bot(_make_channel()))
        interaction = _make_interaction(
            user=_make_member(OTHER_USER_ID, role_ids=(ORGANIZER_ROLE_ID,))
        )

        await cog._edit_impl(interaction, event_id)

        interaction.response.send_modal.assert_awaited_once()

    async def test_nonexistent_event_shows_error(self, db) -> None:
        cog = Events(_make_bot(_make_channel()))
        interaction = _make_interaction(user=_make_member(CREATOR_ID))

        await cog._edit_impl(interaction, "does-not-exist")

        args, _ = interaction.response.send_message.call_args
        assert "找不到" in args[0]


class TestCancel:
    async def test_cancels_and_disables_rsvp_buttons(self, db) -> None:
        event_id = await _create_event(db)
        await repo.set_event_message(event_id, GUILD_ID, 777)
        channel = _make_channel()
        cog = Events(_make_bot(channel))
        interaction = _make_interaction(user=_make_member(CREATOR_ID))

        await cog._cancel_impl(interaction, event_id, "測試取消")

        row = await repo.owned_event(event_id, GUILD_ID)
        assert row["status"] == "cancelled"
        channel.fetch_message.assert_awaited_once()
        _, kwargs = channel.fetch_message.return_value.edit.call_args
        # RsvpButton 是 DynamicItem，disabled 狀態在包起來的 .item（真正的
        # discord.ui.Button）上，DynamicItem 本身沒有轉發這個屬性。
        assert all(child.item.disabled for child in kwargs["view"].children)  # type: ignore[attr-defined]

    async def test_notifies_yes_rsvps(self, db) -> None:
        event_id = await _create_event(db)
        await repo.set_event_message(event_id, GUILD_ID, 777)
        await repo.upsert_rsvp(event_id, GUILD_ID, OTHER_USER_ID, "yes")
        channel = _make_channel()
        cog = Events(_make_bot(channel))
        interaction = _make_interaction(user=_make_member(CREATOR_ID))

        await cog._cancel_impl(interaction, event_id, "測試取消")

        channel.send.assert_awaited_once()
        _, kwargs = channel.send.call_args
        assert str(OTHER_USER_ID) in kwargs["content"]
        assert "測試取消" in kwargs["content"]

    async def test_no_yes_rsvps_sends_no_notification(self, db) -> None:
        event_id = await _create_event(db)
        await repo.set_event_message(event_id, GUILD_ID, 777)
        channel = _make_channel()
        cog = Events(_make_bot(channel))
        interaction = _make_interaction(user=_make_member(CREATOR_ID))

        await cog._cancel_impl(interaction, event_id, None)

        channel.send.assert_not_awaited()

    async def test_syncs_native_event_cancellation(self, db) -> None:
        event_id = await _create_event(db)
        await repo.set_event_discord_id(event_id, GUILD_ID, 888)
        cog = Events(_make_bot(_make_channel()))
        guild = MagicMock(id=GUILD_ID)
        scheduled = MagicMock()
        scheduled.cancel = AsyncMock()
        guild.get_scheduled_event = MagicMock(return_value=scheduled)
        interaction = _make_interaction(user=_make_member(CREATOR_ID), guild=guild)

        await cog._cancel_impl(interaction, event_id, None)

        scheduled.cancel.assert_awaited_once()

    async def test_non_manager_is_denied(self, db) -> None:
        event_id = await _create_event(db)
        await _restrict_to_organizer_role(db)
        cog = Events(_make_bot(_make_channel()))
        interaction = _make_interaction(user=_make_member(OTHER_USER_ID))

        await cog._cancel_impl(interaction, event_id, None)

        row = await repo.owned_event(event_id, GUILD_ID)
        assert row["status"] == "scheduled"

    async def test_already_cancelled_shows_notice(self, db) -> None:
        event_id = await _create_event(db)
        await repo.cancel_event(event_id, GUILD_ID)
        cog = Events(_make_bot(_make_channel()))
        interaction = _make_interaction(user=_make_member(CREATOR_ID))

        await cog._cancel_impl(interaction, event_id, None)

        args, _ = interaction.response.send_message.call_args
        assert "已經是取消狀態" in args[0]


class TestInvite:
    async def test_invites_a_user_and_notifies_them(self, db) -> None:
        event_id = await _create_event(db)
        await repo.set_event_message(event_id, GUILD_ID, 777)
        channel = _make_channel()
        cog = Events(_make_bot(channel))
        interaction = _make_interaction(user=_make_member(CREATOR_ID))
        invitee = SimpleNamespace(id=OTHER_USER_ID)

        await cog._invite_impl(interaction, event_id, invitee, None, False)

        invitees = await repo.list_event_invitees(event_id, GUILD_ID)
        assert invitees[0]["target_id"] == str(OTHER_USER_ID)
        channel.send.assert_awaited_once()
        _, kwargs = channel.send.call_args
        assert str(OTHER_USER_ID) in kwargs["content"]

    async def test_requires_at_least_one_target(self, db) -> None:
        event_id = await _create_event(db)
        cog = Events(_make_bot(_make_channel()))
        interaction = _make_interaction(user=_make_member(CREATOR_ID))

        await cog._invite_impl(interaction, event_id, None, None, False)

        args, _ = interaction.response.send_message.call_args
        assert "至少指定" in args[0]
        assert await repo.list_event_invitees(event_id, GUILD_ID) == []

    async def test_everyone_requires_guild_setting_enabled(self, db) -> None:
        event_id = await _create_event(db)
        cog = Events(_make_bot(_make_channel()))
        interaction = _make_interaction(user=_make_member(CREATOR_ID))

        await cog._invite_impl(interaction, event_id, None, None, True)

        args, _ = interaction.response.send_message.call_args
        assert "@everyone" in args[0]
        assert await repo.list_event_invitees(event_id, GUILD_ID) == []

    async def test_everyone_allowed_when_setting_enabled(self, db) -> None:
        event_id = await _create_event(db)
        await repo.ensure_guild(GUILD_ID, "Asia/Taipei")
        await repo.update_guild_settings(GUILD_ID, allow_everyone_ping=1)
        cog = Events(_make_bot(_make_channel()))
        interaction = _make_interaction(user=_make_member(CREATOR_ID))

        await cog._invite_impl(interaction, event_id, None, None, True)

        invitees = await repo.list_event_invitees(event_id, GUILD_ID)
        assert invitees[0]["target_type"] == "everyone"

    async def test_non_manager_is_denied(self, db) -> None:
        event_id = await _create_event(db)
        await _restrict_to_organizer_role(db)
        cog = Events(_make_bot(_make_channel()))
        interaction = _make_interaction(user=_make_member(OTHER_USER_ID))
        invitee = SimpleNamespace(id=OTHER_USER_ID)

        await cog._invite_impl(interaction, event_id, invitee, None, False)

        assert await repo.list_event_invitees(event_id, GUILD_ID) == []


class TestPing:
    async def test_pings_no_response_by_default(self, db) -> None:
        event_id = await _create_event(db, user_ids=[OTHER_USER_ID])
        channel = _make_channel()
        cog = Events(_make_bot(channel))
        interaction = _make_interaction(user=_make_member(CREATOR_ID))

        await cog._ping_impl(interaction, event_id, None)

        channel.send.assert_awaited_once()
        _, kwargs = channel.send.call_args
        assert str(OTHER_USER_ID) in kwargs["content"]

    async def test_pings_yes_when_filter_given(self, db) -> None:
        event_id = await _create_event(db)
        await repo.upsert_rsvp(event_id, GUILD_ID, OTHER_USER_ID, "yes")
        channel = _make_channel()
        cog = Events(_make_bot(channel))
        interaction = _make_interaction(user=_make_member(CREATOR_ID))
        filter_choice = SimpleNamespace(value="yes")

        await cog._ping_impl(interaction, event_id, filter_choice)

        _, kwargs = channel.send.call_args
        assert str(OTHER_USER_ID) in kwargs["content"]

    async def test_empty_target_list_sends_no_channel_message(self, db) -> None:
        event_id = await _create_event(db)
        channel = _make_channel()
        cog = Events(_make_bot(channel))
        interaction = _make_interaction(user=_make_member(CREATOR_ID))
        filter_choice = SimpleNamespace(value="yes")  # 沒人 RSVP yes

        await cog._ping_impl(interaction, event_id, filter_choice)

        channel.send.assert_not_awaited()
        args, _ = interaction.response.send_message.call_args
        assert "沒有" in args[0]

    async def test_non_manager_is_denied(self, db) -> None:
        event_id = await _create_event(db, user_ids=[OTHER_USER_ID])
        await _restrict_to_organizer_role(db)
        channel = _make_channel()
        cog = Events(_make_bot(channel))
        interaction = _make_interaction(user=_make_member(OTHER_USER_ID))

        await cog._ping_impl(interaction, event_id, None)

        channel.send.assert_not_awaited()
