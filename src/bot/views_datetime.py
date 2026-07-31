"""日期／時間挑選器 —— `/event create` 省略 `time` 參數時的替代輸入方式。

Discord **沒有原生的日期選擇元件**。開發者社群多年來一直在跟官方要這個功能
（見 discord-api-docs 的 discussion #3937、#6988），但目前 bot 能用的元件
只有按鈕、下拉選單（最多 25 個選項）、Modal 文字輸入。

更關鍵的限制：一個「操作列」最多只能放 **5 個按鈕**（`discord.ui.ActionRow`
權重超過 5 就直接拋 `ValueError` —— 這是 Discord API 本身的規則，不是
discord.py 的選擇，Components V2 也沒有放寬）。這代表「一週 7 天排成一列」
的傳統月曆格線在 Discord 裡**做不出來**，不管用不用 V2 元件都一樣。

因此這裡的做法是用下拉選單模擬：
・日期用「捲動 14 天視窗」而非對齊月曆邊界 —— 對齊月份會撞到「一個月最多
  31 天，但 Select 選項上限只有 25」的問題，捲動視窗完全避開這個麻煩。
・幾點、幾分、持續時間各自一個下拉選單。
・全部選完日期與時間才能按「確認時間」（持續時間選填，不影響能否確認），
  此時直接接手到 ConfirmEventView 的預覽發布流程（沿用同一套已測試過的
  確認/取消/發布邏輯，不重新發明一次）。

一則訊息只有 5 個操作列可用（列索引 0–4），4 個下拉選單（日期/小時/分鐘/
持續時間）已經用滿 4 列，因此翻頁與確認/取消這 4 個按鈕全部併到同一列
（權重 4，仍在單列上限 5 之內）。
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord

from src.bot.modals import PendingEvent

log = logging.getLogger(__name__)

_WINDOW_DAYS = 14
_WEEKDAY_LABELS = ("一", "二", "三", "四", "五", "六", "日")  # date.weekday(): 週一=0
_MINUTE_OPTIONS = (0, 15, 30, 45)

# 持續時間選項：value 用 "none" 代表「不設定」，而非空字串 —— 空字串當
# select value 容易在序列化/比對時出問題，用明確字串比較保險。
_DURATION_CHOICES: tuple[tuple[str, int | None], ...] = (
    ("不設定", None),
    ("30 分鐘", 30),
    ("1 小時", 60),
    ("1 小時 30 分", 90),
    ("2 小時", 120),
    ("2 小時 30 分", 150),
    ("3 小時", 180),
    ("4 小時", 240),
    ("半天（12 小時）", 720),
    ("整天（24 小時）", 1440),
)
_DURATION_LABELS: dict[int | None, str] = dict(
    (minutes, label) for label, minutes in _DURATION_CHOICES
)


def _resolve_tz(tz_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        # 理論上不該發生，guild_settings.default_tz 在 config.py 已驗證過；
        # 真的發生時退回一個合理預設，總比整個挑選器掛掉好。
        log.warning("時區 %r 無效，退回 Asia/Taipei", tz_name)
        return ZoneInfo("Asia/Taipei")


class _DateSelect(discord.ui.Select):
    """14 天捲動視窗的日期下拉選單。"""

    def __init__(self, picker: DateTimePickerView) -> None:
        self.picker = picker
        super().__init__(placeholder="選擇日期…", options=self._build_options(), row=0)

    def _build_options(self) -> list[discord.SelectOption]:
        return [
            discord.SelectOption(
                label=self._label(d),
                value=d.isoformat(),
                default=(d == self.picker.selected_date),
            )
            for i in range(_WINDOW_DAYS)
            for d in (self.picker.window_start + timedelta(days=i),)
        ]

    @staticmethod
    def _label(d: date) -> str:
        return f"{d.month}/{d.day}（{_WEEKDAY_LABELS[d.weekday()]}）"

    def refresh(self) -> None:
        self.options = self._build_options()

    async def callback(self, interaction: discord.Interaction) -> None:
        self.picker.selected_date = date.fromisoformat(self.values[0])
        self.refresh()
        await self.picker.rerender(interaction)


class _HourSelect(discord.ui.Select):
    def __init__(self, picker: DateTimePickerView) -> None:
        self.picker = picker
        options = [
            discord.SelectOption(
                label=f"{h} 點", value=str(h), default=(h == picker.selected_hour)
            )
            for h in range(24)
        ]
        super().__init__(placeholder="選擇幾點…", options=options, row=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        self.picker.selected_hour = int(self.values[0])
        for opt in self.options:
            opt.default = opt.value == self.values[0]
        await self.picker.rerender(interaction)


class _MinuteSelect(discord.ui.Select):
    def __init__(self, picker: DateTimePickerView) -> None:
        self.picker = picker
        options = [
            discord.SelectOption(
                label=f"{m:02d} 分", value=str(m), default=(m == picker.selected_minute)
            )
            for m in _MINUTE_OPTIONS
        ]
        super().__init__(placeholder="選擇幾分…", options=options, row=2)

    async def callback(self, interaction: discord.Interaction) -> None:
        self.picker.selected_minute = int(self.values[0])
        for opt in self.options:
            opt.default = opt.value == self.values[0]
        await self.picker.rerender(interaction)


class _DurationSelect(discord.ui.Select):
    """持續時間（選填）。不影響「確認時間」按鈕是否可按。"""

    def __init__(self, picker: DateTimePickerView) -> None:
        self.picker = picker
        super().__init__(placeholder="選擇持續時間（選填）…", options=self._build_options(), row=3)

    def _build_options(self) -> list[discord.SelectOption]:
        return [
            discord.SelectOption(
                label=label,
                value="none" if minutes is None else str(minutes),
                default=(minutes == self.picker.selected_duration_minutes),
            )
            for label, minutes in _DURATION_CHOICES
        ]

    async def callback(self, interaction: discord.Interaction) -> None:
        raw = self.values[0]
        self.picker.selected_duration_minutes = None if raw == "none" else int(raw)
        for opt in self.options:
            opt.default = opt.value == raw
        await self.picker.rerender(interaction)


class DateTimePickerView(discord.ui.View):
    """4 個下拉選單（日期/小時/分鐘/持續時間）+ 1 列導覽與確認按鈕。

    元件權重總和 24（4 個 select 各權重 5 = 20，翻頁＋確認＋取消 4 個按鈕
    權重 4），在單則訊息 25 的權重上限之內，也不需要用到 Components V2。
    """

    def __init__(
        self, *, pending: PendingEvent, description: str | None, event_id: str
    ) -> None:
        super().__init__(timeout=300)  # 5 分鐘沒選完就作廢，避免預覽訊息無限期卡著
        self.pending = pending
        self.description = description
        self.event_id = event_id
        self.message: discord.Message | None = None

        self.tz = _resolve_tz(pending.tz)
        today = datetime.now(self.tz).date()
        self.window_start = today
        self.selected_date: date | None = None
        self.selected_hour: int | None = None
        self.selected_minute: int | None = None
        # 若使用者在指令裡已經打了 duration（只是沒打 time），沿用那個值
        # 當作挑選器的初始選擇，使用者不動它也會被保留。
        self.selected_duration_minutes: int | None = pending.duration_minutes

        self.date_select = _DateSelect(self)
        self.add_item(self.date_select)

        self.hour_select = _HourSelect(self)
        self.add_item(self.hour_select)

        self.minute_select = _MinuteSelect(self)
        self.add_item(self.minute_select)

        self.duration_select = _DurationSelect(self)
        self.add_item(self.duration_select)

        self.prev_button: discord.ui.Button = discord.ui.Button(
            label="◀ 更早", style=discord.ButtonStyle.secondary, row=4, disabled=True
        )
        self.prev_button.callback = self._on_prev
        self.add_item(self.prev_button)

        self.next_button: discord.ui.Button = discord.ui.Button(
            label="更晚 ▶", style=discord.ButtonStyle.secondary, row=4
        )
        self.next_button.callback = self._on_next
        self.add_item(self.next_button)

        self.confirm_button: discord.ui.Button = discord.ui.Button(
            label="確認時間", style=discord.ButtonStyle.success, emoji="✅", row=4, disabled=True
        )
        self.confirm_button.callback = self._on_confirm
        self.add_item(self.confirm_button)

        self.cancel_button: discord.ui.Button = discord.ui.Button(
            label="取消", style=discord.ButtonStyle.secondary, emoji="❌", row=4
        )
        self.cancel_button.callback = self._on_cancel
        self.add_item(self.cancel_button)

    def build_embed(self) -> discord.Embed:
        date_text = (
            f"{self.selected_date.month}/{self.selected_date.day}"
            f"（{_WEEKDAY_LABELS[self.selected_date.weekday()]}）"
            if self.selected_date
            else "（尚未選擇）"
        )
        time_text = (
            f"{self.selected_hour} 點 {self.selected_minute:02d} 分"
            if self.selected_hour is not None and self.selected_minute is not None
            else "（尚未選擇）"
        )
        duration_text = _DURATION_LABELS.get(self.selected_duration_minutes, "不設定")

        return discord.Embed(
            title=f"📅 {self.pending.title}",
            description=(
                f"日期：{date_text}\n時間：{time_text}\n持續時間：{duration_text}\n\n"
                "日期與時間選好後按「✅ 確認時間」會進入發布預覽（持續時間選填）；"
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

    async def rerender(self, interaction: discord.Interaction) -> None:
        self.confirm_button.disabled = not self._all_selected()
        today = datetime.now(self.tz).date()
        self.prev_button.disabled = self.window_start <= today
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def _on_prev(self, interaction: discord.Interaction) -> None:
        today = datetime.now(self.tz).date()
        self.window_start = max(today, self.window_start - timedelta(days=_WINDOW_DAYS))
        self.date_select.refresh()
        await self.rerender(interaction)

    async def _on_next(self, interaction: discord.Interaction) -> None:
        self.window_start = self.window_start + timedelta(days=_WINDOW_DAYS)
        self.date_select.refresh()
        await self.rerender(interaction)

    async def _on_cancel(self, interaction: discord.Interaction) -> None:
        for child in self.children:
            child.disabled = True  # type: ignore[attr-defined]
        self.stop()
        await interaction.response.edit_message(
            content="已取消，活動未建立。", embed=None, view=self
        )

    async def _on_confirm(self, interaction: discord.Interaction) -> None:
        # 延遲匯入避免 views_datetime.py ↔ views_invitees.py 的循環參照。
        from src.bot.views_invitees import InviteePickerView
        from src.db import repo

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
        finalized = replace(
            self.pending,
            starts_at_utc=int(naive.timestamp() * 1000),
            duration_minutes=self.selected_duration_minutes,
        )
        self.stop()

        # 時間確定後接著選要 tag 的人／身分組，跟直接打 time 的路徑走同一步
        # （見 modals.py 的說明）。
        settings = await repo.get_guild_settings(finalized.guild_id)
        allow_everyone = bool(settings and settings["allow_everyone_ping"])

        invitee_picker = InviteePickerView(
            pending=finalized,
            description=self.description,
            event_id=self.event_id,
            allow_everyone_ping=allow_everyone,
        )
        await interaction.response.edit_message(
            content="請選擇要標記的參加對象（選填，可直接按下一步略過）：",
            embed=invitee_picker.build_embed(),
            view=invitee_picker,
        )
        invitee_picker.message = await interaction.original_response()

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True  # type: ignore[attr-defined]
        if self.message is not None:
            try:
                await self.message.edit(
                    content="⌛ 已逾時未選擇時間，活動未建立。請重新使用 `/event create`。",
                    embed=None,
                    view=self,
                )
            except discord.HTTPException:
                pass  # 訊息可能已被使用者刪除，逾時清理失敗不影響任何資料正確性
