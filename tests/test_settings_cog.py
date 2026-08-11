"""`/settings`／`/timezone` 測試——都拆了 `_impl`，直接呼叫、不透過
app_commands 裝飾器（比照 events.py／polls.py 的拆法）。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from src.bot.cogs.settings import Settings, Timezone
from src.db import repo

GUILD_ID = 111111111111111111
USER_ID = 222222222222222222
ROLE_ID = 333333333333333333
CHANNEL_ID = 444444444444444444


def _make_interaction(*, user_id: int = USER_ID) -> MagicMock:
    interaction = MagicMock()
    interaction.guild_id = GUILD_ID
    interaction.user = MagicMock(id=user_id)
    interaction.response = AsyncMock()
    return interaction


class TestSettingsImpl:
    async def test_only_updates_given_fields(self, db) -> None:
        cog = Settings(MagicMock())
        interaction = _make_interaction()

        await cog._settings_impl(interaction, None, "Asia/Tokyo", None, None, False, None, None)

        settings = await repo.get_guild_settings(GUILD_ID)
        assert settings["default_tz"] == "Asia/Tokyo"
        # 沒帶到的欄位維持預設值
        assert settings["allow_everyone_ping"] == 0
        assert settings["default_reminders"] == "5"
        interaction.response.send_message.assert_awaited_once()
        args, kwargs = interaction.response.send_message.call_args
        assert kwargs["ephemeral"] is True
        assert "預設時區" in args[0]

    async def test_updates_channel(self, db) -> None:
        cog = Settings(MagicMock())
        interaction = _make_interaction()
        channel = MagicMock(id=CHANNEL_ID)
        channel.mention = f"<#{CHANNEL_ID}>"

        await cog._settings_impl(interaction, channel, None, None, None, False, None, None)

        settings = await repo.get_guild_settings(GUILD_ID)
        assert settings["announce_channel_id"] == str(CHANNEL_ID)

    async def test_organizer_role_is_set(self, db) -> None:
        cog = Settings(MagicMock())
        interaction = _make_interaction()
        role = MagicMock(id=ROLE_ID)
        role.mention = f"<@&{ROLE_ID}>"

        await cog._settings_impl(interaction, None, None, None, role, False, None, None)

        settings = await repo.get_guild_settings(GUILD_ID)
        assert settings["organizer_role_id"] == str(ROLE_ID)

    async def test_clear_organizer_role_wins_over_organizer_role_param(self, db) -> None:
        await repo.ensure_guild(GUILD_ID, "Asia/Taipei")
        await repo.update_guild_settings(GUILD_ID, organizer_role_id=str(ROLE_ID))
        cog = Settings(MagicMock())
        interaction = _make_interaction()
        role = MagicMock(id=ROLE_ID)

        await cog._settings_impl(interaction, None, None, None, role, True, None, None)

        settings = await repo.get_guild_settings(GUILD_ID)
        assert settings["organizer_role_id"] is None

    async def test_reminders_stored_and_parsed_preview_shown(self, db) -> None:
        cog = Settings(MagicMock())
        interaction = _make_interaction()

        await cog._settings_impl(interaction, None, None, "1440,60,10", None, False, None, None)

        settings = await repo.get_guild_settings(GUILD_ID)
        assert settings["default_reminders"] == "1440,60,10"
        args, _ = interaction.response.send_message.call_args
        assert "1440" in args[0]
        assert "60" in args[0]
        assert "10" in args[0]

    async def test_reminders_with_unparseable_value_shows_warning(self, db) -> None:
        cog = Settings(MagicMock())
        interaction = _make_interaction()

        await cog._settings_impl(interaction, None, None, "abc,-5,0", None, False, None, None)

        args, _ = interaction.response.send_message.call_args
        assert "解析不出任何有效值" in args[0]

    async def test_everyone_ping_toggle(self, db) -> None:
        cog = Settings(MagicMock())
        interaction = _make_interaction()

        await cog._settings_impl(interaction, None, None, None, None, False, True, None)

        settings = await repo.get_guild_settings(GUILD_ID)
        assert settings["allow_everyone_ping"] == 1

    async def test_sync_native_events_toggle(self, db) -> None:
        cog = Settings(MagicMock())
        interaction = _make_interaction()

        await cog._settings_impl(interaction, None, None, None, None, False, None, False)

        settings = await repo.get_guild_settings(GUILD_ID)
        assert settings["sync_native_events"] == 0

    async def test_invalid_tz_rejected_without_writing(self, db) -> None:
        cog = Settings(MagicMock())
        interaction = _make_interaction()

        await cog._settings_impl(
            interaction, None, "Not/A_Real_Zone", None, None, False, None, None
        )

        args, kwargs = interaction.response.send_message.call_args
        assert "不是合法的時區字串" in args[0]
        assert kwargs["ephemeral"] is True
        # ensure_guild 都還沒被呼叫，guild_settings 應該不存在
        assert await repo.get_guild_settings(GUILD_ID) is None

    async def test_no_params_given_is_a_noop(self, db) -> None:
        cog = Settings(MagicMock())
        interaction = _make_interaction()

        await cog._settings_impl(interaction, None, None, None, None, False, None, None)

        args, kwargs = interaction.response.send_message.call_args
        assert "沒有帶任何要調整的參數" in args[0]
        assert kwargs["ephemeral"] is True
        assert await repo.get_guild_settings(GUILD_ID) is None

    async def test_self_heals_missing_guild_row(self, db) -> None:
        """guild_settings 這一列理論上 on_guild_join 就會建好，這裡確認就算
        沒有也不會炸掉，而是自動補上。"""
        assert await repo.get_guild_settings(GUILD_ID) is None
        cog = Settings(MagicMock())
        interaction = _make_interaction()

        await cog._settings_impl(interaction, None, "Asia/Taipei", None, None, False, None, None)

        assert await repo.get_guild_settings(GUILD_ID) is not None


class TestTimezoneSetImpl:
    async def test_valid_tz_is_stored(self, db) -> None:
        cog = Timezone(MagicMock())
        interaction = _make_interaction()

        await cog._set_impl(interaction, "Asia/Tokyo")

        prefs = await repo.get_user_prefs(USER_ID)
        assert prefs["tz"] == "Asia/Tokyo"
        args, kwargs = interaction.response.send_message.call_args
        assert "Asia/Tokyo" in args[0]
        assert kwargs["ephemeral"] is True

    async def test_invalid_tz_is_rejected(self, db) -> None:
        cog = Timezone(MagicMock())
        interaction = _make_interaction()

        await cog._set_impl(interaction, "Not/A_Real_Zone")

        args, kwargs = interaction.response.send_message.call_args
        assert "不是合法的時區字串" in args[0]
        assert kwargs["ephemeral"] is True
        assert await repo.get_user_prefs(USER_ID) is None

    async def test_overwrites_existing_preference(self, db) -> None:
        cog = Timezone(MagicMock())
        interaction = _make_interaction()

        await cog._set_impl(interaction, "Asia/Tokyo")
        await cog._set_impl(interaction, "America/Los_Angeles")

        prefs = await repo.get_user_prefs(USER_ID)
        assert prefs["tz"] == "America/Los_Angeles"
