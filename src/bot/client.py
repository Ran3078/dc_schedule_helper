"""Bot 主體。"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from src.bot.views_poll import PollVoteSelect
from src.bot.views_rsvp import PositionSelect, RsvpButton
from src.config import Settings
from src.db import repo

log = logging.getLogger(__name__)

INITIAL_COGS: tuple[str, ...] = (
    "src.bot.cogs.meta",
    "src.bot.cogs.events",
    "src.bot.cogs.polls",
    "src.bot.cogs.scheduler",
    "src.bot.cogs.native_events",
    "src.bot.cogs.settings",  # 同一個模組裡 Settings／Timezone 兩個 cog 都在
    "src.bot.cogs.ff14",  # /ff14_recruit（M8）
)


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

        # 持久化元件在此註冊。RsvpButton／PollVoteSelect／PositionSelect
        # （M8）都用 DynamicItem（custom_id 帶 event_id/poll_id，每則公告
        # 訊息都不同），走 add_dynamic_items 而非 add_view —— 不需要在啟動時
        # 逐一重新綁定每則舊訊息，Discord 每次互動都會把訊息當下的元件結構
        # 送回來，靠 regex 樣板比對即時重建。
        self.add_dynamic_items(RsvpButton, PollVoteSelect, PositionSelect)

        # 見 _on_app_command_error 的說明：沒有這個，指令處理中任何沒被接住的
        # 例外（最常見是 DB 連線瞬斷）對使用者來說就是「該申請未回應」，
        # 看起來像 bot 掛了，其實只是那一次指令沒回覆。
        self.tree.on_error = self._on_app_command_error

        await self._sync_commands()

    async def _sync_commands(self) -> None:
        """指令同步：一律走 global。

        多伺服器必須用 global 註冊，但 Discord 對 global 指令有快取，改動後
        最長要等 1 小時才在各伺服器生效。這裡曾經額外對 DEV_GUILD_ID 做一次
        guild-scoped 同步換取即時生效，但實測 Discord **不會**把 global 跟
        guild-scoped 這兩種註冊視為同一個指令、不會自動去重——會讓開發伺服器
        的指令選單上，每個指令都同時看到 global 跟 guild-scoped 兩份一模
        一樣的紀錄。與其為了省一點等待時間製造這個更明顯的問題，不如全部
        走 global；下面順便清空 DEV_GUILD_ID 過去累積的 guild-scoped 舊
        註冊，讓已經在用的人不會繼續卡著重複的指令。
        """
        if self.dev_guild is not None:
            self.tree.clear_commands(guild=self.dev_guild)
            await self.tree.sync(guild=self.dev_guild)
            log.info("已清空開發伺服器 %s 舊的 guild-scoped 指令註冊", self.settings.dev_guild_id)

        synced_global = await self.tree.sync()
        log.info("已 global 同步 %d 個指令（各伺服器最長 1 小時內生效）", len(synced_global))

    async def _on_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        """指令處理過程中任何未攔截的例外，最後都會落到這裡。

        discord.py 預設的 `CommandTree.on_error` 只會把例外寫進 log，不會回覆
        使用者任何東西——對使用者來說，這代表 Discord 用戶端在 3 秒後顯示
        「該申請未回應」，看起來像 bot 整個掛掉，實際上只是那一次指令沒接住
        例外。DB 連線瞬斷（見 `db/engine.py` 開頭對 Turso 閒置逾時的說明，
        `scheduler.py` 的排程迴圈已經吃過一次虧）是最常見的觸發原因，這裡
        比照同樣的精神：讓使用者知道「失敗了，可以再試一次」，比讓互動默默
        逾時對使用者友善得多。
        """
        command_name = interaction.command.name if interaction.command else "?"
        log.exception("指令 /%s 發生未預期錯誤", command_name, exc_info=error)

        message = "指令執行時發生錯誤，請稍後再試一次。"
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            pass  # interaction 可能真的已經逾期失效，沒有更多能做的

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
