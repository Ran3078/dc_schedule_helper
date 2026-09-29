"""`/countdown`：自訂倒數提醒（例如朋友退伍倒數），每天固定時間發一次，
直到目標日期當天發送完自動停止。

跟 `events`／`reminders` 是分開的一張表（`countdowns`），不是借用活動的
「開始前 N 分鐘」一次性提醒機制——這裡要的是「每天固定時間發一次、直到
目標日期」的重複提醒，語意不同，硬塞進 events 會讓兩邊排程邏輯互相污染，
見 `008_countdowns.sql` 的說明。

權限比照 `/event create`／`/event edit`：建立需要 `is_organizer`；取消
需要「建立者本人或 organizer 身分組」，這條 `_can_manage` 邏輯故意跟
`cogs/events.py` 各自複製一份而非抽共用——理由同 `PROCESS.md` 既有慣例，
頻道解析／小型權限 glue 本來就是每個 cog 各自的責任。
"""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from discord import app_commands
from discord.ext import commands, tasks

from src.bot.cogs._shared import is_organizer, resolve_user_tz
from src.bot.embeds import Row, build_countdown_embed
from src.db import repo
from src.domain.countdown import days_remaining, should_send_today
from src.lib.clock import now_ms
from src.lib.ids import new_id
from src.lib.timeparse import TimeParseError, parse_date, parse_time_of_day

log = logging.getLogger(__name__)

_MAX_TITLE_LENGTH = 200
_TICK_SECONDS = 300


class Countdown(commands.GroupCog, group_name="countdown", group_description="倒數提醒"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.countdown_tick.start()

    async def cog_unload(self) -> None:
        self.countdown_tick.cancel()

    def _can_manage(
        self, interaction: discord.Interaction, countdown: Row, guild_settings: Row | None
    ) -> bool:
        member = interaction.user
        if countdown["creator_id"] == str(member.id):
            return True
        if not isinstance(member, discord.Member):
            return False
        return is_organizer(member, guild_settings)

    @app_commands.command(name="create", description="建立倒數提醒")
    @app_commands.describe(
        title="倒數標題，例如「退伍倒數」",
        date="目標日期，例如 2027-06-15 或 6/15",
        time="每天發送的時間（24 小時制），例如 09:00",
        content="訊息內容（選填）",
        channel="要發到哪個頻道（選填，預設是目前這個頻道）",
    )
    @app_commands.guild_only()
    async def create(
        self,
        interaction: discord.Interaction,
        title: str,
        date: str,
        time: str,
        content: str | None = None,
        channel: discord.TextChannel | None = None,
    ) -> None:
        await self._create_impl(interaction, title, date, time, content, channel)

    async def _create_impl(
        self,
        interaction: discord.Interaction,
        title: str,
        date: str,
        time: str,
        content: str | None,
        channel: discord.TextChannel | None,
    ) -> None:
        assert interaction.guild_id is not None  # guild_only() 保證

        guild_settings = await repo.get_guild_settings(interaction.guild_id)
        if not isinstance(interaction.user, discord.Member) or not is_organizer(
            interaction.user, guild_settings
        ):
            await interaction.response.send_message(
                "這個伺服器限定特定身分組才能建立倒數提醒，請洽伺服器管理員。",
                ephemeral=True,
            )
            return

        title = title.strip()
        if not title:
            await interaction.response.send_message("倒數標題不能是空的。", ephemeral=True)
            return
        if len(title) > _MAX_TITLE_LENGTH:
            await interaction.response.send_message(
                f"倒數標題太長了（{len(title)} 字，上限 {_MAX_TITLE_LENGTH} 字）。",
                ephemeral=True,
            )
            return

        tz = await resolve_user_tz(self.bot, interaction.guild_id, interaction.user.id)

        try:
            target_date_utc = parse_date(date, tz)
        except TimeParseError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        try:
            send_hour, send_minute = parse_time_of_day(time)
        except TimeParseError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        target_channel = channel or interaction.channel
        content_value = content.strip() or None if content else None
        countdown_id = new_id()
        await repo.create_countdown(
            countdown_id=countdown_id,
            guild_id=interaction.guild_id,
            channel_id=target_channel.id,
            creator_id=interaction.user.id,
            title=title,
            content=content_value,
            target_date_utc=target_date_utc,
            tz=tz,
            send_hour=send_hour,
            send_minute=send_minute,
        )

        try:
            zone = ZoneInfo(tz)
        except ZoneInfoNotFoundError:
            zone = ZoneInfo("Asia/Taipei")
        remaining = days_remaining(target_date_utc, datetime.now(zone))

        await interaction.response.send_message(
            f"✅ 倒數提醒已建立（ID `{countdown_id}`）：**{title}**，"
            f"目前還剩 **{remaining}** 天，每天 {send_hour:02d}:{send_minute:02d} "
            f"發到 {target_channel.mention}。",
            ephemeral=True,
        )

    @app_commands.command(name="list", description="列出這個伺服器還在倒數中的提醒")
    @app_commands.guild_only()
    async def list_countdowns(self, interaction: discord.Interaction) -> None:
        await self._list_impl(interaction)

    async def _list_impl(self, interaction: discord.Interaction) -> None:
        assert interaction.guild_id is not None

        countdowns = await repo.list_active_countdowns_in_guild(interaction.guild_id)
        if not countdowns:
            await interaction.response.send_message(
                "目前沒有進行中的倒數提醒。", ephemeral=True
            )
            return

        lines = []
        for countdown in countdowns:
            try:
                zone = ZoneInfo(countdown["tz"])
            except ZoneInfoNotFoundError:
                zone = ZoneInfo("Asia/Taipei")
            remaining = days_remaining(countdown["target_date_utc"], datetime.now(zone))
            lines.append(
                f"・**{countdown['title']}**（還剩 {remaining} 天，"
                f"每天 {countdown['send_hour']:02d}:{countdown['send_minute']:02d}，"
                f"<#{countdown['channel_id']}>）\n"
                f"　ID: `{countdown['id']}`"
            )

        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @app_commands.command(name="cancel", description="取消一個倒數提醒")
    @app_commands.describe(countdown_id="倒數 ID（見 /countdown list）")
    @app_commands.guild_only()
    async def cancel(self, interaction: discord.Interaction, countdown_id: str) -> None:
        await self._cancel_impl(interaction, countdown_id)

    async def _cancel_impl(self, interaction: discord.Interaction, countdown_id: str) -> None:
        assert interaction.guild_id is not None

        countdown = await repo.owned_countdown(countdown_id, interaction.guild_id)
        if countdown is None:
            await interaction.response.send_message(
                f"找不到倒數 `{countdown_id}`（可能是打錯了，或屬於其他伺服器）。",
                ephemeral=True,
            )
            return

        guild_settings = await repo.get_guild_settings(interaction.guild_id)
        if not self._can_manage(interaction, countdown, guild_settings):
            await interaction.response.send_message(
                "只有建立者或管理員身分組可以取消這個倒數提醒。", ephemeral=True
            )
            return

        ok = await repo.cancel_countdown(countdown_id, interaction.guild_id)
        if not ok:
            await interaction.response.send_message(
                "這個倒數已經結束或取消過了。", ephemeral=True
            )
            return

        await interaction.response.send_message("✅ 倒數提醒已取消。", ephemeral=True)

    @tasks.loop(seconds=_TICK_SECONDS)
    async def countdown_tick(self) -> None:
        # ★ 跟 scheduler.reminder_tick／weekly_digest.digest_tick 同樣的理由：
        # bare except 把「這一輪失敗」和「這個功能死掉」徹底分開，DB 連線
        # 瞬斷是背景排程遇得到的常態，不該讓整個任務永久停止。
        try:
            countdowns = await repo.list_active_countdowns()
        except Exception:
            log.exception("撈取進行中的倒數提醒失敗，這一輪跳過，下一輪再試")
            return

        for countdown in countdowns:
            try:
                await self._maybe_send_countdown(countdown)
            except Exception:
                log.exception("倒數提醒 %s 處理失敗", countdown["id"])

    @countdown_tick.before_loop
    async def _before_countdown_tick(self) -> None:
        await self.bot.wait_until_ready()

    async def _maybe_send_countdown(self, countdown: Row) -> None:
        try:
            zone = ZoneInfo(countdown["tz"])
        except ZoneInfoNotFoundError:
            zone = ZoneInfo("Asia/Taipei")
        now_local = datetime.now(zone)

        if not should_send_today(
            countdown["send_hour"], countdown["send_minute"], countdown["last_sent_utc"], now_local
        ):
            return

        channel = await self._resolve_channel(int(countdown["channel_id"]))
        if channel is None:
            log.warning(
                "倒數提醒 %s 的頻道 %s 找不到（可能已被刪除），跳過",
                countdown["id"],
                countdown["channel_id"],
            )
            return

        remaining = days_remaining(countdown["target_date_utc"], now_local)
        embed = build_countdown_embed(countdown, remaining)

        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            log.warning("倒數提醒 %s 發送失敗", countdown["id"], exc_info=True)
            return

        await repo.mark_countdown_sent(countdown["id"], now_ms(), completed=remaining <= 0)

    async def _resolve_channel(self, channel_id: int) -> discord.abc.Messageable | None:
        channel = self.bot.get_channel(channel_id)
        if channel is not None:
            return channel
        try:
            return await self.bot.fetch_channel(channel_id)
        except discord.HTTPException:
            return None


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Countdown(bot))
