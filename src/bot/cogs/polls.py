"""`/poll` 指令群組：建立、關閉、查看投票結果。

`create` 只收 question／multi／anonymous／allow_change／closes／kind 這些
單行或布林/選項類的指令參數，說明文字與選項（可能好幾行，或排程投票的候選
時段）改用 Modal／挑選器收——詳見 `modals_poll.PollDetailsModal` 開頭的說明。
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from src.bot.cogs._shared import guild_tz
from src.bot.embeds import Row, build_event_embed, build_poll_embed
from src.bot.modals_poll import PollDetailsModal
from src.bot.views_poll import build_poll_vote_view
from src.bot.views_rsvp import build_rsvp_view
from src.db import repo
from src.domain.polls import all_voter_ids, build_tally, pick_winning_time_slot
from src.domain.reminders import parse_default_reminders
from src.domain.rsvp import build_rsvp_summary
from src.lib.ids import new_id
from src.lib.mentions import build_allowed_mentions, build_mention_content
from src.lib.timeparse import TimeParseError, parse_datetime

log = logging.getLogger(__name__)

# Discord Embed 欄位標題上限 256 字元；預留欄位標籤空間，訂一個更保守的上限。
MAX_QUESTION_LENGTH = 200


class Polls(commands.GroupCog, group_name="poll", group_description="投票"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _resolve_channel(self, channel_id: int) -> discord.abc.Messageable | None:
        """比照 scheduler.py 的頻道解析：優先吃快取，沒有才補一次 API 呼叫。"""
        channel = self.bot.get_channel(channel_id)
        if channel is not None:
            return channel
        try:
            return await self.bot.fetch_channel(channel_id)
        except discord.HTTPException:
            return None

    @app_commands.command(name="create", description="建立投票")
    @app_commands.describe(
        question="投票問題",
        multi="是否可複選（預設否）",
        anonymous="是否匿名，只顯示票數不顯示是誰投的（預設否）",
        allow_change="投完後是否允許改票（預設允許）",
        closes="預計截止時間，例如 8/1 20:00（選填，僅顯示用，不會自動關閉）",
        kind="一般文字選項，或排程投票（選項會被解析成時間，顯示 Discord 時間戳）",
    )
    @app_commands.choices(
        kind=[
            app_commands.Choice(name="一般", value="generic"),
            app_commands.Choice(name="排程（選項是候選時間）", value="time_slot"),
        ]
    )
    @app_commands.guild_only()
    async def create(
        self,
        interaction: discord.Interaction,
        question: str,
        multi: bool = False,
        anonymous: bool = False,
        allow_change: bool = True,
        closes: str | None = None,
        kind: app_commands.Choice[str] | None = None,
    ) -> None:
        # 邏輯拆到 _create_impl：方便用假 interaction 直接測試指令參數的驗證，
        # 不用真的觸發 send_modal（比照 close/_close_impl 的拆法）。
        await self._create_impl(interaction, question, multi, anonymous, allow_change, closes, kind)

    async def _create_impl(
        self,
        interaction: discord.Interaction,
        question: str,
        multi: bool,
        anonymous: bool,
        allow_change: bool,
        closes: str | None,
        kind: app_commands.Choice[str] | None,
    ) -> None:
        assert interaction.guild_id is not None  # guild_only() 保證

        question = question.strip()
        if not question:
            await interaction.response.send_message("投票問題不能是空的。", ephemeral=True)
            return
        if len(question) > MAX_QUESTION_LENGTH:
            await interaction.response.send_message(
                f"投票問題太長了（{len(question)} 字，上限 {MAX_QUESTION_LENGTH} 字）。",
                ephemeral=True,
            )
            return

        kind_value = kind.value if kind else "generic"
        tz = await guild_tz(self.bot, interaction.guild_id)

        closes_at: int | None = None
        if closes:
            try:
                closes_at = parse_datetime(closes, tz)
            except TimeParseError as exc:
                await interaction.response.send_message(str(exc), ephemeral=True)
                return

        # Modal 必須是這個 interaction 的第一個回應，不能先 defer——所有不需要
        # 選項內容就能做的驗證（問題文字、closes 時間格式）都得在這之前做完。
        await interaction.response.send_modal(
            PollDetailsModal(
                question=question,
                multi=multi,
                anonymous=anonymous,
                allow_change=allow_change,
                closes_at=closes_at,
                kind=kind_value,
                tz=tz,
            )
        )

    @app_commands.command(name="close", description="關閉投票")
    @app_commands.describe(poll_id="投票 ID（見公告卡片下方）")
    @app_commands.rename(poll_id="id")
    @app_commands.guild_only()
    async def close(self, interaction: discord.Interaction, poll_id: str) -> None:
        # 邏輯拆到 _close_impl：app_commands.command 裝飾器包出來的方法不方便
        # 直接測試，拆出來的版本是普通 async 方法，可以用假 interaction 直接
        # 呼叫（比照 views.ConfirmEventView 的 confirm/_confirm_impl 拆法）。
        await self._close_impl(interaction, poll_id)

    async def _close_impl(self, interaction: discord.Interaction, poll_id: str) -> None:
        assert interaction.guild_id is not None

        poll = await repo.owned_poll(poll_id, interaction.guild_id)
        if poll is None:
            await interaction.response.send_message(
                f"找不到投票 `{poll_id}`（可能是打錯了，或該投票屬於其他伺服器）。",
                ephemeral=True,
            )
            return
        if poll["creator_id"] != str(interaction.user.id):
            await interaction.response.send_message(
                "只有建立投票的人可以關閉這個投票。", ephemeral=True
            )
            return

        ok = await repo.close_poll(poll_id, interaction.guild_id)
        if not ok:
            await interaction.response.send_message("這個投票已經是關閉狀態了。", ephemeral=True)
            return

        await interaction.response.send_message("投票已關閉。", ephemeral=True)

        poll = await repo.owned_poll(poll_id, interaction.guild_id)
        if poll is None:
            return
        options = await repo.list_poll_options(poll_id, interaction.guild_id)
        votes = await repo.list_poll_votes(poll_id, interaction.guild_id)

        # 關閉成功後把公告卡片也更新成「已截止」＋停用下拉選單，讓還看得到
        # 舊訊息的人不會誤以為還能投——callback 裡的 status 檢查是最後一道
        # 防呆，這裡才是使用者實際會看到的視覺回饋。
        if poll["message_id"] and poll["channel_id"]:
            channel = await self._resolve_channel(int(poll["channel_id"]))
            if channel is not None:
                try:
                    message = await channel.fetch_message(int(poll["message_id"]))
                    await message.edit(
                        embed=build_poll_embed(poll, options, votes),
                        view=build_poll_vote_view(
                            poll_id, options, multi=bool(poll["multi"]), disabled=True
                        ),
                    )
                except discord.HTTPException:
                    log.warning("關閉投票 %s 後更新公告訊息失敗", poll_id, exc_info=True)

        if poll["kind"] == "time_slot":
            await self._maybe_create_event_from_time_slot(interaction, poll, options, votes)

    async def _maybe_create_event_from_time_slot(
        self,
        interaction: discord.Interaction,
        poll: Row,
        options: list[Row],
        votes: list[Row],
    ) -> None:
        """時段投票關閉後，把票數最高的時段自動變成正式活動（`PLAN.md` §6
        Phase 2 第 1 項規劃的招牌功能）。

        參加對象＝這個投票裡所有投過票的人，不分投給哪個選項——誰投票就代表
        誰在意這個活動，跟「誰能 RSVP 不受邀請名單限制」（見 `PROCESS.md`
        第 8 點）同一種精神。平票／沒人投票都刻意不自動建立（見
        `domain.polls.pick_winning_time_slot`），只回覆訊息讓使用者自己判斷、
        手動 `/event create`。
        """
        assert interaction.guild_id is not None

        tally = build_tally(options, votes)
        winner, reason, tied = pick_winning_time_slot(options, tally)

        if reason == "no_votes":
            await interaction.followup.send("沒有人投票，不會自動建立活動。", ephemeral=True)
            return
        if reason == "tie":
            labels = "、".join(o["label"] for o in tied)
            await interaction.followup.send(
                f"最高票的時段平手（{labels}），不會自動建立活動，"
                "請自行用 `/event create` 建立。",
                ephemeral=True,
            )
            return

        assert winner is not None
        if winner["meta"] is None:
            await interaction.followup.send(
                "票數最高的選項沒有時間資料，已略過自動建立活動。", ephemeral=True
            )
            return
        try:
            starts_at_utc = int(winner["meta"])
        except (TypeError, ValueError):
            log.warning("投票 %s 的獲勝選項時間資料損毀：%r", poll["id"], winner["meta"])
            await interaction.followup.send(
                "票數最高的選項時間資料損毀，無法自動建立活動，"
                "請自行用 `/event create` 建立。",
                ephemeral=True,
            )
            return

        voter_ids = all_voter_ids(votes)
        tz = await guild_tz(self.bot, interaction.guild_id)
        guild_settings = await repo.get_guild_settings(interaction.guild_id)
        reminder_offsets = parse_default_reminders(
            guild_settings["default_reminders"] if guild_settings else None
        )

        event_id = new_id()
        await repo.create_event(
            event_id=event_id,
            guild_id=interaction.guild_id,
            channel_id=poll["channel_id"],
            creator_id=interaction.user.id,
            title=poll["question"],
            starts_at_utc=starts_at_utc,
            tz=tz,
            description=f"由投票 `{poll['id']}` 自動建立",
            user_ids=voter_ids,
            reminder_offsets_min=reminder_offsets,
        )
        event_row = await repo.owned_event(event_id, interaction.guild_id)
        assert event_row is not None  # 剛剛才在同一次交易寫入
        invitees = await repo.list_event_invitees(event_id, interaction.guild_id)
        rsvp_summary = (
            build_rsvp_summary(interaction.guild, invitees, rsvps=[])
            if interaction.guild is not None
            else None
        )

        channel = await self._resolve_channel(int(poll["channel_id"]))
        if channel is None:
            await interaction.followup.send(
                f"✅ 已依票數最高的時段自動建立活動（ID `{event_id}`），"
                f"但找不到頻道發公告，請用 `/event info {event_id}` 查看。",
                ephemeral=True,
            )
            return

        try:
            message = await channel.send(
                content=build_mention_content(voter_ids, [], tag_everyone=False),
                embed=build_event_embed(event_row, invitees, rsvp_summary),
                allowed_mentions=build_allowed_mentions(voter_ids, [], tag_everyone=False),
                view=build_rsvp_view(event_id),
            )
        except discord.HTTPException:
            log.exception("依投票 %s 自動建立的活動 %s 發布公告失敗", poll["id"], event_id)
            await interaction.followup.send(
                f"✅ 已依票數最高的時段自動建立活動（ID `{event_id}`），但公告訊息發送失敗，"
                f"請用 `/event info {event_id}` 查看。",
                ephemeral=True,
            )
            return

        await repo.set_event_message(event_id, interaction.guild_id, message.id)
        await interaction.followup.send(f"✅ 已自動建立活動：{message.jump_url}", ephemeral=True)

    @app_commands.command(name="results", description="查看投票結果")
    @app_commands.describe(poll_id="投票 ID（見公告卡片下方）")
    @app_commands.rename(poll_id="id")
    @app_commands.guild_only()
    async def results(self, interaction: discord.Interaction, poll_id: str) -> None:
        assert interaction.guild_id is not None
        await interaction.response.defer(ephemeral=False)

        poll = await repo.owned_poll(poll_id, interaction.guild_id)
        if poll is None:
            await interaction.followup.send(
                f"找不到投票 `{poll_id}`（可能是打錯了，或該投票屬於其他伺服器）。",
                ephemeral=True,
            )
            return

        options = await repo.list_poll_options(poll_id, interaction.guild_id)
        votes = await repo.list_poll_votes(poll_id, interaction.guild_id)
        await interaction.followup.send(embed=build_poll_embed(poll, options, votes))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Polls(bot))
