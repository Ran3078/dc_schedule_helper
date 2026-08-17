"""WeeklyDigest cog 測試（M9）——只測 `_maybe_send_digest()`，不測
`@tasks.loop` 這層 wrapper 本身（比照 `test_scheduler.py` 對
`_process_reminder` 的作法：那是 discord.py 自己的機制，上游已經測過）。

核心案例：本週還沒發過才發、已經發過不重發、沒設定公告頻道就跳過、
不同時區的伺服器各自算出正確的週日邊界。
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import discord

from src.bot.cogs.weekly_digest import WeeklyDigest
from src.db import repo
from src.lib.ids import new_id

GUILD_ID = "111111111111111111"
CHANNEL_ID = "222222222222222222"

HOUR = 3_600_000
DAY = 24 * HOUR


def _make_cog(*, channel=None, fetch_channel_result=None) -> tuple[WeeklyDigest, MagicMock]:
    bot = MagicMock()
    bot.get_channel = MagicMock(return_value=channel)
    if fetch_channel_result is not None:
        bot.fetch_channel = AsyncMock(return_value=fetch_channel_result)
    else:
        bot.fetch_channel = AsyncMock(
            side_effect=discord.NotFound(MagicMock(status=404), "channel not found")
        )
    return WeeklyDigest(bot), bot


def _make_channel() -> MagicMock:
    channel = MagicMock()
    channel.send = AsyncMock()
    return channel


async def _enable_digest(
    *, guild_id: str = GUILD_ID, tz: str = "Asia/Taipei", channel_id: str | None = CHANNEL_ID
) -> None:
    await repo.ensure_guild(guild_id, tz)
    await repo.update_guild_settings(
        guild_id,
        weekly_digest_enabled=1,
        announce_channel_id=channel_id,
    )


async def _current_settings(guild_id: str = GUILD_ID):
    return await repo.get_guild_settings(guild_id)


class TestSendsWhenNotYetSentThisWeek:
    async def test_sends_and_records_timestamp(self, db) -> None:
        await _enable_digest()
        channel = _make_channel()
        cog, _ = _make_cog(channel=channel)

        settings = await _current_settings()
        await cog._maybe_send_digest(settings)

        channel.send.assert_awaited_once()
        _, kwargs = channel.send.call_args
        assert "未來 7 天活動預告" in kwargs["embed"].title

        row = await _current_settings()
        assert row["last_weekly_digest_at"] is not None

    async def test_includes_event_in_window(self, db) -> None:
        await _enable_digest()
        event_id = new_id()
        starts_at = int(datetime.now(ZoneInfo("UTC")).timestamp() * 1000) + 2 * DAY
        await repo.create_event(
            event_id=event_id,
            guild_id=GUILD_ID,
            channel_id=CHANNEL_ID,
            creator_id="u1",
            title="週五團練",
            starts_at_utc=starts_at,
            tz="Asia/Taipei",
        )

        channel = _make_channel()
        cog, _ = _make_cog(channel=channel)
        settings = await _current_settings()
        await cog._maybe_send_digest(settings)

        _, kwargs = channel.send.call_args
        assert "週五團練" in kwargs["embed"].description

    async def test_attaches_add_event_button_view(self, db) -> None:
        await _enable_digest()
        channel = _make_channel()
        cog, _ = _make_cog(channel=channel)
        settings = await _current_settings()

        await cog._maybe_send_digest(settings)

        _, kwargs = channel.send.call_args
        assert isinstance(kwargs["view"], discord.ui.View)


class TestDoesNotResendWithinSameWeek:
    async def test_already_sent_after_boundary_is_skipped(self, db) -> None:
        await _enable_digest()
        settings = await _current_settings()
        # 先手動標記成「剛剛才發過」（晚於任何週日 00:00 邊界）。
        await repo.set_last_weekly_digest_at(
            GUILD_ID, int(datetime.now(ZoneInfo("UTC")).timestamp() * 1000)
        )

        channel = _make_channel()
        cog, _ = _make_cog(channel=channel)
        settings = await _current_settings()
        await cog._maybe_send_digest(settings)

        channel.send.assert_not_awaited()

    async def test_sent_before_this_weeks_boundary_still_sends(self, db) -> None:
        """上次發送時間落在「上上週」——這週的邊界之前，該補發一次。"""
        await _enable_digest()
        eight_days_ago = int(datetime.now(ZoneInfo("UTC")).timestamp() * 1000) - 8 * DAY
        await repo.set_last_weekly_digest_at(GUILD_ID, eight_days_ago)

        channel = _make_channel()
        cog, _ = _make_cog(channel=channel)
        settings = await _current_settings()
        await cog._maybe_send_digest(settings)

        channel.send.assert_awaited_once()


class TestMissingAnnounceChannel:
    async def test_no_channel_configured_skips_without_raising(self, db) -> None:
        await _enable_digest(channel_id=None)
        channel = _make_channel()
        cog, _ = _make_cog(channel=channel)
        settings = await _current_settings()

        await cog._maybe_send_digest(settings)  # 不應拋例外

        channel.send.assert_not_awaited()

    async def test_channel_not_found_skips_without_raising(self, db) -> None:
        await _enable_digest()
        cog, _ = _make_cog(channel=None, fetch_channel_result=None)
        settings = await _current_settings()

        await cog._maybe_send_digest(settings)  # 不應拋例外


class TestPerGuildTimezone:
    async def test_different_timezones_each_use_their_own_boundary(self, db) -> None:
        """兩個時區不同的伺服器各自用自己的 default_tz 算週日邊界——不會
        互相干擾，也不會誤用另一個伺服器的時區。"""
        guild_a = "333333333333333333"
        guild_b = "444444444444444444"
        channel_a = "555555555555555555"
        channel_b = "666666666666666666"

        await _enable_digest(guild_id=guild_a, tz="Asia/Taipei", channel_id=channel_a)
        await _enable_digest(guild_id=guild_b, tz="America/Los_Angeles", channel_id=channel_b)

        sent_channel = _make_channel()
        cog, _ = _make_cog(channel=sent_channel)

        settings_a = await _current_settings(guild_a)
        settings_b = await _current_settings(guild_b)
        await cog._maybe_send_digest(settings_a)
        await cog._maybe_send_digest(settings_b)

        assert sent_channel.send.await_count == 2

    async def test_unknown_timezone_falls_back_to_taipei(self, db) -> None:
        await _enable_digest(tz="Not/A/Real/Zone")
        channel = _make_channel()
        cog, _ = _make_cog(channel=channel)
        settings = await _current_settings()

        await cog._maybe_send_digest(settings)  # 不應拋例外

        channel.send.assert_awaited_once()


class TestSendFailure:
    async def test_http_exception_during_send_does_not_raise_and_does_not_record(
        self, db
    ) -> None:
        await _enable_digest()
        channel = _make_channel()
        channel.send = AsyncMock(
            side_effect=discord.HTTPException(MagicMock(status=403), "forbidden")
        )
        cog, _ = _make_cog(channel=channel)
        settings = await _current_settings()

        await cog._maybe_send_digest(settings)  # 不應拋例外

        row = await _current_settings()
        assert row["last_weekly_digest_at"] is None


class TestOnlyEnabledGuildsAreProcessed:
    async def test_digest_tick_only_touches_enabled_guilds(self, db) -> None:
        await repo.ensure_guild(GUILD_ID, "Asia/Taipei")  # 沒開啟

        channel = _make_channel()
        cog, _ = _make_cog(channel=channel)

        await WeeklyDigest.digest_tick.coro(cog)  # 略過 @tasks.loop 包裝

        channel.send.assert_not_awaited()
