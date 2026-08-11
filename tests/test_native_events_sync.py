"""bot/native_events.py 的 sync_create 測試：呼叫 Discord API 建立原生活動的
參數組裝，跟同步失敗時的容錯行為。用假的 discord.Guild，不需要真的連線。
"""

from __future__ import annotations

from datetime import UTC
from unittest.mock import AsyncMock, MagicMock

import discord

from src.bot.native_events import sync_cancel, sync_create, sync_edit
from src.domain.native_events import DEFAULT_LOCATION_TEXT

NOW = 1_785_700_800_000  # 任意固定的 epoch ms，方便斷言


def _event(**overrides) -> dict:
    defaults = dict(
        id="evt0000001",
        title="週五團練",
        starts_at_utc=NOW,
        ends_at_utc=None,
        location=None,
        description=None,
    )
    return {**defaults, **overrides}


def _make_guild(scheduled_event_id: int = 555) -> MagicMock:
    guild = MagicMock()
    fake_scheduled = MagicMock()
    fake_scheduled.id = scheduled_event_id
    guild.create_scheduled_event = AsyncMock(return_value=fake_scheduled)
    return guild


class TestSyncCreate:
    async def test_returns_scheduled_event_id_on_success(self) -> None:
        guild = _make_guild(scheduled_event_id=777)
        result = await sync_create(guild, _event())
        assert result == 777

    async def test_uses_external_entity_type_and_guild_only_privacy(self) -> None:
        guild = _make_guild()
        await sync_create(guild, _event())

        _, kwargs = guild.create_scheduled_event.call_args
        assert kwargs["entity_type"] is discord.EntityType.external
        assert kwargs["privacy_level"] is discord.PrivacyLevel.guild_only

    async def test_truncates_title_to_100_chars(self) -> None:
        guild = _make_guild()
        long_title = "字" * 150
        await sync_create(guild, _event(title=long_title))

        _, kwargs = guild.create_scheduled_event.call_args
        assert kwargs["name"] == long_title[:100]
        assert len(kwargs["name"]) == 100

    async def test_uses_given_location_when_present(self) -> None:
        guild = _make_guild()
        await sync_create(guild, _event(location="語音頻道 #團練室"))

        _, kwargs = guild.create_scheduled_event.call_args
        assert kwargs["location"] == "語音頻道 #團練室"

    async def test_falls_back_to_default_location_when_missing(self) -> None:
        guild = _make_guild()
        await sync_create(guild, _event(location=None))

        _, kwargs = guild.create_scheduled_event.call_args
        assert kwargs["location"] == DEFAULT_LOCATION_TEXT

    async def test_end_time_defaults_to_two_hours_after_start(self) -> None:
        guild = _make_guild()
        await sync_create(guild, _event(ends_at_utc=None))

        _, kwargs = guild.create_scheduled_event.call_args
        delta = kwargs["end_time"] - kwargs["start_time"]
        assert delta.total_seconds() == 2 * 3600

    async def test_start_and_end_time_are_timezone_aware_utc(self) -> None:
        guild = _make_guild()
        await sync_create(guild, _event())

        _, kwargs = guild.create_scheduled_event.call_args
        assert kwargs["start_time"].tzinfo == UTC
        assert kwargs["end_time"].tzinfo == UTC

    async def test_omits_description_kwarg_when_not_set(self) -> None:
        guild = _make_guild()
        await sync_create(guild, _event(description=None))

        _, kwargs = guild.create_scheduled_event.call_args
        assert "description" not in kwargs

    async def test_includes_description_when_set(self) -> None:
        guild = _make_guild()
        await sync_create(guild, _event(description="打完第三章"))

        _, kwargs = guild.create_scheduled_event.call_args
        assert kwargs["description"] == "打完第三章"

    async def test_returns_none_on_http_exception(self) -> None:
        guild = _make_guild()
        guild.create_scheduled_event = AsyncMock(
            side_effect=discord.HTTPException(MagicMock(status=403), "forbidden")
        )

        result = await sync_create(guild, _event())  # 不應拋例外

        assert result is None


def _make_scheduled_event() -> MagicMock:
    scheduled = MagicMock()
    scheduled.edit = AsyncMock(return_value=scheduled)
    scheduled.cancel = AsyncMock(return_value=scheduled)
    return scheduled


def _make_guild_with_cached_event(scheduled: MagicMock | None) -> MagicMock:
    """`_resolve_scheduled_event` 先吃 `get_scheduled_event`（同步呼叫）——
    這裡模擬「快取命中」的情境，`fetch_scheduled_event` 不會被呼叫到。"""
    guild = MagicMock()
    guild.get_scheduled_event = MagicMock(return_value=scheduled)
    guild.fetch_scheduled_event = AsyncMock(return_value=scheduled)
    return guild


class TestSyncEdit:
    async def test_edits_with_updated_fields(self) -> None:
        scheduled = _make_scheduled_event()
        guild = _make_guild_with_cached_event(scheduled)

        result = await sync_edit(guild, 555, _event(title="改過的標題", location="新地點"))

        assert result is True
        scheduled.edit.assert_awaited_once()
        _, kwargs = scheduled.edit.call_args
        assert kwargs["name"] == "改過的標題"
        assert kwargs["location"] == "新地點"

    async def test_falls_back_to_default_location_when_missing(self) -> None:
        scheduled = _make_scheduled_event()
        guild = _make_guild_with_cached_event(scheduled)

        await sync_edit(guild, 555, _event(location=None))

        _, kwargs = scheduled.edit.call_args
        assert kwargs["location"] == DEFAULT_LOCATION_TEXT

    async def test_returns_false_when_event_not_found(self) -> None:
        guild = _make_guild_with_cached_event(None)

        result = await sync_edit(guild, 555, _event())  # 不應拋例外

        assert result is False

    async def test_falls_back_to_fetch_when_not_cached(self) -> None:
        scheduled = _make_scheduled_event()
        guild = MagicMock()
        guild.get_scheduled_event = MagicMock(return_value=None)
        guild.fetch_scheduled_event = AsyncMock(return_value=scheduled)

        result = await sync_edit(guild, 555, _event())

        assert result is True
        guild.fetch_scheduled_event.assert_awaited_once_with(555)

    async def test_returns_false_on_http_exception(self) -> None:
        scheduled = _make_scheduled_event()
        scheduled.edit = AsyncMock(
            side_effect=discord.HTTPException(MagicMock(status=403), "forbidden")
        )
        guild = _make_guild_with_cached_event(scheduled)

        result = await sync_edit(guild, 555, _event())  # 不應拋例外

        assert result is False


class TestSyncCancel:
    async def test_cancels_the_scheduled_event(self) -> None:
        scheduled = _make_scheduled_event()
        guild = _make_guild_with_cached_event(scheduled)

        result = await sync_cancel(guild, 555)

        assert result is True
        scheduled.cancel.assert_awaited_once()

    async def test_returns_false_when_event_not_found(self) -> None:
        guild = _make_guild_with_cached_event(None)

        result = await sync_cancel(guild, 555)  # 不應拋例外——可能已經被手動刪除

        assert result is False

    async def test_returns_false_on_http_exception(self) -> None:
        scheduled = _make_scheduled_event()
        scheduled.cancel = AsyncMock(
            side_effect=discord.HTTPException(MagicMock(status=403), "forbidden")
        )
        guild = _make_guild_with_cached_event(scheduled)

        result = await sync_cancel(guild, 555)  # 不應拋例外

        assert result is False
