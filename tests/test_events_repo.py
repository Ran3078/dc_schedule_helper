"""events 的 repo 查詢測試。承接 test_multi_guild.py 的隔離測試，這裡專注在
CRUD 語意本身：list_events 的三種 scope、set_event_message 的歸屬檢查、
cancel_event 的狀態轉換。
"""

from __future__ import annotations

import pytest

from src.db import repo
from src.lib.clock import now_ms
from src.lib.ids import new_id

GUILD_A = "111111111111111111"
GUILD_B = "222222222222222222"
USER_1 = "1111"
USER_2 = "2222"

NOW = now_ms()
HOUR = 3_600_000


async def _create(
    db,
    *,
    guild_id: str = GUILD_A,
    creator_id: str = USER_1,
    title: str = "團練",
    starts_at_utc: int = NOW + HOUR,
    **kwargs,
) -> str:
    event_id = new_id()
    await repo.create_event(
        event_id=event_id,
        guild_id=guild_id,
        channel_id="c1",
        creator_id=creator_id,
        title=title,
        starts_at_utc=starts_at_utc,
        tz="Asia/Taipei",
        **kwargs,
    )
    return event_id


class TestCreateEvent:
    async def test_optional_fields_default_to_none(self, db) -> None:
        event_id = await _create(db)
        row = await repo.owned_event(event_id, GUILD_A)
        assert row is not None
        assert row["location"] is None
        assert row["description"] is None
        assert row["ends_at_utc"] is None
        assert row["status"] == "scheduled"

    async def test_stores_all_provided_fields(self, db) -> None:
        event_id = await _create(
            db, location="語音頻道", description="打完第三章", ends_at_utc=NOW + 2 * HOUR
        )
        row = await repo.owned_event(event_id, GUILD_A)
        assert row["location"] == "語音頻道"
        assert row["description"] == "打完第三章"
        assert row["ends_at_utc"] == NOW + 2 * HOUR

    async def test_restrict_rsvp_defaults_to_false(self, db) -> None:
        event_id = await _create(db)
        row = await repo.owned_event(event_id, GUILD_A)
        assert row["restrict_rsvp"] == 0

    async def test_restrict_rsvp_can_be_enabled(self, db) -> None:
        event_id = await _create(db, restrict_rsvp=True)
        row = await repo.owned_event(event_id, GUILD_A)
        assert row["restrict_rsvp"] == 1


class TestCreateEventInvitees:
    """create_event 的 user_ids/role_ids/tag_everyone 要跟活動本身同一次寫入。"""

    async def test_inserts_user_and_role_invitees(self, db) -> None:
        event_id = await _create(db, user_ids=["u1", "u2"], role_ids=["r1"])
        invitees = await repo.list_event_invitees(event_id, GUILD_A)

        users = {i["target_id"] for i in invitees if i["target_type"] == "user"}
        roles = {i["target_id"] for i in invitees if i["target_type"] == "role"}
        assert users == {"u1", "u2"}
        assert roles == {"r1"}

    async def test_tag_everyone_stores_guild_id_as_target(self, db) -> None:
        event_id = await _create(db, guild_id=GUILD_A, tag_everyone=True)
        invitees = await repo.list_event_invitees(event_id, GUILD_A)

        assert len(invitees) == 1
        assert invitees[0]["target_type"] == "everyone"
        assert invitees[0]["target_id"] == GUILD_A

    async def test_no_invitees_by_default(self, db) -> None:
        event_id = await _create(db)
        assert await repo.list_event_invitees(event_id, GUILD_A) == []

    async def test_accepts_int_ids(self, db) -> None:
        """UserSelect.values 給的是 int id，repo 層要自己轉字串存進 TEXT 欄位。"""
        event_id = await _create(db, user_ids=[123456789], role_ids=[987654321])
        invitees = await repo.list_event_invitees(event_id, GUILD_A)
        ids = {i["target_id"] for i in invitees}
        assert ids == {"123456789", "987654321"}

    async def test_duplicate_ids_are_tolerated(self, db) -> None:
        """理論上不會發生（UserSelect 選項天生不重複），但 INSERT OR IGNORE
        保底不該讓整批寫入失敗。"""
        event_id = await _create(db, user_ids=["u1", "u1"])
        invitees = await repo.list_event_invitees(event_id, GUILD_A)
        assert len(invitees) == 1


class TestListEventInviteesIsolation:
    async def test_scoped_to_guild_via_join(self, db) -> None:
        """event_invitees 沒有自己的 guild_id 欄位，隔離全靠 JOIN events ——
        這是最容易漏測的一塊。"""
        event_a = await _create(db, guild_id=GUILD_A, user_ids=["u1"])
        event_b = await _create(db, guild_id=GUILD_B, user_ids=["u2"])

        assert {i["target_id"] for i in await repo.list_event_invitees(event_a, GUILD_A)} == {
            "u1"
        }
        assert await repo.list_event_invitees(event_a, GUILD_B) == []
        assert {i["target_id"] for i in await repo.list_event_invitees(event_b, GUILD_B)} == {
            "u2"
        }


class TestUpsertRsvp:
    async def test_records_rsvp(self, db) -> None:
        event_id = await _create(db)
        ok = await repo.upsert_rsvp(event_id, GUILD_A, USER_1, "yes")
        assert ok is True

        rows = await repo.list_rsvps(event_id, GUILD_A)
        assert len(rows) == 1
        assert rows[0]["user_id"] == USER_1
        assert rows[0]["status"] == "yes"

    async def test_revote_overwrites_previous_status(self, db) -> None:
        """改按別的按鈕要覆蓋，不是疊加成兩筆紀錄。"""
        event_id = await _create(db)
        await repo.upsert_rsvp(event_id, GUILD_A, USER_1, "maybe")
        await repo.upsert_rsvp(event_id, GUILD_A, USER_1, "yes")

        rows = await repo.list_rsvps(event_id, GUILD_A)
        assert len(rows) == 1
        assert rows[0]["status"] == "yes"

    async def test_multiple_users_each_get_own_row(self, db) -> None:
        event_id = await _create(db)
        await repo.upsert_rsvp(event_id, GUILD_A, USER_1, "yes")
        await repo.upsert_rsvp(event_id, GUILD_A, USER_2, "no")

        rows = await repo.list_rsvps(event_id, GUILD_A)
        by_user = {r["user_id"]: r["status"] for r in rows}
        assert by_user == {USER_1: "yes", USER_2: "no"}

    async def test_returns_false_for_nonexistent_event(self, db) -> None:
        ok = await repo.upsert_rsvp("nope", GUILD_A, USER_1, "yes")
        assert ok is False

    async def test_returns_false_for_event_in_other_guild(self, db) -> None:
        """就算 event_id 是對的，guild_id 對不上也不該寫入 —— 這是子表歸屬檢查的核心測試。"""
        event_id = await _create(db, guild_id=GUILD_A)
        ok = await repo.upsert_rsvp(event_id, GUILD_B, USER_1, "yes")
        assert ok is False
        assert await repo.list_rsvps(event_id, GUILD_A) == []

    async def test_accepts_int_user_id(self, db) -> None:
        event_id = await _create(db)
        ok = await repo.upsert_rsvp(event_id, GUILD_A, 123456789, "yes")
        assert ok is True
        rows = await repo.list_rsvps(event_id, GUILD_A)
        assert rows[0]["user_id"] == "123456789"


class TestListRsvpsIsolation:
    async def test_scoped_to_guild_via_join(self, db) -> None:
        event_a = await _create(db, guild_id=GUILD_A)
        event_b = await _create(db, guild_id=GUILD_B)
        await repo.upsert_rsvp(event_a, GUILD_A, USER_1, "yes")
        await repo.upsert_rsvp(event_b, GUILD_B, USER_2, "no")

        assert {r["user_id"] for r in await repo.list_rsvps(event_a, GUILD_A)} == {USER_1}
        assert await repo.list_rsvps(event_a, GUILD_B) == []


class TestSetEventMessage:
    async def test_records_message_id(self, db) -> None:
        event_id = await _create(db)
        ok = await repo.set_event_message(event_id, GUILD_A, 999999)
        assert ok is True
        row = await repo.owned_event(event_id, GUILD_A)
        assert row["message_id"] == "999999"

    async def test_refuses_cross_guild_update(self, db) -> None:
        """就算誤傳了別的伺服器的 guild_id，也不能更新到 A 的活動。"""
        event_id = await _create(db, guild_id=GUILD_A)
        ok = await repo.set_event_message(event_id, GUILD_B, 999999)
        assert ok is False
        row = await repo.owned_event(event_id, GUILD_A)
        assert row["message_id"] is None


class TestCancelEvent:
    async def test_cancel_sets_status(self, db) -> None:
        event_id = await _create(db)
        ok = await repo.cancel_event(event_id, GUILD_A)
        assert ok is True
        row = await repo.owned_event(event_id, GUILD_A)
        assert row["status"] == "cancelled"

    async def test_cancel_is_not_repeatable(self, db) -> None:
        """已取消的活動不該再被「取消」一次（rowcount 語意上等同樂觀鎖）。"""
        event_id = await _create(db)
        assert await repo.cancel_event(event_id, GUILD_A) is True
        assert await repo.cancel_event(event_id, GUILD_A) is False

    async def test_cannot_cancel_other_guilds_event(self, db) -> None:
        event_id = await _create(db, guild_id=GUILD_A)
        assert await repo.cancel_event(event_id, GUILD_B) is False
        row = await repo.owned_event(event_id, GUILD_A)
        assert row["status"] == "scheduled"


class TestUpdateEvent:
    async def test_overwrites_all_four_fields(self, db) -> None:
        event_id = await _create(
            db, title="舊標題", starts_at_utc=NOW + HOUR, location="舊地點"
        )
        ok = await repo.update_event(
            event_id,
            GUILD_A,
            title="新標題",
            starts_at_utc=NOW + 2 * HOUR,
            location="新地點",
            description="新內容",
        )
        assert ok is True

        row = await repo.owned_event(event_id, GUILD_A)
        assert row["title"] == "新標題"
        assert row["starts_at_utc"] == NOW + 2 * HOUR
        assert row["location"] == "新地點"
        assert row["description"] == "新內容"

    async def test_can_clear_location_and_description(self, db) -> None:
        event_id = await _create(db, location="舊地點", description="舊內容")
        await repo.update_event(
            event_id,
            GUILD_A,
            title="標題",
            starts_at_utc=NOW + HOUR,
            location=None,
            description=None,
        )
        row = await repo.owned_event(event_id, GUILD_A)
        assert row["location"] is None
        assert row["description"] is None

    async def test_does_not_touch_ends_at_utc(self, db) -> None:
        """這輪不開放編輯結束時間，維持原值。"""
        event_id = await _create(db, ends_at_utc=NOW + 3 * HOUR)
        await repo.update_event(
            event_id, GUILD_A, title="標題", starts_at_utc=NOW + HOUR,
            location=None, description=None,
        )
        row = await repo.owned_event(event_id, GUILD_A)
        assert row["ends_at_utc"] == NOW + 3 * HOUR

    async def test_refuses_cross_guild_update(self, db) -> None:
        event_id = await _create(db, guild_id=GUILD_A, title="原標題")
        ok = await repo.update_event(
            event_id, GUILD_B, title="被改的標題", starts_at_utc=NOW + HOUR,
            location=None, description=None,
        )
        assert ok is False
        row = await repo.owned_event(event_id, GUILD_A)
        assert row["title"] == "原標題"

    async def test_returns_false_for_nonexistent_event(self, db) -> None:
        ok = await repo.update_event(
            "nope", GUILD_A, title="x", starts_at_utc=NOW, location=None, description=None
        )
        assert ok is False


class TestAddEventInvitee:
    async def test_adds_user_invitee(self, db) -> None:
        event_id = await _create(db)
        ok = await repo.add_event_invitee(event_id, GUILD_A, "user", USER_2)
        assert ok is True
        invitees = await repo.list_event_invitees(event_id, GUILD_A)
        assert invitees[0]["target_type"] == "user"
        assert invitees[0]["target_id"] == USER_2

    async def test_duplicate_invitee_is_ignored_not_an_error(self, db) -> None:
        event_id = await _create(db)
        await repo.add_event_invitee(event_id, GUILD_A, "role", "r1")
        await repo.add_event_invitee(event_id, GUILD_A, "role", "r1")  # 不應拋例外
        invitees = await repo.list_event_invitees(event_id, GUILD_A)
        assert len(invitees) == 1

    async def test_refuses_cross_guild_insert(self, db) -> None:
        event_id = await _create(db, guild_id=GUILD_A)
        ok = await repo.add_event_invitee(event_id, GUILD_B, "user", USER_2)
        assert ok is False
        assert await repo.list_event_invitees(event_id, GUILD_A) == []

    async def test_returns_false_for_nonexistent_event(self, db) -> None:
        ok = await repo.add_event_invitee("nope", GUILD_A, "user", USER_2)
        assert ok is False


class TestListEventsUpcoming:
    async def test_excludes_past_events(self, db) -> None:
        await _create(db, title="過去的活動", starts_at_utc=NOW - HOUR)
        await _create(db, title="未來的活動", starts_at_utc=NOW + HOUR)

        rows = await repo.list_events(GUILD_A, scope="upcoming")
        assert [r["title"] for r in rows] == ["未來的活動"]

    async def test_excludes_cancelled_events(self, db) -> None:
        event_id = await _create(db, starts_at_utc=NOW + HOUR)
        await repo.cancel_event(event_id, GUILD_A)

        rows = await repo.list_events(GUILD_A, scope="upcoming")
        assert rows == []

    async def test_orders_by_start_time_ascending(self, db) -> None:
        await _create(db, title="較晚", starts_at_utc=NOW + 3 * HOUR)
        await _create(db, title="較早", starts_at_utc=NOW + HOUR)

        rows = await repo.list_events(GUILD_A, scope="upcoming")
        assert [r["title"] for r in rows] == ["較早", "較晚"]

    async def test_respects_limit(self, db) -> None:
        for i in range(5):
            await _create(db, title=f"活動{i}", starts_at_utc=NOW + (i + 1) * HOUR)

        rows = await repo.list_events(GUILD_A, scope="upcoming", limit=2)
        assert len(rows) == 2

    async def test_is_scoped_to_guild(self, db) -> None:
        await _create(db, guild_id=GUILD_A, title="A的活動", starts_at_utc=NOW + HOUR)
        await _create(db, guild_id=GUILD_B, title="B的活動", starts_at_utc=NOW + HOUR)

        rows = await repo.list_events(GUILD_A, scope="upcoming")
        assert [r["title"] for r in rows] == ["A的活動"]


class TestListEventsMine:
    async def test_filters_by_creator(self, db) -> None:
        await _create(db, creator_id=USER_1, title="我的", starts_at_utc=NOW + HOUR)
        await _create(db, creator_id=USER_2, title="別人的", starts_at_utc=NOW + HOUR)

        rows = await repo.list_events(GUILD_A, scope="mine", user_id=USER_1)
        assert [r["title"] for r in rows] == ["我的"]

    async def test_requires_user_id(self, db) -> None:
        with pytest.raises(ValueError, match="user_id"):
            await repo.list_events(GUILD_A, scope="mine")


class TestSetEventDiscordId:
    async def test_records_discord_event_id(self, db) -> None:
        event_id = await _create(db)
        ok = await repo.set_event_discord_id(event_id, GUILD_A, 888888)
        assert ok is True
        row = await repo.owned_event(event_id, GUILD_A)
        assert row["discord_event_id"] == "888888"

    async def test_refuses_cross_guild_update(self, db) -> None:
        event_id = await _create(db, guild_id=GUILD_A)
        ok = await repo.set_event_discord_id(event_id, GUILD_B, 888888)
        assert ok is False
        row = await repo.owned_event(event_id, GUILD_A)
        assert row["discord_event_id"] is None


class TestGetEventByDiscordId:
    async def test_finds_event_by_discord_id(self, db) -> None:
        event_id = await _create(db, guild_id=GUILD_A)
        await repo.set_event_discord_id(event_id, GUILD_A, 888888)

        row = await repo.get_event_by_discord_id(888888, GUILD_A)
        assert row is not None
        assert row["id"] == event_id

    async def test_scoped_to_guild(self, db) -> None:
        event_id = await _create(db, guild_id=GUILD_A)
        await repo.set_event_discord_id(event_id, GUILD_A, 888888)

        assert await repo.get_event_by_discord_id(888888, GUILD_B) is None

    async def test_returns_none_when_unknown(self, db) -> None:
        assert await repo.get_event_by_discord_id(999999999, GUILD_A) is None


class TestDeleteRsvp:
    async def test_deletes_existing_row(self, db) -> None:
        event_id = await _create(db)
        await repo.upsert_rsvp(event_id, GUILD_A, USER_1, "yes")

        await repo.delete_rsvp(event_id, GUILD_A, USER_1)

        assert await repo.list_rsvps(event_id, GUILD_A) == []

    async def test_does_not_affect_other_users(self, db) -> None:
        event_id = await _create(db)
        await repo.upsert_rsvp(event_id, GUILD_A, USER_1, "yes")
        await repo.upsert_rsvp(event_id, GUILD_A, USER_2, "no")

        await repo.delete_rsvp(event_id, GUILD_A, USER_1)

        rows = await repo.list_rsvps(event_id, GUILD_A)
        assert {r["user_id"] for r in rows} == {USER_2}

    async def test_deleting_nonexistent_row_does_not_raise(self, db) -> None:
        event_id = await _create(db)
        await repo.delete_rsvp(event_id, GUILD_A, USER_1)  # 不應拋例外

    async def test_scoped_to_guild(self, db) -> None:
        """guild_id 對不上就不該刪到別的伺服器的 RSVP。"""
        event_id = await _create(db, guild_id=GUILD_A)
        await repo.upsert_rsvp(event_id, GUILD_A, USER_1, "yes")

        await repo.delete_rsvp(event_id, GUILD_B, USER_1)

        rows = await repo.list_rsvps(event_id, GUILD_A)
        assert len(rows) == 1


class TestListEventsAll:
    async def test_includes_cancelled_and_past(self, db) -> None:
        past_id = await _create(db, title="過去", starts_at_utc=NOW - HOUR)
        await repo.cancel_event(past_id, GUILD_A)
        await _create(db, title="未來", starts_at_utc=NOW + HOUR)

        rows = await repo.list_events(GUILD_A, scope="all")
        assert {r["title"] for r in rows} == {"過去", "未來"}

    async def test_rejects_unknown_scope(self, db) -> None:
        with pytest.raises(ValueError, match="scope"):
            await repo.list_events(GUILD_A, scope="nonsense")
