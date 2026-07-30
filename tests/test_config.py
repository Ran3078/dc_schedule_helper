"""設定層測試。重點是 dev_guild_id 的選填語意與命名相容。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.config import Settings

BASE_ENV = {
    "DISCORD_TOKEN": "t",
    "DISCORD_APP_ID": "123456789012345678",
    "TURSO_DATABASE_URL": "libsql://example.turso.io",
    "TURSO_AUTH_TOKEN": "tok",
}


def _make(monkeypatch: pytest.MonkeyPatch, **extra: str) -> Settings:
    """在乾淨的環境下建立 Settings。

    _env_file=None 是必要的：否則開發者本機的 .env 會滲進測試，
    讓「未設定 DEV_GUILD_ID」這類案例在有 .env 的機器上失敗。
    """
    for key in (*BASE_ENV, "DEV_GUILD_ID", "GUILD_ID", "PORT", "DEFAULT_TZ"):
        monkeypatch.delenv(key, raising=False)
    for key, value in {**BASE_ENV, **extra}.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)  # type: ignore[call-arg]


class TestDevGuildId:
    def test_is_optional(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """不設也要能開機 —— 多伺服器模式下它只是開發便利，不是必要設定。"""
        assert _make(monkeypatch).dev_guild_id is None

    def test_reads_dev_guild_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert _make(monkeypatch, DEV_GUILD_ID="42").dev_guild_id == 42

    def test_accepts_legacy_guild_id_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """相容早期的 GUILD_ID 命名，避免已填在 Render 上的值失效。"""
        assert _make(monkeypatch, GUILD_ID="42").dev_guild_id == 42

    def test_dev_guild_id_wins_over_legacy_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = _make(monkeypatch, DEV_GUILD_ID="1", GUILD_ID="2")
        assert settings.dev_guild_id == 1


class TestRequiredFields:
    def test_missing_token_fails_at_startup(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """設定不全要開機即失敗，而不是跑到一半才炸。"""
        for key in (*BASE_ENV, "DEV_GUILD_ID", "GUILD_ID"):
            monkeypatch.delenv(key, raising=False)
        for key, value in BASE_ENV.items():
            if key != "DISCORD_TOKEN":
                monkeypatch.setenv(key, value)

        with pytest.raises(ValidationError, match="discord_token"):
            Settings(_env_file=None)  # type: ignore[call-arg]


class TestTimezoneValidation:
    def test_rejects_invalid_timezone(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with pytest.raises(ValidationError, match="IANA"):
            _make(monkeypatch, DEFAULT_TZ="Mars/Olympus_Mons")

    def test_accepts_valid_timezone(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert _make(monkeypatch, DEFAULT_TZ="Europe/London").default_tz == "Europe/London"
