"""多伺服器隔離測試。

這些測試守的是「A 伺服器的資料絕不能洩漏到 B 伺服器」。往後每加一個查詢函式，
都應該在這裡補一條對應的隔離測試 —— 這類漏洞不會讓程式報錯，只會安靜地把
別人的活動列給你看，靠人工 review 很難抓。
"""

from __future__ import annotations

from src.db import repo
from src.lib.clock import now_ms

GUILD_A = "111111111111111111"
GUILD_B = "222222222222222222"

NOW = 1785000000000


async def _insert_event(db, event_id: str, guild_id: str, title: str) -> None:
    await db.execute(
        "INSERT INTO events (id, guild_id, channel_id, creator_id, title, "
        "starts_at_utc, tz, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (event_id, guild_id, "c1", "u1", title, NOW, "Asia/Taipei", NOW, NOW),
    )


class TestGuildSettings:
    async def test_ensure_guild_creates_row(self, db) -> None:
        await repo.ensure_guild(GUILD_A, "Asia/Taipei")
        settings = await repo.get_guild_settings(GUILD_A)
        assert settings is not None
        assert settings["default_tz"] == "Asia/Taipei"

    async def test_ensure_guild_is_idempotent(self, db) -> None:
        """on_ready 會因重連而多次觸發，重跑不能出錯。"""
        await repo.ensure_guild(GUILD_A, "Asia/Taipei")
        await repo.ensure_guild(GUILD_A, "Asia/Taipei")
        assert await repo.count_guilds() == 1

    async def test_ensure_guild_does_not_overwrite_user_changes(self, db) -> None:
        """重啟不能把使用者調過的設定重置回預設值。"""
        await repo.ensure_guild(GUILD_A, "Asia/Taipei")
        await db.execute(
            "UPDATE guild_settings SET default_tz=?, allow_everyone_ping=1 WHERE guild_id=?",
            ("America/New_York", GUILD_A),
        )

        await repo.ensure_guild(GUILD_A, "Asia/Taipei")

        settings = await repo.get_guild_settings(GUILD_A)
        assert settings["default_tz"] == "America/New_York"
        assert settings["allow_everyone_ping"] == 1

    async def test_guilds_have_independent_settings(self, db) -> None:
        await repo.ensure_guild(GUILD_A, "Asia/Taipei")
        await repo.ensure_guild(GUILD_B, "Asia/Taipei")
        await db.execute(
            "UPDATE guild_settings SET default_tz=? WHERE guild_id=?",
            ("Europe/London", GUILD_B),
        )

        a = await repo.get_guild_settings(GUILD_A)
        b = await repo.get_guild_settings(GUILD_B)
        assert a["default_tz"] == "Asia/Taipei"
        assert b["default_tz"] == "Europe/London"

    async def test_accepts_int_guild_id(self, db) -> None:
        """Discord 給的 guild.id 是 int，DB 欄位是 TEXT —— repo 必須自己轉。"""
        await repo.ensure_guild(int(GUILD_A), "Asia/Taipei")
        assert await repo.get_guild_settings(int(GUILD_A)) is not None
        assert await repo.get_guild_settings(GUILD_A) is not None


class TestEventIsolation:
    async def test_owned_event_returns_event_from_same_guild(self, db) -> None:
        await _insert_event(db, "evtA", GUILD_A, "A 的團練")
        found = await repo.owned_event("evtA", GUILD_A)
        assert found is not None
        assert found["title"] == "A 的團練"

    async def test_owned_event_hides_other_guilds_event(self, db) -> None:
        """就算 ID 猜對了，也不能拿到別的伺服器的活動。"""
        await _insert_event(db, "evtA", GUILD_A, "A 的團練")
        assert await repo.owned_event("evtA", GUILD_B) is None

    async def test_listing_is_scoped_to_guild(self, db) -> None:
        await _insert_event(db, "evtA1", GUILD_A, "A1")
        await _insert_event(db, "evtA2", GUILD_A, "A2")
        await _insert_event(db, "evtB1", GUILD_B, "B1")

        rows = await db.query_all(
            "SELECT id FROM events WHERE guild_id = ? ORDER BY id", (GUILD_A,)
        )
        assert [r["id"] for r in rows] == ["evtA1", "evtA2"]

    async def test_same_title_in_different_guilds_is_fine(self, db) -> None:
        await _insert_event(db, "evtA", GUILD_A, "週五團練")
        await _insert_event(db, "evtB", GUILD_B, "週五團練")
        assert await repo.owned_event("evtA", GUILD_A) is not None
        assert await repo.owned_event("evtB", GUILD_B) is not None


class TestPollIsolation:
    async def test_owned_poll_hides_other_guilds_poll(self, db) -> None:
        await db.execute(
            "INSERT INTO polls (id, guild_id, channel_id, question, created_at) "
            "VALUES (?,?,?,?,?)",
            ("polA", GUILD_A, "c1", "哪天好？", NOW),
        )
        assert await repo.owned_poll("polA", GUILD_A) is not None
        assert await repo.owned_poll("polA", GUILD_B) is None


class TestClock:
    def test_now_ms_is_epoch_milliseconds(self) -> None:
        value = now_ms()
        # 2020-01-01 ~ 2100-01-01 的毫秒區間，足以抓到「秒 vs 毫秒」的單位錯誤
        assert 1_577_836_800_000 < value < 4_102_444_800_000
