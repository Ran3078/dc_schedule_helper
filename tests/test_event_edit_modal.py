"""`EventEditModal`（`/event edit`）測試——回報過的 bug：編輯活動時只能改
開始時間，結束時間改不動。修法是幫 Modal 補上第五個「時長」欄位，跟
`/event create` 的 duration 參數同一套解析邏輯，送出時整批覆寫進
`repo.update_event`（見 modals.py 的說明）。

`event_row["message_id"]`／`["discord_event_id"]` 在這裡建立的測試活動一律
是 `None`，所以 `on_submit` 裡「重繪公告卡片」「同步原生活動」那兩段分支
不會被觸發，不需要另外 mock channel/guild 的那些呼叫——這是刻意選的測試
邊界，公告卡片重繪、原生活動同步已經在各自建立時的路徑測過。
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

from src.bot.modals import EventEditModal, _format_duration_minutes
from src.db import repo
from src.lib.clock import now_ms
from src.lib.ids import new_id

GUILD_ID = 111111111111111111
CHANNEL_ID = 222222222222222222
CREATOR_ID = 333333333333333333

NOW = now_ms()
HOUR = 3_600_000
MINUTE = 60_000


async def _create_event(db, **overrides) -> str:
    event_id = new_id()
    defaults = dict(
        event_id=event_id,
        guild_id=GUILD_ID,
        channel_id=CHANNEL_ID,
        creator_id=CREATOR_ID,
        title="舊標題",
        starts_at_utc=NOW + HOUR,
        tz="Asia/Taipei",
    )
    await repo.create_event(**{**defaults, **overrides})
    return event_id


def _make_interaction() -> MagicMock:
    interaction = MagicMock()
    interaction.guild = None  # 略過 build_rsvp_summary 那條分支，不影響本檔案要測的行為
    interaction.response = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=False)
    return interaction


class TestFormatDurationMinutes:
    def test_whole_hours(self) -> None:
        assert _format_duration_minutes(120) == "2h"

    def test_minutes_only(self) -> None:
        assert _format_duration_minutes(45) == "45m"

    def test_hours_and_minutes(self) -> None:
        assert _format_duration_minutes(90) == "1h30m"

    def test_zero_minutes(self) -> None:
        assert _format_duration_minutes(0) == "0m"


class TestDurationDefaultPrefill:
    async def test_prefills_from_existing_ends_at_utc(self, db) -> None:
        event_id = await _create_event(
            db, starts_at_utc=NOW + HOUR, ends_at_utc=NOW + HOUR + 90 * MINUTE
        )
        event = await repo.owned_event(event_id, GUILD_ID)

        modal = EventEditModal(event=event, event_id=event_id, guild_id=GUILD_ID, tz="Asia/Taipei")

        assert modal.duration_input.default == "1h30m"

    async def test_blank_when_no_end_time_set(self, db) -> None:
        event_id = await _create_event(db)
        event = await repo.owned_event(event_id, GUILD_ID)

        modal = EventEditModal(event=event, event_id=event_id, guild_id=GUILD_ID, tz="Asia/Taipei")

        assert modal.duration_input.default is None


class TestEditUpdatesEndTime:
    async def test_setting_duration_updates_ends_at_utc(self, db) -> None:
        event_id = await _create_event(db, starts_at_utc=NOW + HOUR)
        event = await repo.owned_event(event_id, GUILD_ID)

        modal = EventEditModal(event=event, event_id=event_id, guild_id=GUILD_ID, tz="Asia/Taipei")
        modal.duration_input._value = "2h"
        interaction = _make_interaction()

        await modal.on_submit(interaction)

        row = await repo.owned_event(event_id, GUILD_ID)
        assert row["ends_at_utc"] == row["starts_at_utc"] + 2 * HOUR

    async def test_changing_start_time_recomputes_end_time_from_duration(self, db) -> None:
        """這是原始回報的情境：改開始時間，結束時間要跟著位移，不是維持
        原本那個現在已經對不上的絕對時間點。"""
        event_id = await _create_event(
            db, starts_at_utc=NOW + HOUR, ends_at_utc=NOW + 3 * HOUR
        )
        event = await repo.owned_event(event_id, GUILD_ID)

        modal = EventEditModal(event=event, event_id=event_id, guild_id=GUILD_ID, tz="Asia/Taipei")
        assert modal.duration_input.default == "2h"  # 原本 1 小時到 3 小時，時長 2 小時

        # 模擬使用者改了時間輸入框，但沒動時長欄位（預設值原樣送出）。
        # 時間欄位格式只到分鐘（見 EventEditModal 的說明），這裡先把測試用的
        # 新時間點捨去到整分鐘，不然格式化再解析回去會因為秒數被捨去而對不上。
        new_start = (NOW + 5 * HOUR) // MINUTE * MINUTE
        modal.time_input._value = datetime.fromtimestamp(
            new_start / 1000, tz=ZoneInfo("Asia/Taipei")
        ).strftime("%Y-%m-%d %H:%M")
        modal.duration_input._value = modal.duration_input.default
        interaction = _make_interaction()

        await modal.on_submit(interaction)

        row = await repo.owned_event(event_id, GUILD_ID)
        assert row["starts_at_utc"] == new_start
        assert row["ends_at_utc"] == new_start + 2 * HOUR

    async def test_blank_duration_clears_end_time(self, db) -> None:
        event_id = await _create_event(
            db, starts_at_utc=NOW + HOUR, ends_at_utc=NOW + 3 * HOUR
        )
        event = await repo.owned_event(event_id, GUILD_ID)

        modal = EventEditModal(event=event, event_id=event_id, guild_id=GUILD_ID, tz="Asia/Taipei")
        modal.duration_input._value = ""
        interaction = _make_interaction()

        await modal.on_submit(interaction)

        row = await repo.owned_event(event_id, GUILD_ID)
        assert row["ends_at_utc"] is None

    async def test_invalid_duration_is_rejected_without_updating(self, db) -> None:
        event_id = await _create_event(db, starts_at_utc=NOW + HOUR)
        event = await repo.owned_event(event_id, GUILD_ID)

        modal = EventEditModal(event=event, event_id=event_id, guild_id=GUILD_ID, tz="Asia/Taipei")
        modal.duration_input._value = "不是時長"
        interaction = _make_interaction()

        await modal.on_submit(interaction)

        interaction.response.send_message.assert_awaited_once()
        row = await repo.owned_event(event_id, GUILD_ID)
        assert row["title"] == "舊標題"  # 沒有任何欄位被更新

    async def test_zero_duration_is_rejected(self, db) -> None:
        event_id = await _create_event(db, starts_at_utc=NOW + HOUR)
        event = await repo.owned_event(event_id, GUILD_ID)

        modal = EventEditModal(event=event, event_id=event_id, guild_id=GUILD_ID, tz="Asia/Taipei")
        modal.duration_input._value = "0m"
        interaction = _make_interaction()

        await modal.on_submit(interaction)

        args, _ = interaction.response.send_message.call_args
        assert "大於 0" in args[0]
