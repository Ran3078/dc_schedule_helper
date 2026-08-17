"""`@Schedule_Notify` 快速選單（M9）：直接在頻道裡 @ 到 bot，就跳出按鈕
選單，不用先打指令名稱、也不用記參數——降低不熟悉 slash 指令的人的使用
門檻。

偵測「訊息有沒有 @ 到 bot」用 `message.mentions`（結構化欄位）而非
`message.content` 文字比對，**不需要** `MESSAGE_CONTENT` 特權 intent——
這個專案至今刻意不開這個 intent（見 README），這裡維持不用申請。
"""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from src.bot.embeds import build_event_list_embed
from src.bot.modals_quick import QuickEventModal, QuickFf14Modal, QuickPollModal
from src.db import repo
from src.lib.clock import now_ms

log = logging.getLogger(__name__)

_MENU_TIMEOUT_SECONDS = 300
_WINDOW_DAYS = 7


class MentionMenuView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=_MENU_TIMEOUT_SECONDS)
        self.message: discord.Message | None = None

    @discord.ui.button(label="建立活動", style=discord.ButtonStyle.primary, emoji="📅")
    async def create_event(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(QuickEventModal())

    @discord.ui.button(label="FF14 招募", style=discord.ButtonStyle.primary, emoji="🗡️")
    async def create_ff14_recruit(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(QuickFf14Modal())

    @discord.ui.button(label="建立投票", style=discord.ButtonStyle.primary, emoji="🗳️")
    async def create_poll(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(QuickPollModal())

    @discord.ui.button(label="本週活動", style=discord.ButtonStyle.secondary, emoji="📋")
    async def show_week(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await self._show_week_impl(interaction)

    async def _show_week_impl(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            return
        await interaction.response.defer(ephemeral=True)

        now = now_ms()
        events = await repo.list_upcoming_events_in_window(
            interaction.guild_id, now, now + _WINDOW_DAYS * 24 * 3_600_000
        )
        embed = build_event_list_embed(events, title="📋 未來 7 天活動")
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def on_timeout(self) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Item):
                child.disabled = True  # type: ignore[attr-defined]
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass  # 訊息可能已被使用者刪除，逾時清理失敗不影響任何資料正確性


class MentionMenu(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        # message.author.bot：避免自己 tag 自己、或其他 bot tag 到造成無限
        # 循環（雖然這個 bot 不會回 tag 別人，這裡還是防禦性地擋掉）。
        # message.guild is None：私訊沒有 guild_settings/頻道概念，這個選單
        # 全部功能都需要伺服器情境，直接略過。
        if message.author.bot or message.guild is None:
            return
        if self.bot.user is None or self.bot.user not in message.mentions:
            return

        view = MentionMenuView()
        try:
            sent = await message.reply(
                content="要做什麼？（5 分鐘後這個選單會失效，指令本身不受影響）",
                view=view,
                mention_author=False,
            )
        except discord.HTTPException:
            log.warning("回覆 @提及選單失敗（頻道 %s）", message.channel.id, exc_info=True)
            return
        view.message = sent


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MentionMenu(bot))
