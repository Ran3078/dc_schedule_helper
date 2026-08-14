"""PositionSelect 測試 —— FF14 位置報名的持久化下拉選單（M8，DynamicItem）。

比照 test_rsvp_button.py／test_polls_views.py 的風格：
・custom_id 格式與 from_custom_id 的往返正確性
・只有「參加」的人能選位置
・選定位置後彈出職業選單（本檔案只驗證有彈出，職業選定後的寫入邏輯在
  test_job_picker.py）
・「取消我的位置」釋出名額、確定名額被釋出時觸發候補遞補通知
・活動不存在/已取消時的防呆
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from src.bot.views_rsvp import _CLEAR_VALUE, _POSITION_TEMPLATE, PositionSelect
from src.db import repo
from src.lib.clock import now_ms
from src.lib.ids import new_id

GUILD_ID = 111111111111111111
CHANNEL_ID = 222222222222222222
USER_ID = 333333333333333333
OTHER_USER_ID = 444444444444444444


async def _create_event(db, **kwargs) -> str:
    event_id = new_id()
    await repo.create_event(
        event_id=event_id,
        guild_id=GUILD_ID,
        channel_id=CHANNEL_ID,
        creator_id=USER_ID,
        title="零式團練",
        starts_at_utc=now_ms() + 3_600_000,
        tz="Asia/Taipei",
        **kwargs,
    )
    return event_id


async def _make_slot(event_id: str, position: str = "D1") -> str:
    await repo.set_event_role_slots(event_id, GUILD_ID, [position])
    slots = await repo.list_event_role_slots(event_id, GUILD_ID)
    return slots[0]["id"]


def _make_interaction(*, user_id: int = USER_ID) -> MagicMock:
    interaction = MagicMock()
    interaction.guild_id = GUILD_ID
    interaction.user = SimpleNamespace(id=user_id)
    interaction.response = AsyncMock()
    interaction.message = AsyncMock()
    interaction.guild = MagicMock(id=GUILD_ID, members=[])
    interaction.channel = MagicMock()
    interaction.channel.send = AsyncMock()
    interaction.original_response = AsyncMock()
    return interaction


def _select_with_value(event_id: str, value: str) -> PositionSelect:
    """比照 test_polls_views.py 的手法：直接寫 `_values` 模擬 Discord 派發
    前幫 Select 灌好的選取結果，不用真的跑一次 gateway 往返。"""
    select = PositionSelect(event_id=event_id)
    select.item._values = [value]
    return select


class TestCustomIdRoundTrip:
    def test_custom_id_matches_template(self) -> None:
        select = PositionSelect(event_id="abc123XYZ0")
        assert select.item.custom_id == "ev:pos:abc123XYZ0"
        assert _POSITION_TEMPLATE.match(select.item.custom_id)

    def test_template_parses_event_id(self) -> None:
        match = _POSITION_TEMPLATE.match("ev:pos:abc123XYZ0")
        assert match is not None
        assert match["event_id"] == "abc123XYZ0"

    async def test_from_custom_id_reconstructs_select(self) -> None:
        match = _POSITION_TEMPLATE.match("ev:pos:abc123XYZ0")
        assert match is not None
        select = await PositionSelect.from_custom_id(MagicMock(), MagicMock(), match)
        assert select.event_id == "abc123XYZ0"


class TestCallback:
    async def test_requires_yes_rsvp_first(self, db) -> None:
        event_id = await _create_event(db)
        slot_id = await _make_slot(event_id)
        interaction = _make_interaction()

        await _select_with_value(event_id, slot_id).callback(interaction)

        args, kwargs = interaction.response.send_message.call_args
        assert "先按「參加」" in args[0]
        assert kwargs["ephemeral"] is True

    async def test_opens_job_picker_after_yes_rsvp(self, db) -> None:
        event_id = await _create_event(db)
        slot_id = await _make_slot(event_id, "D1")
        await repo.upsert_rsvp(event_id, GUILD_ID, USER_ID, "yes")
        interaction = _make_interaction()

        await _select_with_value(event_id, slot_id).callback(interaction)

        interaction.response.send_message.assert_awaited_once()
        _, kwargs = interaction.response.send_message.call_args
        assert kwargs["ephemeral"] is True
        from src.bot.views_role_job import JobPickerView

        assert isinstance(kwargs["view"], JobPickerView)
        assert kwargs["view"].position == "D1"

    async def test_maybe_or_no_rsvp_is_rejected(self, db) -> None:
        event_id = await _create_event(db)
        slot_id = await _make_slot(event_id)
        await repo.upsert_rsvp(event_id, GUILD_ID, USER_ID, "maybe")
        interaction = _make_interaction()

        await _select_with_value(event_id, slot_id).callback(interaction)

        args, _ = interaction.response.send_message.call_args
        assert "先按「參加」" in args[0]

    async def test_nonexistent_event_shows_error(self, db) -> None:
        interaction = _make_interaction()

        await _select_with_value("does-not-exist", "x").callback(interaction)

        args, _ = interaction.response.send_message.call_args
        assert "找不到" in args[0]

    async def test_cancelled_event_is_rejected(self, db) -> None:
        event_id = await _create_event(db)
        slot_id = await _make_slot(event_id)
        await repo.upsert_rsvp(event_id, GUILD_ID, USER_ID, "yes")
        await repo.cancel_event(event_id, GUILD_ID)
        interaction = _make_interaction()

        await _select_with_value(event_id, slot_id).callback(interaction)

        args, _ = interaction.response.send_message.call_args
        assert "取消" in args[0]

    async def test_stale_slot_id_shows_error(self, db) -> None:
        event_id = await _create_event(db)
        await repo.upsert_rsvp(event_id, GUILD_ID, USER_ID, "yes")
        interaction = _make_interaction()

        await _select_with_value(event_id, "stale-slot-id").callback(interaction)

        args, _ = interaction.response.send_message.call_args
        assert "不存在" in args[0]

    async def test_no_guild_id_is_a_silent_noop(self, db) -> None:
        interaction = _make_interaction()
        interaction.guild_id = None

        await _select_with_value("e1", "x").callback(interaction)

        interaction.response.send_message.assert_not_awaited()


class TestClearSelection:
    async def test_clears_own_signup(self, db) -> None:
        event_id = await _create_event(db)
        slot_id = await _make_slot(event_id)
        await repo.upsert_rsvp(event_id, GUILD_ID, USER_ID, "yes")
        await repo.set_role_signup(event_id, GUILD_ID, USER_ID, slot_id, "武士")
        interaction = _make_interaction()

        await _select_with_value(event_id, _CLEAR_VALUE).callback(interaction)

        assert await repo.list_event_role_signups(event_id, GUILD_ID) == []
        args, _ = interaction.response.send_message.call_args
        assert "取消" in args[0]

    async def test_nothing_selected_shows_notice(self, db) -> None:
        event_id = await _create_event(db)
        await _make_slot(event_id)
        await repo.upsert_rsvp(event_id, GUILD_ID, USER_ID, "yes")
        interaction = _make_interaction()

        await _select_with_value(event_id, _CLEAR_VALUE).callback(interaction)

        args, _ = interaction.response.send_message.call_args
        assert "沒有選擇" in args[0]

    async def test_clearing_confirmed_slot_promotes_waitlist_and_notifies(self, db) -> None:
        event_id = await _create_event(db)
        slot_id = await _make_slot(event_id)
        await repo.upsert_rsvp(event_id, GUILD_ID, USER_ID, "yes")
        await repo.upsert_rsvp(event_id, GUILD_ID, OTHER_USER_ID, "yes")
        await repo.set_role_signup(event_id, GUILD_ID, USER_ID, slot_id, "武士")
        await repo.set_role_signup(event_id, GUILD_ID, OTHER_USER_ID, slot_id, "忍者")
        interaction = _make_interaction()

        await _select_with_value(event_id, _CLEAR_VALUE).callback(interaction)

        signups = {
            s["user_id"]: s["waitlisted"]
            for s in await repo.list_event_role_signups(event_id, GUILD_ID)
        }
        assert signups[str(OTHER_USER_ID)] == 0
        interaction.channel.send.assert_awaited_once()
        _, kwargs = interaction.channel.send.call_args
        assert str(OTHER_USER_ID) in kwargs["content"]

    async def test_clearing_waitlisted_slot_does_not_notify(self, db) -> None:
        event_id = await _create_event(db)
        slot_id = await _make_slot(event_id)
        await repo.upsert_rsvp(event_id, GUILD_ID, USER_ID, "yes")
        await repo.upsert_rsvp(event_id, GUILD_ID, OTHER_USER_ID, "yes")
        await repo.set_role_signup(event_id, GUILD_ID, USER_ID, slot_id, "武士")
        await repo.set_role_signup(event_id, GUILD_ID, OTHER_USER_ID, slot_id, "忍者")
        interaction = _make_interaction(user_id=OTHER_USER_ID)  # 候補的那位取消

        await _select_with_value(event_id, _CLEAR_VALUE).callback(interaction)

        interaction.channel.send.assert_not_awaited()
        signups = {
            s["user_id"]: s["waitlisted"]
            for s in await repo.list_event_role_signups(event_id, GUILD_ID)
        }
        assert signups[str(USER_ID)] == 0
