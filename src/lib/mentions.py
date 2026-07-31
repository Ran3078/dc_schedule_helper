"""Mention 白名單處理。

一律用 `discord.AllowedMentions` 明確列出允許提及的對象，而不是讓 Discord
解析 `content` 字串裡看到的任何 `<@id>` 就發送通知。這樣即使日後不小心把
使用者輸入（例如活動標題）原封不動塞進 content，也不會被拿來偽造 mention
發動騷擾式通知 —— 不在白名單裡的 ID，就算字串長得像 mention 也不會觸發推播。

`@everyone` / `@here` 預設關閉，只有伺服器設定 `allow_everyone_ping` 開啟時
才允許（見 `guild_settings` 與 `/settings`）。
"""

from __future__ import annotations

import discord

Row = dict[str, str]


def build_mention_content(
    user_ids: list[int | str], role_ids: list[int | str], *, tag_everyone: bool = False
) -> str | None:
    """組出要放進 message content 的 mention 字串。

    純 embed 不會觸發手機推播通知，只有 message content 裡的 mention 才會 ——
    這是公告訊息同時帶 content 與 embed 的原因。全部留空時回傳 None（不附加
    任何內容，channel.send 不接受空字串 content）。
    """
    parts: list[str] = []
    if tag_everyone:
        parts.append("@everyone")
    parts.extend(f"<@{uid}>" for uid in user_ids)
    parts.extend(f"<@&{rid}>" for rid in role_ids)
    return " ".join(parts) if parts else None


def build_allowed_mentions(
    user_ids: list[int | str], role_ids: list[int | str], *, tag_everyone: bool = False
) -> discord.AllowedMentions:
    """白名單只放這次公告明確選定的對象，不是「content 裡出現的任何 mention」。"""
    return discord.AllowedMentions(
        users=[discord.Object(id=int(uid)) for uid in user_ids],
        roles=[discord.Object(id=int(rid)) for rid in role_ids],
        everyone=tag_everyone,
        replied_user=False,
    )


def invitee_rows(user_ids: list[int | str], role_ids: list[int | str]) -> list[Row]:
    """組出跟 `event_invitees` 資料表列形狀一致的 dict，供 embed 顯示邀請名單。

    用於「尚未寫入資料庫的預覽畫面」—— 讓預覽與發布後看到的邀請名單欄位
    走同一段渲染邏輯（見 embeds.py 的 `_mention_list`），不必為預覽另外做
    一套格式。
    """
    return [{"target_type": "user", "target_id": str(uid)} for uid in user_ids] + [
        {"target_type": "role", "target_id": str(rid)} for rid in role_ids
    ]
