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

from collections.abc import Sequence
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


# ── 活動 ──────────────────────────────────────────────────────────────────


async def create_event(
    *,
    event_id: str,
    guild_id: int | str,
    channel_id: int | str,
    creator_id: int | str,
    title: str,
    starts_at_utc: int,
    tz: str,
    ends_at_utc: int | None = None,
    location: str | None = None,
    description: str | None = None,
    user_ids: Sequence[int | str] = (),
    role_ids: Sequence[int | str] = (),
    tag_everyone: bool = False,
    restrict_rsvp: bool = False,
) -> None:
    """建立活動，並在**同一次 commit**內一併寫入邀請名單。

    活動與邀請名單要嘛一起成功、要嘛一起不存在 —— 用 engine.run() 手動組多句
    SQL 而非分兩次呼叫 execute()，避免中途失敗留下「活動建了但邀請名單是空的」
    這種不一致狀態。

    restrict_rsvp 為 True 時，RsvpButton 只接受落在邀請名單展開後的成員
    （見 domain/invitees.py）；預設 False，任何看得到公告的人都能回覆。
    """
    now = now_ms()
    event_params = (
        event_id,
        str(guild_id),
        str(channel_id),
        str(creator_id),
        title,
        starts_at_utc,
        ends_at_utc,
        tz,
        location,
        description,
        int(restrict_rsvp),
        now,
        now,
    )

    invitee_params: list[tuple[str, str, str, int]] = [
        (event_id, "user", str(uid), 0) for uid in user_ids
    ]
    invitee_params += [(event_id, "role", str(rid), 0) for rid in role_ids]
    if tag_everyone:
        invitee_params.append((event_id, "everyone", str(guild_id), 0))

    def _tx(conn: Any) -> None:
        conn.execute(
            "INSERT INTO events "
            "(id, guild_id, channel_id, creator_id, title, starts_at_utc, ends_at_utc, "
            "tz, location, description, restrict_rsvp, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            event_params,
        )
        for params in invitee_params:
            # OR IGNORE：理論上同一批選擇不會有重複，但 UserSelect/RoleSelect
            # 若被重複觸發也不該讓整個交易因唯一鍵衝突而炸掉。
            conn.execute(
                "INSERT OR IGNORE INTO event_invitees "
                "(event_id, target_type, target_id, required) VALUES (?,?,?,?)",
                params,
            )
        conn.commit()

    await engine.run(_tx)


async def list_event_invitees(event_id: str, guild_id: int | str) -> list[Row]:
    """列出活動的邀請對象，JOIN events 一起限定 guild_id。

    event_invitees 本身沒有 guild_id 欄位（子表靠母體界定範圍，見本檔案開頭
    的多伺服器紀律說明），用 JOIN 而非「先 owned_event() 再查子表」兩步走，
    可以避免兩步之間 guild_id 打錯導致查到別的伺服器資料的疏漏。
    """
    return await engine.query_all(
        "SELECT ei.* FROM event_invitees ei "
        "JOIN events e ON e.id = ei.event_id "
        "WHERE ei.event_id = ? AND e.guild_id = ?",
        (event_id, str(guild_id)),
    )


async def upsert_rsvp(
    event_id: str,
    guild_id: int | str,
    user_id: int | str,
    status: str,
    *,
    note: str | None = None,
) -> bool:
    """記錄某人的出席回覆。回傳 False 代表活動不存在，或不屬於這個伺服器
    （沒有寫入任何東西）。

    用 `INSERT ... SELECT ... WHERE EXISTS` 而非「先 owned_event() 查一次、
    確認後再另外 INSERT」：後者兩次查詢之間有極小的競態空隙（活動剛好在
    這中間被取消或刪除），前者把歸屬檢查直接做進同一句 SQL、一次往返，
    沒有這個空隙 —— 也是本檔案開頭第 3 點紀律（子表操作前先確認母體歸屬）
    的具體實作方式之一。

    `status` 的合法值由呼叫端保證（見 views_rsvp.py 的 custom_id 樣板本身
    只接受 yes/maybe/no），這裡不重複驗證。
    """
    now = now_ms()
    rowcount = await engine.execute(
        "INSERT INTO rsvps (event_id, user_id, status, note, responded_at) "
        "SELECT ?, ?, ?, ?, ? WHERE EXISTS "
        "(SELECT 1 FROM events WHERE id = ? AND guild_id = ?) "
        "ON CONFLICT(event_id, user_id) DO UPDATE SET "
        "status = excluded.status, note = excluded.note, responded_at = excluded.responded_at",
        (event_id, str(user_id), status, note, now, event_id, str(guild_id)),
    )
    return rowcount > 0


async def list_rsvps(event_id: str, guild_id: int | str) -> list[Row]:
    """列出活動的出席回覆，JOIN events 一起限定 guild_id（理由同 list_event_invitees）。"""
    return await engine.query_all(
        "SELECT r.* FROM rsvps r "
        "JOIN events e ON e.id = r.event_id "
        "WHERE r.event_id = ? AND e.guild_id = ?",
        (event_id, str(guild_id)),
    )


async def set_event_message(event_id: str, guild_id: int | str, message_id: int | str) -> bool:
    """記錄公告訊息 ID，供日後編輯訊息（M3 起的 RSVP 即時更新需要）。

    guild_id 進 WHERE 是刻意的：就算呼叫端傳錯了別的伺服器的 event_id，
    這裡也不會誤更新到別人的活動。
    """
    rowcount = await engine.execute(
        "UPDATE events SET message_id = ?, updated_at = ? WHERE id = ? AND guild_id = ?",
        (str(message_id), now_ms(), event_id, str(guild_id)),
    )
    return rowcount > 0


async def list_events(
    guild_id: int | str,
    *,
    scope: str = "upcoming",
    user_id: int | str | None = None,
    limit: int = 10,
) -> list[Row]:
    """列出活動。scope 決定範圍：

    - "upcoming"：該伺服器所有尚未開始的活動（依時間近到遠）
    - "mine"：僅限 user_id 建立、且尚未開始的活動（需傳 user_id）
    - "all"：不限狀態與時間，依建立時間新到舊（可看到已取消 / 已結束的活動）
    """
    if scope == "mine":
        if user_id is None:
            raise ValueError('scope="mine" 需要提供 user_id')
        return await engine.query_all(
            "SELECT * FROM events WHERE guild_id = ? AND creator_id = ? "
            "AND status = 'scheduled' AND starts_at_utc >= ? "
            "ORDER BY starts_at_utc ASC LIMIT ?",
            (str(guild_id), str(user_id), now_ms(), limit),
        )
    if scope == "all":
        return await engine.query_all(
            "SELECT * FROM events WHERE guild_id = ? ORDER BY created_at DESC LIMIT ?",
            (str(guild_id), limit),
        )
    if scope == "upcoming":
        return await engine.query_all(
            "SELECT * FROM events WHERE guild_id = ? "
            "AND status = 'scheduled' AND starts_at_utc >= ? "
            "ORDER BY starts_at_utc ASC LIMIT ?",
            (str(guild_id), now_ms(), limit),
        )
    raise ValueError(f"未知的 scope: {scope!r}（應為 upcoming/mine/all）")


async def cancel_event(event_id: str, guild_id: int | str) -> bool:
    rowcount = await engine.execute(
        "UPDATE events SET status = 'cancelled', updated_at = ? "
        "WHERE id = ? AND guild_id = ? AND status = 'scheduled'",
        (now_ms(), event_id, str(guild_id)),
    )
    return rowcount > 0
