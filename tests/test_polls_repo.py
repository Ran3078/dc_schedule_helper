"""投票的 repo 層測試：交易寫入、投票語意（單選覆蓋／複選整批替換／改票鎖）、
樂觀鎖、多伺服器隔離。"""

from __future__ import annotations

from src.db import repo
from src.lib.ids import new_id

GUILD_A = "111111111111111111"
GUILD_B = "222222222222222222"


async def _create_poll(db, *, guild_id: str = GUILD_A, options=None, **kwargs) -> str:
    poll_id = new_id()
    options = options or [("A", None), ("B", None)]
    await repo.create_poll(
        poll_id=poll_id,
        guild_id=guild_id,
        channel_id="c1",
        creator_id="u1",
        question="晚餐吃什麼",
        options=options,
        **kwargs,
    )
    return poll_id


class TestCreatePoll:
    async def test_writes_poll_row(self, db) -> None:
        poll_id = await _create_poll(db)
        poll = await repo.owned_poll(poll_id, GUILD_A)
        assert poll is not None
        assert poll["question"] == "晚餐吃什麼"
        assert poll["status"] == "open"

    async def test_writes_all_options_atomically(self, db) -> None:
        poll_id = await _create_poll(
            db, options=[("火鍋", None), ("燒肉", None), ("拉麵", None)]
        )
        options = await repo.list_poll_options(poll_id, GUILD_A)
        assert [o["label"] for o in options] == ["火鍋", "燒肉", "拉麵"]

    async def test_options_keep_sort_order(self, db) -> None:
        poll_id = await _create_poll(db, options=[("C", None), ("A", None), ("B", None)])
        options = await repo.list_poll_options(poll_id, GUILD_A)
        assert [o["sort"] for o in options] == [0, 1, 2]
        assert [o["label"] for o in options] == ["C", "A", "B"]

    async def test_time_slot_meta_is_stored(self, db) -> None:
        poll_id = await _create_poll(db, options=[("<t:100:F>", "100")], kind="time_slot")
        options = await repo.list_poll_options(poll_id, GUILD_A)
        assert options[0]["meta"] == "100"

    async def test_flags_are_stored_as_ints(self, db) -> None:
        poll_id = await _create_poll(db, multi=True, anonymous=True, allow_change=False)
        poll = await repo.owned_poll(poll_id, GUILD_A)
        assert poll["multi"] == 1
        assert poll["anonymous"] == 1
        assert poll["allow_change"] == 0

    async def test_description_is_stored_when_given(self, db) -> None:
        poll_id = await _create_poll(db, description="這次要約平日還是假日晚上")
        poll = await repo.owned_poll(poll_id, GUILD_A)
        assert poll["description"] == "這次要約平日還是假日晚上"

    async def test_description_defaults_to_none(self, db) -> None:
        poll_id = await _create_poll(db)
        poll = await repo.owned_poll(poll_id, GUILD_A)
        assert poll["description"] is None


class TestOwnedPollMultiGuildIsolation:
    async def test_wrong_guild_returns_none(self, db) -> None:
        poll_id = await _create_poll(db, guild_id=GUILD_A)
        assert await repo.owned_poll(poll_id, GUILD_B) is None

    async def test_list_options_scoped_to_guild(self, db) -> None:
        poll_id = await _create_poll(db, guild_id=GUILD_A)
        assert await repo.list_poll_options(poll_id, GUILD_B) == []

    async def test_list_votes_scoped_to_guild(self, db) -> None:
        poll_id = await _create_poll(db, guild_id=GUILD_A)
        options = await repo.list_poll_options(poll_id, GUILD_A)
        await repo.cast_vote(poll_id, GUILD_A, 111, [options[0]["id"]], allow_change=True)
        assert await repo.list_poll_votes(poll_id, GUILD_B) == []


class TestCastVote:
    async def test_single_choice_records_vote(self, db) -> None:
        poll_id = await _create_poll(db)
        options = await repo.list_poll_options(poll_id, GUILD_A)

        result = await repo.cast_vote(
            poll_id, GUILD_A, 111, [options[0]["id"]], allow_change=True
        )

        assert result == "ok"
        votes = await repo.list_poll_votes(poll_id, GUILD_A)
        assert [v["option_id"] for v in votes] == [options[0]["id"]]

    async def test_revote_replaces_previous_choice(self, db) -> None:
        poll_id = await _create_poll(db)
        options = await repo.list_poll_options(poll_id, GUILD_A)

        await repo.cast_vote(poll_id, GUILD_A, 111, [options[0]["id"]], allow_change=True)
        await repo.cast_vote(poll_id, GUILD_A, 111, [options[1]["id"]], allow_change=True)

        votes = await repo.list_poll_votes(poll_id, GUILD_A)
        assert [v["option_id"] for v in votes] == [options[1]["id"]]

    async def test_multi_select_replaces_full_set(self, db) -> None:
        poll_id = await _create_poll(
            db, options=[("A", None), ("B", None), ("C", None)], multi=True
        )
        options = await repo.list_poll_options(poll_id, GUILD_A)
        ids = [o["id"] for o in options]

        await repo.cast_vote(poll_id, GUILD_A, 111, [ids[0], ids[1]], allow_change=True)
        await repo.cast_vote(poll_id, GUILD_A, 111, [ids[2]], allow_change=True)

        votes = await repo.list_poll_votes(poll_id, GUILD_A)
        assert [v["option_id"] for v in votes] == [ids[2]]

    async def test_disallowed_change_blocks_second_vote(self, db) -> None:
        poll_id = await _create_poll(db, allow_change=False)
        options = await repo.list_poll_options(poll_id, GUILD_A)

        first = await repo.cast_vote(
            poll_id, GUILD_A, 111, [options[0]["id"]], allow_change=False
        )
        second = await repo.cast_vote(
            poll_id, GUILD_A, 111, [options[1]["id"]], allow_change=False
        )

        assert first == "ok"
        assert second == "locked"
        votes = await repo.list_poll_votes(poll_id, GUILD_A)
        assert [v["option_id"] for v in votes] == [options[0]["id"]]

    async def test_different_users_are_independent_under_allow_change_false(self, db) -> None:
        poll_id = await _create_poll(db, allow_change=False)
        options = await repo.list_poll_options(poll_id, GUILD_A)

        first = await repo.cast_vote(
            poll_id, GUILD_A, 111, [options[0]["id"]], allow_change=False
        )
        second = await repo.cast_vote(
            poll_id, GUILD_A, 222, [options[0]["id"]], allow_change=False
        )

        assert first == "ok"
        assert second == "ok"

    async def test_vote_on_closed_poll_is_rejected(self, db) -> None:
        poll_id = await _create_poll(db)
        options = await repo.list_poll_options(poll_id, GUILD_A)
        await repo.close_poll(poll_id, GUILD_A)

        result = await repo.cast_vote(
            poll_id, GUILD_A, 111, [options[0]["id"]], allow_change=True
        )
        assert result == "closed"

    async def test_vote_on_nonexistent_poll_is_rejected(self, db) -> None:
        result = await repo.cast_vote("does-not-exist", GUILD_A, 111, ["o1"], allow_change=True)
        assert result == "not_found"

    async def test_vote_from_wrong_guild_is_rejected(self, db) -> None:
        poll_id = await _create_poll(db, guild_id=GUILD_A)
        options = await repo.list_poll_options(poll_id, GUILD_A)

        result = await repo.cast_vote(
            poll_id, GUILD_B, 111, [options[0]["id"]], allow_change=True
        )
        assert result == "not_found"


class TestClosePoll:
    async def test_first_close_succeeds(self, db) -> None:
        poll_id = await _create_poll(db)
        assert await repo.close_poll(poll_id, GUILD_A) is True
        poll = await repo.owned_poll(poll_id, GUILD_A)
        assert poll["status"] == "closed"

    async def test_second_close_fails(self, db) -> None:
        poll_id = await _create_poll(db)
        assert await repo.close_poll(poll_id, GUILD_A) is True
        assert await repo.close_poll(poll_id, GUILD_A) is False

    async def test_wrong_guild_cannot_close(self, db) -> None:
        poll_id = await _create_poll(db, guild_id=GUILD_A)
        assert await repo.close_poll(poll_id, GUILD_B) is False
        poll = await repo.owned_poll(poll_id, GUILD_A)
        assert poll["status"] == "open"


class TestSetPollMessage:
    async def test_records_message_id(self, db) -> None:
        poll_id = await _create_poll(db)
        assert await repo.set_poll_message(poll_id, GUILD_A, 999) is True
        poll = await repo.owned_poll(poll_id, GUILD_A)
        assert poll["message_id"] == "999"

    async def test_wrong_guild_does_not_update(self, db) -> None:
        poll_id = await _create_poll(db, guild_id=GUILD_A)
        assert await repo.set_poll_message(poll_id, GUILD_B, 999) is False
