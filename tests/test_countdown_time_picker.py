"""`CountdownTimePickerView` 測試——`/countdown create`、@提及選單「建立
倒數」在標題／目標日期／內容確定後，改用下拉選單挑「每天幾點發送」。
比照 `test_datetime_picker.py` 的測法，重用 `views_datetime.HourSelect`／
`MinuteSelect`，這裡只測這個 View 自己的邏輯（何時能按確認、確認後有沒有
正確寫入 DB）。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from src.bot.views_countdown import CountdownTimePickerView
from src.db import repo
from src.lib.clock import now_ms

GUILD_ID = 111111111111111111
CHANNEL_ID = 222222222222222222
CREATOR_ID = 333333333333333333

NOW = now_ms()
DAY = 24 * 3_600_000


def _make_view(**overrides) -> CountdownTimePickerView:
    defaults = dict(
        guild_id=GUILD_ID,
        channel_id=CHANNEL_ID,
        creator_id=CREATOR_ID,
        title="退伍倒數",
        content=None,
        target_date_utc=NOW + 30 * DAY,
        tz="Asia/Taipei",
    )
    return CountdownTimePickerView(**{**defaults, **overrides})


def _make_interaction() -> MagicMock:
    interaction = MagicMock()
    interaction.response = AsyncMock()
    return interaction


class TestConfirmButtonState:
    def test_disabled_initially(self) -> None:
        view = _make_view()
        assert view.confirm_button.disabled is True

    async def test_enabled_after_picking_hour_and_minute(self) -> None:
        """比照 test_datetime_picker.py 的測法：直接寫入 Select 的 `_values`
        私有屬性模擬使用者選過（Discord 互動時是由框架填入，這裡測試環境
        沒有真的互動可以觸發）。"""
        view = _make_view()
        interaction = _make_interaction()

        view.hour_select._values = ["9"]
        await view.hour_select.callback(interaction)
        view.minute_select._values = ["0"]
        await view.minute_select.callback(interaction)

        assert view.confirm_button.disabled is False
        assert view.selected_hour == 9
        assert view.selected_minute == 0


class TestOnConfirm:
    async def test_creates_countdown_with_selected_time(self, db) -> None:
        view = _make_view(title="退伍倒數", content="加油")
        view.selected_hour = 9
        view.selected_minute = 30
        interaction = _make_interaction()

        await view._on_confirm(interaction)

        rows = await repo.list_active_countdowns_in_guild(GUILD_ID)
        assert len(rows) == 1
        assert rows[0]["title"] == "退伍倒數"
        assert rows[0]["content"] == "加油"
        assert rows[0]["send_hour"] == 9
        assert rows[0]["send_minute"] == 30

    async def test_confirm_message_renders_prefix_count_suffix(self, db) -> None:
        view = _make_view(target_date_utc=NOW + 5 * DAY, title="退伍倒數", suffix="天")
        view.selected_hour = 9
        view.selected_minute = 0
        interaction = _make_interaction()

        await view._on_confirm(interaction)

        _, kwargs = interaction.response.edit_message.call_args
        assert "已建立" in kwargs["content"]
        assert "退伍倒數5天" in kwargs["content"]

    async def test_countup_mode_stores_mode_and_renders_elapsed(self, db) -> None:
        """正數模式：target_date_utc 這裡存的是過去的錨點，count 要用
        elapsed（遞增）而不是 remaining（遞減）。"""
        anchor = NOW - 12 * DAY
        view = _make_view(
            target_date_utc=anchor, title="沒有小名的日子第", suffix="天", mode="countup"
        )
        view.selected_hour = 9
        view.selected_minute = 0
        interaction = _make_interaction()

        await view._on_confirm(interaction)

        rows = await repo.list_active_countdowns_in_guild(GUILD_ID)
        assert rows[0]["mode"] == "countup"
        assert rows[0]["suffix"] == "天"
        _, kwargs = interaction.response.edit_message.call_args
        assert "沒有小名的日子第12天" in kwargs["content"]

    async def test_disables_all_children_after_confirm(self, db) -> None:
        view = _make_view()
        view.selected_hour = 9
        view.selected_minute = 0
        interaction = _make_interaction()

        await view._on_confirm(interaction)

        assert all(child.disabled for child in view.children)


class TestOnCancel:
    async def test_cancel_does_not_create_countdown(self, db) -> None:
        view = _make_view()
        interaction = _make_interaction()

        await view._on_cancel(interaction)

        rows = await repo.list_active_countdowns_in_guild(GUILD_ID)
        assert rows == []
        _, kwargs = interaction.response.edit_message.call_args
        assert "已取消" in kwargs["content"]

    async def test_cancel_disables_all_children(self, db) -> None:
        view = _make_view()
        interaction = _make_interaction()

        await view._on_cancel(interaction)

        assert all(child.disabled for child in view.children)


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
        import discord

        view = _make_view()
        message = MagicMock()
        message.edit = AsyncMock(
            side_effect=discord.HTTPException(MagicMock(status=404), "not found")
        )
        view.message = message

        await view.on_timeout()  # 不應拋例外
