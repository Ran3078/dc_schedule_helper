"""按鈕觸發的快速建立 Modal（M9）測試——`QuickEventModal`／`QuickFf14Modal`／
`QuickPollModal`。比照 `test_ff14_recruit.py` 已經用過的測法：直接建構
Modal、手動寫入 `TextInput._value`，呼叫 `on_submit`。

驗證規則本身（權限／標題／時長）已經在 `test_shared_validation.py`
測過 `validate_event_draft`，這裡只測「Modal 呼叫驗證後怎麼分流」。

`QuickEventModal`／`QuickFf14Modal` 都沒有時間欄位——固定走
`DateTimePickerView`，`starts_at_utc` 已知的分支（走 `InviteePickerView`）
只留在 `QuickFf14PositionPickerView.on_pick` 這種被共用的下游元件上，
直接建構 `PendingEvent` 測，不透過 Modal 走。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import discord

from src.bot.modals_quick import (
    QuickEventModal,
    QuickFf14Modal,
    QuickFf14PositionPickerView,
    QuickPollModal,
)
from src.bot.views_datetime import DateTimePickerView
from src.bot.views_invitees import InviteePickerView
from src.db import repo
from src.lib.clock import now_ms

GUILD_ID = 111111111111111111
CHANNEL_ID = 222222222222222222
CREATOR_ID = 333333333333333333

NOW = now_ms()
HOUR = 3_600_000


def _make_member(user_id: int) -> MagicMock:
    member = MagicMock(spec=discord.Member)
    member.id = user_id
    member.roles = []
    return member


def _make_channel() -> MagicMock:
    channel = MagicMock()
    channel.fetch_message = AsyncMock(return_value=AsyncMock())
    return channel


def _make_interaction() -> MagicMock:
    interaction = MagicMock()
    interaction.guild_id = GUILD_ID
    interaction.channel_id = CHANNEL_ID
    interaction.user = _make_member(CREATOR_ID)
    interaction.response = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=False)

    channel = _make_channel()
    interaction.channel = channel
    interaction.guild = MagicMock(id=GUILD_ID)

    client = MagicMock()
    client.get_channel.return_value = channel
    client.fetch_channel = AsyncMock(return_value=channel)
    client.settings = None
    interaction.client = client

    fake_message = MagicMock()
    fake_message.id = 555
    interaction.original_response = AsyncMock(return_value=fake_message)
    return interaction


class TestQuickEventModalSubmit:
    async def test_empty_title_is_rejected(self, db) -> None:
        modal = QuickEventModal()
        modal.title_input._value = "   "
        interaction = _make_interaction()

        await modal.on_submit(interaction)

        args, kwargs = interaction.response.send_message.call_args
        assert "不能是空的" in args[0]
        assert kwargs["ephemeral"] is True

    async def test_unknown_time_routes_to_datetime_picker(self, db) -> None:
        modal = QuickEventModal()
        modal.title_input._value = "揪團練習"
        interaction = _make_interaction()

        await modal.on_submit(interaction)

        _, kwargs = interaction.response.send_message.call_args
        assert isinstance(kwargs["view"], DateTimePickerView)

    async def test_description_is_captured(self, db) -> None:
        modal = QuickEventModal()
        modal.title_input._value = "揪團練習"
        modal.description_input._value = "帶藥水"
        interaction = _make_interaction()

        await modal.on_submit(interaction)

        _, kwargs = interaction.response.send_message.call_args
        assert isinstance(kwargs["view"], DateTimePickerView)
        assert kwargs["view"].description == "帶藥水"

    async def test_blank_description_becomes_none(self, db) -> None:
        modal = QuickEventModal()
        modal.title_input._value = "揪團練習"
        interaction = _make_interaction()

        await modal.on_submit(interaction)

        _, kwargs = interaction.response.send_message.call_args
        assert kwargs["view"].description is None

    async def test_organizer_role_required_when_configured(self, db) -> None:
        await repo.ensure_guild(GUILD_ID, "Asia/Taipei")
        await repo.update_guild_settings(GUILD_ID, organizer_role_id="999999999999999999")
        modal = QuickEventModal()
        modal.title_input._value = "揪團練習"
        interaction = _make_interaction()

        await modal.on_submit(interaction)

        args, _ = interaction.response.send_message.call_args
        assert "特定身分組" in args[0]
        interaction.response.send_message.assert_awaited_once()


class TestQuickFf14ModalSubmit:
    async def test_valid_input_opens_position_picker(self, db) -> None:
        modal = QuickFf14Modal()
        modal.title_input._value = "零式團練"
        interaction = _make_interaction()

        await modal.on_submit(interaction)

        interaction.response.send_message.assert_awaited_once()
        _, kwargs = interaction.response.send_message.call_args
        assert isinstance(kwargs["view"], QuickFf14PositionPickerView)

    async def test_empty_title_is_rejected(self, db) -> None:
        modal = QuickFf14Modal()
        modal.title_input._value = "   "
        interaction = _make_interaction()

        await modal.on_submit(interaction)

        args, _ = interaction.response.send_message.call_args
        assert "不能是空的" in args[0]


def _make_picker_interaction() -> MagicMock:
    interaction = _make_interaction()
    interaction.response.edit_message = AsyncMock()
    return interaction


class TestQuickFf14PositionPickerOnPick:
    async def test_unknown_time_routes_to_datetime_picker_with_positions(self, db) -> None:
        from src.bot.modals import PendingEvent

        pending = PendingEvent(
            guild_id=GUILD_ID,
            channel_id=CHANNEL_ID,
            creator_id=CREATOR_ID,
            title="零式團練",
            tz="Asia/Taipei",
            location=None,
            duration_minutes=None,
            starts_at_utc=None,
        )
        picker = QuickFf14PositionPickerView(pending=pending, guild_settings=None)
        interaction = _make_picker_interaction()

        await picker.on_pick(interaction, ["D1", "MT"])

        _, kwargs = interaction.response.edit_message.call_args
        assert isinstance(kwargs["view"], DateTimePickerView)
        assert kwargs["view"].positions == ["MT", "D1"]

    async def test_known_time_routes_to_invitee_picker_with_positions(self, db) -> None:
        from src.bot.modals import PendingEvent

        pending = PendingEvent(
            guild_id=GUILD_ID,
            channel_id=CHANNEL_ID,
            creator_id=CREATOR_ID,
            title="零式團練",
            tz="Asia/Taipei",
            location=None,
            duration_minutes=None,
            starts_at_utc=NOW + HOUR,
        )
        picker = QuickFf14PositionPickerView(pending=pending, guild_settings=None)
        interaction = _make_picker_interaction()

        await picker.on_pick(interaction, ["D3"])

        _, kwargs = interaction.response.edit_message.call_args
        assert isinstance(kwargs["view"], InviteePickerView)
        assert kwargs["view"].positions == ["D3"]


class TestQuickPollModalSubmit:
    async def test_valid_input_creates_and_publishes_poll(self, db) -> None:
        modal = QuickPollModal()
        modal.question_input._value = "晚上吃什麼"
        modal.options_input._value = "火鍋\n燒肉\n拉麵"
        interaction = _make_interaction()

        await modal.on_submit(interaction)

        interaction.response.send_message.assert_awaited_once()
        _, kwargs = interaction.response.send_message.call_args
        assert kwargs["embed"].title is not None or kwargs["embed"] is not None

    async def test_empty_question_is_rejected(self, db) -> None:
        modal = QuickPollModal()
        modal.question_input._value = "   "
        modal.options_input._value = "火鍋\n燒肉"
        interaction = _make_interaction()

        await modal.on_submit(interaction)

        args, kwargs = interaction.response.send_message.call_args
        assert "不能是空的" in args[0]
        assert kwargs["ephemeral"] is True

    async def test_too_few_options_is_rejected(self, db) -> None:
        modal = QuickPollModal()
        modal.question_input._value = "晚上吃什麼"
        modal.options_input._value = "火鍋"
        interaction = _make_interaction()

        await modal.on_submit(interaction)

        args, _ = interaction.response.send_message.call_args
        assert "選項數量" in args[0]

    async def test_no_permission_gate_unlike_event_drafts(self, db) -> None:
        """跟 QuickEventModal／QuickFf14Modal 不同——投票沒有 organizer 限制,
        比照 /poll create 既有行為。"""
        await repo.ensure_guild(GUILD_ID, "Asia/Taipei")
        await repo.update_guild_settings(GUILD_ID, organizer_role_id="999999999999999999")
        modal = QuickPollModal()
        modal.question_input._value = "晚上吃什麼"
        modal.options_input._value = "火鍋\n燒肉"
        interaction = _make_interaction()

        await modal.on_submit(interaction)

        interaction.response.send_message.assert_awaited_once()
        args, kwargs = interaction.response.send_message.call_args
        assert not args  # 沒有走「不能是空的」/「特定身分組」那條錯誤訊息分支
        assert "embed" in kwargs
