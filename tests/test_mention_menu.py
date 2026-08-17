"""`@提及`快速選單（M9）測試——`MentionMenu.on_message` 的觸發條件，以及
`MentionMenuView` 四顆按鈕各自送出正確的 Modal／清單。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import discord

from src.bot.cogs.mention_menu import MentionMenu, MentionMenuView
from src.bot.modals_quick import QuickEventModal, QuickFf14Modal, QuickPollModal
from src.db import repo
from src.lib.clock import now_ms
from src.lib.ids import new_id

GUILD_ID = 111111111111111111
CHANNEL_ID = 222222222222222222

BOT_USER_ID = 999999999999999999


_BOT_USER = MagicMock(spec=discord.ClientUser)
_BOT_USER.id = BOT_USER_ID


def _make_bot(*, mentioned: bool) -> MagicMock:
    bot = MagicMock()
    bot.user = _BOT_USER
    return bot


def _make_message(*, is_bot: bool = False, has_guild: bool = True, mentions_bot: bool) -> MagicMock:
    message = MagicMock()
    message.author.bot = is_bot
    message.guild = MagicMock(id=GUILD_ID) if has_guild else None
    # 同一個 MagicMock 實例——bot.user 跟 message.mentions 裡的元素必須是
    # *同一個物件*，`in` 判斷才會成立（MagicMock 預設用 identity 比較）。
    message.mentions = [_BOT_USER] if mentions_bot else []
    message.channel = MagicMock(id=CHANNEL_ID)
    message.reply = AsyncMock()
    return message


class TestOnMessageTrigger:
    async def test_replies_with_menu_when_bot_is_mentioned(self, db) -> None:
        bot = _make_bot(mentioned=True)
        cog = MentionMenu(bot)
        message = _make_message(mentions_bot=True)
        sent = MagicMock()
        message.reply.return_value = sent

        await cog.on_message(message)

        message.reply.assert_awaited_once()
        _, kwargs = message.reply.call_args
        assert isinstance(kwargs["view"], MentionMenuView)
        assert kwargs["mention_author"] is False

    async def test_ignores_message_without_mention(self, db) -> None:
        bot = _make_bot(mentioned=False)
        cog = MentionMenu(bot)
        message = _make_message(mentions_bot=False)

        await cog.on_message(message)

        message.reply.assert_not_awaited()

    async def test_ignores_bot_authored_messages(self, db) -> None:
        bot = _make_bot(mentioned=True)
        cog = MentionMenu(bot)
        message = _make_message(is_bot=True, mentions_bot=True)

        await cog.on_message(message)

        message.reply.assert_not_awaited()

    async def test_ignores_dm_messages(self, db) -> None:
        bot = _make_bot(mentioned=True)
        cog = MentionMenu(bot)
        message = _make_message(has_guild=False, mentions_bot=True)

        await cog.on_message(message)

        message.reply.assert_not_awaited()

    async def test_reply_failure_is_swallowed(self, db) -> None:
        bot = _make_bot(mentioned=True)
        cog = MentionMenu(bot)
        message = _make_message(mentions_bot=True)
        message.reply = AsyncMock(
            side_effect=discord.HTTPException(MagicMock(status=403), "forbidden")
        )

        await cog.on_message(message)  # 不應拋例外


def _make_button_interaction() -> MagicMock:
    interaction = MagicMock()
    interaction.guild_id = GUILD_ID
    interaction.response = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=False)
    return interaction


class TestMentionMenuViewButtons:
    async def test_create_event_opens_quick_event_modal(self, db) -> None:
        view = MentionMenuView()
        interaction = _make_button_interaction()

        await view.create_event.callback(interaction)

        interaction.response.send_modal.assert_awaited_once()
        modal = interaction.response.send_modal.call_args[0][0]
        assert isinstance(modal, QuickEventModal)

    async def test_ff14_button_opens_quick_ff14_modal(self, db) -> None:
        view = MentionMenuView()
        interaction = _make_button_interaction()

        await view.create_ff14_recruit.callback(interaction)

        interaction.response.send_modal.assert_awaited_once()
        modal = interaction.response.send_modal.call_args[0][0]
        assert isinstance(modal, QuickFf14Modal)

    async def test_poll_button_opens_quick_poll_modal(self, db) -> None:
        view = MentionMenuView()
        interaction = _make_button_interaction()

        await view.create_poll.callback(interaction)

        interaction.response.send_modal.assert_awaited_once()
        modal = interaction.response.send_modal.call_args[0][0]
        assert isinstance(modal, QuickPollModal)

    async def test_show_week_button_sends_ephemeral_event_list(self, db) -> None:
        event_id = new_id()
        starts_at = now_ms() + 3_600_000
        await repo.create_event(
            event_id=event_id,
            guild_id=GUILD_ID,
            channel_id=CHANNEL_ID,
            creator_id="u1",
            title="週五團練",
            starts_at_utc=starts_at,
            tz="Asia/Taipei",
        )

        view = MentionMenuView()
        interaction = _make_button_interaction()
        interaction.followup = AsyncMock()

        await view.show_week.callback(interaction)

        interaction.response.defer.assert_awaited_once_with(ephemeral=True)
        interaction.followup.send.assert_awaited_once()
        _, kwargs = interaction.followup.send.call_args
        assert "週五團練" in kwargs["embed"].description
        assert kwargs["ephemeral"] is True

    async def test_show_week_button_ignored_outside_guild(self, db) -> None:
        view = MentionMenuView()
        interaction = _make_button_interaction()
        interaction.guild_id = None
        interaction.followup = AsyncMock()

        await view.show_week.callback(interaction)

        interaction.response.defer.assert_not_awaited()
        interaction.followup.send.assert_not_awaited()


class TestMentionMenuViewTimeout:
    async def test_on_timeout_disables_children_and_edits_message(self, db) -> None:
        view = MentionMenuView()
        message = MagicMock()
        message.edit = AsyncMock()
        view.message = message

        await view.on_timeout()

        for child in view.children:
            assert child.disabled is True
        message.edit.assert_awaited_once()

    async def test_on_timeout_swallows_edit_failure(self, db) -> None:
        view = MentionMenuView()
        message = MagicMock()
        message.edit = AsyncMock(
            side_effect=discord.HTTPException(MagicMock(status=404), "not found")
        )
        view.message = message

        await view.on_timeout()  # 不應拋例外
