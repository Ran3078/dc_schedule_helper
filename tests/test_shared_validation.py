"""`cogs/_shared.validate_event_draft` 測試（M9）：`/event create`、
`/ff14_recruit`、@提及選單的快速建立三個入口共用的驗證規則——權限、標題、
時間、時長。這裡直接測共用函式本身；`Events._create_impl`／
`Ff14._recruit_impl` 呼叫這個函式後的既有測試檔案（`test_event_management_cog.py`
`test_ff14_recruit.py`）原樣重跑就是這次重構最重要的回歸保證。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import discord

from src.bot.cogs._shared import DraftValidationError, validate_event_draft
from src.bot.modals import PendingEvent
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


def _make_bot() -> MagicMock:
    bot = MagicMock()
    bot.settings = None  # 見 guild_tz 的 self-heal 分支說明
    return bot


def _make_interaction(*, user: MagicMock | None = None) -> MagicMock:
    interaction = MagicMock()
    interaction.guild_id = GUILD_ID
    interaction.channel_id = CHANNEL_ID
    interaction.user = user or _make_member(CREATOR_ID)
    return interaction


class TestValidateEventDraft:
    async def test_valid_input_returns_pending_event(self, db) -> None:
        bot = _make_bot()
        interaction = _make_interaction()

        draft = await validate_event_draft(
            bot, interaction, title="零式團練", time="8/1 20:00", location="語音頻道",
            duration="2h",
        )

        assert isinstance(draft, PendingEvent)
        assert draft.title == "零式團練"
        assert draft.location == "語音頻道"
        assert draft.starts_at_utc is not None
        assert draft.duration_minutes == 120
        assert draft.channel_id == CHANNEL_ID  # 佔位值，呼叫端自己覆寫

    async def test_omitted_time_leaves_starts_at_utc_none(self, db) -> None:
        bot = _make_bot()
        interaction = _make_interaction()

        draft = await validate_event_draft(
            bot, interaction, title="零式團練", time=None, location=None, duration=None
        )

        assert isinstance(draft, PendingEvent)
        assert draft.starts_at_utc is None

    async def test_blank_location_becomes_none(self, db) -> None:
        bot = _make_bot()
        interaction = _make_interaction()

        draft = await validate_event_draft(
            bot, interaction, title="零式團練", time=None, location="   ", duration=None
        )

        assert isinstance(draft, PendingEvent)
        assert draft.location is None

    async def test_empty_title_is_rejected(self, db) -> None:
        bot = _make_bot()
        interaction = _make_interaction()

        result = await validate_event_draft(
            bot, interaction, title="   ", time=None, location=None, duration=None
        )

        assert isinstance(result, DraftValidationError)
        assert "不能是空的" in result.message

    async def test_title_too_long_is_rejected(self, db) -> None:
        bot = _make_bot()
        interaction = _make_interaction()

        result = await validate_event_draft(
            bot, interaction, title="太" * 201, time=None, location=None, duration=None
        )

        assert isinstance(result, DraftValidationError)
        assert "太長了" in result.message

    async def test_invalid_time_is_rejected(self, db) -> None:
        bot = _make_bot()
        interaction = _make_interaction()

        result = await validate_event_draft(
            bot, interaction, title="零式團練", time="不是時間", location=None, duration=None
        )

        assert isinstance(result, DraftValidationError)

    async def test_invalid_duration_is_rejected(self, db) -> None:
        bot = _make_bot()
        interaction = _make_interaction()

        result = await validate_event_draft(
            bot, interaction, title="零式團練", time=None, location=None, duration="不是時長"
        )

        assert isinstance(result, DraftValidationError)

    async def test_zero_duration_is_rejected(self, db) -> None:
        bot = _make_bot()
        interaction = _make_interaction()

        result = await validate_event_draft(
            bot, interaction, title="零式團練", time=None, location=None, duration="0m"
        )

        assert isinstance(result, DraftValidationError)
        assert "大於 0" in result.message

    async def test_organizer_role_required_when_configured(self, db) -> None:
        await _restrict_to_organizer_role(db)
        bot = _make_bot()
        interaction = _make_interaction(user=_make_member(OTHER_USER_ID))

        result = await validate_event_draft(
            bot, interaction, title="零式團練", time=None, location=None, duration=None
        )

        assert isinstance(result, DraftValidationError)
        assert "特定身分組" in result.message

    async def test_organizer_role_member_is_allowed(self, db) -> None:
        await _restrict_to_organizer_role(db)
        bot = _make_bot()
        interaction = _make_interaction(
            user=_make_member(OTHER_USER_ID, role_ids=(ORGANIZER_ROLE_ID,))
        )

        result = await validate_event_draft(
            bot, interaction, title="零式團練", time=None, location=None, duration=None
        )

        assert isinstance(result, PendingEvent)

    async def test_non_member_user_is_rejected(self, db) -> None:
        """理論上不會發生（guild_only() 保證互動來自伺服器），但防禦性地
        確保 interaction.user 不是 discord.Member 時不會誤判成有權限。"""
        bot = _make_bot()
        interaction = _make_interaction(user=MagicMock(id=CREATOR_ID))  # 沒有 spec=Member

        result = await validate_event_draft(
            bot, interaction, title="零式團練", time=None, location=None, duration=None
        )

        assert isinstance(result, DraftValidationError)

    async def test_uses_user_timezone_override(self, db) -> None:
        """個人時區覆寫優先於伺服器預設——見 resolve_user_tz。"""
        await repo.set_user_tz(CREATOR_ID, "America/Los_Angeles")
        bot = _make_bot()
        interaction = _make_interaction()

        draft = await validate_event_draft(
            bot, interaction, title="零式團練", time="8/1 20:00", location=None, duration=None
        )

        assert isinstance(draft, PendingEvent)
        assert draft.tz == "America/Los_Angeles"
