"""`/event` 指令群組：建立、列出、查看、編輯、取消、邀請、催促。

FF14 團本職位名額改由獨立指令 `/ff14_recruit`（見 `cogs/ff14.py`）在建立
活動當下一次收，不是這個群組底下的子指令——理由見該檔案開頭的說明。
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from discord import app_commands
from discord.ext import commands

from src.bot import native_events
from src.bot.cogs._shared import (
    DraftValidationError,
    is_organizer,
    resolve_user_tz,
    validate_event_draft,
)
from src.bot.embeds import Row, build_event_embed, build_event_list_embed
from src.bot.modals import EventDescriptionModal, EventEditModal
from src.bot.views_rsvp import build_event_controls_view
from src.db import repo
from src.domain.rsvp import build_rsvp_summary
from src.lib.ids import new_id
from src.lib.mentions import build_allowed_mentions, build_mention_content

log = logging.getLogger(__name__)

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

_RSVP_FILTER_LABELS = {"no_response": "未回覆", "yes": "參加", "maybe": "待定"}


class Events(commands.GroupCog, group_name="event", group_description="活動管理"):
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

    async def _resolve_announce_channel(
        self, interaction: discord.Interaction, guild_settings: Row | None
    ) -> discord.abc.Messageable | None:
        """`/event create` 發布公告要發到哪裡：`guild_settings.announce_channel_id`
        設定了就用那個頻道，沒設定維持發在指令所在頻道。"""
        channel_id = guild_settings["announce_channel_id"] if guild_settings else None
        if channel_id:
            channel = await self._resolve_channel(int(channel_id))
            if channel is not None:
                return channel
            log.warning("設定的公告頻道 %s 已經找不到了，改用指令所在頻道", channel_id)
        return interaction.channel

    def _can_manage(
        self, interaction: discord.Interaction, event: Row, guild_settings: Row | None
    ) -> bool:
        """活動建立者本人，或 `guild_settings.organizer_role_id` 設定的身分組
        成員，才能編輯/取消/邀請/催促這個活動。"""
        member = interaction.user
        if event["creator_id"] == str(member.id):
            return True
        if not isinstance(member, discord.Member):
            return False
        return is_organizer(member, guild_settings)

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
        # 邏輯拆到 _create_impl：方便用假 interaction 直接測權限檢查，不用真的
        # 觸發 send_modal（比照 polls.py 的 _create_impl 拆法）。
        await self._create_impl(interaction, title, time, location, duration)

    async def _create_impl(
        self,
        interaction: discord.Interaction,
        title: str,
        time: str | None,
        location: str | None,
        duration: str | None,
    ) -> None:
        assert interaction.guild_id is not None  # guild_only() 保證

        # 權限／標題／時間／時長驗證集中在 _shared.validate_event_draft——
        # /event create、/ff14_recruit、@提及選單的快速建立三個入口共用同一套
        # 規則，理由見該函式的說明。
        draft = await validate_event_draft(
            self.bot, interaction, title=title, time=time, location=location, duration=duration
        )
        if isinstance(draft, DraftValidationError):
            await interaction.response.send_message(draft.message, ephemeral=True)
            return

        # 公告要發到哪個頻道，這裡先定案，之後 EventDescriptionModal →
        # DateTimePickerView/InviteePickerView → ConfirmEventView 全程沿用
        # 這個 pending.channel_id，不必每一步都重新查一次
        # announce_channel_id（也不會發生「這一步查到的跟下一步查到的剛好
        # 不一樣」這種不一致）。validate_event_draft 回傳的 draft.channel_id
        # 只是 interaction.channel_id 佔位，這裡換成真正解析出來的公告頻道。
        guild_settings = await repo.get_guild_settings(interaction.guild_id)
        announce_channel = await self._resolve_announce_channel(interaction, guild_settings)
        channel_id = announce_channel.id if announce_channel is not None else interaction.channel_id
        pending = replace(draft, channel_id=channel_id)

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
                tz_name = await resolve_user_tz(
                    self.bot, interaction.guild_id, interaction.user.id
                )
            except Exception:
                log.warning("time autocomplete 取得時區失敗，改用預設值", exc_info=True)

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
        role_slots = await repo.list_event_role_slots(event_id, interaction.guild_id)
        role_signups = await repo.list_event_role_signups(event_id, interaction.guild_id)

        await interaction.followup.send(
            embed=build_event_embed(
                event, invitees, rsvp_summary, role_slots, role_signups
            )
        )

    @app_commands.command(name="edit", description="編輯活動")
    @app_commands.describe(event_id="活動 ID（見 /event list 或公告卡片下方）")
    @app_commands.guild_only()
    async def edit(self, interaction: discord.Interaction, event_id: str) -> None:
        await self._edit_impl(interaction, event_id)

    async def _edit_impl(self, interaction: discord.Interaction, event_id: str) -> None:
        assert interaction.guild_id is not None

        event = await repo.owned_event(event_id, interaction.guild_id)
        if event is None:
            await interaction.response.send_message(
                f"找不到活動 `{event_id}`（可能是打錯了，或該活動屬於其他伺服器）。",
                ephemeral=True,
            )
            return

        guild_settings = await repo.get_guild_settings(interaction.guild_id)
        if not self._can_manage(interaction, event, guild_settings):
            await interaction.response.send_message(
                "只有活動建立者或管理員身分組可以編輯這個活動。", ephemeral=True
            )
            return

        tz = await resolve_user_tz(self.bot, interaction.guild_id, interaction.user.id)
        # Modal 必須是這個 interaction 的第一個回應，不能先 defer。
        await interaction.response.send_modal(
            EventEditModal(event=event, event_id=event_id, guild_id=interaction.guild_id, tz=tz)
        )

    @app_commands.command(name="cancel", description="取消活動")
    @app_commands.describe(
        event_id="活動 ID（見 /event list 或公告卡片下方）",
        reason="取消原因（選填，會通知已回覆參加的人）",
    )
    @app_commands.guild_only()
    async def cancel(
        self, interaction: discord.Interaction, event_id: str, reason: str | None = None
    ) -> None:
        await self._cancel_impl(interaction, event_id, reason)

    async def _cancel_impl(
        self, interaction: discord.Interaction, event_id: str, reason: str | None
    ) -> None:
        assert interaction.guild_id is not None

        event = await repo.owned_event(event_id, interaction.guild_id)
        if event is None:
            await interaction.response.send_message(
                f"找不到活動 `{event_id}`（可能是打錯了，或該活動屬於其他伺服器）。",
                ephemeral=True,
            )
            return

        guild_settings = await repo.get_guild_settings(interaction.guild_id)
        if not self._can_manage(interaction, event, guild_settings):
            await interaction.response.send_message(
                "只有活動建立者或管理員身分組可以取消這個活動。", ephemeral=True
            )
            return

        ok = await repo.cancel_event(event_id, interaction.guild_id)
        if not ok:
            await interaction.response.send_message("這個活動已經是取消狀態了。", ephemeral=True)
            return

        await interaction.response.send_message("✅ 活動已取消。", ephemeral=True)

        event = await repo.owned_event(event_id, interaction.guild_id)
        assert event is not None  # 剛剛才取消成功
        invitees = await repo.list_event_invitees(event_id, interaction.guild_id)
        rsvps = await repo.list_rsvps(event_id, interaction.guild_id)
        role_slots = await repo.list_event_role_slots(event_id, interaction.guild_id)
        role_signups = await repo.list_event_role_signups(event_id, interaction.guild_id)
        summary = (
            build_rsvp_summary(interaction.guild, invitees, rsvps)
            if interaction.guild is not None
            else None
        )

        # 同步取消原生活動（M6 當時只做 sync_create，這裡是那個缺口的呼叫端）。
        if event["discord_event_id"] and interaction.guild is not None:
            await native_events.sync_cancel(interaction.guild, int(event["discord_event_id"]))

        channel = None
        if event["channel_id"]:
            channel = await self._resolve_channel(int(event["channel_id"]))
        if channel is None:
            return

        # 公告卡片重繪成刪除線+已取消，RSVP 按鈕與職位選單全部 disabled——
        # RsvpButton／PositionSelect callback 裡的活動狀態檢查是最後一道
        # 防呆，這裡才是使用者實際會看到的視覺回饋。
        if event["message_id"]:
            try:
                message = await channel.fetch_message(int(event["message_id"]))
                await message.edit(
                    embed=build_event_embed(
                        event, invitees, summary, role_slots, role_signups
                    ),
                    view=build_event_controls_view(
                        event_id, role_slots, role_signups, disabled=True
                    ),
                )
            except discord.HTTPException:
                log.warning("取消活動 %s 後更新公告訊息失敗", event_id, exc_info=True)

        yes_ids = summary.yes if summary else []
        if not yes_ids:
            return
        text = f"🚫 活動「{event['title']}」已取消。"
        if reason:
            text += f"\n原因：{reason}"
        content = build_mention_content(yes_ids, [], tag_everyone=False)
        try:
            await channel.send(
                content=f"{content}\n{text}" if content else text,
                allowed_mentions=build_allowed_mentions(yes_ids, [], tag_everyone=False),
            )
        except discord.HTTPException:
            log.warning("取消活動 %s 後通知參加者失敗", event_id, exc_info=True)

    @app_commands.command(name="invite", description="追加參加對象")
    @app_commands.describe(
        event_id="活動 ID（見 /event list 或公告卡片下方）",
        user="要邀請的人（選填）",
        role="要邀請的身分組（選填）",
        everyone="邀請所有人（選填，伺服器要先用 /settings 開放 @everyone 通知）",
    )
    @app_commands.guild_only()
    async def invite(
        self,
        interaction: discord.Interaction,
        event_id: str,
        user: discord.Member | None = None,
        role: discord.Role | None = None,
        everyone: bool = False,
    ) -> None:
        await self._invite_impl(interaction, event_id, user, role, everyone)

    async def _invite_impl(
        self,
        interaction: discord.Interaction,
        event_id: str,
        user: discord.Member | None,
        role: discord.Role | None,
        everyone: bool,
    ) -> None:
        assert interaction.guild_id is not None

        if user is None and role is None and not everyone:
            await interaction.response.send_message(
                "請至少指定 user、role 其中一個，或把 everyone 設成 True。", ephemeral=True
            )
            return

        event = await repo.owned_event(event_id, interaction.guild_id)
        if event is None:
            await interaction.response.send_message(
                f"找不到活動 `{event_id}`（可能是打錯了，或該活動屬於其他伺服器）。",
                ephemeral=True,
            )
            return

        guild_settings = await repo.get_guild_settings(interaction.guild_id)
        if not self._can_manage(interaction, event, guild_settings):
            await interaction.response.send_message(
                "只有活動建立者或管理員身分組可以追加參加對象。", ephemeral=True
            )
            return

        if everyone and not (guild_settings and guild_settings["allow_everyone_ping"]):
            await interaction.response.send_message(
                "這個伺服器沒有開放 @everyone 通知，請洽管理員用 /settings 開啟。",
                ephemeral=True,
            )
            return

        if user is not None:
            await repo.add_event_invitee(event_id, interaction.guild_id, "user", user.id)
        if role is not None:
            await repo.add_event_invitee(event_id, interaction.guild_id, "role", role.id)
        if everyone:
            await repo.add_event_invitee(
                event_id, interaction.guild_id, "everyone", interaction.guild_id
            )

        await interaction.response.send_message("✅ 已追加參加對象。", ephemeral=True)

        invitees = await repo.list_event_invitees(event_id, interaction.guild_id)
        rsvps = await repo.list_rsvps(event_id, interaction.guild_id)
        role_slots = await repo.list_event_role_slots(event_id, interaction.guild_id)
        role_signups = await repo.list_event_role_signups(event_id, interaction.guild_id)
        summary = (
            build_rsvp_summary(interaction.guild, invitees, rsvps)
            if interaction.guild is not None
            else None
        )

        channel = None
        if event["channel_id"]:
            channel = await self._resolve_channel(int(event["channel_id"]))
        if channel is None:
            return

        if event["message_id"]:
            try:
                message = await channel.fetch_message(int(event["message_id"]))
                # 沒帶 view=：這裡不動任何控制元件，只重繪 embed（見 edit()
                # 的語意——省略的參數維持原樣，不會把 view 清空）。role_slots/
                # role_signups 還是要帶，否則已經設定過職位的活動會在這次
                # 重繪後憑空少掉那幾個欄位（embed 是整包替換，不是只補丁）。
                await message.edit(
                    embed=build_event_embed(
                        event, invitees, summary, role_slots, role_signups
                    )
                )
            except discord.HTTPException:
                log.warning("追加參加對象後更新活動 %s 公告訊息失敗", event_id, exc_info=True)

        user_ids = [user.id] if user is not None else []
        role_ids = [role.id] if role is not None else []
        content = build_mention_content(user_ids, role_ids, tag_everyone=everyone)
        if content is None:
            return
        text = f"{content} 你被邀請參加「{event['title']}」！"
        if event["message_id"]:
            text += (
                f"\nhttps://discord.com/channels/"
                f"{interaction.guild_id}/{event['channel_id']}/{event['message_id']}"
            )
        try:
            await channel.send(
                content=text,
                allowed_mentions=build_allowed_mentions(user_ids, role_ids, tag_everyone=everyone),
            )
        except discord.HTTPException:
            log.warning("邀請通知傳送失敗（活動 %s）", event_id, exc_info=True)

    @app_commands.command(name="ping", description="催促尚未回覆的人")
    @app_commands.describe(
        event_id="活動 ID（見 /event list 或公告卡片下方）",
        rsvp_filter="要催的對象（預設未回覆）",
    )
    @app_commands.rename(rsvp_filter="filter")
    @app_commands.choices(
        rsvp_filter=[
            app_commands.Choice(name="未回覆", value="no_response"),
            app_commands.Choice(name="參加", value="yes"),
            app_commands.Choice(name="待定", value="maybe"),
        ]
    )
    @app_commands.guild_only()
    async def ping(
        self,
        interaction: discord.Interaction,
        event_id: str,
        rsvp_filter: app_commands.Choice[str] | None = None,
    ) -> None:
        await self._ping_impl(interaction, event_id, rsvp_filter)

    async def _ping_impl(
        self,
        interaction: discord.Interaction,
        event_id: str,
        rsvp_filter: app_commands.Choice[str] | None,
    ) -> None:
        assert interaction.guild_id is not None

        event = await repo.owned_event(event_id, interaction.guild_id)
        if event is None:
            await interaction.response.send_message(
                f"找不到活動 `{event_id}`（可能是打錯了，或該活動屬於其他伺服器）。",
                ephemeral=True,
            )
            return

        guild_settings = await repo.get_guild_settings(interaction.guild_id)
        if not self._can_manage(interaction, event, guild_settings):
            await interaction.response.send_message(
                "只有活動建立者或管理員身分組可以催促。", ephemeral=True
            )
            return

        if interaction.guild is None:
            await interaction.response.send_message(
                "目前無法計算名單，請稍後再試。", ephemeral=True
            )
            return

        filter_value = rsvp_filter.value if rsvp_filter else "no_response"
        invitees = await repo.list_event_invitees(event_id, interaction.guild_id)
        rsvps = await repo.list_rsvps(event_id, interaction.guild_id)
        summary = build_rsvp_summary(interaction.guild, invitees, rsvps)
        target_ids: list[int] = {
            "no_response": summary.no_response,
            "yes": summary.yes,
            "maybe": summary.maybe,
        }[filter_value]

        if not target_ids:
            await interaction.response.send_message(
                f"目前沒有「{_RSVP_FILTER_LABELS[filter_value]}」的人可以催。", ephemeral=True
            )
            return

        channel = None
        if event["channel_id"]:
            channel = await self._resolve_channel(int(event["channel_id"]))
        if channel is None:
            await interaction.response.send_message(
                "找不到活動所在的頻道，無法發送催促訊息。", ephemeral=True
            )
            return

        content = build_mention_content(target_ids, [], tag_everyone=False)
        text = f"{content}\n⏰ 別忘了回覆「{event['title']}」的參加意願！"
        if event["message_id"]:
            text += (
                f"\nhttps://discord.com/channels/"
                f"{interaction.guild_id}/{event['channel_id']}/{event['message_id']}"
            )
        try:
            await channel.send(
                content=text,
                allowed_mentions=build_allowed_mentions(target_ids, [], tag_everyone=False),
            )
        except discord.HTTPException:
            log.warning("催促訊息傳送失敗（活動 %s）", event_id, exc_info=True)
            await interaction.response.send_message(
                "催促訊息發送失敗，請稍後再試。", ephemeral=True
            )
            return

        await interaction.response.send_message(f"✅ 已催促 {len(target_ids)} 人。", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Events(bot))
