"""跨 cog 共用的小工具。只放真的被兩個以上 cog 用到的邏輯，不是預先建立的
「共用層」——取得伺服器時區、個人時區覆寫、管理權限檢查，`Events`／`Polls`／
`Settings` 都需要；活動草稿驗證（M9 起）則是 `Events`／`Ff14`／
`MentionMenu` 三個入口共用。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import discord
from discord.ext import commands

from src.bot.modals import PendingEvent
from src.db import repo
from src.lib.timeparse import TimeParseError, parse_datetime, parse_duration_minutes

Row = dict[str, Any]

# Discord Embed 標題硬上限 256 字元；預留欄位標籤與圖示空間，訂一個更保守的
# 上限。跟 cogs/events.py／cogs/ff14.py 各自的 MAX_TITLE_LENGTH 是同一個
# 數字——這裡是三個入口共用驗證邏輯唯一的權威來源，那兩個常數之後可以
# 直接改成 import 這個。
MAX_TITLE_LENGTH = 200


async def guild_tz(bot: commands.Bot, guild_id: int) -> str:
    """取得該伺服器的預設時區。

    正常情況下 on_guild_join / on_ready 早就建好 guild_settings，這裡的
    self-heal 只防禦「bot 被邀入時剛好連線中斷導致沒建成」這類邊界情況，
    不是常態路徑。
    """
    settings = await repo.get_guild_settings(guild_id)
    if settings is not None:
        return settings["default_tz"]

    default_tz = getattr(bot, "settings", None)
    tz = default_tz.default_tz if default_tz else "Asia/Taipei"
    await repo.ensure_guild(guild_id, tz)
    return tz


async def resolve_user_tz(bot: commands.Bot, guild_id: int, user_id: int) -> str:
    """使用者自己打時間字串／挑時間時要用哪個時區解析：`user_prefs.tz`
    個人覆寫優先，沒設定才退回伺服器預設（`guild_tz`）。

    只用在「解讀使用者輸入」的地方（`/event create`／`/event edit`／
    `/poll create`）——系統層級的動作（提醒排程、投票關閉時自動建立活動）
    不該綁定「剛好是誰觸發了這個動作」的個人時區，那些地方繼續用
    `guild_tz`。
    """
    prefs = await repo.get_user_prefs(user_id)
    if prefs and prefs["tz"]:
        return prefs["tz"]
    return await guild_tz(bot, guild_id)


def is_organizer(member: discord.Member, guild_settings: Row | None) -> bool:
    """有沒有權限建立/管理活動。

    `guild_settings.organizer_role_id` 沒設定（`None`，預設）時人人都算——
    這是既有預設行為，設定後收斂成只有該身分組成員才算。

    呼叫端如果是在管理「特定活動」（edit/cancel/invite/ping），還要另外
    OR 上「是不是活動建立者本人」——這裡只管身分組，不知道活動本身。
    """
    role_id = guild_settings["organizer_role_id"] if guild_settings else None
    if role_id is None:
        return True
    return any(str(r.id) == role_id for r in member.roles)


@dataclass(frozen=True, slots=True)
class DraftValidationError:
    """訊息可直接 ephemeral 回覆給使用者。"""

    message: str


async def validate_event_draft(
    bot: commands.Bot,
    interaction: discord.Interaction,
    *,
    title: str,
    time: str | None,
    location: str | None,
    duration: str | None,
) -> PendingEvent | DraftValidationError:
    """`/event create`、`/ff14_recruit`、@提及選單的快速建立三個入口共用的
    草稿驗證：權限（`is_organizer`）、標題（trim＋長度）、時間／時長解析。

    回傳值是聯集：驗證通過給 `PendingEvent`，失敗給 `DraftValidationError`
    （呼叫端負責用 `interaction.response.send_message(result.message,
    ephemeral=True)` 回覆，這裡不直接回覆——因為某些呼叫端在驗證通過後
    還要接著呼叫 `interaction.response.send_modal(...)`，回覆的時機要留給
    呼叫端自己決定）。

    `PendingEvent.channel_id` 這裡先用 `interaction.channel_id` 佔位——公告
    頻道解析（`guild_settings.announce_channel_id`）是每個 cog 各自的
    `_resolve_announce_channel` 負責，跟 `_resolve_channel` 一樣刻意不收進
    這個共用函式（理由同 `PROCESS.md` 既有慣例），呼叫端驗證通過後自己用
    `dataclasses.replace(pending, channel_id=...)` 覆寫。
    """
    assert interaction.guild_id is not None  # guild_only() 保證

    guild_settings = await repo.get_guild_settings(interaction.guild_id)
    if not isinstance(interaction.user, discord.Member) or not is_organizer(
        interaction.user, guild_settings
    ):
        return DraftValidationError("這個伺服器限定特定身分組才能建立活動，請洽伺服器管理員。")

    title = title.strip()
    if not title:
        return DraftValidationError("活動標題不能是空的。")
    if len(title) > MAX_TITLE_LENGTH:
        return DraftValidationError(
            f"活動標題太長了（{len(title)} 字，上限 {MAX_TITLE_LENGTH} 字）。"
        )

    tz = await resolve_user_tz(bot, interaction.guild_id, interaction.user.id)

    # time 是選填的：留空的話，呼叫端會改顯示日期時間挑選器（見 modals.py
    # 的分流邏輯與 views_datetime.py 開頭對 Discord 元件限制的說明——沒有
    # 原生日期選擇元件，只能用下拉選單模擬）。
    starts_at_utc: int | None = None
    if time:
        try:
            starts_at_utc = parse_datetime(time, tz)
        except TimeParseError as exc:
            return DraftValidationError(str(exc))

    duration_minutes: int | None = None
    if duration:
        try:
            duration_minutes = parse_duration_minutes(duration)
        except TimeParseError as exc:
            return DraftValidationError(str(exc))
        if duration_minutes <= 0:
            return DraftValidationError("時長必須大於 0。")

    return PendingEvent(
        guild_id=interaction.guild_id,
        channel_id=interaction.channel_id,
        creator_id=interaction.user.id,
        title=title,
        tz=tz,
        location=(location.strip() if location and location.strip() else None),
        duration_minutes=duration_minutes,
        starts_at_utc=starts_at_utc,
    )
