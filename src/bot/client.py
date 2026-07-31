"""Bot 主體。"""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from src.bot.views_rsvp import RsvpButton
from src.config import Settings
from src.db import repo

log = logging.getLogger(__name__)

# 隨里程碑逐步加入：polls / settings / scheduler
INITIAL_COGS: tuple[str, ...] = ("src.bot.cogs.meta", "src.bot.cogs.events")


class ScheduleBot(commands.Bot):
    def __init__(self, settings: Settings) -> None:
        # 不需要 message_content（特權 intent）—— 全部走 slash 指令，不讀任何聊天內容。
        # members 則是必要的：把「參加對象」裡的角色展開成成員清單、算出誰還沒回覆，
        # 都需要成員快取。這是特權 intent，要去 Developer Portal → Bot →
        # Privileged Gateway Intents 開啟 SERVER MEMBERS INTENT。
        #
        # 多伺服器的兩個天花板（超過才需要處理，目前不用）：
        #   * 超過 100 個伺服器時，特權 intent 需要向 Discord 申請審核
        #   * 成員快取吃 RAM，Render 免費方案只有 512MB；幾十個伺服器就要評估
        #     改用 chunk_guilds_at_startup=False + 按需 fetch
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
        self.dev_guild = (
            discord.Object(id=settings.dev_guild_id)
            if settings.dev_guild_id is not None
            else None
        )

    async def setup_hook(self) -> None:
        for cog in INITIAL_COGS:
            await self.load_extension(cog)
            log.info("已載入 cog: %s", cog)

        # 持久化元件在此註冊。RsvpButton 用 DynamicItem（custom_id 帶
        # event_id，每則公告訊息都不同），走 add_dynamic_items 而非
        # add_view —— 不需要在啟動時逐一重新綁定每則舊訊息，Discord 每次
        # 互動都會把訊息當下的元件結構送回來，靠 regex 樣板比對即時重建。
        self.add_dynamic_items(RsvpButton)

        await self._sync_commands()

    async def _sync_commands(self) -> None:
        """指令同步：global 為主，開發伺服器額外做一次即時同步。

        多伺服器必須用 global 註冊，但 Discord 對 global 指令有快取，改動後最長要等
        1 小時才在各伺服器生效。因此若設了 DEV_GUILD_ID，就對該伺服器再做一次
        guild-scoped 同步 —— guild-scoped 是即時生效的，開發時不必等。

        副作用：開發伺服器會同時看到 global 與 guild 兩份註冊。Discord 的行為是
        guild-scoped 優先，不會出現重複的指令。
        """
        synced_global = await self.tree.sync()
        log.info("已 global 同步 %d 個指令（各伺服器最長 1 小時內生效）", len(synced_global))

        if self.dev_guild is not None:
            self.tree.copy_global_to(guild=self.dev_guild)
            synced_dev = await self.tree.sync(guild=self.dev_guild)
            log.info(
                "已對開發伺服器 %s 同步 %d 個指令（即時生效）",
                self.settings.dev_guild_id,
                len(synced_dev),
            )

    # M5 起若有其他持久化元件（例如投票按鈕），一樣在 setup_hook 用
    # add_dynamic_items 註冊，不需要額外的輔助方法。

    async def on_ready(self) -> None:
        assert self.user is not None
        log.info("已登入：%s (id=%s)", self.user, self.user.id)

        # on_ready 可能因重連而多次觸發，ensure_guild 是 idempotent 的，重跑無妨。
        for guild in self.guilds:
            await repo.ensure_guild(guild.id, self.settings.default_tz)

        log.info(
            "服務中的伺服器共 %d 個：%s",
            len(self.guilds),
            ", ".join(f"{g.name}({g.id})" for g in self.guilds) or "（尚未加入任何伺服器）",
        )

    async def on_guild_join(self, guild: discord.Guild) -> None:
        """加入新伺服器時建立該伺服器的預設設定。"""
        await repo.ensure_guild(guild.id, self.settings.default_tz)
        log.info("已加入新伺服器：%s (%s)", guild.name, guild.id)

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """被移出伺服器。

        刻意**不刪除**該伺服器的資料 —— 若日後又被邀回來，活動與設定還在。
        資料量極小（本專案一個伺服器的資料是 KB 量級），沒有清理的必要。
        """
        log.info("已離開伺服器：%s (%s)，資料保留", guild.name, guild.id)
