"""`/ff14_recruit`（M8）測試：`_recruit_impl` 的權限/驗證，跟
`Ff14RecruitModal.on_submit` 依 `starts_at_utc` 是否已知分流到
`DateTimePickerView`／`InviteePickerView`、且職位選擇正確傳下去。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import discord

from src.bot.cogs.ff14 import Ff14
from src.bot.modals import PendingEvent
from src.bot.modals_ff14 import Ff14RecruitModal
from src.bot.views_datetime import DateTimePickerView
from src.bot.views_invitees import InviteePickerView
from src.db import repo
from src.lib.clock import now_ms

GUILD_ID = 111111111111111111
CHANNEL_ID = 222222222222222222
CREATOR_ID = 333333333333333333
OTHER_USER_ID = 444444444444444444
ORGANIZER_ROLE_ID = 555555555555555555

NOW = now_ms()
HOUR = 3_600_000


async def _restrict_to_organizer_role(db) -> None:
    await repo.ensure_guild(GUILD_ID, "Asia/Taipei")
    await repo.update_guild_settings(GUILD_ID, organizer_role_id=str(ORGANIZER_ROLE_ID))


def _make_member(user_id: int, role_ids: tuple[int, ...] = ()) -> MagicMock:
    member = MagicMock(spec=discord.Member)
    member.id = user_id
    member.roles = [MagicMock(id=rid) for rid in role_ids]
    return member


def _make_channel() -> MagicMock:
    channel = MagicMock()
    channel.fetch_message = AsyncMock(return_value=AsyncMock())
    return channel


def _make_bot(channel: MagicMock | None = None) -> MagicMock:
    bot = MagicMock()
    bot.get_channel.return_value = channel or _make_channel()
    bot.fetch_channel = AsyncMock(return_value=channel or _make_channel())
    bot.settings = None
    return bot


def _make_interaction(*, user: MagicMock | None = None) -> MagicMock:
    interaction = MagicMock()
    interaction.guild_id = GUILD_ID
    interaction.user = user or _make_member(CREATOR_ID)
    interaction.response = AsyncMock()
    interaction.channel_id = CHANNEL_ID
    interaction.channel = MagicMock(id=CHANNEL_ID)
    interaction.guild = MagicMock(id=GUILD_ID)
    interaction.guild.get_channel.return_value = interaction.channel
    return interaction


class TestRecruitPermission:
    async def test_organizer_role_required_when_configured(self, db) -> None:
        await _restrict_to_organizer_role(db)
        cog = Ff14(_make_bot())
        interaction = _make_interaction(user=_make_member(OTHER_USER_ID))

        await cog._recruit_impl(interaction, "零式團練", None, None, None)

        interaction.response.send_modal.assert_not_awaited()
        args, kwargs = interaction.response.send_message.call_args
        assert "特定身分組" in args[0]
        assert kwargs["ephemeral"] is True

    async def test_organizer_role_member_is_allowed(self, db) -> None:
        await _restrict_to_organizer_role(db)
        cog = Ff14(_make_bot())
        interaction = _make_interaction(
            user=_make_member(OTHER_USER_ID, role_ids=(ORGANIZER_ROLE_ID,))
        )

        await cog._recruit_impl(interaction, "零式團練", None, None, None)

        interaction.response.send_modal.assert_awaited_once()

    async def test_empty_title_is_rejected(self, db) -> None:
        cog = Ff14(_make_bot())
        interaction = _make_interaction()

        await cog._recruit_impl(interaction, "   ", None, None, None)

        args, _ = interaction.response.send_message.call_args
        assert "不能是空的" in args[0]
        interaction.response.send_modal.assert_not_awaited()

    async def test_title_too_long_is_rejected(self, db) -> None:
        cog = Ff14(_make_bot())
        interaction = _make_interaction()

        await cog._recruit_impl(interaction, "太" * 201, None, None, None)

        args, _ = interaction.response.send_message.call_args
        assert "太長了" in args[0]

    async def test_invalid_time_is_rejected(self, db) -> None:
        cog = Ff14(_make_bot())
        interaction = _make_interaction()

        await cog._recruit_impl(interaction, "零式團練", "不是時間", None, None)

        interaction.response.send_modal.assert_not_awaited()

    async def test_invalid_duration_is_rejected(self, db) -> None:
        cog = Ff14(_make_bot())
        interaction = _make_interaction()

        await cog._recruit_impl(interaction, "零式團練", None, None, "不是時長")

        interaction.response.send_modal.assert_not_awaited()

    async def test_valid_input_opens_ff14_recruit_modal(self, db) -> None:
        cog = Ff14(_make_bot())
        interaction = _make_interaction()

        await cog._recruit_impl(interaction, "零式團練", "8/1 20:00", "語音頻道", "2h")

        interaction.response.send_modal.assert_awaited_once()
        modal = interaction.response.send_modal.call_args[0][0]
        assert isinstance(modal, Ff14RecruitModal)
        assert modal.pending.title == "零式團練"
        assert modal.pending.location == "語音頻道"
        assert modal.pending.starts_at_utc is not None

    async def test_omitted_time_leaves_starts_at_utc_none(self, db) -> None:
        cog = Ff14(_make_bot())
        interaction = _make_interaction()

        await cog._recruit_impl(interaction, "零式團練", None, None, None)

        modal = interaction.response.send_modal.call_args[0][0]
        assert modal.pending.starts_at_utc is None


def _make_pending(**overrides) -> PendingEvent:
    defaults = dict(
        guild_id=GUILD_ID,
        channel_id=CHANNEL_ID,
        creator_id=CREATOR_ID,
        title="零式團練",
        tz="Asia/Taipei",
        location=None,
        duration_minutes=None,
        starts_at_utc=None,
    )
    return PendingEvent(**{**defaults, **overrides})


def _make_modal_interaction() -> MagicMock:
    interaction = MagicMock()
    interaction.guild_id = GUILD_ID
    interaction.response = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=False)

    fake_message = MagicMock()
    fake_message.id = 555
    interaction.original_response = AsyncMock(return_value=fake_message)
    return interaction


class TestFf14RecruitModalSubmit:
    async def test_unknown_time_routes_to_datetime_picker_with_positions(self, db) -> None:
        modal = Ff14RecruitModal(_make_pending(starts_at_utc=None), event_id="e1")
        modal.position_select._values = ["D1", "MT"]
        interaction = _make_modal_interaction()

        await modal.on_submit(interaction)

        _, kwargs = interaction.response.send_message.call_args
        assert isinstance(kwargs["view"], DateTimePickerView)
        assert kwargs["view"].positions == ["MT", "D1"]  # 已排序成 POSITIONS 的順序

    async def test_known_time_routes_to_invitee_picker_with_positions(self, db) -> None:
        modal = Ff14RecruitModal(
            _make_pending(starts_at_utc=NOW + HOUR), event_id="e1"
        )
        modal.position_select._values = ["D3"]
        interaction = _make_modal_interaction()

        await modal.on_submit(interaction)

        _, kwargs = interaction.response.send_message.call_args
        assert isinstance(kwargs["view"], InviteePickerView)
        assert kwargs["view"].positions == ["D3"]

    async def test_description_is_captured(self, db) -> None:
        modal = Ff14RecruitModal(
            _make_pending(starts_at_utc=NOW + HOUR), event_id="e1"
        )
        modal.description_input._value = "打完拿獎勵"
        modal.position_select._values = ["MT"]
        interaction = _make_modal_interaction()

        await modal.on_submit(interaction)

        _, kwargs = interaction.response.send_message.call_args
        assert kwargs["view"].description == "打完拿獎勵"

    async def test_empty_description_becomes_none(self, db) -> None:
        modal = Ff14RecruitModal(
            _make_pending(starts_at_utc=NOW + HOUR), event_id="e1"
        )
        modal.position_select._values = ["MT"]
        interaction = _make_modal_interaction()

        await modal.on_submit(interaction)

        _, kwargs = interaction.response.send_message.call_args
        assert kwargs["view"].description is None
