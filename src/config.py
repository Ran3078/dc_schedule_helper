"""環境變數設定。缺少必填值時開機即失敗，而非跑到一半才炸。"""

from __future__ import annotations

from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import field_validator
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
    guild_id: int

    turso_database_url: str
    turso_auth_token: str

    # Render 會自動注入 PORT
    port: int = 10000
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
