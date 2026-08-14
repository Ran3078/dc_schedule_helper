"""排程投票（`kind=time_slot`）的候選時段挑選器。

`/poll create` 選了「排程」之後，選項不再讓使用者手動打時間字串——改成跟
`/event create` 省略 `time` 時一樣的下拉選單（見 `views_datetime.py` 開頭對
Discord 沒有原生日期元件的說明），重複「選日期時間 → 按新增」來累積候選
時段，全程不用打字，最後按「建立投票」才真的寫入資料庫、公開發布。

日期/小時/分鐘的 Select 元件與時區解析直接沿用 `views_datetime.py`（見該檔案
對 `DateSelect`/`HourSelect`/`MinuteSelect`/`resolve_tz` 為何升級成模組內共用
元件的說明），這裡不重新實作一份。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import discord

from src.bot.embeds import build_poll_embed
from src.bot.views_datetime import (
    WEEKDAY_LABELS,
    WINDOW_DAYS,
    DateSelect,
    HourSelect,
    MinuteSelect,
    resolve_tz,
)
from src.bot.views_poll import build_poll_vote_view
from src.db import repo
from src.domain.polls import MAX_OPTIONS, MIN_OPTIONS
from src.lib.ids import new_id
from src.lib.timeparse import discord_timestamp

log = logging.getLogger(__name__)


def _plain_label(epoch_ms: int, tz: ZoneInfo) -> str:
    """把候選時段格式化成純文字，給投票下拉選單的 `SelectOption.label` 用。

    `<t:epoch:F>` 這種 Discord 時間戳語法只有在 embed／訊息內容裡才會被
    客戶端解析成好看的日期時間——**Select 選單的選項文字是純文字欄位，不會
    解析任何 markup**，直接塞 `<t:...>` 進去，使用者在下拉選單看到的就是那串
    原始字元（這正是這次要修的 bug）。投票卡片本身（embed）另外用
    `discord_timestamp()` 顯示，兩處各自用最適合的格式，不能共用同一個
    `label` 字串。
    """
    dt = datetime.fromtimestamp(epoch_ms / 1000, tz=tz)
    return f"{dt.month}/{dt.day}（{WEEKDAY_LABELS[dt.weekday()]}）{dt.hour:02d}:{dt.minute:02d}"


@dataclass(frozen=True, slots=True)
class PendingTimeSlotPoll:
    """`/poll create` 的指令參數與 Modal 內容都已驗證完成，只差候選時段還沒選。"""

    guild_id: int
    channel_id: int
    creator_id: int
    question: str
    multi: bool
    anonymous: bool
    allow_change: bool
    closes_at: int | None
    description: str | None


class TimeSlotPickerView(discord.ui.View):
    """3 個下拉選單（日期/小時/分鐘）+ 2 列按鈕（翻頁＋新增／移除＋建立＋取消）。

    元件權重總和 21（3 個 select 各權重 5 = 15，6 個按鈕權重 6），在單則訊息
    25 的權重上限之內，5 個操作列（0–4）剛好用滿。
    """

    def __init__(self, *, params: PendingTimeSlotPoll, tz: str) -> None:
        super().__init__(timeout=300)  # 5 分鐘沒選完就作廢，避免預覽訊息無限期卡著
        self.params = params
        self.message: discord.Message | None = None

        self.tz = resolve_tz(tz)
        today = datetime.now(self.tz).date()
        self.window_start = today
        self.selected_date: date | None = None
        self.selected_hour: int | None = None
        self.selected_minute: int | None = None
        # 依新增順序累積，不自動排序——使用者通常本來就會依時間先後點選。
        self.candidates: list[int] = []

        self.date_select = DateSelect(self)
        self.add_item(self.date_select)

        self.hour_select = HourSelect(self)
        self.add_item(self.hour_select)

        self.minute_select = MinuteSelect(self)
        self.add_item(self.minute_select)

        self.prev_button: discord.ui.Button = discord.ui.Button(
            label="◀ 更早", style=discord.ButtonStyle.secondary, row=3, disabled=True
        )
        self.prev_button.callback = self._on_prev
        self.add_item(self.prev_button)

        self.next_button: discord.ui.Button = discord.ui.Button(
            label="更晚 ▶", style=discord.ButtonStyle.secondary, row=3
        )
        self.next_button.callback = self._on_next
        self.add_item(self.next_button)

        self.add_button: discord.ui.Button = discord.ui.Button(
            label="➕ 新增這個時間",
            style=discord.ButtonStyle.primary,
            row=3,
            disabled=True,
        )
        self.add_button.callback = self._on_add
        self.add_item(self.add_button)

        self.remove_button: discord.ui.Button = discord.ui.Button(
            label="↩️ 移除最後一個",
            style=discord.ButtonStyle.secondary,
            row=4,
            disabled=True,
        )
        self.remove_button.callback = self._on_remove_last
        self.add_item(self.remove_button)

        self.create_button: discord.ui.Button = discord.ui.Button(
            label="✅ 建立投票",
            style=discord.ButtonStyle.success,
            row=4,
            disabled=True,
        )
        self.create_button.callback = self._on_create
        self.add_item(self.create_button)

        self.cancel_button: discord.ui.Button = discord.ui.Button(
            label="❌ 取消", style=discord.ButtonStyle.secondary, row=4
        )
        self.cancel_button.callback = self._on_cancel
        self.add_item(self.cancel_button)

    def build_embed(self) -> discord.Embed:
        date_text = (
            f"{self.selected_date.month}/{self.selected_date.day}"
            f"（{WEEKDAY_LABELS[self.selected_date.weekday()]}）"
            if self.selected_date
            else "（尚未選擇）"
        )
        time_text = (
            f"{self.selected_hour} 點 {self.selected_minute:02d} 分"
            if self.selected_hour is not None and self.selected_minute is not None
            else "（尚未選擇）"
        )

        if self.candidates:
            candidates_text = "\n".join(discord_timestamp(e, "F") for e in self.candidates)
        else:
            candidates_text = "（尚未新增）"

        description = f"{self.params.description}\n\n" if self.params.description else ""

        return discord.Embed(
            title=f"🗳️ {self.params.question}",
            description=(
                f"{description}"
                f"已選候選時段（{len(self.candidates)}/{MAX_OPTIONS}）：\n{candidates_text}\n\n"
                f"目前選取：{date_text}　{time_text}\n\n"
                "日期與時間選好後按「➕ 新增這個時間」加入候選名單，可以重複新增；"
                f"至少需要 {MIN_OPTIONS} 個候選時段才能按「✅ 建立投票」。"
                "「更晚 ▶」可以往後翻找更遠的日期。"
            ),
            colour=discord.Colour.blurple(),
        )

    def _all_selected(self) -> bool:
        return (
            self.selected_date is not None
            and self.selected_hour is not None
            and self.selected_minute is not None
        )

    def _refresh_button_states(self) -> None:
        self.add_button.disabled = not self._all_selected() or len(
            self.candidates
        ) >= MAX_OPTIONS
        self.remove_button.disabled = not self.candidates
        self.create_button.disabled = len(self.candidates) < MIN_OPTIONS
        today = datetime.now(self.tz).date()
        self.prev_button.disabled = self.window_start <= today

    async def rerender(self, interaction: discord.Interaction) -> None:
        self._refresh_button_states()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def _on_prev(self, interaction: discord.Interaction) -> None:
        today = datetime.now(self.tz).date()
        self.window_start = max(today, self.window_start - timedelta(days=WINDOW_DAYS))
        self.date_select.refresh()
        await self.rerender(interaction)

    async def _on_next(self, interaction: discord.Interaction) -> None:
        self.window_start = self.window_start + timedelta(days=WINDOW_DAYS)
        self.date_select.refresh()
        await self.rerender(interaction)

    async def _on_add(self, interaction: discord.Interaction) -> None:
        assert self.selected_date is not None
        assert self.selected_hour is not None
        assert self.selected_minute is not None

        naive = datetime(
            self.selected_date.year,
            self.selected_date.month,
            self.selected_date.day,
            self.selected_hour,
            self.selected_minute,
            tzinfo=self.tz,
        )
        epoch = int(naive.timestamp() * 1000)
        if epoch not in self.candidates and len(self.candidates) < MAX_OPTIONS:
            self.candidates.append(epoch)
        await self.rerender(interaction)

    async def _on_remove_last(self, interaction: discord.Interaction) -> None:
        if self.candidates:
            self.candidates.pop()
        await self.rerender(interaction)

    async def _on_cancel(self, interaction: discord.Interaction) -> None:
        for child in self.children:
            child.disabled = True  # type: ignore[attr-defined]
        self.stop()
        await interaction.response.edit_message(
            content="已取消，投票未建立。", embed=None, view=self
        )

    async def _on_create(self, interaction: discord.Interaction) -> None:
        option_pairs = [(_plain_label(e, self.tz), str(e)) for e in self.candidates]

        poll_id = new_id()
        await repo.create_poll(
            poll_id=poll_id,
            guild_id=self.params.guild_id,
            channel_id=self.params.channel_id,
            creator_id=self.params.creator_id,
            question=self.params.question,
            options=option_pairs,
            kind="time_slot",
            multi=self.params.multi,
            anonymous=self.params.anonymous,
            allow_change=self.params.allow_change,
            closes_at=self.params.closes_at,
            description=self.params.description,
        )
        poll = await repo.owned_poll(poll_id, self.params.guild_id)
        assert poll is not None  # 剛剛才在同一次交易寫入
        poll_options = await repo.list_poll_options(poll_id, self.params.guild_id)

        for child in self.children:
            child.disabled = True  # type: ignore[attr-defined]
        self.stop()

        message = None
        channel = interaction.channel
        # 用 duck typing 而非 isinstance(..., discord.abc.Messageable)：比照
        # views.ConfirmEventView._confirm_impl 的理由，測試時可以用簡單的假
        # 物件替代，不必仿造 Discord 內部的頻道類別階層。
        if channel is not None and hasattr(channel, "send"):
            try:
                message = await channel.send(
                    embed=build_poll_embed(poll, poll_options, [], interaction.guild),
                    view=build_poll_vote_view(poll_id, poll_options, multi=self.params.multi),
                )
            except discord.HTTPException:
                log.exception("投票 %s 已建立，但發布公告訊息失敗", poll_id)

        if message is not None:
            await repo.set_poll_message(poll_id, self.params.guild_id, message.id)
            content = f"✅ 投票已發布：{message.jump_url}"
        else:
            content = (
                f"✅ 投票已建立（ID `{poll_id}`），但公告訊息發送失敗，"
                f"請用 `/poll results {poll_id}` 查看。"
            )
        await interaction.response.edit_message(content=content, embed=None, view=self)

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True  # type: ignore[attr-defined]
        if self.message is not None:
            try:
                await self.message.edit(
                    content="⌛ 已逾時未完成，投票未建立。請重新使用 `/poll create`。",
                    embed=None,
                    view=self,
                )
            except discord.HTTPException:
                pass  # 訊息可能已被使用者刪除，逾時清理失敗不影響任何資料正確性
