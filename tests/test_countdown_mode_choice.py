"""`CountdownModeChoiceView` 測試——`QuickCountdownModal` 送出後的下一步：
Modal 收集文字時還不知道使用者要選倒數還是正數，同一段文字（`raw_value`）
在兩種模式下解析方式不同，這個 View 的兩顆按鈕各自負責解析、失敗給清楚的
錯誤訊息、成功轉進 `CountdownTimePickerView`。
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import discord

from src.bot.views_countdown import CountdownModeChoiceView, CountdownTimePickerView
from src.lib.clock import now_ms

GUILD_ID = 111111111111111111
CHANNEL_ID = 222222222222222222
CREATOR_ID = 333333333333333333

NOW = now_ms()
DAY = 24 * 3_600_000
TPE = ZoneInfo("Asia/Taipei")


def _make_view(**overrides) -> CountdownModeChoiceView:
    defaults = dict(
        guild_id=GUILD_ID,
        channel_id=CHANNEL_ID,
        creator_id=CREATOR_ID,
        title="退伍倒數",
        raw_value="2027-06-15",
        suffix="天",
        content=None,
        tz="Asia/Taipei",
    )
    return CountdownModeChoiceView(**{**defaults, **overrides})


def _make_interaction() -> MagicMock:
    interaction = MagicMock()
    interaction.response = AsyncMock()

    fake_message = MagicMock()
    fake_message.id = 555
    interaction.original_response = AsyncMock(return_value=fake_message)
    return interaction


class TestChooseCountdown:
    async def test_valid_date_opens_time_picker_in_countdown_mode(self) -> None:
        view = _make_view(raw_value="2027-06-15")
        interaction = _make_interaction()

        await view.choose_countdown.callback(interaction)

        _, kwargs = interaction.response.edit_message.call_args
        assert isinstance(kwargs["view"], CountdownTimePickerView)
        assert kwargs["view"].mode == "countdown"

    async def test_day_count_shortcut_also_works(self) -> None:
        """倒數模式的「日期」欄位本來就支援「還剩幾天」的數字捷徑。"""
        view = _make_view(raw_value="500")
        interaction = _make_interaction()

        await view.choose_countdown.callback(interaction)

        _, kwargs = interaction.response.edit_message.call_args
        assert isinstance(kwargs["view"], CountdownTimePickerView)

    async def test_invalid_value_shows_error_without_opening_picker(self) -> None:
        view = _make_view(raw_value="不是日期")
        interaction = _make_interaction()

        await view.choose_countdown.callback(interaction)

        _, kwargs = interaction.response.edit_message.call_args
        assert "無法解析" in kwargs["content"]
        assert "view" not in kwargs or not isinstance(kwargs.get("view"), CountdownTimePickerView)


class TestChooseCountup:
    async def test_valid_day_count_opens_time_picker_in_countup_mode(self) -> None:
        view = _make_view(raw_value="12")
        interaction = _make_interaction()

        await view.choose_countup.callback(interaction)

        _, kwargs = interaction.response.edit_message.call_args
        picker = kwargs["view"]
        assert isinstance(picker, CountdownTimePickerView)
        assert picker.mode == "countup"

    async def test_anchor_is_computed_n_days_ago(self) -> None:
        """輸入 12 代表「目前已經第 12 天」，錨點要反推成「今天－12 天」，
        建立當下用 days_elapsed 算出來要正好等於 12。"""
        from src.domain.countdown import days_elapsed

        view = _make_view(raw_value="12")
        interaction = _make_interaction()

        await view.choose_countup.callback(interaction)

        _, kwargs = interaction.response.edit_message.call_args
        picker = kwargs["view"]
        elapsed = days_elapsed(picker.target_date_utc, datetime.now(TPE))
        assert elapsed == 12

    async def test_invalid_value_shows_error_without_opening_picker(self) -> None:
        view = _make_view(raw_value="不是天數")
        interaction = _make_interaction()

        await view.choose_countup.callback(interaction)

        _, kwargs = interaction.response.edit_message.call_args
        assert "不是合法的天數" in kwargs["content"]

    async def test_zero_is_rejected(self) -> None:
        view = _make_view(raw_value="0")
        interaction = _make_interaction()

        await view.choose_countup.callback(interaction)

        _, kwargs = interaction.response.edit_message.call_args
        assert "大於 0" in kwargs["content"]


class TestCarriesForwardModalFields:
    async def test_title_content_suffix_carried_into_picker(self) -> None:
        view = _make_view(
            raw_value="2027-06-15", title="退伍倒數", suffix="天", content="加油"
        )
        interaction = _make_interaction()

        await view.choose_countdown.callback(interaction)

        _, kwargs = interaction.response.edit_message.call_args
        picker = kwargs["view"]
        assert picker.title == "退伍倒數"
        assert picker.suffix == "天"
        assert picker.content == "加油"
        assert picker.channel_id == CHANNEL_ID
        assert picker.guild_id == GUILD_ID
        assert picker.creator_id == CREATOR_ID


class TestOnTimeout:
    async def test_disables_children_and_edits_message(self) -> None:
        view = _make_view()
        message = MagicMock()
        message.edit = AsyncMock()
        view.message = message

        await view.on_timeout()

        assert all(child.disabled for child in view.children)
        message.edit.assert_awaited_once()

    async def test_swallows_edit_failure(self) -> None:
        view = _make_view()
        message = MagicMock()
        message.edit = AsyncMock(
            side_effect=discord.HTTPException(MagicMock(status=404), "not found")
        )
        view.message = message

        await view.on_timeout()  # 不應拋例外
