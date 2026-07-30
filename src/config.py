"""環境變數設定。缺少必填值時開機即失敗，而非跑到一半才炸。"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger(__name__)


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

    @field_validator("discord_token", mode="before")
    @classmethod
    def _clean_discord_token(cls, v: Any) -> Any:
        """在送去 Discord 被打回 401 之前，先擋掉最常見的填錯。

        Discord 對錯誤的 token 只回一句 "Improper token has been passed."，
        看不出是複製錯值、還是貼上時混到空白。這裡把已知的錯法變成清楚的訊息。
        """
        if not isinstance(v, str):
            return v

        # 貼進 Render 環境變數時很容易混到換行或前後空白，Discord 會直接拒收
        token = v.strip().strip("\"'")

        if not token:
            raise ValueError("DISCORD_TOKEN 是空的")

        # Application ID 是純數字，是最常被誤填成 token 的值
        if token.isdigit():
            raise ValueError(
                "DISCORD_TOKEN 看起來是 Application ID（純數字）。"
                "Bot token 要去 Developer Portal → Bot → Reset Token 取得"
            )

        # Bot token 的格式是三段以 '.' 分隔的字串。不硬性擋（Discord 可能改格式），
        # 但明顯不像的話先警告，免得又浪費一次 deploy。
        if token.count(".") != 2:
            log.warning(
                "DISCORD_TOKEN 格式不像 bot token（預期三段以 '.' 分隔，實際有 %d 個 '.'）。"
                "是不是複製到 Client Secret 了？",
                token.count("."),
            )

        return token

    @field_validator("turso_auth_token", "turso_database_url", mode="before")
    @classmethod
    def _strip_turso_values(cls, v: Any) -> Any:
        return v.strip().strip("\"'") if isinstance(v, str) else v

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
