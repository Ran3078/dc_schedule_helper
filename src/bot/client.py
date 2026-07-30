"""Bot 主體。"""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from src.config import Settings

log = logging.getLogger(__name__)

# 隨里程碑逐步加入：events / polls / settings / scheduler
INITIAL_COGS: tuple[str, ...] = ("src.bot.cogs.meta",)


class ScheduleBot(commands.Bot):
    def __init__(self, settings: Settings) -> None:
        # 不需要 message_content（特權 intent）—— 全部走 slash 指令，不讀任何聊天內容。
        # members 則是必要的：把「參加對象」裡的角色展開成成員清單、算出誰還沒回覆，
        # 都需要成員快取。這是特權 intent，要去 Developer Portal → Bot →
        # Privileged Gateway Intents 開啟 SERVER MEMBERS INTENT（<100 伺服器無需審核）。
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = False
        # 原生活動分頁同步需要收 scheduled event 事件
        intents.guild_scheduled_events = True

        super().__init__(
            command_prefix=commands.when_mentioned,  # 實際上只用 slash 指令
            intents=intents,
            help_command=None,
            application_id=settings.discord_app_id,
        )
        self.settings = settings
        self.guild_object = discord.Object(id=settings.guild_id)

    async def setup_hook(self) -> None:
        for cog in INITIAL_COGS:
            await self.load_extension(cog)
            log.info("已載入 cog: %s", cog)

        # 持久化 View 在此註冊（timeout=None + 固定 custom_id），
        # 重啟後舊訊息上的按鈕才還按得動。M3 起會有實際內容。
        self._register_persistent_views()

        # 指令一律 guild-scoped：私服不需要 global，且 guild-scoped 更新即時生效，
        # global 有最長 1 小時的傳播延遲。
        self.tree.copy_global_to(guild=self.guild_object)
        synced = await self.tree.sync(guild=self.guild_object)
        log.info("已同步 %d 個指令到 guild %s", len(synced), self.settings.guild_id)

    def _register_persistent_views(self) -> None:
        # M3: self.add_view(RsvpView())
        # M5: self.add_view(PollView())
        pass

    async def on_ready(self) -> None:
        assert self.user is not None
        log.info("已登入：%s (id=%s)", self.user, self.user.id)
        guild = self.get_guild(self.settings.guild_id)
        if guild is None:
            log.warning(
                "找不到 guild %s —— bot 可能還沒被邀請進該伺服器，或 GUILD_ID 填錯",
                self.settings.guild_id,
            )
        else:
            log.info("目標伺服器：%s（%d 位成員）", guild.name, guild.member_count or -1)
