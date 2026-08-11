"""跨 cog 共用的小工具。只放真的被兩個以上 cog 用到的邏輯，不是預先建立的
「共用層」——取得伺服器時區、個人時區覆寫、管理權限檢查，`Events`／`Polls`／
`Settings` 都需要。
"""

from __future__ import annotations

from typing import Any

import discord
from discord.ext import commands

from src.db import repo

Row = dict[str, Any]


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
