"""repo.py 的職位報名（FF14，M8）測試：`set_event_role_slots` 的位置增刪
保留/清空報名、`set_role_signup` 額滿轉候補、`remove_role_signup`／
`promote_next_waitlisted` 的遞補流程，以及所有函式的跨伺服器拒絕。
"""

from __future__ import annotations

from src.db import repo
from src.lib.clock import now_ms
from src.lib.ids import new_id

GUILD_A = "111111111111111111"
GUILD_B = "222222222222222222"
USER_1 = "1111"
USER_2 = "2222"
USER_3 = "3333"

NOW = now_ms()
HOUR = 3_600_000


async def _create_event(db, *, guild_id: str = GUILD_A) -> str:
    event_id = new_id()
    await repo.create_event(
        event_id=event_id,
        guild_id=guild_id,
        channel_id="c1",
        creator_id=USER_1,
        title="零式團練",
        starts_at_utc=NOW + HOUR,
        tz="Asia/Taipei",
    )
    return event_id


class TestSetEventRoleSlots:
    async def test_creates_slots_for_new_positions(self, db) -> None:
        event_id = await _create_event(db)
        ok = await repo.set_event_role_slots(event_id, GUILD_A, ["MT", "ST", "D1"])
        assert ok is True

        slots = await repo.list_event_role_slots(event_id, GUILD_A)
        assert [s["position"] for s in slots] == ["MT", "ST", "D1"]

    async def test_unchanged_position_keeps_its_id_and_signups(self, db) -> None:
        event_id = await _create_event(db)
        await repo.set_event_role_slots(event_id, GUILD_A, ["MT", "ST"])
        slots = await repo.list_event_role_slots(event_id, GUILD_A)
        mt_id = next(s["id"] for s in slots if s["position"] == "MT")
        await repo.set_role_signup(event_id, GUILD_A, USER_1, mt_id, "騎士")

        # 重新設定，MT 還在（跟 ST 一起再多開一個 D1）
        await repo.set_event_role_slots(event_id, GUILD_A, ["MT", "ST", "D1"])

        new_slots = await repo.list_event_role_slots(event_id, GUILD_A)
        new_mt_id = next(s["id"] for s in new_slots if s["position"] == "MT")
        assert new_mt_id == mt_id
        signups = await repo.list_event_role_signups(event_id, GUILD_A)
        assert any(s["user_id"] == USER_1 and s["role_slot_id"] == mt_id for s in signups)

    async def test_removed_position_clears_its_signups(self, db) -> None:
        event_id = await _create_event(db)
        await repo.set_event_role_slots(event_id, GUILD_A, ["MT", "ST"])
        slots = await repo.list_event_role_slots(event_id, GUILD_A)
        mt_id = next(s["id"] for s in slots if s["position"] == "MT")
        await repo.set_role_signup(event_id, GUILD_A, USER_1, mt_id, "騎士")

        # 重新設定，拿掉 MT
        await repo.set_event_role_slots(event_id, GUILD_A, ["ST"])

        new_slots = await repo.list_event_role_slots(event_id, GUILD_A)
        assert [s["position"] for s in new_slots] == ["ST"]
        signups = await repo.list_event_role_signups(event_id, GUILD_A)
        assert signups == []

    async def test_empty_list_clears_everything(self, db) -> None:
        event_id = await _create_event(db)
        await repo.set_event_role_slots(event_id, GUILD_A, ["MT", "ST"])

        await repo.set_event_role_slots(event_id, GUILD_A, [])

        assert await repo.list_event_role_slots(event_id, GUILD_A) == []

    async def test_returns_false_for_nonexistent_event(self, db) -> None:
        ok = await repo.set_event_role_slots("nope", GUILD_A, ["MT"])
        assert ok is False

    async def test_refuses_cross_guild_update(self, db) -> None:
        event_id = await _create_event(db, guild_id=GUILD_A)
        ok = await repo.set_event_role_slots(event_id, GUILD_B, ["MT"])
        assert ok is False
        assert await repo.list_event_role_slots(event_id, GUILD_A) == []

    async def test_resetting_same_positions_preserves_order(self, db) -> None:
        event_id = await _create_event(db)
        await repo.set_event_role_slots(event_id, GUILD_A, ["MT", "ST", "D1"])

        await repo.set_event_role_slots(event_id, GUILD_A, ["MT", "ST", "D1"])

        slots = await repo.list_event_role_slots(event_id, GUILD_A)
        assert [s["position"] for s in slots] == ["MT", "ST", "D1"]


async def _make_slot(db, event_id: str, position: str = "D1") -> str:
    await repo.set_event_role_slots(event_id, GUILD_A, [position])
    slots = await repo.list_event_role_slots(event_id, GUILD_A)
    return slots[0]["id"]


class TestSetRoleSignup:
    async def test_first_signup_is_confirmed(self, db) -> None:
        event_id = await _create_event(db)
        slot_id = await _make_slot(db, event_id)

        row = await repo.set_role_signup(event_id, GUILD_A, USER_1, slot_id, "武士")

        assert row is not None
        assert row["waitlisted"] == 0
        assert row["job"] == "武士"

    async def test_second_signup_to_full_slot_is_waitlisted(self, db) -> None:
        event_id = await _create_event(db)
        slot_id = await _make_slot(db, event_id)
        await repo.set_role_signup(event_id, GUILD_A, USER_1, slot_id, "武士")

        row = await repo.set_role_signup(event_id, GUILD_A, USER_2, slot_id, "忍者")

        assert row["waitlisted"] == 1

    async def test_switching_slot_frees_the_old_one(self, db) -> None:
        event_id = await _create_event(db)
        await repo.set_event_role_slots(event_id, GUILD_A, ["D1", "D3"])
        slots = await repo.list_event_role_slots(event_id, GUILD_A)
        d1_id = next(s["id"] for s in slots if s["position"] == "D1")
        d3_id = next(s["id"] for s in slots if s["position"] == "D3")
        await repo.set_role_signup(event_id, GUILD_A, USER_1, d1_id, "武士")

        await repo.set_role_signup(event_id, GUILD_A, USER_1, d3_id, "舞者")

        signups = await repo.list_event_role_signups(event_id, GUILD_A)
        assert len(signups) == 1
        assert signups[0]["role_slot_id"] == d3_id

    async def test_reselecting_own_slot_stays_confirmed(self, db) -> None:
        """換到自己原本已經佔滿的同一個位置（例如同位置換職業）——不該因為
        「查到一個確定名額」而誤判成候補，那個確定名額其實就是自己。"""
        event_id = await _create_event(db)
        slot_id = await _make_slot(db, event_id)
        await repo.set_role_signup(event_id, GUILD_A, USER_1, slot_id, "武士")

        row = await repo.set_role_signup(event_id, GUILD_A, USER_1, slot_id, "忍者")

        assert row["waitlisted"] == 0
        assert row["job"] == "忍者"

    async def test_returns_none_for_nonexistent_slot(self, db) -> None:
        event_id = await _create_event(db)
        row = await repo.set_role_signup(event_id, GUILD_A, USER_1, "nope", "武士")
        assert row is None

    async def test_returns_none_for_cross_guild_slot(self, db) -> None:
        event_id = await _create_event(db, guild_id=GUILD_A)
        slot_id = await _make_slot(db, event_id)
        row = await repo.set_role_signup(event_id, GUILD_B, USER_1, slot_id, "武士")
        assert row is None


class TestRemoveRoleSignup:
    async def test_deletes_and_returns_the_row(self, db) -> None:
        event_id = await _create_event(db)
        slot_id = await _make_slot(db, event_id)
        await repo.set_role_signup(event_id, GUILD_A, USER_1, slot_id, "武士")

        row = await repo.remove_role_signup(event_id, GUILD_A, USER_1)

        assert row["role_slot_id"] == slot_id
        assert row["waitlisted"] == 0
        assert await repo.list_event_role_signups(event_id, GUILD_A) == []

    async def test_distinguishes_confirmed_from_waitlisted(self, db) -> None:
        event_id = await _create_event(db)
        slot_id = await _make_slot(db, event_id)
        await repo.set_role_signup(event_id, GUILD_A, USER_1, slot_id, "武士")
        await repo.set_role_signup(event_id, GUILD_A, USER_2, slot_id, "忍者")

        row = await repo.remove_role_signup(event_id, GUILD_A, USER_2)

        assert row["waitlisted"] == 1

    async def test_returns_none_when_no_signup_exists(self, db) -> None:
        event_id = await _create_event(db)
        assert await repo.remove_role_signup(event_id, GUILD_A, USER_1) is None

    async def test_refuses_cross_guild(self, db) -> None:
        event_id = await _create_event(db, guild_id=GUILD_A)
        slot_id = await _make_slot(db, event_id)
        await repo.set_role_signup(event_id, GUILD_A, USER_1, slot_id, "武士")

        assert await repo.remove_role_signup(event_id, GUILD_B, USER_1) is None
        assert len(await repo.list_event_role_signups(event_id, GUILD_A)) == 1


class TestPromoteNextWaitlisted:
    async def test_promotes_earliest_waitlisted(self, db) -> None:
        event_id = await _create_event(db)
        slot_id = await _make_slot(db, event_id)
        await repo.set_role_signup(event_id, GUILD_A, USER_1, slot_id, "武士")
        await repo.set_role_signup(event_id, GUILD_A, USER_2, slot_id, "忍者")
        await repo.set_role_signup(event_id, GUILD_A, USER_3, slot_id, "武僧")

        promoted = await repo.promote_next_waitlisted(slot_id)

        assert promoted is not None
        assert promoted["user_id"] == USER_2
        assert promoted["job"] == "忍者"
        signups = {s["user_id"]: s["waitlisted"] for s in await repo.list_event_role_signups(
            event_id, GUILD_A
        )}
        assert signups[USER_2] == 0
        assert signups[USER_3] == 1

    async def test_returns_none_when_nobody_waiting(self, db) -> None:
        event_id = await _create_event(db)
        slot_id = await _make_slot(db, event_id)
        await repo.set_role_signup(event_id, GUILD_A, USER_1, slot_id, "武士")

        assert await repo.promote_next_waitlisted(slot_id) is None

    async def test_returns_none_for_unknown_slot(self, db) -> None:
        assert await repo.promote_next_waitlisted("nope") is None


class TestGetRsvpStatus:
    async def test_returns_status_when_present(self, db) -> None:
        event_id = await _create_event(db)
        await repo.upsert_rsvp(event_id, GUILD_A, USER_1, "yes")

        assert await repo.get_rsvp_status(event_id, GUILD_A, USER_1) == "yes"

    async def test_returns_none_when_no_rsvp(self, db) -> None:
        event_id = await _create_event(db)
        assert await repo.get_rsvp_status(event_id, GUILD_A, USER_1) is None

    async def test_scoped_to_guild(self, db) -> None:
        event_id = await _create_event(db, guild_id=GUILD_A)
        await repo.upsert_rsvp(event_id, GUILD_A, USER_1, "yes")

        assert await repo.get_rsvp_status(event_id, GUILD_B, USER_1) is None
