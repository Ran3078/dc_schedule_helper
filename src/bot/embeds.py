"""活動公告 Embed。"""

from __future__ import annotations

from typing import Any

import discord

from src.domain.polls import build_tally
from src.domain.rsvp import RsvpSummary
from src.lib.timeparse import discord_timestamp

Row = dict[str, Any]

STATUS_LABELS = {
    "scheduled": ("📅", discord.Colour.blurple()),
    "cancelled": ("🚫", discord.Colour.dark_grey()),
    "completed": ("✅", discord.Colour.green()),
}

# Discord embed 單一欄位值硬上限 1024 字元，留一點餘裕避免壓線失敗
_MAX_FIELD_LEN = 1000


def _display_name(guild: discord.Guild | None, user_id: str | int) -> str:
    """優先顯示伺服器暱稱（`member.display_name`）。

    `<@id>` mention 標記寫在 embed 欄位裡時，Discord**不保證**每個客戶端都會
    解析成好讀的「@使用者名稱」——那是純粹靠客戶端本機快取的 best-effort
    行為，桌面版通常快取得比較完整，手機版/網頁版常常沒快取到，同一則
    公告會在不同平台上顯示成天差地遠的樣子（沒快取到的直接顯示原始的
    `<@1234567890>` 文字）。這跟訊息 content 裡的 mention 不同——那些
    Discord 伺服器端會把使用者資料整包附進訊息回傳，保證每個客戶端都能
    正確解析（見 `lib/mentions.py` 為什麼推播通知要走 content 而非 embed）。

    因此這裡改用固定的純文字暱稱，不依賴客戶端本機快取，三個平台顯示
    保證一致。代價是這些名字不再是可點擊跳轉個人頁面的 mention。

    `guild` 沒給、或該使用者不在 guild 的成員快取裡（已離開伺服器、或
    bot 剛啟動還沒抓到完整成員列表）時，退回原本的 `<@id>` mention 標記
    ——這種情況下至少讓 Discord 自己盡力解析，好過完全顯示不出任何資訊。
    """
    if guild is not None:
        member = guild.get_member(int(user_id))
        if member is not None:
            return member.display_name
    return f"<@{user_id}>"


def _role_display_name(guild: discord.Guild | None, role_id: str | int) -> str:
    """身分組版的 `_display_name`，理由相同。"""
    if guild is not None:
        role = guild.get_role(int(role_id))
        if role is not None:
            return f"@{role.name}"
    return f"<@&{role_id}>"


def _format_user_mentions(user_ids: list[int], guild: discord.Guild | None = None) -> str:
    """把使用者 ID 清單轉成顯示用字串，超過欄位字元上限時截斷並註明剩餘人數。

    活動邀請幾十上百人時，光是「參加」清單就可能塞不進 Discord embed 單一
    欄位的 1024 字元上限 —— 寧可清單不完整也不要讓整個 API 呼叫因超字數失敗。
    """
    if not user_ids:
        return "（無）"

    names = [_display_name(guild, uid) for uid in user_ids]
    text = "、".join(names)
    if len(text) <= _MAX_FIELD_LEN:
        return text

    kept: list[str] = []
    length = 0
    for name in names:
        addition = len(name) + (1 if kept else 0)  # 「、」分隔符的長度
        if length + addition > _MAX_FIELD_LEN:
            break
        kept.append(name)
        length += addition
    return "、".join(kept) + f" …等 {len(names) - len(kept)} 人"


# 位置代碼的圖示，純視覺區分（見 domain/roles.py 的 POSITIONS）。
_POSITION_ICONS = {
    "MT": "🛡️", "ST": "🛡️",
    "H1": "💚", "H2": "💚",
    "D1": "⚔️", "D2": "⚔️",
    "D3": "🏹",
    "D4": "🔮",
}


def _format_role_slot_field(
    slot: Row, signups: list[Row], guild: discord.Guild | None = None
) -> tuple[str, str]:
    """組單一位置的 embed 欄位 (name, value)。`signups` 是這個位置（同一個
    `role_slot_id`）的所有報名列，confirmed 理論上最多 1 筆，候補可能多筆
    ——依 `signed_up_at` 排序讓候補順序在畫面上跟遞補順序一致。
    """
    confirmed = [s for s in signups if not s["waitlisted"]]
    waitlisted = sorted(
        (s for s in signups if s["waitlisted"]), key=lambda s: s["signed_up_at"]
    )

    icon = _POSITION_ICONS.get(slot["position"], "🔸")
    name = f"{icon} {slot['position']}（{len(confirmed)}/1）"

    lines = (
        [f"{_display_name(guild, s['user_id'])}（{s['job']}）" for s in confirmed]
        if confirmed
        else ["（尚無人選）"]
    )
    if waitlisted:
        lines.append(
            "候補："
            + "、".join(f"{_display_name(guild, s['user_id'])}（{s['job']}）" for s in waitlisted)
        )
    return name, "\n".join(lines)


def _mention_list(invitees: list[Row], guild: discord.Guild | None = None) -> list[str]:
    """把 event_invitees 的列（或 mentions.invitee_rows() 組出的假列）轉成
    顯示用字串清單。純字串格式化，不需要呼叫 Discord API（`guild` 有給的話
    只是查本機快取，不會另外發 API 請求）—— 找不到對應成員/身分組時退回
    `<@id>` mention 標記，不會因為這裡沒驗證 ID 是否存在而出錯。
    """
    mentions = []
    for row in invitees:
        target_type, target_id = row["target_type"], row["target_id"]
        if target_type == "user":
            mentions.append(_display_name(guild, target_id))
        elif target_type == "role":
            mentions.append(_role_display_name(guild, target_id))
        elif target_type == "everyone":
            mentions.append("@everyone")
    return mentions


def build_event_embed(
    event: Row,
    invitees: list[Row] | None = None,
    rsvp_summary: RsvpSummary | None = None,
    role_slots: list[Row] | None = None,
    role_signups: list[Row] | None = None,
    guild: discord.Guild | None = None,
) -> discord.Embed:
    """建立活動公告卡片。

    event 是 events 表的一列（見 001_init.sql）。時間一律用 Discord 動態時間戳
    顯示 —— 客戶端會自動換算成檢視者的本地時區，這裡不做任何時區運算。

    invitees 是 event_invitees 的列（或發布前用 mentions.invitee_rows() 組出
    的預覽假列），用來顯示「🔔 邀請對象」欄位。**這個欄位只是顯示**，實際會
    不會推播通知取決於訊息 content 裡的 mention 與 allowed_mentions 白名單
    （見 lib/mentions.py），跟這裡無關。

    rsvp_summary 是 domain.rsvp.build_rsvp_summary() 算出來的參加/待定/
    不參加/未回覆分類，只有傳了才會顯示這幾個欄位（`/event list` 這類簡要
    情境不需要，見 build_event_list_embed）。

    role_slots／role_signups 是 M8 職位報名（見 domain/roles.py）：
    event_role_slots／event_role_signups 的列，只有 role_slots 非空才會
    顯示位置欄位——沒設定位置的活動兩個參數都留 None，行為跟這個功能加
    進來之前完全一樣。

    guild 有給的話，所有名單一律顯示伺服器暱稱而非 `<@id>` mention 標記
    （見 `_display_name` 的說明：embed 欄位裡的 mention 在不同 Discord
    客戶端解析結果不一致，同一則公告桌面版/手機版/網頁版會顯示不一樣）。
    不給（維持預設 `None`）就退回原本的 mention 標記——多半是還沒有
    guild 物件可用的呼叫端（例如 `/event list`／`/event info` 之外極少數
    情境），或既有測試沒有更新。
    """
    icon, colour = STATUS_LABELS.get(event["status"], ("📅", discord.Colour.blurple()))
    title = event["title"]
    if event["status"] == "cancelled":
        title = f"~~{title}~~（已取消）"

    embed = discord.Embed(title=f"{icon} {title}", colour=colour)

    starts_at = event["starts_at_utc"]
    time_value = f"{discord_timestamp(starts_at, 'F')}　·　{discord_timestamp(starts_at, 'R')}"
    if event["ends_at_utc"]:
        time_value += f"\n結束：{discord_timestamp(event['ends_at_utc'], 't')}"
    embed.add_field(name="🕐 時間", value=time_value, inline=False)

    if event["location"]:
        embed.add_field(name="📍 地點", value=event["location"], inline=False)

    if event["description"]:
        embed.add_field(name="📝 內容", value=event["description"], inline=False)

    if role_slots:
        by_slot: dict[str, list[Row]] = {}
        for signup in role_signups or []:
            by_slot.setdefault(signup["role_slot_id"], []).append(signup)
        for slot in role_slots:
            name, value = _format_role_slot_field(slot, by_slot.get(slot["id"], []), guild)
            embed.add_field(name=name, value=value, inline=False)

    if invitees:
        mentions = _mention_list(invitees, guild)
        if mentions:
            name = "🔔 邀請對象"
            if event.get("restrict_rsvp"):
                name += "（僅限以下對象回覆）"
            embed.add_field(name=name, value="、".join(mentions), inline=False)

    if rsvp_summary is not None:
        embed.add_field(
            name=f"✅ 參加（{len(rsvp_summary.yes)}）",
            value=_format_user_mentions(rsvp_summary.yes, guild),
            inline=False,
        )
        if rsvp_summary.maybe:
            embed.add_field(
                name=f"❔ 待定（{len(rsvp_summary.maybe)}）",
                value=_format_user_mentions(rsvp_summary.maybe, guild),
                inline=False,
            )
        if rsvp_summary.no:
            embed.add_field(
                name=f"❌ 不參加（{len(rsvp_summary.no)}）",
                value=_format_user_mentions(rsvp_summary.no, guild),
                inline=False,
            )
        if rsvp_summary.no_response:
            embed.add_field(
                name=f"⏳ 未回覆（{len(rsvp_summary.no_response)}）",
                value=_format_user_mentions(rsvp_summary.no_response, guild),
                inline=False,
            )

    embed.add_field(
        name="👤 發起人", value=_display_name(guild, event["creator_id"]), inline=True
    )
    embed.set_footer(text=f"活動 ID：{event['id']}")

    return embed


def build_reminder_embed(reminder: Row) -> discord.Embed:
    """提醒訊息的卡片。`reminder` 是 `repo.list_due_reminders()` 那個 JOIN
    查詢的一列，同時帶著提醒本身與活動的欄位（title/starts_at_utc/location/
    message_id/guild_id/channel_id）。

    時間刻意用 Discord 動態時間戳 `<t:...:R>` 而不是寫死「還有 X 分鐘」這種
    文字 —— 這樣不管提醒實際送達時間有沒有延遲（例如 bot 剛從停機恢復、
    補發一則逾期提醒），客戶端顯示的相對時間永遠正確，不需要為「準時送達」
    和「延遲補發」兩種情境各寫一套文案（見 domain/reminders.py 的逾期補償
    規則）。
    """
    description = f"{discord_timestamp(reminder['starts_at_utc'], 'R')} 開始"
    if reminder.get("location"):
        description += f"\n📍 {reminder['location']}"
    if reminder.get("message_id") and reminder.get("channel_id") and reminder.get("guild_id"):
        jump_url = (
            f"https://discord.com/channels/"
            f"{reminder['guild_id']}/{reminder['channel_id']}/{reminder['message_id']}"
        )
        description += f"\n[查看活動公告]({jump_url})"

    return discord.Embed(
        title=f"⏰ 提醒：{reminder['title']}",
        description=description,
        colour=discord.Colour.orange(),
    )


def build_poll_embed(
    poll: Row, options: list[Row], votes: list[Row], guild: discord.Guild | None = None
) -> discord.Embed:
    """投票卡片：每個選項一個欄位顯示票數，非匿名模式另外列出是誰投的。

    poll 是 polls 表的一列，options 是 poll_options（已依 sort 排序），votes
    是 poll_votes。票數統計交給 domain.polls.build_tally 算，這裡只管排版。

    guild 有給就顯示伺服器暱稱而非 `<@id>` mention 標記，理由同
    `build_event_embed` 的說明。
    """
    closed = poll["status"] == "closed"
    title = f"🗳️ {poll['question']}"
    if closed:
        title += "（已截止）"
    embed = discord.Embed(
        title=title,
        colour=discord.Colour.dark_grey() if closed else discord.Colour.blurple(),
    )

    if poll.get("description"):
        embed.add_field(name="📝 說明", value=poll["description"], inline=False)

    tally = build_tally(options, votes)
    for option in options:
        voter_ids = tally.get(option["id"], [])
        # kind='time_slot' 的 option['label'] 是純文字（例如 "8/3（一）04:00"）
        # ——那是給投票下拉選單用的，Select 選項文字不會解析 Discord 時間戳
        # markup。embed 欄位名稱會解析，所以這裡從 meta（epoch 字串）現算一次
        # 好看的動態時間戳，兩處各自用最適合的格式。
        if poll["kind"] == "time_slot" and option.get("meta"):
            label_text = discord_timestamp(int(option["meta"]), "F")
        else:
            label_text = option["label"]
        name = f"{label_text}（{len(voter_ids)} 票）"
        value = (
            "🔒 匿名投票，僅顯示票數"
            if poll["anonymous"]
            else _format_user_mentions(voter_ids, guild)
        )
        embed.add_field(name=name, value=value, inline=False)

    if poll.get("closes_at") and not closed:
        embed.add_field(
            name="⏰ 預計截止",
            value=discord_timestamp(poll["closes_at"], "R"),
            inline=False,
        )

    mode = "複選" if poll["multi"] else "單選"
    change = "可改票" if poll["allow_change"] else "不可改票"
    embed.set_footer(text=f"投票 ID：{poll['id']}　·　{mode}　·　{change}")
    return embed


def build_event_list_embed(events: list[Row], *, title: str) -> discord.Embed:
    """`/event list` 用的簡要清單，每個活動一行。"""
    embed = discord.Embed(title=title, colour=discord.Colour.blurple())

    if not events:
        embed.description = "沒有符合條件的活動。"
        return embed

    lines = []
    for event in events:
        icon, _ = STATUS_LABELS.get(event["status"], ("📅", None))
        line = f"{icon} **{event['title']}** — {discord_timestamp(event['starts_at_utc'], 'f')}"
        if event["location"]:
            line += f"（📍 {event['location']}）"
        line += f"\n> ID: `{event['id']}`"
        lines.append(line)

    embed.description = "\n".join(lines)
    return embed
