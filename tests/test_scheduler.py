"""Scheduler cog 測試 —— 提醒派送迴圈的核心處理邏輯。

不測試 `@tasks.loop` 這層 wrapper 本身（那是 discord.py 自己的、已經有
上游測試覆蓋的機制），只測試 `_process_reminder()`：每一則到期提醒該不該
發、發送失敗要怎麼收尾、逾期補償規則有沒有正確套用。

這是本專案對「bot 停機後復活，提醒不重複、不亂噴」這條 M4 完成標準的
直接驗證。

時間計算全部以固定的 PROCESS_NOW 為基準，用 offset_min／starts_at_utc
反推出想要的 fire_at_utc，而不是先建活動再回頭湊時間 —— 這樣每個測試
案例的「為什麼選這個數字」都清楚可查，不會算錯。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import discord

from src.bot.cogs.scheduler import Scheduler
from src.db import repo
from src.lib.clock import now_ms
from src.lib.ids import new_id

GUILD_ID = "111111111111111111"
CHANNEL_ID = "222222222222222222"

MINUTE = 60_000
HOUR = 3_600_000

# 全部測試的「現在時間」基準點。實際的活動開始時間／提醒偏移量都是相對
# 這個點反推，讓每個案例的到期／逾期狀態一目了然。
PROCESS_NOW = now_ms() + 100 * HOUR


async def _create_event_with_reminder(
    db, *, starts_at_utc: int, offset_min: int, **kwargs
) -> tuple[str, str]:
    """建一個活動並直接排一則提醒，回傳 (event_id, reminder_id)。"""
    event_id = new_id()
    await repo.create_event(
        event_id=event_id,
        guild_id=GUILD_ID,
        channel_id=CHANNEL_ID,
        creator_id="u1",
        title="週五團練",
        starts_at_utc=starts_at_utc,
        tz="Asia/Taipei",
        reminder_offsets_min=[offset_min],
        **kwargs,
    )
    row = await db.query_one("SELECT id FROM reminders WHERE event_id = ?", (event_id,))
    return event_id, row["id"]


async def _due_reminder_for(db, event_id: str, *, at: int = PROCESS_NOW) -> dict:
    due = await repo.list_due_reminders(at)
    return next(r for r in due if r["event_id"] == event_id)


def _make_scheduler(*, channel=None, fetch_channel_result=None) -> tuple[Scheduler, MagicMock]:
    bot = MagicMock()
    bot.get_channel = MagicMock(return_value=channel)
    if fetch_channel_result is not None:
        bot.fetch_channel = AsyncMock(return_value=fetch_channel_result)
    else:
        bot.fetch_channel = AsyncMock(
            side_effect=discord.NotFound(MagicMock(status=404), "channel not found")
        )
    return Scheduler(bot), bot


def _make_channel() -> MagicMock:
    channel = MagicMock()
    channel.send = AsyncMock()
    return channel


class TestSendsOnTime:
    """情境：活動 40 分鐘後開始，提醒設定「提前 45 分鐘」，所以 5 分鐘前
    就該發（在 15 分鐘容忍窗內，不算逾期）。"""

    STARTS_AT = PROCESS_NOW + 40 * MINUTE
    OFFSET_MIN = 45  # fire_at_utc = PROCESS_NOW - 5min

    async def test_claims_and_sends(self, db) -> None:
        event_id, reminder_id = await _create_event_with_reminder(
            db, starts_at_utc=self.STARTS_AT, offset_min=self.OFFSET_MIN
        )
        reminder = await _due_reminder_for(db, event_id)

        channel = _make_channel()
        scheduler, _ = _make_scheduler(channel=channel)
        await scheduler._process_reminder(reminder, PROCESS_NOW)

        channel.send.assert_awaited_once()
        row = await db.query_one("SELECT state FROM reminders WHERE id = ?", (reminder_id,))
        assert row["state"] == "sent"

    async def test_sends_content_with_invitee_mentions(self, db) -> None:
        event_id, _ = await _create_event_with_reminder(
            db,
            starts_at_utc=self.STARTS_AT,
            offset_min=self.OFFSET_MIN,
            user_ids=[111],
            role_ids=[222],
        )
        reminder = await _due_reminder_for(db, event_id)

        channel = _make_channel()
        scheduler, _ = _make_scheduler(channel=channel)
        await scheduler._process_reminder(reminder, PROCESS_NOW)

        _, kwargs = channel.send.call_args
        assert kwargs["content"] == "<@111> <@&222>"

    async def test_embed_reflects_event_title(self, db) -> None:
        event_id, _ = await _create_event_with_reminder(
            db, starts_at_utc=self.STARTS_AT, offset_min=self.OFFSET_MIN
        )
        reminder = await _due_reminder_for(db, event_id)

        channel = _make_channel()
        scheduler, _ = _make_scheduler(channel=channel)
        await scheduler._process_reminder(reminder, PROCESS_NOW)

        _, kwargs = channel.send.call_args
        assert "週五團練" in kwargs["embed"].title


class TestDeduplication:
    async def test_already_claimed_reminder_is_not_sent_again(self, db) -> None:
        """核心防護：搶鎖失敗就不該再發送一次。"""
        event_id, reminder_id = await _create_event_with_reminder(
            db, starts_at_utc=PROCESS_NOW + 40 * MINUTE, offset_min=45
        )
        reminder = await _due_reminder_for(db, event_id)
        await repo.claim_reminder(reminder_id)  # 模擬已經被處理過一次

        channel = _make_channel()
        scheduler, _ = _make_scheduler(channel=channel)
        await scheduler._process_reminder(reminder, PROCESS_NOW)

        channel.send.assert_not_awaited()


class TestOverdueCompensation:
    async def test_overdue_but_event_not_started_still_sends(self, db) -> None:
        """bot 停機恢復後，提醒本身逾期了（30 分鐘前就該發），但活動還要
        10 分鐘才開始 —— 仍要補發一次「即將開始」。"""
        starts_at = PROCESS_NOW + 10 * MINUTE
        offset_min = 40  # fire_at_utc = PROCESS_NOW - 30min，超過 15 分鐘容忍窗
        event_id, reminder_id = await _create_event_with_reminder(
            db, starts_at_utc=starts_at, offset_min=offset_min
        )
        reminder = await _due_reminder_for(db, event_id)

        channel = _make_channel()
        scheduler, _ = _make_scheduler(channel=channel)
        await scheduler._process_reminder(reminder, PROCESS_NOW)

        channel.send.assert_awaited_once()
        row = await db.query_one("SELECT state FROM reminders WHERE id = ?", (reminder_id,))
        assert row["state"] == "sent"

    async def test_overdue_and_event_already_started_is_skipped_not_sent(self, db) -> None:
        """活動 10 分鐘前就已經開始，提醒又逾期超過容忍窗 —— 放棄補發，
        避免半夜噴一串早就開始的活動提醒（M4 完成標準明確要求的行為）。"""
        starts_at = PROCESS_NOW - 10 * MINUTE
        offset_min = 20  # fire_at_utc = starts_at - 20min = PROCESS_NOW - 30min
        event_id, reminder_id = await _create_event_with_reminder(
            db, starts_at_utc=starts_at, offset_min=offset_min
        )
        reminder = await _due_reminder_for(db, event_id)

        channel = _make_channel()
        scheduler, _ = _make_scheduler(channel=channel)
        await scheduler._process_reminder(reminder, PROCESS_NOW)

        channel.send.assert_not_awaited()
        row = await db.query_one("SELECT state FROM reminders WHERE id = ?", (reminder_id,))
        assert row["state"] == "skipped"


class TestChannelResolutionFailure:
    STARTS_AT = PROCESS_NOW + 40 * MINUTE
    OFFSET_MIN = 45

    async def test_missing_channel_marks_failed_without_raising(self, db) -> None:
        event_id, reminder_id = await _create_event_with_reminder(
            db, starts_at_utc=self.STARTS_AT, offset_min=self.OFFSET_MIN
        )
        reminder = await _due_reminder_for(db, event_id)

        scheduler, _ = _make_scheduler(channel=None, fetch_channel_result=None)
        await scheduler._process_reminder(reminder, PROCESS_NOW)  # 不應拋例外

        row = await db.query_one("SELECT state FROM reminders WHERE id = ?", (reminder_id,))
        assert row["state"] == "failed"

    async def test_falls_back_to_fetch_channel_when_cache_misses(self, db) -> None:
        event_id, _ = await _create_event_with_reminder(
            db, starts_at_utc=self.STARTS_AT, offset_min=self.OFFSET_MIN
        )
        reminder = await _due_reminder_for(db, event_id)

        channel = _make_channel()
        scheduler, bot = _make_scheduler(channel=None, fetch_channel_result=channel)
        await scheduler._process_reminder(reminder, PROCESS_NOW)

        bot.fetch_channel.assert_awaited_once()
        channel.send.assert_awaited_once()


class TestSendFailure:
    async def test_http_exception_during_send_marks_failed(self, db) -> None:
        event_id, reminder_id = await _create_event_with_reminder(
            db, starts_at_utc=PROCESS_NOW + 40 * MINUTE, offset_min=45
        )
        reminder = await _due_reminder_for(db, event_id)

        channel = _make_channel()
        channel.send = AsyncMock(
            side_effect=discord.HTTPException(MagicMock(status=403), "forbidden")
        )
        scheduler, _ = _make_scheduler(channel=channel)
        await scheduler._process_reminder(reminder, PROCESS_NOW)  # 不應拋例外

        row = await db.query_one("SELECT state FROM reminders WHERE id = ?", (reminder_id,))
        assert row["state"] == "failed"
