"""DateTimePickerView 測試。

Discord 沒有原生日期選擇元件（見 views_datetime.py 開頭說明），這裡是用
「14 天捲動視窗 + 小時/分鐘下拉選單」模擬出來的替代方案。測試重點：

・視窗捲動不能捲到今天以前（活動不能排在過去）
・日期／小時／分鐘要三者都選了，「確認時間」才能按
・確認後要正確轉交給 InviteePickerView（選邀請對象那一步），且此時仍未寫入
   任何資料庫紀錄 —— 真正寫入是流程最後 ConfirmEventView 的確認按鈕才做的事
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

from src.bot.modals import PendingEvent
from src.bot.views_datetime import DateTimePickerView
from src.bot.views_invitees import InviteePickerView
from src.db import repo

GUILD_ID = 111111111111111111
CHANNEL_ID = 222222222222222222
USER_ID = 333333333333333333
TPE = ZoneInfo("Asia/Taipei")


def _make_pending(**overrides) -> PendingEvent:
    defaults = dict(
        guild_id=GUILD_ID,
        channel_id=CHANNEL_ID,
        creator_id=USER_ID,
        title="週五團練",
        tz="Asia/Taipei",
        location=None,
        duration_minutes=None,
        starts_at_utc=None,  # 走月曆挑選器的情境一定是還沒選時間
    )
    return PendingEvent(**{**defaults, **overrides})


def _make_interaction() -> MagicMock:
    interaction = MagicMock()
    interaction.response = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=False)

    fake_message = MagicMock()
    fake_message.id = 555
    fake_message.jump_url = "https://discord.com/channels/x/y/555"
    interaction.original_response = AsyncMock(return_value=fake_message)

    interaction.channel = AsyncMock()
    interaction.channel.send = AsyncMock(return_value=fake_message)
    return interaction


def _pick(view: DateTimePickerView, *, day_offset: int, hour: int, minute: int) -> date:
    """依序模擬使用者選日期、小時、分鐘三個下拉選單。

    直接寫入 Select 的 _values 私有屬性 —— Discord 互動時是由框架的
    `_refresh_state` 寫入這個屬性，我們略過整個 gateway/HTTP 往返，
    只驗證 callback 收到選擇後的行為是否正確。
    """
    target_date = view.window_start + timedelta(days=day_offset)
    view.date_select._values = [target_date.isoformat()]
    view.hour_select._values = [str(hour)]
    view.minute_select._values = [str(minute)]
    return target_date


class TestInitialState:
    def test_date_select_has_14_day_window(self) -> None:
        view = DateTimePickerView(pending=_make_pending(), description=None, event_id="e1")
        assert len(view.date_select.options) == 14

    def test_first_date_option_is_today(self) -> None:
        view = DateTimePickerView(pending=_make_pending(), description=None, event_id="e1")
        today = datetime.now(TPE).date()
        assert view.date_select.options[0].value == today.isoformat()

    def test_hour_select_has_24_options(self) -> None:
        view = DateTimePickerView(pending=_make_pending(), description=None, event_id="e1")
        assert {o.value for o in view.hour_select.options} == {str(h) for h in range(24)}

    def test_minute_select_has_quarter_hour_options(self) -> None:
        view = DateTimePickerView(pending=_make_pending(), description=None, event_id="e1")
        assert {o.value for o in view.minute_select.options} == {"0", "15", "30", "45"}

    def test_hour_labels_use_dian_not_shi(self) -> None:
        """使用者要求：小時要顯示成「幾點」而不是「幾時」。"""
        view = DateTimePickerView(pending=_make_pending(), description=None, event_id="e1")
        labels = {o.label for o in view.hour_select.options}
        assert "20 點" in labels
        assert not any("時" in label for label in labels)

    def test_duration_select_includes_half_and_full_day(self) -> None:
        view = DateTimePickerView(pending=_make_pending(), description=None, event_id="e1")
        values_to_labels = {o.value: o.label for o in view.duration_select.options}
        assert values_to_labels["720"] == "半天（12 小時）"
        assert values_to_labels["1440"] == "整天（24 小時）"

    def test_duration_select_defaults_to_none(self) -> None:
        view = DateTimePickerView(
            pending=_make_pending(duration_minutes=None), description=None, event_id="e1"
        )
        assert view.selected_duration_minutes is None
        default_option = next(o for o in view.duration_select.options if o.default)
        assert default_option.value == "none"

    def test_duration_select_inherits_value_from_command_option(self) -> None:
        """指令裡已經打了 duration、只留空 time 時，挑選器要沿用那個值。"""
        view = DateTimePickerView(
            pending=_make_pending(duration_minutes=90), description=None, event_id="e1"
        )
        assert view.selected_duration_minutes == 90
        default_option = next(o for o in view.duration_select.options if o.default)
        assert default_option.value == "90"

    async def test_confirm_enables_without_touching_duration(self) -> None:
        """持續時間選填：只選日期/小時/分鐘，不碰持續時間，也要能按確認。"""
        view = DateTimePickerView(pending=_make_pending(), description=None, event_id="e1")
        interaction = _make_interaction()
        _pick(view, day_offset=1, hour=9, minute=0)
        await view.date_select.callback(interaction)
        await view.hour_select.callback(interaction)
        await view.minute_select.callback(interaction)

        assert view.confirm_button.disabled is False
        assert view.selected_duration_minutes is None

    def test_prev_button_disabled_at_start(self) -> None:
        """視窗一開始就是今天，不能再往回捲。"""
        view = DateTimePickerView(pending=_make_pending(), description=None, event_id="e1")
        assert view.prev_button.disabled is True

    def test_next_button_enabled_at_start(self) -> None:
        view = DateTimePickerView(pending=_make_pending(), description=None, event_id="e1")
        assert view.next_button.disabled is False

    def test_confirm_button_disabled_until_all_selected(self) -> None:
        view = DateTimePickerView(pending=_make_pending(), description=None, event_id="e1")
        assert view.confirm_button.disabled is True


class TestWindowNavigation:
    async def test_next_advances_window_by_14_days(self) -> None:
        view = DateTimePickerView(pending=_make_pending(), description=None, event_id="e1")
        original_start = view.window_start
        await view._on_next(_make_interaction())
        assert view.window_start == original_start + timedelta(days=14)
        assert view.date_select.options[0].value == view.window_start.isoformat()

    async def test_next_enables_prev_button(self) -> None:
        view = DateTimePickerView(pending=_make_pending(), description=None, event_id="e1")
        await view._on_next(_make_interaction())
        assert view.prev_button.disabled is False

    async def test_prev_cannot_go_before_today(self) -> None:
        """就算跑到未來很多視窗，往回捲最遠也只能捲回今天，不能排出過去的日期。"""
        view = DateTimePickerView(pending=_make_pending(), description=None, event_id="e1")
        await view._on_next(_make_interaction())
        await view._on_next(_make_interaction())
        await view._on_prev(_make_interaction())
        await view._on_prev(_make_interaction())
        await view._on_prev(_make_interaction())  # 多按幾次也不該捲過頭

        today = datetime.now(TPE).date()
        assert view.window_start == today
        assert view.prev_button.disabled is True


class TestSelection:
    async def test_selecting_date_only_keeps_confirm_disabled(self) -> None:
        view = DateTimePickerView(pending=_make_pending(), description=None, event_id="e1")
        view.date_select._values = [view.window_start.isoformat()]
        await view.date_select.callback(_make_interaction())
        assert view.selected_date == view.window_start
        assert view.confirm_button.disabled is True

    async def test_selecting_all_three_enables_confirm(self) -> None:
        view = DateTimePickerView(pending=_make_pending(), description=None, event_id="e1")
        interaction = _make_interaction()
        _pick(view, day_offset=3, hour=20, minute=30)

        await view.date_select.callback(interaction)
        await view.hour_select.callback(interaction)
        await view.minute_select.callback(interaction)

        assert view.confirm_button.disabled is False
        assert view.selected_hour == 20
        assert view.selected_minute == 30

    async def test_selecting_duration_updates_state_and_default_flag(self) -> None:
        view = DateTimePickerView(pending=_make_pending(), description=None, event_id="e1")
        interaction = _make_interaction()
        view.duration_select._values = ["120"]

        await view.duration_select.callback(interaction)

        assert view.selected_duration_minutes == 120
        default_option = next(o for o in view.duration_select.options if o.default)
        assert default_option.value == "120"

    async def test_selecting_none_duration_clears_state(self) -> None:
        view = DateTimePickerView(
            pending=_make_pending(duration_minutes=90), description=None, event_id="e1"
        )
        interaction = _make_interaction()
        view.duration_select._values = ["none"]

        await view.duration_select.callback(interaction)

        assert view.selected_duration_minutes is None


class TestConfirm:
    async def test_confirm_hands_off_to_invitee_picker(self, db) -> None:
        view = DateTimePickerView(
            pending=_make_pending(title="確認交接測試"), description="內容", event_id="e1"
        )
        interaction = _make_interaction()
        _pick(view, day_offset=2, hour=19, minute=0)
        await view.date_select.callback(interaction)
        await view.hour_select.callback(interaction)
        await view.minute_select.callback(interaction)

        await view._on_confirm(interaction)

        _, kwargs = interaction.response.edit_message.call_args
        assert isinstance(kwargs["view"], InviteePickerView)
        assert kwargs["view"].pending.title == "確認交接測試"

    async def test_confirm_computes_correct_utc_epoch(self, db) -> None:
        view = DateTimePickerView(pending=_make_pending(), description=None, event_id="e1")
        interaction = _make_interaction()
        target_date = _pick(view, day_offset=1, hour=20, minute=0)
        await view.date_select.callback(interaction)
        await view.hour_select.callback(interaction)
        await view.minute_select.callback(interaction)

        await view._on_confirm(interaction)

        expected = datetime(
            target_date.year, target_date.month, target_date.day, 20, 0, tzinfo=TPE
        )
        _, kwargs = interaction.response.edit_message.call_args
        assert kwargs["view"].pending.starts_at_utc == int(expected.timestamp() * 1000)

    async def test_confirm_carries_selected_duration_into_pending(self, db) -> None:
        view = DateTimePickerView(pending=_make_pending(), description=None, event_id="e1")
        interaction = _make_interaction()
        _pick(view, day_offset=1, hour=20, minute=0)
        await view.date_select.callback(interaction)
        await view.hour_select.callback(interaction)
        await view.minute_select.callback(interaction)
        view.duration_select._values = ["90"]
        await view.duration_select.callback(interaction)

        await view._on_confirm(interaction)

        _, kwargs = interaction.response.edit_message.call_args
        finalized = kwargs["view"].pending
        assert finalized.duration_minutes == 90
        assert finalized.ends_at_utc == finalized.starts_at_utc + 90 * 60_000

    async def test_confirm_with_full_day_duration(self, db) -> None:
        view = DateTimePickerView(pending=_make_pending(), description=None, event_id="e1")
        interaction = _make_interaction()
        _pick(view, day_offset=1, hour=9, minute=0)
        await view.date_select.callback(interaction)
        await view.hour_select.callback(interaction)
        await view.minute_select.callback(interaction)
        view.duration_select._values = ["1440"]
        await view.duration_select.callback(interaction)

        await view._on_confirm(interaction)

        _, kwargs = interaction.response.edit_message.call_args
        finalized = kwargs["view"].pending
        assert finalized.duration_minutes == 1440
        assert finalized.ends_at_utc == finalized.starts_at_utc + 1440 * 60_000

    async def test_confirm_without_touching_duration_keeps_it_none(self, db) -> None:
        view = DateTimePickerView(pending=_make_pending(), description=None, event_id="e1")
        interaction = _make_interaction()
        _pick(view, day_offset=1, hour=20, minute=0)
        await view.date_select.callback(interaction)
        await view.hour_select.callback(interaction)
        await view.minute_select.callback(interaction)

        await view._on_confirm(interaction)

        _, kwargs = interaction.response.edit_message.call_args
        assert kwargs["view"].pending.duration_minutes is None

    async def test_confirm_does_not_write_to_database_yet(self, db) -> None:
        """轉交給 ConfirmEventView 只是換一個預覽畫面，真正寫入要等使用者再按一次發布。"""
        view = DateTimePickerView(pending=_make_pending(), description=None, event_id="e1")
        interaction = _make_interaction()
        _pick(view, day_offset=0, hour=12, minute=0)
        await view.date_select.callback(interaction)
        await view.hour_select.callback(interaction)
        await view.minute_select.callback(interaction)

        await view._on_confirm(interaction)

        interaction.channel.send.assert_not_awaited()  # 不該呼叫過 channel.send
        assert await repo.owned_event("e1", GUILD_ID) is None


class TestCancel:
    async def test_cancel_disables_all_children(self) -> None:
        view = DateTimePickerView(pending=_make_pending(), description=None, event_id="e1")
        await view._on_cancel(_make_interaction())
        assert all(child.disabled for child in view.children)  # type: ignore[attr-defined]

    async def test_cancel_edits_message_with_cancellation_notice(self) -> None:
        view = DateTimePickerView(pending=_make_pending(), description=None, event_id="e1")
        interaction = _make_interaction()
        await view._on_cancel(interaction)
        _, kwargs = interaction.response.edit_message.call_args
        assert "取消" in kwargs["content"]


class TestTimeout:
    async def test_on_timeout_edits_stored_message(self) -> None:
        view = DateTimePickerView(pending=_make_pending(), description=None, event_id="e1")
        fake_message = AsyncMock()
        view.message = fake_message

        await view.on_timeout()

        fake_message.edit.assert_awaited_once()
        _, kwargs = fake_message.edit.call_args
        assert "逾時" in kwargs["content"]
