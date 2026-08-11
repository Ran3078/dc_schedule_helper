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
5. 唯一的例外是 `list_due_reminders()`：排程迴圈是系統層級的背景工作，不是
   代表任何特定伺服器的使用者操作，本來就該一次撈出「全部伺服器」到期的
   提醒。它用 JOIN events 把 guild_id 隨每一列帶出來，之後處理該列時仍然
   用那一列自己的 guild_id，並未略過範圍界定 —— 只是「界定範圍」的層級
   從「呼叫這個函式的當下」改成「處理每一列的當下」。

`_owned_event()` / `_owned_poll()` 就是為第 3 點準備的取用入口。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

from src.db import engine
from src.domain.reminders import DEFAULT_REMINDERS_CSV
from src.lib.clock import now_ms
from src.lib.ids import new_id

Row = dict[str, Any]


# ── 伺服器設定 ────────────────────────────────────────────────────────────


async def ensure_guild(guild_id: int | str, default_tz: str) -> None:
    """確保該伺服器有一列設定。已存在則不動（不會覆寫使用者調過的設定）。

    用 INSERT OR IGNORE 而非先查再插：libsql 對唯一鍵衝突拋的是普通 ValueError
    而非 IntegrityError，靠例外判斷不可靠（見 engine.py 的驅動行為說明）。

    default_reminders 明確帶入 DEFAULT_REMINDERS_CSV，而不是依賴資料表欄位
    本身的 SQL DEFAULT —— SQLite 的 ALTER TABLE 不支援直接改欄位預設值
    （要改只能整張表重建），把預設值的真相留在 Python 常數這裡，之後要調整
    只需要改一個地方，不用碰資料庫結構。
    """
    now = now_ms()
    await engine.execute(
        "INSERT OR IGNORE INTO guild_settings "
        "(guild_id, default_tz, default_reminders, created_at, updated_at) "
        "VALUES (?,?,?,?,?)",
        (str(guild_id), default_tz, DEFAULT_REMINDERS_CSV, now, now),
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
    reminder_offsets_min: Sequence[int] = (),
) -> None:
    """建立活動，並在**同一次 commit**內一併寫入邀請名單與提醒排程。

    活動、邀請名單、提醒要嘛一起成功、要嘛一起不存在 —— 用 engine.run()
    手動組多句 SQL 而非分開呼叫 execute()，避免中途失敗留下「活動建了但
    提醒沒排」這種不一致狀態。

    restrict_rsvp 為 True 時，RsvpButton 只接受落在邀請名單展開後的成員
    （見 domain/invitees.py）；預設 False，任何看得到公告的人都能回覆。

    reminder_offsets_min 是「提前幾分鐘提醒」的清單（例如 [1440, 60, 10]）。
    會排定當下已經來不及的 offset 直接跳過不排（activities starting soon
    的「提前 1 天」提醒沒有意義），而不是硬塞一筆 fire_at_utc 在過去的列，
    那種列進了 scheduler 的逾期補償邏輯只會立刻被判定要跳過，白白佔位。
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

    reminder_params: list[tuple[str, str, int, int]] = []
    for offset in reminder_offsets_min:
        fire_at_utc = starts_at_utc - offset * 60_000
        if fire_at_utc <= now:
            continue
        reminder_params.append((new_id(), event_id, fire_at_utc, offset))

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
        for params in reminder_params:
            conn.execute(
                "INSERT INTO reminders (id, event_id, fire_at_utc, offset_min) "
                "VALUES (?,?,?,?)",
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


async def delete_rsvp(event_id: str, guild_id: int | str, user_id: int | str) -> None:
    """刪掉某人的出席回覆，還原成「未回覆」（M6：原生活動卡片點「取消有興趣」
    時用——取消有興趣的訊號強度跟明確按「不參加」不一樣，不該覆寫成 status='no'，
    直接刪掉這一列讓它回到沒有紀錄的狀態）。

    多伺服器邊界檢查比照 upsert_rsvp 的 WHERE EXISTS 寫法，只是換成 DELETE。
    列不存在（本來就沒回覆過，或 event_id/guild_id 對不上）時單純刪 0 列，
    不當成錯誤。
    """
    await engine.execute(
        "DELETE FROM rsvps WHERE event_id = ? AND user_id = ? "
        "AND EXISTS (SELECT 1 FROM events WHERE id = ? AND guild_id = ?)",
        (event_id, str(user_id), event_id, str(guild_id)),
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


async def set_event_discord_id(
    event_id: str, guild_id: int | str, discord_event_id: int | str
) -> bool:
    """記錄同步出去的原生 Scheduled Event ID（M6），供日後反查
    （`get_event_by_discord_id`）與取消同步（M7 的 `/event cancel`）用。"""
    rowcount = await engine.execute(
        "UPDATE events SET discord_event_id = ?, updated_at = ? WHERE id = ? AND guild_id = ?",
        (str(discord_event_id), now_ms(), event_id, str(guild_id)),
    )
    return rowcount > 0


async def get_event_by_discord_id(discord_event_id: int | str, guild_id: int | str) -> Row | None:
    """依原生 Scheduled Event ID 反查我們的活動列。

    原生活動卡片的「有興趣／取消有興趣」互動事件（`on_scheduled_event_user_add`
    / `_remove`）只給得到 Discord 那邊的 event id，需要這個反查才能知道是
    我們哪一場活動、進而更新對應的 RSVP。
    """
    return await engine.query_one(
        "SELECT * FROM events WHERE discord_event_id = ? AND guild_id = ?",
        (str(discord_event_id), str(guild_id)),
    )


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


# ── 提醒排程 ──────────────────────────────────────────────────────────────
#
# list_due_reminders() 是本檔案開頭第 5 點寫的唯一例外：系統層級的背景
# 排程工作，本來就該一次撈出所有伺服器到期的提醒，不是代表某個使用者操作。


async def list_due_reminders(now_ms_: int, *, limit: int = 50) -> list[Row]:
    """撈出到期但還沒處理的提醒，JOIN events 一次把處理時需要的活動欄位
    （guild_id / channel_id / title / starts_at_utc / location / message_id）
    帶出來，避免每一列提醒都要再查一次活動（N+1）。

    只挑 `events.status = 'scheduled'` 的活動 —— 活動被取消後，它的提醒
    永遠不會再被這個查詢選中，等同自動失效，不需要另外去把 reminders
    也標記掉。
    """
    return await engine.query_all(
        "SELECT r.id, r.event_id, r.fire_at_utc, r.offset_min, "
        "e.guild_id, e.channel_id, e.title, e.starts_at_utc, e.location, "
        "e.message_id "
        "FROM reminders r "
        "JOIN events e ON e.id = r.event_id "
        "WHERE r.state = 'pending' AND r.fire_at_utc <= ? AND e.status = 'scheduled' "
        "ORDER BY r.fire_at_utc ASC LIMIT ?",
        (now_ms_, limit),
    )


async def claim_reminder(reminder_id: str) -> bool:
    """搶下一個提醒的處理權，標成 'sent'。回傳 False 代表已經被別人搶走
    （或根本不是 pending 狀態），不該再發送。

    刻意在**送出 Discord 訊息之前**呼叫（而不是送出之後才標記）：萬一送出
    後、標記前 process 就當掉，寧可漏發一次提醒，也不要因為重啟後重新
    處理同一列而對整個頻道重複發送、重複 tag 一次所有人。
    """
    rowcount = await engine.execute(
        "UPDATE reminders SET state = 'sent', sent_at = ? WHERE id = ? AND state = 'pending'",
        (now_ms(), reminder_id),
    )
    return rowcount > 0


async def skip_reminder(reminder_id: str) -> bool:
    """逾期又不需要補發時，標成 'skipped'。同樣走樂觀鎖，語意上跟
    claim_reminder 是同一種「宣告這一列我處理完了」的動作，只是結果不同。
    """
    rowcount = await engine.execute(
        "UPDATE reminders SET state = 'skipped', sent_at = ? WHERE id = ? AND state = 'pending'",
        (now_ms(), reminder_id),
    )
    return rowcount > 0


async def mark_reminder_failed(reminder_id: str) -> None:
    """claim_reminder 成功後才會呼叫這個 —— 這一列已經是我們獨佔的了
    （state 已經被我們改成 'sent'），所以這裡不需要 WHERE state='pending'
    的樂觀鎖，單純把結果覆寫成 'failed' 以便日後排查「這則提醒為什麼沒發出去」。
    """
    await engine.execute(
        "UPDATE reminders SET state = 'failed' WHERE id = ?",
        (reminder_id,),
    )


# ── 投票 ──────────────────────────────────────────────────────────────────


async def create_poll(
    *,
    poll_id: str,
    guild_id: int | str,
    channel_id: int | str,
    creator_id: int | str,
    question: str,
    options: Sequence[tuple[str, str | None]],
    kind: str = "generic",
    multi: bool = False,
    max_choices: int | None = None,
    anonymous: bool = False,
    allow_change: bool = True,
    closes_at: int | None = None,
    description: str | None = None,
) -> None:
    """建立投票，並在**同一次 commit** 內一併寫入所有選項。

    理由同 create_event：投票跟它的選項要嘛一起成功、要嘛一起不存在，避免
    「投票建了但一個選項都沒有」這種中途失敗留下的不一致狀態。

    options 是 (label, meta) 的序列，依序決定 poll_options.sort；meta 用於
    kind='time_slot' 時存候選時間的 epoch 字串，generic 投票就是 None。
    """
    now = now_ms()
    poll_params = (
        poll_id,
        str(guild_id),
        str(channel_id),
        str(creator_id),
        question,
        kind,
        int(multi),
        max_choices,
        int(anonymous),
        int(allow_change),
        closes_at,
        description,
        now,
    )
    option_params = [
        (new_id(), poll_id, label, meta, sort) for sort, (label, meta) in enumerate(options)
    ]

    def _tx(conn: Any) -> None:
        conn.execute(
            "INSERT INTO polls "
            "(id, guild_id, channel_id, creator_id, question, kind, multi, "
            "max_choices, anonymous, allow_change, closes_at, description, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            poll_params,
        )
        for params in option_params:
            conn.execute(
                "INSERT INTO poll_options (id, poll_id, label, meta, sort) VALUES (?,?,?,?,?)",
                params,
            )
        conn.commit()

    await engine.run(_tx)


async def set_poll_message(poll_id: str, guild_id: int | str, message_id: int | str) -> bool:
    """記錄公告訊息 ID，供投票後即時更新卡片、以及 /poll close 編輯訊息用。"""
    rowcount = await engine.execute(
        "UPDATE polls SET message_id = ? WHERE id = ? AND guild_id = ?",
        (str(message_id), poll_id, str(guild_id)),
    )
    return rowcount > 0


async def list_poll_options(poll_id: str, guild_id: int | str) -> list[Row]:
    """列出投票的選項，JOIN polls 一起限定 guild_id（理由同 list_event_invitees）。"""
    return await engine.query_all(
        "SELECT po.* FROM poll_options po "
        "JOIN polls p ON p.id = po.poll_id "
        "WHERE po.poll_id = ? AND p.guild_id = ? "
        "ORDER BY po.sort ASC",
        (poll_id, str(guild_id)),
    )


async def list_poll_votes(poll_id: str, guild_id: int | str) -> list[Row]:
    """列出投票的所有票，JOIN polls 一起限定 guild_id。"""
    return await engine.query_all(
        "SELECT pv.* FROM poll_votes pv "
        "JOIN polls p ON p.id = pv.poll_id "
        "WHERE pv.poll_id = ? AND p.guild_id = ?",
        (poll_id, str(guild_id)),
    )


async def cast_vote(
    poll_id: str,
    guild_id: int | str,
    user_id: int | str,
    option_ids: Sequence[str],
    *,
    allow_change: bool,
) -> Literal["ok", "locked", "closed", "not_found"]:
    """記錄一次投票：整批替換該使用者在這個投票裡的選擇。

    用「先刪光這個人在這個 poll 的舊票，再依這次選取的 option_ids 全部重新
    插入」而非逐一 toggle —— Select 元件每次互動 Discord 都會回傳「目前
    完整選取的選項清單」，整批替換剛好對應這個互動模型，單選（一個
    option_id）跟複選（多個）共用同一段邏輯。

    allow_change=False 時，若使用者在這個 poll 已經有過投票紀錄，直接拒絕
    這次的變更（回傳 "locked"），不覆蓋原本的票。存在性與開放狀態的檢查
    跟寫入放在同一個交易內，避免「查的時候還開著、寫的時候已經關了」的競態。
    """
    now = now_ms()
    guild_id_s = str(guild_id)
    user_id_s = str(user_id)

    def _tx(conn: Any) -> str:
        row = conn.execute(
            "SELECT status FROM polls WHERE id = ? AND guild_id = ?",
            (poll_id, guild_id_s),
        ).fetchone()
        if row is None:
            return "not_found"
        if row[0] != "open":
            return "closed"

        if not allow_change:
            existing = conn.execute(
                "SELECT 1 FROM poll_votes WHERE poll_id = ? AND user_id = ? LIMIT 1",
                (poll_id, user_id_s),
            ).fetchone()
            if existing is not None:
                return "locked"

        conn.execute(
            "DELETE FROM poll_votes WHERE poll_id = ? AND user_id = ?",
            (poll_id, user_id_s),
        )
        for option_id in option_ids:
            conn.execute(
                "INSERT INTO poll_votes (poll_id, option_id, user_id, voted_at) VALUES (?,?,?,?)",
                (poll_id, option_id, user_id_s, now),
            )
        conn.commit()
        return "ok"

    return await engine.run(_tx)


async def close_poll(poll_id: str, guild_id: int | str) -> bool:
    """關閉投票。樂觀鎖：只能從 'open' 轉 'closed'，重複關閉回傳 False。"""
    rowcount = await engine.execute(
        "UPDATE polls SET status = 'closed' WHERE id = ? AND guild_id = ? AND status = 'open'",
        (poll_id, str(guild_id)),
    )
    return rowcount > 0
