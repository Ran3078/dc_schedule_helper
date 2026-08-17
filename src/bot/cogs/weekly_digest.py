"""每週活動清單（M9）：伺服器開啟後（`/settings weekly_digest:true`，預設
關閉），每週日 00:00（該伺服器 `default_tz`）自動在公告頻道發一則「未來
7 天活動預告」，附一顆「新增行程」按鈕。

跟 `scheduler.py` 的提醒派送同一種「真相在 DB、不用記憶體排程」精神：
`guild_settings.last_weekly_digest_at` 記上次成功發送的時間點，任務迴圈
每次醒來都重新算一次「這週發過了沒」，bot 重啟/休眠喚醒後自動接續，不需要
額外復原邏輯。頻率抓 5 分鐘一次——不像提醒要精準到分鐘，晚個幾分鐘無感。
"""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from discord.ext import commands, tasks

from src.bot.embeds import Row, build_event_list_embed
from src.bot.modals_quick import QuickEventModal
from src.db import repo
from src.domain.weekly_digest import most_recent_sunday_midnight
from src.lib.clock import now_ms

log = logging.getLogger(__name__)

_TICK_SECONDS = 300
_WINDOW_DAYS = 7


class _AddEventView(discord.ui.View):
    """每週清單訊息附的「新增行程」按鈕。**不是**持久化 `DynamicItem`：這則
    公告只需要在使用者實際看到的當下（訊息還留著的幾天內）能點，不像 RSVP
    按鈕要撐過好幾週；也不需要記得是哪一週的清單（`QuickEventModal` 本身
    不帶任何週次資訊），所以不用 `DynamicItem` 的 custom_id regex 機制，
    一般 `View(timeout=None)` 附加即可——沒有 timeout 是因為公告訊息會留著
    好幾天，短命 View 的 5 分鐘 timeout 不合用。
    """

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="新增行程", style=discord.ButtonStyle.success, emoji="➕")
    async def add_event(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(QuickEventModal())


class WeeklyDigest(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.digest_tick.start()

    async def cog_unload(self) -> None:
        self.digest_tick.cancel()

    @tasks.loop(seconds=_TICK_SECONDS)
    async def digest_tick(self) -> None:
        # ★ 跟 scheduler.reminder_tick 同樣的理由：bare except 把「這一輪
        # 失敗」和「這個功能死掉」徹底分開，DB 連線瞬斷是背景排程遇得到的
        # 常態，不該讓整個任務永久停止。
        try:
            guilds = await repo.list_guilds_with_weekly_digest_enabled()
        except Exception:
            log.exception("撈取開啟每週清單的伺服器失敗，這一輪跳過，下一輪再試")
            return

        for guild_settings in guilds:
            try:
                await self._maybe_send_digest(guild_settings)
            except Exception:
                # 單一伺服器失敗不該連累其他伺服器這一輪都發不出去。
                log.exception(
                    "伺服器 %s 的每週清單處理失敗", guild_settings["guild_id"]
                )

    @digest_tick.before_loop
    async def _before_digest_tick(self) -> None:
        await self.bot.wait_until_ready()

    async def _maybe_send_digest(self, guild_settings: Row) -> None:
        try:
            tz = ZoneInfo(guild_settings["default_tz"])
        except ZoneInfoNotFoundError:
            tz = ZoneInfo("Asia/Taipei")

        # datetime.now(tz) 是 tz-aware，most_recent_sunday_midnight 只動
        # 日期/時刻分量、不碰 tzinfo，算出來的邊界仍是同一個時區的 aware
        # datetime，.timestamp() 會依 ZoneInfo 的實際規則（含 DST）正確換算
        # 成 UTC，不需要再手動補一次 tzinfo。
        boundary_local = most_recent_sunday_midnight(datetime.now(tz))
        boundary_utc = int(boundary_local.timestamp() * 1000)

        last_sent = guild_settings["last_weekly_digest_at"]
        if last_sent is not None and last_sent >= boundary_utc:
            return  # 這週已經發過了

        guild_id = guild_settings["guild_id"]
        channel_id = guild_settings["announce_channel_id"]
        if not channel_id:
            log.warning("伺服器 %s 開了每週清單但沒設定公告頻道，跳過", guild_id)
            return

        channel = await self._resolve_channel(int(channel_id))
        if channel is None:
            log.warning(
                "伺服器 %s 的公告頻道 %s 找不到（可能已被刪除），跳過每週清單",
                guild_id,
                channel_id,
            )
            return

        now = now_ms()
        events = await repo.list_upcoming_events_in_window(
            guild_id, now, now + _WINDOW_DAYS * 24 * 3_600_000
        )
        embed = build_event_list_embed(events, title="📅 未來 7 天活動預告")

        try:
            await channel.send(embed=embed, view=_AddEventView())
        except discord.HTTPException:
            log.warning("伺服器 %s 的每週清單發送失敗", guild_id, exc_info=True)
            return

        await repo.set_last_weekly_digest_at(guild_id, now)

    async def _resolve_channel(self, channel_id: int) -> discord.abc.Messageable | None:
        channel = self.bot.get_channel(channel_id)
        if channel is not None:
            return channel
        try:
            return await self.bot.fetch_channel(channel_id)
        except discord.HTTPException:
            return None


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(WeeklyDigest(bot))
