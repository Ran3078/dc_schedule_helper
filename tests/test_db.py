"""DB 層測試。

這裡守的是幾個「錯了會很難查」的行為：
  * migration 必須冪等（Render 每次 deploy 都會重跑）
  * query_* 必須回傳欄位名正確的 dict
  * execute() 的 rowcount 必須能當樂觀鎖用 —— 提醒不重複派送全靠它
  * 單選投票的「先刪後插」語意
"""

from __future__ import annotations

import pytest

from src.db.migrate import _split_statements, run_migrations
from src.lib.ids import build_custom_id, new_id, parse_custom_id

NOW = 1785000000000  # 固定 epoch，避免測試受當下時間影響

INSERT_POLL = (
    "INSERT INTO polls (id, guild_id, channel_id, question, multi, created_at) "
    "VALUES (?,?,?,?,?,?)"
)
INSERT_OPTION = "INSERT INTO poll_options (id, poll_id, label, sort) VALUES (?,?,?,?)"
INSERT_VOTE = (
    "INSERT INTO poll_votes (poll_id, option_id, user_id, voted_at) VALUES (?,?,?,?)"
)

EXPECTED_TABLES = {
    "guild_settings",
    "user_prefs",
    "events",
    "event_invitees",
    "rsvps",
    "polls",
    "poll_options",
    "poll_votes",
    "reminders",
    "_migrations",
}


async def _insert_event(db, event_id: str = "evt0000001") -> None:
    await db.execute(
        "INSERT INTO events (id, guild_id, channel_id, creator_id, title, "
        "starts_at_utc, tz, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (event_id, "g1", "c1", "u1", "週五團練", NOW, "Asia/Taipei", NOW, NOW),
    )


class TestMigrations:
    def test_split_statements_strips_comments(self) -> None:
        sql = "-- 註解\nCREATE TABLE a (id TEXT);\n-- 又一個註解\nCREATE TABLE b (id TEXT);\n"
        stmts = _split_statements(sql)
        assert len(stmts) == 2
        assert all(not s.lstrip().startswith("--") for s in stmts)
        assert all(s.strip() for s in stmts)

    async def test_creates_all_tables(self, db) -> None:
        rows = await db.query_all("SELECT name FROM sqlite_master WHERE type='table'")
        assert EXPECTED_TABLES <= {r["name"] for r in rows}

    async def test_creates_scheduler_index(self, db) -> None:
        """提醒 tick 每 30 秒撈一次待發清單，沒這個索引會全表掃描。"""
        rows = await db.query_all("SELECT name FROM sqlite_master WHERE type='index'")
        assert "idx_reminders_due" in {r["name"] for r in rows}

    async def test_is_idempotent(self, db) -> None:
        """Render 每次 deploy 都跑 migration，重跑不能出錯也不能重複記錄。"""
        await run_migrations()
        await run_migrations()
        assert await db.query_scalar("SELECT COUNT(*) FROM _migrations") == 1


class TestQueries:
    async def test_query_one_returns_dict_with_column_names(self, db) -> None:
        await _insert_event(db)
        row = await db.query_one("SELECT * FROM events WHERE id=?", ("evt0000001",))
        assert isinstance(row, dict)
        assert row["title"] == "週五團練"
        assert row["starts_at_utc"] == NOW

    async def test_unset_optional_columns_are_none(self, db) -> None:
        """地點與活動內容都是選填，沒填時要是 None 而不是空字串。"""
        await _insert_event(db)
        row = await db.query_one(
            "SELECT location, description FROM events WHERE id=?", ("evt0000001",)
        )
        assert row is not None
        assert row["location"] is None
        assert row["description"] is None

    async def test_query_one_returns_none_when_missing(self, db) -> None:
        assert await db.query_one("SELECT * FROM events WHERE id=?", ("nope",)) is None

    async def test_query_all_returns_empty_list_when_no_rows(self, db) -> None:
        assert await db.query_all("SELECT * FROM events") == []

    async def test_transaction_applies_all_statements(self, db) -> None:
        await _insert_event(db)
        await db.transaction([
            ("UPDATE events SET location=? WHERE id=?", ("語音頻道", "evt0000001")),
            ("UPDATE events SET description=? WHERE id=?", ("打完第三章", "evt0000001")),
        ])
        row = await db.query_one(
            "SELECT location, description FROM events WHERE id=?", ("evt0000001",)
        )
        assert row["location"] == "語音頻道"
        assert row["description"] == "打完第三章"

    async def test_execute_many_inserts_all(self, db) -> None:
        await db.execute(INSERT_POLL, ("pol1", "g1", "c1", "哪天好？", 0, NOW))
        await db.execute_many(
            INSERT_OPTION,
            [("o1", "pol1", "週五", 0), ("o2", "pol1", "週六", 1), ("o3", "pol1", "週日", 2)],
        )
        count = await db.query_scalar(
            "SELECT COUNT(*) FROM poll_options WHERE poll_id=?", ("pol1",)
        )
        assert count == 3

    async def test_execute_many_tolerates_empty_batch(self, db) -> None:
        await db.execute_many(INSERT_OPTION, [])

    async def test_reconnects_after_close(self, db) -> None:
        """Render 休眠喚醒後連線可能已死，engine 必須能自己重建。"""
        await db.close()
        assert await db.query_scalar("SELECT 1") == 1


class TestOptimisticLock:
    """提醒派送的防重複機制。這是 M4 排程正確性的地基。"""

    async def test_claim_succeeds_once_only(self, db) -> None:
        await _insert_event(db)
        await db.execute(
            "INSERT INTO reminders (id, event_id, fire_at_utc, offset_min) VALUES (?,?,?,?)",
            ("rem1", "evt0000001", NOW - 60_000, 10),
        )
        claim_sql = "UPDATE reminders SET state='sent', sent_at=? WHERE id=? AND state='pending'"

        assert await db.execute(claim_sql, (NOW, "rem1")) == 1, "第一次應搶到"
        assert await db.execute(claim_sql, (NOW, "rem1")) == 0, "第二次應搶不到，否則會重複發提醒"


class TestSingleChoiceVoting:
    async def test_revote_replaces_previous_choice(self, db) -> None:
        """單選：改票要覆蓋，不能累加成兩票。"""
        await db.execute(INSERT_POLL, ("pol1", "g1", "c1", "哪天好？", 0, NOW))
        await db.execute_many(
            INSERT_OPTION, [("o1", "pol1", "週五", 0), ("o2", "pol1", "週六", 1)]
        )

        def _vote(conn, option_id: str) -> None:
            conn.execute("DELETE FROM poll_votes WHERE poll_id=? AND user_id=?", ("pol1", "u1"))
            conn.execute(INSERT_VOTE, ("pol1", option_id, "u1", NOW))
            conn.commit()

        await db.run(lambda c: _vote(c, "o1"))
        await db.run(lambda c: _vote(c, "o2"))

        rows = await db.query_all(
            "SELECT option_id FROM poll_votes WHERE poll_id=? AND user_id=?", ("pol1", "u1")
        )
        assert [r["option_id"] for r in rows] == ["o2"]

    async def test_duplicate_vote_is_rejected_by_pk(self, db) -> None:
        """複選 toggle 靠 PK 防止同一人對同一選項投兩次。

        順帶釘住一個 libsql 0.1.11 的驅動行為：唯一鍵衝突拋的是普通 ValueError，
        不是 DBAPI 的 IntegrityError。若哪天驅動改成標準例外，這個測試會失敗提醒我們
        —— 屆時可以把 upsert 相關的錯誤處理寫得更精確。
        """
        await db.execute(INSERT_POLL, ("pol1", "g1", "c1", "可以哪幾天？", 1, NOW))
        await db.execute(INSERT_OPTION, ("o1", "pol1", "週五", 0))
        await db.execute(INSERT_VOTE, ("pol1", "o1", "u1", NOW))

        with pytest.raises(ValueError, match="UNIQUE constraint failed"):
            await db.execute(INSERT_VOTE, ("pol1", "o1", "u1", NOW))

    async def test_insert_or_ignore_is_the_safe_upsert_path(self, db) -> None:
        """因為衝突例外型別不可靠，重複投票一律走 SQL 層的 INSERT OR IGNORE。"""
        await db.execute(INSERT_POLL, ("pol1", "g1", "c1", "可以哪幾天？", 1, NOW))
        await db.execute(INSERT_OPTION, ("o1", "pol1", "週五", 0))
        insert_or_ignore = INSERT_VOTE.replace("INSERT INTO", "INSERT OR IGNORE INTO")

        assert await db.execute(insert_or_ignore, ("pol1", "o1", "u1", NOW)) == 1
        assert await db.execute(insert_or_ignore, ("pol1", "o1", "u1", NOW)) == 0

        count = await db.query_scalar("SELECT COUNT(*) FROM poll_votes WHERE poll_id=?", ("pol1",))
        assert count == 1


class TestIds:
    def test_new_id_is_unique_and_fixed_length(self) -> None:
        ids = {new_id() for _ in range(5000)}
        assert len(ids) == 5000
        assert all(len(i) == 10 for i in ids)

    def test_new_id_avoids_confusable_characters(self) -> None:
        """去掉 0/O/1/l/I，使用者才能照著 /event info <id> 手打。"""
        joined = "".join(new_id() for _ in range(500))
        assert not set(joined) & set("01lOI")

    def test_custom_id_fits_discord_limit(self) -> None:
        cid = build_custom_id("ev", "rsvp", new_id(), "yes")
        assert len(cid) <= 100
        assert parse_custom_id(cid)[:2] == ["ev", "rsvp"]

    def test_custom_id_rejects_separator_in_parts(self) -> None:
        with pytest.raises(ValueError):
            build_custom_id("ev", "a:b")

    def test_custom_id_rejects_overlong(self) -> None:
        with pytest.raises(ValueError):
            build_custom_id("ev", "x" * 200)
