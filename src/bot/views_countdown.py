"""`/countdown create`、@提及選單「建立倒數」共用的每天發送時間挑選器。

標題／目標日期／內容確定後，改用下拉選單挑「每天幾點發送」，不像
`/countdown` 剛推出時那樣讓使用者手打 `HH:MM`——理由同 `views_datetime.py`
開頭的說明：按鈕/指令觸發的使用者手打時間格式容易出錯，下拉選單直接消除
這個問題。

直接重用 `views_datetime.HourSelect`／`MinuteSelect`——這兩個元件本來就
特地拿掉底線前綴、設計成模組內共用（見該檔案的說明：只要呼叫端提供
`selected_hour`／`selected_minute`／`rerender()` 就能接上），不需要再寫
一份一樣的下拉選單。分鐘只有整點/15/30/45 四個選項，倒數提醒不需要精確
到分鐘。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord

from src.bot.views_datetime import HourSelect, MinuteSelect

log = logging.getLogger(__name__)


class CountdownTimePickerView(discord.ui.View):
    def __init__(
        self,
        *,
        guild_id: int,
        channel_id: int,
        creator_id: int,
        title: str,
        content: str | None,
        target_date_utc: int,
        tz: str,
        mode: str = "countdown",
        suffix: str | None = None,
    ) -> None:
        super().__init__(timeout=300)  # 5 分鐘沒選完就作廢，避免預覽訊息無限期卡著
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.creator_id = creator_id
        self.title = title
        self.content = content
        self.target_date_utc = target_date_utc
        self.tz = tz
        self.mode = mode
        self.suffix = suffix
        self.message: discord.Message | None = None

        self.selected_hour: int | None = None
        self.selected_minute: int | None = None

        # HourSelect/MinuteSelect 的 row（1、2）是寫死在 views_datetime.py
        # 建構子裡的——兩個下拉選單各自獨占一列（Select 權重 5，一列放不下
        # 別的元件），按鈕另外要一列，見下方 row=3。
        self.hour_select = HourSelect(self)
        self.add_item(self.hour_select)

        self.minute_select = MinuteSelect(self)
        self.add_item(self.minute_select)

        self.confirm_button: discord.ui.Button = discord.ui.Button(
            label="確認建立", style=discord.ButtonStyle.success, emoji="✅", row=3, disabled=True
        )
        self.confirm_button.callback = self._on_confirm
        self.add_item(self.confirm_button)

        self.cancel_button: discord.ui.Button = discord.ui.Button(
            label="取消", style=discord.ButtonStyle.secondary, emoji="❌", row=3
        )
        self.cancel_button.callback = self._on_cancel
        self.add_item(self.cancel_button)

    def _preview_count(self) -> int:
        from src.domain.countdown import days_elapsed, days_remaining

        try:
            zone = ZoneInfo(self.tz)
        except ZoneInfoNotFoundError:
            zone = ZoneInfo("Asia/Taipei")
        now_local = datetime.now(zone)
        if self.mode == "countup":
            return days_elapsed(self.target_date_utc, now_local)
        return days_remaining(self.target_date_utc, now_local)

    def build_embed(self) -> discord.Embed:
        from src.domain.countdown import render_countdown_text

        time_text = (
            f"{self.selected_hour} 點 {self.selected_minute:02d} 分"
            if self.selected_hour is not None and self.selected_minute is not None
            else "（尚未選擇）"
        )
        preview = render_countdown_text(self.title, self._preview_count(), self.suffix)
        return discord.Embed(
            title=f"⏳ {preview}",
            description=f"每天發送時間：{time_text}\n\n選好後按「✅ 確認建立」。",
            colour=discord.Colour.gold(),
        )

    def _all_selected(self) -> bool:
        return self.selected_hour is not None and self.selected_minute is not None

    async def rerender(self, interaction: discord.Interaction) -> None:
        self.confirm_button.disabled = not self._all_selected()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def _on_confirm(self, interaction: discord.Interaction) -> None:
        from src.db import repo
        from src.domain.countdown import render_countdown_text
        from src.lib.ids import new_id

        assert self.selected_hour is not None
        assert self.selected_minute is not None
        self.stop()

        countdown_id = new_id()
        await repo.create_countdown(
            countdown_id=countdown_id,
            guild_id=self.guild_id,
            channel_id=self.channel_id,
            creator_id=self.creator_id,
            title=self.title,
            content=self.content,
            target_date_utc=self.target_date_utc,
            tz=self.tz,
            send_hour=self.selected_hour,
            send_minute=self.selected_minute,
            mode=self.mode,
            suffix=self.suffix,
        )

        preview = render_countdown_text(self.title, self._preview_count(), self.suffix)

        for child in self.children:
            if isinstance(child, discord.ui.Item):
                child.disabled = True  # type: ignore[attr-defined]
        await interaction.response.edit_message(
            content=(
                f"✅ 倒數提醒已建立（ID `{countdown_id}`）：**{preview}**，"
                f"每天 {self.selected_hour:02d}:{self.selected_minute:02d} 發送。"
            ),
            embed=None,
            view=self,
        )

    async def _on_cancel(self, interaction: discord.Interaction) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Item):
                child.disabled = True  # type: ignore[attr-defined]
        self.stop()
        await interaction.response.edit_message(
            content="已取消，倒數提醒未建立。", embed=None, view=self
        )

    async def on_timeout(self) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Item):
                child.disabled = True  # type: ignore[attr-defined]
        if self.message is not None:
            try:
                await self.message.edit(
                    content="⌛ 已逾時未選擇時間，倒數提醒未建立。請重新試一次。",
                    embed=None,
                    view=self,
                )
            except discord.HTTPException:
                pass  # 訊息可能已被使用者刪除，逾時清理失敗不影響任何資料正確性


class CountdownModeChoiceView(discord.ui.View):
    """`QuickCountdownModal` 送出後的下一步：Modal 收集文字時還不知道使用者
    要選倒數還是正數，同一個「日期／天數」欄位在兩種模式下解析方式不同
    （倒數：`parse_target_date`；正數：`parse_day_count` 換算成錨點），
    所以要等模式確定了才能解析。比照 `modals_quick.QuickFf14PositionPickerView`
    的套路——短命 View，不是持久化 `DynamicItem`。

    解析失敗（不管哪個模式）直接把錯誤訊息編輯回這則訊息並結束，不重新
    顯示選單讓使用者重試——跟這個 repo 其他驗證失敗的處理方式一致
    （ephemeral 錯誤訊息，使用者自己重新觸發一次整個流程）。
    """

    def __init__(
        self,
        *,
        guild_id: int,
        channel_id: int,
        creator_id: int,
        title: str,
        raw_value: str,
        suffix: str | None,
        content: str | None,
        tz: str,
    ) -> None:
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.creator_id = creator_id
        self.title = title
        self.raw_value = raw_value
        self.suffix = suffix
        self.content = content
        self.tz = tz
        self.message: discord.Message | None = None

    def build_embed(self) -> discord.Embed:
        return discord.Embed(
            title=f"⏳/📈 {self.title}",
            description="選擇這是「倒數」到某個日期，還是從某天開始「正數」累加。",
            colour=discord.Colour.gold(),
        )

    async def _open_time_picker(
        self, interaction: discord.Interaction, *, mode: str, target_date_utc: int
    ) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Item):
                child.disabled = True  # type: ignore[attr-defined]
        self.stop()

        picker = CountdownTimePickerView(
            guild_id=self.guild_id,
            channel_id=self.channel_id,
            creator_id=self.creator_id,
            title=self.title,
            content=self.content,
            target_date_utc=target_date_utc,
            tz=self.tz,
            mode=mode,
            suffix=self.suffix,
        )
        await interaction.response.edit_message(embed=picker.build_embed(), view=picker)
        picker.message = await interaction.original_response()

    async def _show_error(self, interaction: discord.Interaction, message: str) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Item):
                child.disabled = True  # type: ignore[attr-defined]
        self.stop()
        await interaction.response.edit_message(content=message, embed=None, view=self)

    @discord.ui.button(label="倒數", style=discord.ButtonStyle.primary, emoji="⏳")
    async def choose_countdown(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        from src.lib.timeparse import TimeParseError, parse_target_date

        try:
            target_date_utc = parse_target_date(self.raw_value, self.tz)
        except TimeParseError as exc:
            await self._show_error(interaction, str(exc))
            return

        await self._open_time_picker(interaction, mode="countdown", target_date_utc=target_date_utc)

    @discord.ui.button(label="正數", style=discord.ButtonStyle.primary, emoji="📈")
    async def choose_countup(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        from src.lib.timeparse import TimeParseError, parse_day_count

        try:
            days = parse_day_count(self.raw_value)
        except TimeParseError as exc:
            await self._show_error(interaction, str(exc))
            return

        try:
            zone = ZoneInfo(self.tz)
        except ZoneInfoNotFoundError:
            zone = ZoneInfo("Asia/Taipei")
        # 正數模式的錨點是「今天－使用者輸入的天數」反推回去的，見
        # domain.countdown.days_elapsed 的說明：這樣建立當下 elapsed 就
        # 直接等於使用者輸入的那個數字，不用另外校正 off-by-one。
        today_midnight = datetime.now(zone).replace(hour=0, minute=0, second=0, microsecond=0)
        anchor_utc = int((today_midnight - timedelta(days=days)).timestamp() * 1000)

        await self._open_time_picker(interaction, mode="countup", target_date_utc=anchor_utc)

    async def on_timeout(self) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Item):
                child.disabled = True  # type: ignore[attr-defined]
        if self.message is not None:
            try:
                await self.message.edit(
                    content="⌛ 已逾時未選擇模式，倒數提醒未建立。請重新試一次。",
                    embed=None,
                    view=self,
                )
            except discord.HTTPException:
                pass  # 訊息可能已被使用者刪除，逾時清理失敗不影響任何資料正確性
