"""`/ff14_recruit`：一次收完標題／時間／地點／內容／職位名額，直接建立一場
FF14 團本模式的活動（M8）。

刻意獨立於 `/event` 群組之外（不是 `/event roles` 那種「先建活動、再事後
補職位設定」的兩段式流程）——職位名額是 FF14 團本招募專屬的東西，應該在
建立活動當下就一次收好，見 `modals_ff14.Ff14RecruitModal`。

權限／標題／時間／時長驗證跟 `cogs/events.py` 的 `Events._create_impl`
共用同一個 `cogs/_shared.validate_event_draft`（M9 起，`Ff14`／`Events`／
`MentionMenu` 三個入口共用）；公告頻道解析（`_resolve_channel`／
`_resolve_announce_channel`）維持每個 cog 各自複製一份，理由見
`PROCESS.md` 既有慣例——這兩件事的抽象邊界不一樣，前者是「驗證規則」，
後者是「頻道快取/API 呼叫的小 glue」。
"""

from __future__ import annotations

import logging
from dataclasses import replace

import discord
from discord import app_commands
from discord.ext import commands

from src.bot.cogs._shared import DraftValidationError, validate_event_draft
from src.bot.embeds import Row
from src.bot.modals_ff14 import Ff14RecruitModal
from src.db import repo
from src.lib.ids import new_id

log = logging.getLogger(__name__)


class Ff14(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _resolve_channel(self, channel_id: int) -> discord.abc.Messageable | None:
        channel = self.bot.get_channel(channel_id)
        if channel is not None:
            return channel
        try:
            return await self.bot.fetch_channel(channel_id)
        except discord.HTTPException:
            return None

    async def _resolve_announce_channel(
        self, interaction: discord.Interaction, guild_settings: Row | None
    ) -> discord.abc.Messageable | None:
        channel_id = guild_settings["announce_channel_id"] if guild_settings else None
        if channel_id:
            channel = await self._resolve_channel(int(channel_id))
            if channel is not None:
                return channel
            log.warning("設定的公告頻道 %s 已經找不到了，改用指令所在頻道", channel_id)
        return interaction.channel

    @app_commands.command(name="ff14_recruit", description="建立 FF14 團本招募活動（含職位名額）")
    @app_commands.describe(
        title="活動標題",
        time="活動時間，例如 2026-08-01 20:00 或 8/1 20:00（留空則改用日期時間挑選器）",
        location="地點（選填，可放語音頻道連結或實體地址）",
        duration="時長，例如 2h、90m（選填）",
    )
    @app_commands.guild_only()
    async def ff14_recruit(
        self,
        interaction: discord.Interaction,
        title: str,
        time: str | None = None,
        location: str | None = None,
        duration: str | None = None,
    ) -> None:
        # 邏輯拆到 _recruit_impl：方便用假 interaction 直接測權限檢查，不用
        # 真的觸發 send_modal（比照 events.py 的 _create_impl 拆法）。
        await self._recruit_impl(interaction, title, time, location, duration)

    async def _recruit_impl(
        self,
        interaction: discord.Interaction,
        title: str,
        time: str | None,
        location: str | None,
        duration: str | None,
    ) -> None:
        assert interaction.guild_id is not None  # guild_only() 保證

        draft = await validate_event_draft(
            self.bot, interaction, title=title, time=time, location=location, duration=duration
        )
        if isinstance(draft, DraftValidationError):
            await interaction.response.send_message(draft.message, ephemeral=True)
            return

        guild_settings = await repo.get_guild_settings(interaction.guild_id)
        announce_channel = await self._resolve_announce_channel(interaction, guild_settings)
        channel_id = announce_channel.id if announce_channel is not None else interaction.channel_id
        pending = replace(draft, channel_id=channel_id)

        # Modal 必須是這個 interaction 的第一個回應，不能先 defer。
        await interaction.response.send_modal(Ff14RecruitModal(pending, event_id=new_id()))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Ff14(bot))
