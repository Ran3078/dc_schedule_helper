"""JobPickerView 測試 —— `PositionSelect` 選定位置後的短命職業選單（M8）。

跟 `views_datetime.DateTimePickerView` 同一種短命 View 測法：直接建構
View、寫入 Select 的 `_values`，呼叫 callback，不用真的跑一次 gateway
往返（見 test_datetime_picker.py／test_polls_views.py 同樣的手法）。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from src.bot.views_role_job import JobPickerView
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


async def _make_slot(event_id: str, position: str) -> str:
    await repo.set_event_role_slots(event_id, GUILD_ID, [position])
    slots = await repo.list_event_role_slots(event_id, GUILD_ID)
    return slots[0]["id"]


def _make_channel() -> MagicMock:
    channel = MagicMock()
    channel.fetch_message = AsyncMock(return_value=AsyncMock())
    channel.send = AsyncMock()
    return channel


def _make_interaction(*, user_id: int = USER_ID, channel: MagicMock | None = None) -> MagicMock:
    interaction = MagicMock()
    interaction.guild_id = GUILD_ID
    interaction.user = MagicMock(id=user_id)
    interaction.response = AsyncMock()
    interaction.guild = MagicMock(id=GUILD_ID, members=[])
    interaction.channel = channel or _make_channel()
    resolved_channel = channel or interaction.channel
    interaction.client = MagicMock()
    interaction.client.get_channel.return_value = resolved_channel
    interaction.client.fetch_channel = AsyncMock(return_value=resolved_channel)
    return interaction


def _picker_with_job(event_id: str, role_slot_id: str, position: str, job: str) -> JobPickerView:
    """比照 PositionSelect 測試的 `_select_with_value` 手法：直接寫
    `_values` 模擬使用者在職業選單裡選了什麼。"""
    picker = JobPickerView(event_id=event_id, role_slot_id=role_slot_id, position=position)
    picker.children[0]._values = [job]  # type: ignore[attr-defined]
    return picker


class TestOnPick:
    async def test_first_pick_is_confirmed(self, db) -> None:
        event_id = await _create_event(db)
        slot_id = await _make_slot(event_id, "D1")
        interaction = _make_interaction()

        picker = _picker_with_job(event_id, slot_id, "D1", "武士")
        await picker.on_pick(interaction, "武士")

        signups = await repo.list_event_role_signups(event_id, GUILD_ID)
        assert signups[0]["job"] == "武士"
        assert signups[0]["waitlisted"] == 0
        _, kwargs = interaction.response.edit_message.call_args
        assert "D1" in kwargs["content"]
        assert "武士" in kwargs["content"]

    async def test_second_pick_to_full_slot_is_waitlisted(self, db) -> None:
        event_id = await _create_event(db)
        slot_id = await _make_slot(event_id, "D1")
        await repo.set_role_signup(event_id, GUILD_ID, USER_ID, slot_id, "武士")
        interaction = _make_interaction(user_id=OTHER_USER_ID)

        picker = _picker_with_job(event_id, slot_id, "D1", "忍者")
        await picker.on_pick(interaction, "忍者")

        _, kwargs = interaction.response.edit_message.call_args
        assert "已滿" in kwargs["content"]
        assert "候補名單" in kwargs["content"]

    async def test_switching_slot_frees_old_one_and_notifies_promotion(self, db) -> None:
        event_id = await _create_event(db)
        await repo.set_event_role_slots(event_id, GUILD_ID, ["D1", "D3"])
        slots = await repo.list_event_role_slots(event_id, GUILD_ID)
        d1_id = next(s["id"] for s in slots if s["position"] == "D1")
        d3_id = next(s["id"] for s in slots if s["position"] == "D3")

        await repo.set_role_signup(event_id, GUILD_ID, USER_ID, d1_id, "武士")
        await repo.set_role_signup(event_id, GUILD_ID, OTHER_USER_ID, d1_id, "忍者")  # 候補

        channel = _make_channel()
        interaction = _make_interaction(user_id=USER_ID, channel=channel)
        picker = _picker_with_job(event_id, d3_id, "D3", "舞者")
        await picker.on_pick(interaction, "舞者")

        # USER_ID 換去 D3，D1 空出來，候補的 OTHER_USER_ID 應該被遞補
        signups = {s["user_id"]: s for s in await repo.list_event_role_signups(event_id, GUILD_ID)}
        assert signups[str(USER_ID)]["role_slot_id"] == d3_id
        assert signups[str(OTHER_USER_ID)]["waitlisted"] == 0
        channel.send.assert_awaited_once()
        _, kwargs = channel.send.call_args
        assert str(OTHER_USER_ID) in kwargs["content"]

    async def test_reselecting_same_slot_does_not_trigger_promotion(self, db) -> None:
        """換到自己原本就佔著的同一個位置（例如同位置換職業）不該誤觸發
        「舊位置」的遞補——那個「舊位置」其實就是這個位置本身。"""
        event_id = await _create_event(db)
        slot_id = await _make_slot(event_id, "D1")
        await repo.set_role_signup(event_id, GUILD_ID, USER_ID, slot_id, "武士")
        channel = _make_channel()
        interaction = _make_interaction(channel=channel)

        picker = _picker_with_job(event_id, slot_id, "D1", "忍者")
        await picker.on_pick(interaction, "忍者")

        channel.send.assert_not_awaited()
        signups = await repo.list_event_role_signups(event_id, GUILD_ID)
        assert signups[0]["job"] == "忍者"

    async def test_disables_and_stops_the_view_after_pick(self, db) -> None:
        event_id = await _create_event(db)
        slot_id = await _make_slot(event_id, "D1")
        interaction = _make_interaction()

        picker = _picker_with_job(event_id, slot_id, "D1", "武士")
        await picker.on_pick(interaction, "武士")

        assert all(child.disabled for child in picker.children)
        assert picker.is_finished()

    async def test_refreshes_public_announcement(self, db) -> None:
        event_id = await _create_event(db)
        await repo.set_event_message(event_id, GUILD_ID, 777)
        slot_id = await _make_slot(event_id, "D1")
        channel = _make_channel()
        interaction = _make_interaction(channel=channel)

        picker = _picker_with_job(event_id, slot_id, "D1", "武士")
        await picker.on_pick(interaction, "武士")

        channel.fetch_message.assert_awaited_once()
        _, kwargs = channel.fetch_message.return_value.edit.call_args
        assert kwargs["embed"] is not None
        assert len(kwargs["view"].children) == 4

    async def test_stale_slot_shows_error(self, db) -> None:
        event_id = await _create_event(db)
        interaction = _make_interaction()

        picker = _picker_with_job(event_id, "stale-id", "D1", "武士")
        await picker.on_pick(interaction, "武士")

        args, kwargs = interaction.response.send_message.call_args
        assert "不存在" in args[0]
        assert kwargs["ephemeral"] is True
