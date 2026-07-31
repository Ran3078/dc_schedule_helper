"""設定層測試。重點是 dev_guild_id 的選填語意與命名相容。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.config import Settings

# 假 token，格式與真的一致（三段以 '.' 分隔）但無效
FAKE_TOKEN = "MTIzNDU2Nzg5MDEyMzQ1Njc4.GhIjKl.xyz789abcDEF"

BASE_ENV = {
    "DISCORD_TOKEN": FAKE_TOKEN,
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


class TestDiscordTokenCleaning:
    """Discord 對錯誤 token 只回一句 "Improper token has been passed."，
    看不出是複製錯值還是貼上時混到空白。這些檢查把已知的填錯擋在部署之前。
    """

    @pytest.mark.parametrize(
        "raw",
        [
            f"  {FAKE_TOKEN}  ",  # 前後空白
            f"{FAKE_TOKEN}\n",  # 貼上時帶到換行
            f'"{FAKE_TOKEN}"',  # 自己加了引號
            f"'{FAKE_TOKEN}'",
        ],
        ids=["空白", "換行", "雙引號", "單引號"],
    )
    def test_strips_whitespace_and_quotes(
        self, monkeypatch: pytest.MonkeyPatch, raw: str
    ) -> None:
        assert _make(monkeypatch, DISCORD_TOKEN=raw).discord_token == FAKE_TOKEN

    def test_rejects_application_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """把 Application ID 誤填成 token 是最常見的錯，且純數字必定不是 token。"""
        with pytest.raises(ValidationError, match="Application ID"):
            _make(monkeypatch, DISCORD_TOKEN="1234567890123456789")

    def test_rejects_empty_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with pytest.raises(ValidationError, match="空的"):
            _make(monkeypatch, DISCORD_TOKEN="   ")

    def test_rejects_token_without_dots(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Bot token 必定是三段以 '.' 分隔，沒有句點的值不可能是 token。"""
        with pytest.raises(ValidationError, match="不含任何"):
            _make(monkeypatch, DISCORD_TOKEN="aBcDeF123456")

    def test_names_client_secret_for_32_char_value(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Client Secret 剛好 32 字元英數字 —— 實際踩過的坑，直接點名。"""
        with pytest.raises(ValidationError, match="Client Secret"):
            _make(monkeypatch, DISCORD_TOKEN="a" * 32)

    def test_warns_but_accepts_unusual_segment_count(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """段數不對只警告不硬擋 —— Discord 日後可能調整 token 格式。"""
        with caplog.at_level("WARNING"):
            settings = _make(monkeypatch, DISCORD_TOKEN="aaa.bbb")
        assert settings.discord_token == "aaa.bbb"
        assert "不太像 bot token" in caplog.text


class TestTursoValueCleaning:
    def test_strips_turso_credentials(self, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = _make(
            monkeypatch,
            TURSO_AUTH_TOKEN="  eyJhbGci.abc  \n",
            TURSO_DATABASE_URL=' "libsql://example.turso.io" ',
        )
        assert settings.turso_auth_token == "eyJhbGci.abc"
        assert settings.turso_database_url == "libsql://example.turso.io"


class TestTimezoneValidation:
    def test_rejects_invalid_timezone(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with pytest.raises(ValidationError, match="IANA"):
            _make(monkeypatch, DEFAULT_TZ="Mars/Olympus_Mons")

    def test_accepts_valid_timezone(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert _make(monkeypatch, DEFAULT_TZ="Europe/London").default_tz == "Europe/London"
