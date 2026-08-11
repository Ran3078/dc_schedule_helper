"""`/settings`／`/timezone` 底層的 repo 函式測試：`update_guild_settings`
（只更新有給的欄位）、`get_user_prefs`／`set_user_tz`（個人時區覆寫）。"""

from __future__ import annotations

from src.db import repo

GUILD_A = "111111111111111111"
USER_1 = "1111"


class TestUpdateGuildSettings:
    async def test_only_updates_given_fields(self, db) -> None:
        await repo.ensure_guild(GUILD_A, "Asia/Taipei")

        await repo.update_guild_settings(GUILD_A, default_tz="Asia/Tokyo")

        settings = await repo.get_guild_settings(GUILD_A)
        assert settings["default_tz"] == "Asia/Tokyo"
        # 沒帶到的欄位維持原本的預設值不變
        assert settings["allow_everyone_ping"] == 0
        assert settings["default_reminders"] == "5"

    async def test_updates_multiple_fields_at_once(self, db) -> None:
        await repo.ensure_guild(GUILD_A, "Asia/Taipei")

        await repo.update_guild_settings(
            GUILD_A, allow_everyone_ping=1, sync_native_events=0, organizer_role_id="r1"
        )

        settings = await repo.get_guild_settings(GUILD_A)
        assert settings["allow_everyone_ping"] == 1
        assert settings["sync_native_events"] == 0
        assert settings["organizer_role_id"] == "r1"

    async def test_can_clear_a_field_by_passing_none(self, db) -> None:
        await repo.ensure_guild(GUILD_A, "Asia/Taipei")
        await repo.update_guild_settings(GUILD_A, organizer_role_id="r1")

        await repo.update_guild_settings(GUILD_A, organizer_role_id=None)

        settings = await repo.get_guild_settings(GUILD_A)
        assert settings["organizer_role_id"] is None

    async def test_no_fields_is_a_noop(self, db) -> None:
        await repo.ensure_guild(GUILD_A, "Asia/Taipei")
        before = await repo.get_guild_settings(GUILD_A)

        await repo.update_guild_settings(GUILD_A)  # 不應拋例外

        after = await repo.get_guild_settings(GUILD_A)
        assert before == after


class TestUserPrefs:
    async def test_get_returns_none_when_not_set(self, db) -> None:
        assert await repo.get_user_prefs(USER_1) is None

    async def test_set_then_get_roundtrip(self, db) -> None:
        await repo.set_user_tz(USER_1, "Asia/Tokyo")

        prefs = await repo.get_user_prefs(USER_1)
        assert prefs is not None
        assert prefs["tz"] == "Asia/Tokyo"

    async def test_set_again_overwrites(self, db) -> None:
        await repo.set_user_tz(USER_1, "Asia/Tokyo")
        await repo.set_user_tz(USER_1, "America/Los_Angeles")

        prefs = await repo.get_user_prefs(USER_1)
        assert prefs["tz"] == "America/Los_Angeles"

    async def test_accepts_int_user_id(self, db) -> None:
        await repo.set_user_tz(123456789, "Asia/Tokyo")
        prefs = await repo.get_user_prefs(123456789)
        assert prefs["tz"] == "Asia/Tokyo"
