"""環境變數設定。缺少必填值時開機即失敗，而非跑到一半才炸。"""

from __future__ import annotations

from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    discord_token: str
    discord_app_id: int

    # 開發用伺服器（選填）。
    # 本 bot 支援多伺服器，指令走 global 註冊 —— 但 Discord 對 global 指令有快取，
    # 改動後最長要等 1 小時才會在各伺服器生效。填了這個之後，會額外對該伺服器做一次
    # guild-scoped 同步（即時生效），開發時就不必等。
    # 留空完全不影響功能，只是改指令定義後要等 Discord 傳播。
    dev_guild_id: int | None = Field(
        default=None,
        # 相容舊的 GUILD_ID 命名，避免已填在 Render 上的值失效
        validation_alias=AliasChoices("DEV_GUILD_ID", "GUILD_ID"),
    )

    turso_database_url: str
    turso_auth_token: str

    # Render 會自動注入 PORT
    port: int = 10000
    # 新伺服器加入時的預設時區。各伺服器可用 /settings tz 自行覆寫
    default_tz: str = "Asia/Taipei"
    log_level: str = "INFO"

    @field_validator("default_tz")
    @classmethod
    def _tz_must_be_valid(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"DEFAULT_TZ 不是有效的 IANA 時區名稱: {v!r}") from exc
        return v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """讀取設定（快取）。用函式而非模組層實例，才不會讓測試在 import 時就爆。"""
    return Settings()  # type: ignore[call-arg]
