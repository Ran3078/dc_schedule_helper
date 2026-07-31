"""`/event` 指令群組：建立、列出、查看活動。"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from discord import app_commands
from discord.ext import commands

from src.bot.embeds import build_event_embed, build_event_list_embed
from src.bot.modals import EventDescriptionModal, PendingEvent
from src.db import repo
from src.domain.rsvp import build_rsvp_summary
from src.lib.ids import new_id
from src.lib.timeparse import TimeParseError, parse_datetime, parse_duration_minutes

log = logging.getLogger(__name__)

# Discord Embed 標題硬上限 256 字元；預留欄位標籤與圖示空間，訂一個更保守的上限
MAX_TITLE_LENGTH = 200

_SCOPE_TITLES = {
    "upcoming": "📅 即將到來的活動",
    "mine": "🙋 我建立的活動",
    "all": "🗂️ 全部活動（含已取消／已結束）",
}

# /event create 的 time 自動完成：常見的活動時段，覆蓋今天／明天／後天。
_QUICK_HOURS = (12, 14, 18, 19, 20, 21)
_DAY_LABELS = ("今天", "明天", "後天")

# duration 自動完成：不需要時區，固定候選即可。
_DURATION_PRESETS = ("30m", "1h", "1h30m", "2h", "2h30m", "3h", "4h", "12h", "24h")


class Events(commands.GroupCog, group_name="event", group_description="活動管理"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _guild_tz(self, guild_id: int) -> str:
        """取得該伺服器的預設時區。

        正常情況下 on_guild_join / on_ready 早就建好 guild_settings，這裡的
        self-heal 只防禦「bot 被邀入時剛好連線中斷導致沒建成」這類邊界情況，
        不是常態路徑。
        """
        settings = await repo.get_guild_settings(guild_id)
        if settings is None:
            default_tz = getattr(self.bot, "settings", None)
            tz = default_tz.default_tz if default_tz else "Asia/Taipei"
            await repo.ensure_guild(guild_id, tz)
            return tz
        return settings["default_tz"]

    @app_commands.command(name="create", description="建立新活動")
    @app_commands.describe(
        title="活動標題",
        time="活動時間，例如 2026-08-01 20:00 或 8/1 20:00（留空則改用日期時間挑選器）",
        location="地點（選填，可放語音頻道連結或實體地址）",
        duration="時長，例如 2h、90m（選填）",
    )
    @app_commands.guild_only()
    async def create(
        self,
        interaction: discord.Interaction,
        title: str,
        time: str | None = None,
        location: str | None = None,
        duration: str | None = None,
    ) -> None:
        assert interaction.guild_id is not None  # guild_only() 保證

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

        tz = await self._guild_tz(interaction.guild_id)

        # time 是選填的：留空的話，Modal 送出後會改顯示日期時間挑選器
        # （見 modals.py 的分流邏輯與 views_datetime.py 開頭對 Discord
        # 元件限制的說明 —— 沒有原生日期選擇元件，只能用下拉選單模擬）。
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
                await interaction.response.send_message(
                    "時長必須大於 0。", ephemeral=True
                )
                return

        pending = PendingEvent(
            guild_id=interaction.guild_id,
            channel_id=interaction.channel_id,
            creator_id=interaction.user.id,
            title=title,
            tz=tz,
            location=(location.strip() if location and location.strip() else None),
            duration_minutes=duration_minutes,
            starts_at_utc=starts_at_utc,
        )

        # Modal 必須是這個 interaction 的第一個回應，不能先 defer。
        # 活動內容（選填）在這一步之後才收集；送出後還有一道公開發布前的
        # 預覽確認，見 modals.py 與 views.ConfirmEventView 的說明。
        await interaction.response.send_modal(
            EventDescriptionModal(pending, event_id=new_id())
        )

    @create.autocomplete("time")
    async def create_time_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """建議「今天/明天/後天 幾點」的常見時段，減少要記格式的負擔。

        使用者仍可以直接手打任何支援的格式 —— 這裡只是快速選項，不是唯一輸入方式。
        """
        tz_name = "Asia/Taipei"
        if interaction.guild_id is not None:
            try:
                tz_name = await self._guild_tz(interaction.guild_id)
            except Exception:
                log.warning("time autocomplete 取得伺服器時區失敗，改用預設值", exc_info=True)

        try:
            tz = ZoneInfo(tz_name)
        except ZoneInfoNotFoundError:
            tz = ZoneInfo("Asia/Taipei")

        now = datetime.now(tz)
        choices: list[app_commands.Choice[str]] = []
        for day_offset, label in enumerate(_DAY_LABELS):
            date = (now + timedelta(days=day_offset)).replace(
                minute=0, second=0, microsecond=0
            )
            for hour in _QUICK_HOURS:
                candidate = date.replace(hour=hour)
                if candidate <= now:  # 已經過去的時段不建議，選了也沒意義
                    continue
                value = candidate.strftime("%m/%d %H:%M")
                choices.append(
                    app_commands.Choice(name=f"{label} {hour:02d}:00（{value}）", value=value)
                )

        typed = current.strip().lower()
        if typed:
            choices = [
                c for c in choices if typed in c.value.lower() or typed in c.name.lower()
            ]
        return choices[:25]

    @create.autocomplete("duration")
    async def create_duration_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        typed = current.strip().lower()
        return [
            app_commands.Choice(name=preset, value=preset)
            for preset in _DURATION_PRESETS
            if not typed or typed in preset.lower()
        ][:25]

    @app_commands.command(name="list", description="列出活動")
    @app_commands.describe(scope="範圍", limit="顯示筆數（預設 10，最多 25）")
    @app_commands.choices(
        scope=[
            app_commands.Choice(name="即將到來", value="upcoming"),
            app_commands.Choice(name="我建立的", value="mine"),
            app_commands.Choice(name="全部（含已取消／已結束）", value="all"),
        ]
    )
    @app_commands.guild_only()
    async def list_events(
        self,
        interaction: discord.Interaction,
        scope: app_commands.Choice[str] | None = None,
        limit: int = 10,
    ) -> None:
        assert interaction.guild_id is not None
        scope_value = scope.value if scope else "upcoming"
        limit = max(1, min(limit, 25))

        # DB 往返可能偶爾超過 3 秒的互動期限，先 defer 保險。
        # 非 ephemeral：活動列表本來就是給大家看的公開資訊。
        await interaction.response.defer(ephemeral=False)

        events = await repo.list_events(
            interaction.guild_id,
            scope=scope_value,
            user_id=interaction.user.id,
            limit=limit,
        )
        embed = build_event_list_embed(
            events, title=_SCOPE_TITLES.get(scope_value, "活動列表")
        )
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="info", description="查看活動詳細資訊")
    @app_commands.describe(event_id="活動 ID（見 /event list 或公告卡片下方）")
    @app_commands.guild_only()
    async def info(self, interaction: discord.Interaction, event_id: str) -> None:
        assert interaction.guild_id is not None
        await interaction.response.defer(ephemeral=False)

        event = await repo.owned_event(event_id, interaction.guild_id)
        if event is None:
            await interaction.followup.send(
                f"找不到活動 `{event_id}`（可能是打錯了，或該活動屬於其他伺服器）。",
                ephemeral=True,
            )
            return

        invitees = await repo.list_event_invitees(event_id, interaction.guild_id)
        rsvp_summary = None
        if interaction.guild is not None:
            rsvps = await repo.list_rsvps(event_id, interaction.guild_id)
            rsvp_summary = build_rsvp_summary(interaction.guild, invitees, rsvps)

        await interaction.followup.send(embed=build_event_embed(event, invitees, rsvp_summary))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Events(bot))
