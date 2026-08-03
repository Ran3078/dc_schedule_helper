"""跨 cog 共用的小工具。只放真的被兩個以上 cog 用到的邏輯，不是預先建立的
「共用層」——目前只有取得伺服器時區這一件事，`Events` 與 `Polls` 都需要。
"""

from __future__ import annotations

from discord.ext import commands

from src.db import repo


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
