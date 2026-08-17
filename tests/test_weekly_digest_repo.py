"""repo.py 的每週活動清單（M9）測試：`list_upcoming_events_in_window` 的
時間窗邊界、`list_guilds_with_weekly_digest_enabled` 只撈開啟的伺服器、
`set_last_weekly_digest_at`。
"""

from __future__ import annotations

from src.db import repo
from src.lib.clock import now_ms
from src.lib.ids import new_id

GUILD_A = "111111111111111111"
GUILD_B = "222222222222222222"
USER_1 = "1111"

NOW = now_ms()
HOUR = 3_600_000
DAY = 24 * HOUR


async def _create(db, *, guild_id: str = GUILD_A, starts_at_utc: int = NOW + HOUR, **kwargs) -> str:
    event_id = new_id()
    await repo.create_event(
        event_id=event_id,
        guild_id=guild_id,
        channel_id="c1",
        creator_id=USER_1,
        title="週練",
        starts_at_utc=starts_at_utc,
        tz="Asia/Taipei",
        **kwargs,
    )
    return event_id


class TestListUpcomingEventsInWindow:
    async def test_includes_events_within_window(self, db) -> None:
        event_id = await _create(db, starts_at_utc=NOW + 3 * DAY)

        rows = await repo.list_upcoming_events_in_window(GUILD_A, NOW, NOW + 7 * DAY)

        assert [r["id"] for r in rows] == [event_id]

    async def test_excludes_events_before_window(self, db) -> None:
        await _create(db, starts_at_utc=NOW - HOUR)

        rows = await repo.list_upcoming_events_in_window(GUILD_A, NOW, NOW + 7 * DAY)

        assert rows == []

    async def test_excludes_events_after_window(self, db) -> None:
        await _create(db, starts_at_utc=NOW + 10 * DAY)

        rows = await repo.list_upcoming_events_in_window(GUILD_A, NOW, NOW + 7 * DAY)

        assert rows == []

    async def test_window_boundaries_are_inclusive(self, db) -> None:
        start_id = await _create(db, starts_at_utc=NOW)
        end_id = await _create(db, starts_at_utc=NOW + 7 * DAY)

        rows = await repo.list_upcoming_events_in_window(GUILD_A, NOW, NOW + 7 * DAY)

        assert {r["id"] for r in rows} == {start_id, end_id}

    async def test_excludes_cancelled_events(self, db) -> None:
        event_id = await _create(db, starts_at_utc=NOW + 3 * DAY)
        await repo.cancel_event(event_id, GUILD_A)

        rows = await repo.list_upcoming_events_in_window(GUILD_A, NOW, NOW + 7 * DAY)

        assert rows == []

    async def test_scoped_to_guild(self, db) -> None:
        await _create(db, guild_id=GUILD_A, starts_at_utc=NOW + 3 * DAY)
        other_id = await _create(db, guild_id=GUILD_B, starts_at_utc=NOW + 3 * DAY)

        rows = await repo.list_upcoming_events_in_window(GUILD_B, NOW, NOW + 7 * DAY)

        assert [r["id"] for r in rows] == [other_id]

    async def test_orders_by_start_time_ascending(self, db) -> None:
        later_id = await _create(db, starts_at_utc=NOW + 5 * DAY)
        earlier_id = await _create(db, starts_at_utc=NOW + DAY)

        rows = await repo.list_upcoming_events_in_window(GUILD_A, NOW, NOW + 7 * DAY)

        assert [r["id"] for r in rows] == [earlier_id, later_id]


class TestListGuildsWithWeeklyDigestEnabled:
    async def test_returns_only_enabled_guilds(self, db) -> None:
        await repo.ensure_guild(GUILD_A, "Asia/Taipei")
        await repo.ensure_guild(GUILD_B, "Asia/Taipei")
        await repo.update_guild_settings(GUILD_A, weekly_digest_enabled=1)

        rows = await repo.list_guilds_with_weekly_digest_enabled()

        assert [r["guild_id"] for r in rows] == [GUILD_A]

    async def test_returns_empty_when_none_enabled(self, db) -> None:
        await repo.ensure_guild(GUILD_A, "Asia/Taipei")

        assert await repo.list_guilds_with_weekly_digest_enabled() == []


class TestSetLastWeeklyDigestAt:
    async def test_updates_the_timestamp(self, db) -> None:
        await repo.ensure_guild(GUILD_A, "Asia/Taipei")

        await repo.set_last_weekly_digest_at(GUILD_A, NOW)

        settings = await repo.get_guild_settings(GUILD_A)
        assert settings["last_weekly_digest_at"] == NOW

    async def test_scoped_to_guild(self, db) -> None:
        await repo.ensure_guild(GUILD_A, "Asia/Taipei")
        await repo.ensure_guild(GUILD_B, "Asia/Taipei")

        await repo.set_last_weekly_digest_at(GUILD_A, NOW)

        other = await repo.get_guild_settings(GUILD_B)
        assert other["last_weekly_digest_at"] is None
