"""`/poll create` 的說明／選項輸入 Modal。

`kind` 在指令參數那一步就已經選定（見 `cogs/polls.py` 的
`@app_commands.choices`），Modal 彈出時已經知道是排程投票還是一般投票，兩種
情況的欄位因此不一樣：

- 一律有「投票敘述（選填）」——跟 `/event create` 的活動內容欄位同樣性質。
- `kind=generic` 才多一個「選項（多行）」欄位，使用者自己打，一行一個選項。
- `kind=time_slot` **不**收選項文字——候選時段改用
  `views_poll_timeslot.TimeSlotPickerView` 的日期/小時/分鐘下拉選單挑選，
  全程不用打字（原本這裡會解析每行時間字串，那段邏輯搬到
  `TimeSlotPickerView._on_create` 了）。

Modal 只能是 interaction 的**第一個**回應（不能先 defer 再彈 Modal），所以
`/poll create` 的 question/multi/anonymous/allow_change/closes/kind 驗證都得
在彈出這個 Modal 之前做完（見 `cogs/polls.py` 的 `_create_impl`）。
"""

from __future__ import annotations

import logging

import discord

from src.bot.embeds import build_poll_embed
from src.bot.views_poll import build_poll_vote_view
from src.bot.views_poll_timeslot import PendingTimeSlotPoll, TimeSlotPickerView
from src.db import repo
from src.domain.polls import MAX_OPTIONS, MIN_OPTIONS, split_options
from src.lib.ids import new_id

log = logging.getLogger(__name__)

_OPTIONS_LABEL = f"選項（每行一個，{MIN_OPTIONS}～{MAX_OPTIONS} 個）"
_OPTIONS_PLACEHOLDER = "每行填一個選項，例如：\n火鍋\n燒肉\n拉麵"


class PollDetailsModal(discord.ui.Modal, title="投票內容"):
    """`description_input`／`options_input` 刻意不宣告成類別屬性，全部在
    `__init__` 用建構參數生出來——`discord.ui.TextInput` 建構後才去改
    `.label` 在目前 discord.py 版本會觸發 `DeprecationWarning`（未來改走
    `discord.ui.Label` 包裝元件），而且 `options_input` 本來就只有
    `kind=generic` 才需要存在，用 `add_item()` 動態加比宣告成永遠存在的類別
    屬性更貼近實際情況。
    """

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

        self.description_input: discord.ui.TextInput[PollDetailsModal] = discord.ui.TextInput(
            label="投票敘述（選填）",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=500,
            placeholder="例如：這次要約平日還是假日晚上",
        )
        self.add_item(self.description_input)

        self.options_input: discord.ui.TextInput[PollDetailsModal] | None = None
        if kind == "generic":
            self.options_input = discord.ui.TextInput(
                label=_OPTIONS_LABEL,
                style=discord.TextStyle.paragraph,
                required=True,
                max_length=2000,
                placeholder=_OPTIONS_PLACEHOLDER,
            )
            self.add_item(self.options_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        assert interaction.guild_id is not None  # /poll create 本身已 guild_only()

        description = self.description_input.value.strip() or None

        if self.kind == "time_slot":
            # 候選時段改用下拉選單挑，不在這裡解析任何時間字串——見
            # TimeSlotPickerView._on_create。
            picker = TimeSlotPickerView(
                params=PendingTimeSlotPoll(
                    guild_id=interaction.guild_id,
                    channel_id=interaction.channel_id,
                    creator_id=interaction.user.id,
                    question=self.question,
                    multi=self.multi,
                    anonymous=self.anonymous,
                    allow_change=self.allow_change,
                    closes_at=self.closes_at,
                    description=description,
                ),
                tz=self.tz,
            )
            await interaction.response.send_message(
                embed=picker.build_embed(), view=picker, ephemeral=True
            )
            picker.message = await interaction.original_response()
            return

        assert self.options_input is not None  # kind=generic 時一定有這個欄位
        raw_options = split_options(self.options_input.value)
        if not (MIN_OPTIONS <= len(raw_options) <= MAX_OPTIONS):
            await interaction.response.send_message(
                f"選項數量要在 {MIN_OPTIONS}～{MAX_OPTIONS} 個之間（目前 {len(raw_options)} 個，"
                "Discord 下拉選單本身就有 25 個的上限）。",
                ephemeral=True,
            )
            return
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
            description=description,
        )

        poll = await repo.owned_poll(poll_id, interaction.guild_id)
        assert poll is not None  # 剛剛才在同一次交易寫入
        poll_options = await repo.list_poll_options(poll_id, interaction.guild_id)

        await interaction.response.send_message(
            embed=build_poll_embed(poll, poll_options, [], interaction.guild),
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
