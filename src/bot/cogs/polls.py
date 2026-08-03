"""`/poll` 指令群組：建立、關閉、查看投票結果。

選項走指令參數（`|` 分隔字串），不用 Modal——這是 `PLAN.md` §5.1 的原始設計
（`/poll create <question> <options: 用 | 分隔>`），跟 `/event create` 需要
邀請對象／內容那種多步驟互動鏈不是同一種複雜度，不需要比照那套流程。
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from src.bot.cogs._shared import guild_tz
from src.bot.embeds import build_poll_embed
from src.bot.views_poll import build_poll_vote_view
from src.db import repo
from src.domain.polls import MAX_OPTIONS, MIN_OPTIONS, split_options
from src.lib.ids import new_id
from src.lib.timeparse import TimeParseError, discord_timestamp, parse_datetime

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
        options="選項，用 | 分隔，例如「火鍋|燒肉|拉麵」（2～25 個）",
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
        options: str,
        multi: bool = False,
        anonymous: bool = False,
        allow_change: bool = True,
        closes: str | None = None,
        kind: app_commands.Choice[str] | None = None,
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

        raw_options = split_options(options)
        if not (MIN_OPTIONS <= len(raw_options) <= MAX_OPTIONS):
            await interaction.response.send_message(
                f"選項數量要在 {MIN_OPTIONS}～{MAX_OPTIONS} 個之間（目前 {len(raw_options)} 個，"
                "Discord 下拉選單本身就有 25 個的上限）。",
                ephemeral=True,
            )
            return

        kind_value = kind.value if kind else "generic"
        tz = await guild_tz(self.bot, interaction.guild_id)

        option_pairs: list[tuple[str, str | None]] = []
        if kind_value == "time_slot":
            for raw in raw_options:
                try:
                    epoch = parse_datetime(raw, tz)
                except TimeParseError as exc:
                    await interaction.response.send_message(
                        f"選項「{raw}」不是合法的時間：{exc}", ephemeral=True
                    )
                    return
                option_pairs.append((discord_timestamp(epoch, "F"), str(epoch)))
        else:
            option_pairs = [(raw[:100], None) for raw in raw_options]

        closes_at: int | None = None
        if closes:
            try:
                closes_at = parse_datetime(closes, tz)
            except TimeParseError as exc:
                await interaction.response.send_message(str(exc), ephemeral=True)
                return

        poll_id = new_id()
        await repo.create_poll(
            poll_id=poll_id,
            guild_id=interaction.guild_id,
            channel_id=interaction.channel_id,
            creator_id=interaction.user.id,
            question=question,
            options=option_pairs,
            kind=kind_value,
            multi=multi,
            anonymous=anonymous,
            allow_change=allow_change,
            closes_at=closes_at,
        )

        poll = await repo.owned_poll(poll_id, interaction.guild_id)
        assert poll is not None  # 剛剛才在同一次交易寫入
        poll_options = await repo.list_poll_options(poll_id, interaction.guild_id)

        await interaction.response.send_message(
            embed=build_poll_embed(poll, poll_options, []),
            view=build_poll_vote_view(poll_id, poll_options, multi=multi),
        )
        message = await interaction.original_response()
        await repo.set_poll_message(poll_id, interaction.guild_id, message.id)

    @app_commands.command(name="close", description="關閉投票")
    @app_commands.describe(poll_id="投票 ID（見公告卡片下方）")
    @app_commands.rename(poll_id="id")
    @app_commands.guild_only()
    async def close(self, interaction: discord.Interaction, poll_id: str) -> None:
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

        # 關閉成功後把公告卡片也更新成「已截止」＋停用下拉選單，讓還看得到
        # 舊訊息的人不會誤以為還能投——callback 裡的 status 檢查是最後一道
        # 防呆，這裡才是使用者實際會看到的視覺回饋。
        poll = await repo.owned_poll(poll_id, interaction.guild_id)
        if poll is None or not poll["message_id"] or not poll["channel_id"]:
            return
        channel = await self._resolve_channel(int(poll["channel_id"]))
        if channel is None:
            return
        options = await repo.list_poll_options(poll_id, interaction.guild_id)
        votes = await repo.list_poll_votes(poll_id, interaction.guild_id)
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
