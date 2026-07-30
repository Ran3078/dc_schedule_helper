"""資料存取層。

★ 多伺服器紀律（本專案最容易寫錯、也最難事後補救的一條）★

本 bot 支援多伺服器，因此**每一個查詢都必須以 guild_id 為界**。規則：

1. 任何讀取活動 / 投票的函式，`guild_id` 都是**必填參數**，且必須出現在 WHERE 子句裡。
   不要提供「不分伺服器」的查詢版本 —— 那種函式一旦存在，早晚會有人誤用。
2. `guild_id` 一律來自 `interaction.guild_id`，**絕對不要**從設定檔或全域變數取。
   設定檔裡的 `dev_guild_id` 只用於指令同步，與資料查詢無關。
3. 對子表（rsvps / poll_votes / poll_options / event_invitees / reminders）操作前，
   必須先確認其母體（event / poll）屬於當前 guild。子表本身沒有 guild_id 欄位，
   只靠母體界定範圍 —— 少了這層檢查，A 伺服器的人就能用猜到的 ID 改 B 伺服器的資料。
4. Discord 的 ID 是 64-bit 整數，但 DB 欄位型別是 TEXT。傳入前一律 `str()`，
   否則 `WHERE guild_id = 123` 與存進去的 `'123'` 比不出結果。

`_owned_event()` / `_owned_poll()` 就是為第 3 點準備的取用入口。
"""

from __future__ import annotations

from typing import Any

from src.db import engine
from src.lib.clock import now_ms

Row = dict[str, Any]


# ── 伺服器設定 ────────────────────────────────────────────────────────────


async def ensure_guild(guild_id: int | str, default_tz: str) -> None:
    """確保該伺服器有一列設定。已存在則不動（不會覆寫使用者調過的設定）。

    用 INSERT OR IGNORE 而非先查再插：libsql 對唯一鍵衝突拋的是普通 ValueError
    而非 IntegrityError，靠例外判斷不可靠（見 engine.py 的驅動行為說明）。
    """
    now = now_ms()
    await engine.execute(
        "INSERT OR IGNORE INTO guild_settings "
        "(guild_id, default_tz, created_at, updated_at) VALUES (?,?,?,?)",
        (str(guild_id), default_tz, now, now),
    )


async def get_guild_settings(guild_id: int | str) -> Row | None:
    return await engine.query_one(
        "SELECT * FROM guild_settings WHERE guild_id = ?", (str(guild_id),)
    )


async def count_guilds() -> int:
    return await engine.query_scalar("SELECT COUNT(*) FROM guild_settings") or 0


# ── 母體歸屬檢查 ──────────────────────────────────────────────────────────


async def owned_event(event_id: str, guild_id: int | str) -> Row | None:
    """取得活動，但只在它屬於該伺服器時才回傳。

    所有針對單一活動的操作都應該經過這裡，而不是直接 `WHERE id = ?`。
    """
    return await engine.query_one(
        "SELECT * FROM events WHERE id = ? AND guild_id = ?",
        (event_id, str(guild_id)),
    )


async def owned_poll(poll_id: str, guild_id: int | str) -> Row | None:
    return await engine.query_one(
        "SELECT * FROM polls WHERE id = ? AND guild_id = ?",
        (poll_id, str(guild_id)),
    )
