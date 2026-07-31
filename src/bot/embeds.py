"""活動公告 Embed。"""

from __future__ import annotations

from typing import Any

import discord

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


def _format_user_mentions(user_ids: list[int]) -> str:
    """把使用者 ID 清單轉成 mention 字串，超過欄位字元上限時截斷並註明剩餘人數。

    活動邀請幾十上百人時，光是「參加」清單就可能塞不進 Discord embed 單一
    欄位的 1024 字元上限 —— 寧可清單不完整也不要讓整個 API 呼叫因超字數失敗。
    """
    if not user_ids:
        return "（無）"

    mentions = [f"<@{uid}>" for uid in user_ids]
    text = " ".join(mentions)
    if len(text) <= _MAX_FIELD_LEN:
        return text

    kept: list[str] = []
    length = 0
    for mention in mentions:
        if length + len(mention) + 1 > _MAX_FIELD_LEN:
            break
        kept.append(mention)
        length += len(mention) + 1
    return " ".join(kept) + f" …等 {len(mentions) - len(kept)} 人"


def _mention_list(invitees: list[Row]) -> list[str]:
    """把 event_invitees 的列（或 mentions.invitee_rows() 組出的假列）轉成
    mention 字串。純字串格式化，不需要呼叫 Discord API —— 顯示用的 mention
    markup 是客戶端渲染，不會因為這裡沒驗證 ID 是否存在而出錯。
    """
    mentions = []
    for row in invitees:
        target_type, target_id = row["target_type"], row["target_id"]
        if target_type == "user":
            mentions.append(f"<@{target_id}>")
        elif target_type == "role":
            mentions.append(f"<@&{target_id}>")
        elif target_type == "everyone":
            mentions.append("@everyone")
    return mentions


def build_event_embed(
    event: Row,
    invitees: list[Row] | None = None,
    rsvp_summary: RsvpSummary | None = None,
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

    if invitees:
        mentions = _mention_list(invitees)
        if mentions:
            name = "🔔 邀請對象"
            if event.get("restrict_rsvp"):
                name += "（僅限以下對象回覆）"
            embed.add_field(name=name, value=" ".join(mentions), inline=False)

    if rsvp_summary is not None:
        embed.add_field(
            name=f"✅ 參加（{len(rsvp_summary.yes)}）",
            value=_format_user_mentions(rsvp_summary.yes),
            inline=False,
        )
        if rsvp_summary.maybe:
            embed.add_field(
                name=f"❔ 待定（{len(rsvp_summary.maybe)}）",
                value=_format_user_mentions(rsvp_summary.maybe),
                inline=False,
            )
        if rsvp_summary.no:
            embed.add_field(
                name=f"❌ 不參加（{len(rsvp_summary.no)}）",
                value=_format_user_mentions(rsvp_summary.no),
                inline=False,
            )
        if rsvp_summary.no_response:
            embed.add_field(
                name=f"⏳ 未回覆（{len(rsvp_summary.no_response)}）",
                value=_format_user_mentions(rsvp_summary.no_response),
                inline=False,
            )

    embed.add_field(name="👤 發起人", value=f"<@{event['creator_id']}>", inline=True)
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
