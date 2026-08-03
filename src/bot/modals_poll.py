"""`/poll create` 的選項輸入 Modal。

原本選項是塞在 `options` 這個指令參數裡、用 `|` 分隔（`PLAN.md` §5.1 的原始
設計），實際用起來才發現在 Discord 的單行指令參數輸入框裡打 `2425|8/2 20:00`
這種東西很不方便，尤其是排程投票要打好幾個時間。改成跟 `/event create` 的
活動內容一樣，先收完其他指令參數，再彈一個 Modal 用多行文字框收選項——
一行一個選項，跟打字直覺一致（見 `modals.py` 開頭對 Modal 只能是 interaction
第一個回應的限制說明；這裡 Modal 是 `/poll create` 這個 interaction 的第一個
回應，指令參數驗證都得在彈出 Modal 之前做完）。
"""

from __future__ import annotations

import logging

import discord

from src.bot.embeds import build_poll_embed
from src.bot.views_poll import build_poll_vote_view
from src.db import repo
from src.domain.polls import MAX_OPTIONS, MIN_OPTIONS, split_options
from src.lib.ids import new_id
from src.lib.timeparse import TimeParseError, discord_timestamp, parse_datetime

log = logging.getLogger(__name__)


class PollOptionsModal(discord.ui.Modal, title="投票選項"):
    options_input: discord.ui.TextInput[PollOptionsModal] = discord.ui.TextInput(
        label=f"選項（每行一個，{MIN_OPTIONS}～{MAX_OPTIONS} 個）",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=2000,
        placeholder="火鍋\n燒肉\n拉麵\n\n排程投票每行填一個候選時間，例如：\n8/1 20:00\n8/2 20:00",
    )

    def __init__(
        self,
        *,
        question: str,
        multi: bool,
        anonymous: bool,
        allow_change: bool,
        closes_at: int | None,
        kind: str,
        tz: str,
    ) -> None:
        super().__init__()
        self.question = question
        self.multi = multi
        self.anonymous = anonymous
        self.allow_change = allow_change
        self.closes_at = closes_at
        self.kind = kind
        self.tz = tz

    async def on_submit(self, interaction: discord.Interaction) -> None:
        assert interaction.guild_id is not None  # /poll create 本身已 guild_only()

        raw_options = split_options(self.options_input.value)
        if not (MIN_OPTIONS <= len(raw_options) <= MAX_OPTIONS):
            await interaction.response.send_message(
                f"選項數量要在 {MIN_OPTIONS}～{MAX_OPTIONS} 個之間（目前 {len(raw_options)} 個，"
                "Discord 下拉選單本身就有 25 個的上限）。",
                ephemeral=True,
            )
            return

        option_pairs: list[tuple[str, str | None]] = []
        if self.kind == "time_slot":
            for raw in raw_options:
                try:
                    epoch = parse_datetime(raw, self.tz)
                except TimeParseError as exc:
                    await interaction.response.send_message(
                        f"選項「{raw}」不是合法的時間：{exc}", ephemeral=True
                    )
                    return
                option_pairs.append((discord_timestamp(epoch, "F"), str(epoch)))
        else:
            option_pairs = [(raw[:100], None) for raw in raw_options]

        poll_id = new_id()
        await repo.create_poll(
            poll_id=poll_id,
            guild_id=interaction.guild_id,
            channel_id=interaction.channel_id,
            creator_id=interaction.user.id,
            question=self.question,
            options=option_pairs,
            kind=self.kind,
            multi=self.multi,
            anonymous=self.anonymous,
            allow_change=self.allow_change,
            closes_at=self.closes_at,
        )

        poll = await repo.owned_poll(poll_id, interaction.guild_id)
        assert poll is not None  # 剛剛才在同一次交易寫入
        poll_options = await repo.list_poll_options(poll_id, interaction.guild_id)

        await interaction.response.send_message(
            embed=build_poll_embed(poll, poll_options, []),
            view=build_poll_vote_view(poll_id, poll_options, multi=self.multi),
        )
        message = await interaction.original_response()
        await repo.set_poll_message(poll_id, interaction.guild_id, message.id)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.exception("建立投票時發生未預期錯誤", exc_info=error)
        message = "建立投票時發生錯誤，請稍後再試一次。"
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
