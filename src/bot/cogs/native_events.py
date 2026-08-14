"""Discord 原生「活動」分頁的雙向同步（M6 的另一半）。

`src/bot/native_events.py` 的 `sync_create` 是單向的（我們建活動 → 建原生
活動）；這個 cog 補上反方向：使用者在原生活動卡片點「有興趣／取消有興趣」時，
同步回我們自己的 `rsvps` 表，並重繪公告卡片，讓兩邊看到的參加名單一致。

只處理我們自己建立、`events.discord_event_id` 對得到的原生活動——使用者在
Discord 自己手動開的原生活動完全不受影響（`repo.get_event_by_discord_id`
查無對應列就直接略過）。
"""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from src.bot.embeds import Row, build_event_embed
from src.db import repo
from src.domain.rsvp import build_rsvp_summary

log = logging.getLogger(__name__)


class NativeEvents(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _resolve_channel(self, channel_id: int) -> discord.abc.Messageable | None:
        """比照 scheduler.py／polls.py 的頻道解析：優先吃快取，沒有才補一次 API 呼叫。"""
        channel = self.bot.get_channel(channel_id)
        if channel is not None:
            return channel
        try:
            return await self.bot.fetch_channel(channel_id)
        except discord.HTTPException:
            return None

    @commands.Cog.listener()
    async def on_scheduled_event_user_add(
        self, event: discord.ScheduledEvent, user: discord.User
    ) -> None:
        await self._sync_interest(event, user, interested=True)

    @commands.Cog.listener()
    async def on_scheduled_event_user_remove(
        self, event: discord.ScheduledEvent, user: discord.User
    ) -> None:
        await self._sync_interest(event, user, interested=False)

    async def _sync_interest(
        self, scheduled_event: discord.ScheduledEvent, user: discord.User, *, interested: bool
    ) -> None:
        if user.bot or scheduled_event.guild is None:
            # user.bot：理論上 bot 自己不會出現在原生活動的興趣名單裡，純防禦。
            # guild is None：理論上不會發生（成員/伺服器快取都有開），一樣防禦。
            return

        guild = scheduled_event.guild
        event_row = await repo.get_event_by_discord_id(scheduled_event.id, guild.id)
        if event_row is None:
            return  # 不是我們建立的原生活動，不管

        if interested:
            await repo.upsert_rsvp(event_row["id"], guild.id, user.id, "yes")
        else:
            # 取消有興趣 ≠ 明確表態不參加，還原成「未回覆」而非改成 status='no'
            # （見 domain 層以外的產品決策，記在這次的 plan／PROCESS.md）。
            await repo.delete_rsvp(event_row["id"], guild.id, user.id)

        await self._refresh_announcement(event_row, guild)

    async def _refresh_announcement(self, event_row: Row, guild: discord.Guild) -> None:
        if not event_row["message_id"]:
            return
        channel = await self._resolve_channel(int(event_row["channel_id"]))
        if channel is None:
            return

        invitees = await repo.list_event_invitees(event_row["id"], guild.id)
        rsvps = await repo.list_rsvps(event_row["id"], guild.id)
        # role_slots/role_signups 一併帶上：embed 是整包替換，沒帶的話已經
        # 設定過職位的活動會在這次重繪後憑空少掉那幾個欄位（比照
        # cogs/events.py._invite_impl／modals.EventEditModal 已經修過的
        # 同一類問題）。
        role_slots = await repo.list_event_role_slots(event_row["id"], guild.id)
        role_signups = await repo.list_event_role_signups(event_row["id"], guild.id)
        summary = build_rsvp_summary(guild, invitees, rsvps)

        try:
            message = await channel.fetch_message(int(event_row["message_id"]))
            await message.edit(
                embed=build_event_embed(
                    event_row, invitees, summary, role_slots, role_signups, guild
                )
            )
        except discord.HTTPException:
            log.warning(
                "同步原生活動 %s 興趣後更新公告訊息失敗", event_row["id"], exc_info=True
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(NativeEvents(bot))
