"""TimeSlotPickerView 測試——排程投票的候選時段挑選器。

沿用 DateTimePickerView 測試（tests/test_datetime_picker.py）同樣的假 Select
`_values` 寫入手法，跳過整個 gateway/HTTP 往返，只驗證 callback 收到選擇後的
行為是否正確。這裡的重點跟 `/event create` 的挑選器不同：可以重複「選 → 按
新增」累積多個候選時段，而不是選一次就交出去；「建立投票」要等候選數量達到
`MIN_OPTIONS` 才能按。
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import discord

from src.bot.views_poll_timeslot import PendingTimeSlotPoll, TimeSlotPickerView
from src.domain.polls import MAX_OPTIONS, MIN_OPTIONS

GUILD_ID = 111111111111111111
CHANNEL_ID = 222222222222222222
USER_ID = 333333333333333333


def _make_params(**overrides) -> PendingTimeSlotPoll:
    defaults = dict(
        guild_id=GUILD_ID,
        channel_id=CHANNEL_ID,
        creator_id=USER_ID,
        question="下次團練訂哪天",
        multi=False,
        anonymous=False,
        allow_change=True,
        closes_at=None,
        description=None,
    )
    return PendingTimeSlotPoll(**{**defaults, **overrides})


def _make_interaction() -> MagicMock:
    interaction = MagicMock()
    interaction.response = AsyncMock()

    fake_message = MagicMock()
    fake_message.id = 999999
    fake_message.jump_url = "https://discord.com/channels/x/y/999999"

    interaction.channel = AsyncMock()
    interaction.channel.send = AsyncMock(return_value=fake_message)
    return interaction


def _pick(view: TimeSlotPickerView, *, day_offset: int, hour: int, minute: int) -> None:
    """模擬選日期/小時/分鐘三個下拉選單，理由同 test_datetime_picker.py 的 _pick。"""
    target_date = view.window_start + timedelta(days=day_offset)
    view.date_select._values = [target_date.isoformat()]
    view.hour_select._values = [str(hour)]
    view.minute_select._values = [str(minute)]


async def _select_all(
    view: TimeSlotPickerView, interaction: MagicMock, *, day_offset: int, hour: int, minute: int
) -> None:
    _pick(view, day_offset=day_offset, hour=hour, minute=minute)
    await view.date_select.callback(interaction)
    await view.hour_select.callback(interaction)
    await view.minute_select.callback(interaction)


async def _add_n_candidates(view: TimeSlotPickerView, interaction: MagicMock, n: int) -> None:
    for i in range(n):
        await _select_all(view, interaction, day_offset=i, hour=20, minute=0)
        await view._on_add(interaction)


class TestInitialState:
    def test_add_button_disabled_until_all_selected(self) -> None:
        view = TimeSlotPickerView(params=_make_params(), tz="Asia/Taipei")
        assert view.add_button.disabled is True

    def test_create_button_disabled_with_no_candidates(self) -> None:
        view = TimeSlotPickerView(params=_make_params(), tz="Asia/Taipei")
        assert view.create_button.disabled is True

    def test_remove_button_disabled_with_no_candidates(self) -> None:
        view = TimeSlotPickerView(params=_make_params(), tz="Asia/Taipei")
        assert view.remove_button.disabled is True


class TestSelection:
    async def test_selecting_all_three_enables_add_button(self) -> None:
        view = TimeSlotPickerView(params=_make_params(), tz="Asia/Taipei")
        interaction = _make_interaction()
        await _select_all(view, interaction, day_offset=1, hour=20, minute=0)
        assert view.add_button.disabled is False


class TestAddCandidate:
    async def test_add_appends_candidate_and_rerenders(self) -> None:
        view = TimeSlotPickerView(params=_make_params(), tz="Asia/Taipei")
        interaction = _make_interaction()
        await _select_all(view, interaction, day_offset=1, hour=20, minute=0)

        await view._on_add(interaction)

        assert len(view.candidates) == 1
        interaction.response.edit_message.assert_awaited()

    async def test_single_candidate_leaves_create_button_disabled(self) -> None:
        """MIN_OPTIONS 預設是 2，只加 1 個候選還不能建立。"""
        view = TimeSlotPickerView(params=_make_params(), tz="Asia/Taipei")
        interaction = _make_interaction()
        await _add_n_candidates(view, interaction, 1)

        assert view.create_button.disabled is True

    async def test_create_button_enables_at_min_options(self) -> None:
        view = TimeSlotPickerView(params=_make_params(), tz="Asia/Taipei")
        interaction = _make_interaction()
        await _add_n_candidates(view, interaction, MIN_OPTIONS)

        assert len(view.candidates) == MIN_OPTIONS
        assert view.create_button.disabled is False

    async def test_duplicate_time_is_not_added_twice(self) -> None:
        view = TimeSlotPickerView(params=_make_params(), tz="Asia/Taipei")
        interaction = _make_interaction()
        await _select_all(view, interaction, day_offset=1, hour=20, minute=0)
        await view._on_add(interaction)
        await view._on_add(interaction)  # 同一個時間再按一次

        assert len(view.candidates) == 1

    async def test_add_button_disables_at_max_options(self) -> None:
        view = TimeSlotPickerView(params=_make_params(), tz="Asia/Taipei")
        interaction = _make_interaction()
        await _add_n_candidates(view, interaction, MAX_OPTIONS)

        assert len(view.candidates) == MAX_OPTIONS
        assert view.add_button.disabled is True


class TestRemoveLast:
    async def test_remove_last_pops_most_recent_candidate(self) -> None:
        view = TimeSlotPickerView(params=_make_params(), tz="Asia/Taipei")
        interaction = _make_interaction()
        await _select_all(view, interaction, day_offset=1, hour=20, minute=0)
        await view._on_add(interaction)
        first_candidate = view.candidates[0]
        await _select_all(view, interaction, day_offset=2, hour=9, minute=30)
        await view._on_add(interaction)

        await view._on_remove_last(interaction)

        assert view.candidates == [first_candidate]

    async def test_create_button_disables_again_after_removal_below_min(self) -> None:
        view = TimeSlotPickerView(params=_make_params(), tz="Asia/Taipei")
        interaction = _make_interaction()
        await _add_n_candidates(view, interaction, MIN_OPTIONS)
        assert view.create_button.disabled is False

        await view._on_remove_last(interaction)

        assert view.create_button.disabled is True

    async def test_remove_on_empty_candidates_does_not_raise(self) -> None:
        view = TimeSlotPickerView(params=_make_params(), tz="Asia/Taipei")
        await view._on_remove_last(_make_interaction())  # 不應拋例外
        assert view.candidates == []


class TestCreate:
    async def test_writes_poll_and_options(self, db) -> None:
        view = TimeSlotPickerView(
            params=_make_params(question="下次團練訂哪天"), tz="Asia/Taipei"
        )
        interaction = _make_interaction()
        await _add_n_candidates(view, interaction, MIN_OPTIONS)
        expected_epochs = list(view.candidates)

        await view._on_create(interaction)

        polls = await db.query_all("SELECT * FROM polls")
        assert len(polls) == 1
        assert polls[0]["kind"] == "time_slot"
        assert polls[0]["question"] == "下次團練訂哪天"

        options = await db.query_all(
            "SELECT * FROM poll_options WHERE poll_id = ? ORDER BY sort", (polls[0]["id"],)
        )
        assert [int(o["meta"]) for o in options] == expected_epochs

    async def test_option_labels_are_plain_text_not_discord_timestamp_markup(self, db) -> None:
        """Select 選單的選項文字不會解析 `<t:...>` markup——直接塞那種字串會讓
        使用者在下拉選單看到一串原始字元，這裡確保選項標籤是純文字。"""
        view = TimeSlotPickerView(params=_make_params(), tz="Asia/Taipei")
        interaction = _make_interaction()
        await _add_n_candidates(view, interaction, MIN_OPTIONS)

        await view._on_create(interaction)

        polls = await db.query_all("SELECT * FROM polls")
        options = await db.query_all(
            "SELECT * FROM poll_options WHERE poll_id = ?", (polls[0]["id"],)
        )
        for option in options:
            assert "<t:" not in option["label"]

    async def test_announces_to_channel_and_edits_ephemeral_with_jump_url(self, db) -> None:
        view = TimeSlotPickerView(params=_make_params(), tz="Asia/Taipei")
        interaction = _make_interaction()
        await _add_n_candidates(view, interaction, MIN_OPTIONS)

        await view._on_create(interaction)

        interaction.channel.send.assert_awaited_once()
        _, kwargs = interaction.response.edit_message.call_args
        assert "999999" in kwargs["content"]

    async def test_disables_all_children_after_create(self, db) -> None:
        view = TimeSlotPickerView(params=_make_params(), tz="Asia/Taipei")
        interaction = _make_interaction()
        await _add_n_candidates(view, interaction, MIN_OPTIONS)

        await view._on_create(interaction)

        assert all(child.disabled for child in view.children)  # type: ignore[attr-defined]

    async def test_missing_channel_still_creates_poll(self, db) -> None:
        view = TimeSlotPickerView(params=_make_params(), tz="Asia/Taipei")
        interaction = _make_interaction()
        interaction.channel = None  # 拿不到頻道
        await _add_n_candidates(view, interaction, MIN_OPTIONS)

        await view._on_create(interaction)

        assert len(await db.query_all("SELECT * FROM polls")) == 1
        _, kwargs = interaction.response.edit_message.call_args
        assert "/poll results" in kwargs["content"]

    async def test_channel_send_failure_still_creates_poll(self, db) -> None:
        view = TimeSlotPickerView(params=_make_params(), tz="Asia/Taipei")
        interaction = _make_interaction()
        interaction.channel.send = AsyncMock(
            side_effect=discord.HTTPException(MagicMock(status=500), "boom")
        )
        await _add_n_candidates(view, interaction, MIN_OPTIONS)

        await view._on_create(interaction)  # 不應拋例外

        assert len(await db.query_all("SELECT * FROM polls")) == 1
        _, kwargs = interaction.response.edit_message.call_args
        assert "/poll results" in kwargs["content"]


class TestCancelAndTimeout:
    async def test_cancel_writes_nothing_and_disables_children(self, db) -> None:
        view = TimeSlotPickerView(params=_make_params(), tz="Asia/Taipei")
        await view._on_cancel(_make_interaction())

        assert await db.query_all("SELECT * FROM polls") == []
        assert all(child.disabled for child in view.children)  # type: ignore[attr-defined]

    async def test_on_timeout_edits_stored_message(self) -> None:
        view = TimeSlotPickerView(params=_make_params(), tz="Asia/Taipei")
        fake_message = AsyncMock()
        view.message = fake_message

        await view.on_timeout()

        fake_message.edit.assert_awaited_once()
        _, kwargs = fake_message.edit.call_args
        assert "逾時" in kwargs["content"]
