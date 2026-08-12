"""`/ff14_recruit`：一次收完標題／時間／地點／內容／職位名額，直接建立一場
FF14 團本模式的活動（M8）。

刻意獨立於 `/event` 群組之外（不是 `/event roles` 那種「先建活動、再事後
補職位設定」的兩段式流程）——職位名額是 FF14 團本招募專屬的東西，應該在
建立活動當下就一次收好，見 `modals_ff14.Ff14RecruitModal`。

驗證邏輯（權限／標題長度／時間解析／時長解析／公告頻道）跟
`cogs/events.py` 的 `Events._create_impl` 幾乎一樣——這是刻意複製一份，不是
漏了抽共用函式：`_resolve_channel` 這類頻道解析本來就是「每個 cog 各自
複製一份」而非共用（見 `PROCESS.md`），這裡延續同樣的慣例。
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from src.bot.cogs._shared import is_organizer, resolve_user_tz
from src.bot.embeds import Row
from src.bot.modals import PendingEvent
from src.bot.modals_ff14 import Ff14RecruitModal
from src.db import repo
from src.lib.ids import new_id
from src.lib.timeparse import TimeParseError, parse_datetime, parse_duration_minutes

log = logging.getLogger(__name__)

# 跟 cogs/events.py 的 MAX_TITLE_LENGTH 同一個數字，理由同該檔案的說明。
MAX_TITLE_LENGTH = 200


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

        guild_settings = await repo.get_guild_settings(interaction.guild_id)
        if not isinstance(interaction.user, discord.Member) or not is_organizer(
            interaction.user, guild_settings
        ):
            await interaction.response.send_message(
                "這個伺服器限定特定身分組才能建立活動，請洽伺服器管理員。", ephemeral=True
            )
            return

        title = title.strip()
        if not title:
            await interaction.response.send_message("活動標題不能是空的。", ephemeral=True)
            return
        if len(title) > MAX_TITLE_LENGTH:
            await interaction.response.send_message(
                f"活動標題太長了（{len(title)} 字，上限 {MAX_TITLE_LENGTH} 字）。",
                ephemeral=True,
            )
            return

        tz = await resolve_user_tz(self.bot, interaction.guild_id, interaction.user.id)

        starts_at_utc: int | None = None
        if time:
            try:
                starts_at_utc = parse_datetime(time, tz)
            except TimeParseError as exc:
                await interaction.response.send_message(str(exc), ephemeral=True)
                return

        duration_minutes: int | None = None
        if duration:
            try:
                duration_minutes = parse_duration_minutes(duration)
            except TimeParseError as exc:
                await interaction.response.send_message(str(exc), ephemeral=True)
                return
            if duration_minutes <= 0:
                await interaction.response.send_message("時長必須大於 0。", ephemeral=True)
                return

        announce_channel = await self._resolve_announce_channel(interaction, guild_settings)
        channel_id = announce_channel.id if announce_channel is not None else interaction.channel_id

        pending = PendingEvent(
            guild_id=interaction.guild_id,
            channel_id=channel_id,
            creator_id=interaction.user.id,
            title=title,
            tz=tz,
            location=(location.strip() if location and location.strip() else None),
            duration_minutes=duration_minutes,
            starts_at_utc=starts_at_utc,
        )

        # Modal 必須是這個 interaction 的第一個回應，不能先 defer。
        await interaction.response.send_modal(Ff14RecruitModal(pending, event_id=new_id()))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Ff14(bot))
