"""提醒排程的 repo 層測試：建立時的原子排定、到期查詢、樂觀鎖。"""

from __future__ import annotations

from src.db import repo
from src.lib.clock import now_ms
from src.lib.ids import new_id

GUILD_A = "111111111111111111"
GUILD_B = "222222222222222222"

NOW = now_ms()
HOUR = 3_600_000
MINUTE = 60_000


async def _create(db, *, starts_at_utc: int, guild_id: str = GUILD_A, **kwargs) -> str:
    event_id = new_id()
    await repo.create_event(
        event_id=event_id,
        guild_id=guild_id,
        channel_id="c1",
        creator_id="u1",
        title="團練",
        starts_at_utc=starts_at_utc,
        tz="Asia/Taipei",
        **kwargs,
    )
    return event_id


class TestCreateEventSchedulesReminders:
    async def test_creates_reminder_rows_with_correct_fire_time(self, db) -> None:
        starts = NOW + 2 * HOUR
        event_id = await _create(db, starts_at_utc=starts, reminder_offsets_min=[60, 10])

        rows = await db.query_all(
            "SELECT * FROM reminders WHERE event_id = ? ORDER BY offset_min DESC", (event_id,)
        )
        assert len(rows) == 2
        assert rows[0]["offset_min"] == 60
        assert rows[0]["fire_at_utc"] == starts - 60 * MINUTE
        assert rows[1]["offset_min"] == 10
        assert rows[1]["fire_at_utc"] == starts - 10 * MINUTE
        assert all(r["state"] == "pending" for r in rows)

    async def test_no_offsets_creates_no_reminders(self, db) -> None:
        event_id = await _create(db, starts_at_utc=NOW + HOUR)
        rows = await db.query_all("SELECT * FROM reminders WHERE event_id = ?", (event_id,))
        assert rows == []

    async def test_offset_that_would_fire_in_the_past_is_skipped(self, db) -> None:
        """活動 5 分鐘後開始，「提前 1 天」的提醒排出來會是過去的時間 —— 不該排。"""
        starts = NOW + 5 * MINUTE
        event_id = await _create(db, starts_at_utc=starts, reminder_offsets_min=[1440, 1])

        rows = await db.query_all(
            "SELECT offset_min FROM reminders WHERE event_id = ?", (event_id,)
        )
        assert [r["offset_min"] for r in rows] == [1]

    async def test_reminders_and_event_are_written_atomically(self, db) -> None:
        """間接驗證：能查到活動就該同時查得到提醒，不會有半套資料。"""
        event_id = await _create(db, starts_at_utc=NOW + HOUR, reminder_offsets_min=[30])
        event = await repo.owned_event(event_id, GUILD_A)
        reminders = await db.query_all("SELECT * FROM reminders WHERE event_id = ?", (event_id,))
        assert event is not None
        assert len(reminders) == 1


class TestListDueReminders:
    async def test_returns_due_pending_reminders(self, db) -> None:
        event_id = await _create(db, starts_at_utc=NOW + HOUR, reminder_offsets_min=[45])
        due = await repo.list_due_reminders(NOW + HOUR)
        assert [r["event_id"] for r in due] == [event_id]

    async def test_excludes_future_reminders(self, db) -> None:
        await _create(db, starts_at_utc=NOW + 10 * HOUR, reminder_offsets_min=[30])
        # fire_at_utc = starts - 30min，遠早於現在 5 分鐘視窗，不該被撈到
        due = await repo.list_due_reminders(NOW + 5 * MINUTE)
        assert due == []

    async def test_join_includes_event_fields(self, db) -> None:
        event_id = await _create(
            db, starts_at_utc=NOW + HOUR, reminder_offsets_min=[45], location="語音頻道"
        )
        due = await repo.list_due_reminders(NOW + HOUR)
        row = next(r for r in due if r["event_id"] == event_id)
        assert row["guild_id"] == GUILD_A
        assert row["title"] == "團練"
        assert row["location"] == "語音頻道"

    async def test_excludes_reminders_for_cancelled_events(self, db) -> None:
        event_id = await _create(db, starts_at_utc=NOW + HOUR, reminder_offsets_min=[45])
        await repo.cancel_event(event_id, GUILD_A)
        due = await repo.list_due_reminders(NOW + HOUR)
        assert due == []

    async def test_excludes_already_sent_reminders(self, db) -> None:
        event_id = await _create(db, starts_at_utc=NOW + HOUR, reminder_offsets_min=[45])
        due = await repo.list_due_reminders(NOW + HOUR)
        await repo.claim_reminder(due[0]["id"])

        still_due = await repo.list_due_reminders(NOW + HOUR)
        assert not any(r["event_id"] == event_id for r in still_due)

    async def test_respects_limit(self, db) -> None:
        for _ in range(3):
            await _create(db, starts_at_utc=NOW + HOUR, reminder_offsets_min=[45])
        due = await repo.list_due_reminders(NOW + HOUR, limit=2)
        assert len(due) == 2

    async def test_orders_by_fire_time_ascending(self, db) -> None:
        event_id = await _create(
            db, starts_at_utc=NOW + HOUR, reminder_offsets_min=[45, 30, 50]
        )
        due = await repo.list_due_reminders(NOW + HOUR)
        offsets = [r["offset_min"] for r in due if r["event_id"] == event_id]
        # offset 越大代表 fire_at_utc 越早（越早提醒），所以應該先出現
        assert offsets == sorted(offsets, reverse=True)

    async def test_spans_multiple_guilds(self, db) -> None:
        """list_due_reminders 是唯一一個刻意不分伺服器的查詢，這裡驗證它確實
        能一次撈到多個伺服器的到期提醒（處理時再各自用列上的 guild_id）。"""
        event_a = await _create(
            db, guild_id=GUILD_A, starts_at_utc=NOW + HOUR, reminder_offsets_min=[45]
        )
        event_b = await _create(
            db, guild_id=GUILD_B, starts_at_utc=NOW + HOUR, reminder_offsets_min=[45]
        )

        due = await repo.list_due_reminders(NOW + HOUR)
        guild_ids = {r["guild_id"] for r in due if r["event_id"] in (event_a, event_b)}
        assert guild_ids == {GUILD_A, GUILD_B}


class TestClaimReminder:
    async def test_first_claim_succeeds(self, db) -> None:
        event_id = await _create(db, starts_at_utc=NOW + HOUR, reminder_offsets_min=[45])
        due = await repo.list_due_reminders(NOW + HOUR)
        reminder_id = next(r["id"] for r in due if r["event_id"] == event_id)

        assert await repo.claim_reminder(reminder_id) is True

    async def test_second_claim_fails(self, db) -> None:
        """這是防止提醒重複發送的核心機制。"""
        event_id = await _create(db, starts_at_utc=NOW + HOUR, reminder_offsets_min=[45])
        due = await repo.list_due_reminders(NOW + HOUR)
        reminder_id = next(r["id"] for r in due if r["event_id"] == event_id)

        assert await repo.claim_reminder(reminder_id) is True
        assert await repo.claim_reminder(reminder_id) is False

    async def test_claiming_nonexistent_reminder_fails(self, db) -> None:
        assert await repo.claim_reminder("nope") is False


class TestSkipReminder:
    async def test_skip_succeeds_once(self, db) -> None:
        event_id = await _create(db, starts_at_utc=NOW + HOUR, reminder_offsets_min=[45])
        due = await repo.list_due_reminders(NOW + HOUR)
        reminder_id = next(r["id"] for r in due if r["event_id"] == event_id)

        assert await repo.skip_reminder(reminder_id) is True
        assert await repo.skip_reminder(reminder_id) is False

    async def test_skipped_reminder_disappears_from_due_list(self, db) -> None:
        event_id = await _create(db, starts_at_utc=NOW + HOUR, reminder_offsets_min=[45])
        due = await repo.list_due_reminders(NOW + HOUR)
        reminder_id = next(r["id"] for r in due if r["event_id"] == event_id)
        await repo.skip_reminder(reminder_id)

        still_due = await repo.list_due_reminders(NOW + HOUR)
        assert not any(r["id"] == reminder_id for r in still_due)


class TestMarkReminderFailed:
    async def test_overwrites_claimed_reminder_to_failed(self, db) -> None:
        event_id = await _create(db, starts_at_utc=NOW + HOUR, reminder_offsets_min=[45])
        due = await repo.list_due_reminders(NOW + HOUR)
        reminder_id = next(r["id"] for r in due if r["event_id"] == event_id)
        await repo.claim_reminder(reminder_id)

        await repo.mark_reminder_failed(reminder_id)

        row = await db.query_one("SELECT state FROM reminders WHERE id = ?", (reminder_id,))
        assert row["state"] == "failed"

    async def test_does_not_raise_for_nonexistent_reminder(self, db) -> None:
        await repo.mark_reminder_failed("nope")  # 不應拋例外
