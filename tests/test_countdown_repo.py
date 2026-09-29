"""`countdowns` 表的 repo 測試——建立、母體歸屬檢查、取消的樂觀鎖、
跨伺服器隔離、系統層級的 `list_active_countdowns()`。
"""

from __future__ import annotations

from src.db import repo
from src.lib.clock import now_ms
from src.lib.ids import new_id

GUILD_A = "111111111111111111"
GUILD_B = "222222222222222222"
CHANNEL_ID = "333333333333333333"
CREATOR_ID = "444444444444444444"

NOW = now_ms()
DAY = 24 * 3_600_000


async def _create(db, *, guild_id: str = GUILD_A, **overrides) -> str:
    countdown_id = new_id()
    defaults = dict(
        countdown_id=countdown_id,
        guild_id=guild_id,
        channel_id=CHANNEL_ID,
        creator_id=CREATOR_ID,
        title="退伍倒數",
        content=None,
        target_date_utc=NOW + 30 * DAY,
        tz="Asia/Taipei",
        send_hour=9,
        send_minute=0,
    )
    await repo.create_countdown(**{**defaults, **overrides})
    return countdown_id


class TestCreateAndOwnedCountdown:
    async def test_create_then_read_back(self, db) -> None:
        countdown_id = await _create(db, title="退伍倒數", content="加油")
        row = await repo.owned_countdown(countdown_id, GUILD_A)
        assert row is not None
        assert row["title"] == "退伍倒數"
        assert row["content"] == "加油"
        assert row["status"] == "active"
        assert row["last_sent_utc"] is None

    async def test_mode_and_suffix_default_to_countdown_and_none(self, db) -> None:
        """既有呼叫端（沒帶 mode/suffix）建立出來的行為要跟改動前完全一致。"""
        countdown_id = await _create(db)
        row = await repo.owned_countdown(countdown_id, GUILD_A)
        assert row["mode"] == "countdown"
        assert row["suffix"] is None

    async def test_stores_countup_mode_and_suffix(self, db) -> None:
        countdown_id = await _create(
            db, title="沒有小名的日子第", mode="countup", suffix="天"
        )
        row = await repo.owned_countdown(countdown_id, GUILD_A)
        assert row["mode"] == "countup"
        assert row["suffix"] == "天"

    async def test_cannot_read_from_other_guild(self, db) -> None:
        countdown_id = await _create(db, guild_id=GUILD_A)
        assert await repo.owned_countdown(countdown_id, GUILD_B) is None

    async def test_returns_none_for_nonexistent_id(self, db) -> None:
        assert await repo.owned_countdown("nope", GUILD_A) is None


class TestListActiveCountdownsInGuild:
    async def test_scoped_to_guild(self, db) -> None:
        await _create(db, guild_id=GUILD_A, title="A 的倒數")
        await _create(db, guild_id=GUILD_B, title="B 的倒數")

        rows = await repo.list_active_countdowns_in_guild(GUILD_A)

        assert [r["title"] for r in rows] == ["A 的倒數"]

    async def test_ordered_by_target_date(self, db) -> None:
        await _create(db, title="比較晚", target_date_utc=NOW + 60 * DAY)
        await _create(db, title="比較快", target_date_utc=NOW + 10 * DAY)

        rows = await repo.list_active_countdowns_in_guild(GUILD_A)

        assert [r["title"] for r in rows] == ["比較快", "比較晚"]

    async def test_excludes_cancelled_and_completed(self, db) -> None:
        active_id = await _create(db, title="還在跑")
        cancelled_id = await _create(db, title="已取消")
        await repo.cancel_countdown(cancelled_id, GUILD_A)

        rows = await repo.list_active_countdowns_in_guild(GUILD_A)

        assert [r["id"] for r in rows] == [active_id]


class TestCancelCountdown:
    async def test_cancel_sets_status(self, db) -> None:
        countdown_id = await _create(db)
        ok = await repo.cancel_countdown(countdown_id, GUILD_A)
        assert ok is True
        row = await repo.owned_countdown(countdown_id, GUILD_A)
        assert row["status"] == "cancelled"

    async def test_cancel_is_not_repeatable(self, db) -> None:
        countdown_id = await _create(db)
        assert await repo.cancel_countdown(countdown_id, GUILD_A) is True
        assert await repo.cancel_countdown(countdown_id, GUILD_A) is False

    async def test_cannot_cancel_other_guilds_countdown(self, db) -> None:
        countdown_id = await _create(db, guild_id=GUILD_A)
        assert await repo.cancel_countdown(countdown_id, GUILD_B) is False
        row = await repo.owned_countdown(countdown_id, GUILD_A)
        assert row["status"] == "active"


class TestListActiveCountdowns:
    async def test_lists_across_guilds(self, db) -> None:
        """系統層級背景工作用，跨伺服器一次撈出——理由同 list_due_reminders()。"""
        await _create(db, guild_id=GUILD_A)
        await _create(db, guild_id=GUILD_B)

        rows = await repo.list_active_countdowns()

        assert {r["guild_id"] for r in rows} == {GUILD_A, GUILD_B}

    async def test_excludes_cancelled(self, db) -> None:
        active_id = await _create(db)
        cancelled_id = await _create(db)
        await repo.cancel_countdown(cancelled_id, GUILD_A)

        rows = await repo.list_active_countdowns()

        assert [r["id"] for r in rows] == [active_id]


class TestMarkCountdownSent:
    async def test_records_last_sent_without_completing(self, db) -> None:
        countdown_id = await _create(db)
        await repo.mark_countdown_sent(countdown_id, NOW, completed=False)

        row = await repo.owned_countdown(countdown_id, GUILD_A)
        assert row["last_sent_utc"] == NOW
        assert row["status"] == "active"

    async def test_completing_stops_future_sends(self, db) -> None:
        countdown_id = await _create(db)
        await repo.mark_countdown_sent(countdown_id, NOW, completed=True)

        row = await repo.owned_countdown(countdown_id, GUILD_A)
        assert row["status"] == "completed"

        rows = await repo.list_active_countdowns_in_guild(GUILD_A)
        assert rows == []
