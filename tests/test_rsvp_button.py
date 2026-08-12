"""RsvpButton 測試 —— 持久化 RSVP 按鈕（DynamicItem）。

這是本專案「按鈕必須撐過 bot 重啟」這條規則的具體實作，重點測試：
・custom_id 格式與 from_custom_id 的往返正確性（這是持久化能不能運作的關鍵）
・按下按鈕後資料庫正確寫入、使用者拿到 ephemeral 確認
・公告訊息即時更新反映最新的參加/待定/不參加/未回覆
・活動不存在（已刪除）時不會誤更新、也不會拋例外
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

from src.bot.views_rsvp import _TEMPLATE, RSVP_STATUSES, RsvpButton, build_event_controls_view
from src.db import repo
from src.lib.clock import now_ms
from src.lib.ids import new_id

GUILD_ID = 111111111111111111
CHANNEL_ID = 222222222222222222
USER_ID = 333333333333333333
OTHER_USER_ID = 444444444444444444


async def _create_event(db, *, event_id: str | None = None, **kwargs) -> str:
    event_id = event_id or new_id()
    await repo.create_event(
        event_id=event_id,
        guild_id=GUILD_ID,
        channel_id=CHANNEL_ID,
        creator_id=USER_ID,
        title="週五團練",
        starts_at_utc=now_ms() + 3_600_000,
        tz="Asia/Taipei",
        **kwargs,
    )
    return event_id


def _make_interaction(*, guild=None) -> MagicMock:
    interaction = MagicMock()
    interaction.guild_id = GUILD_ID
    interaction.user = SimpleNamespace(id=USER_ID)
    interaction.response = AsyncMock()

    fake_message = AsyncMock()
    interaction.message = fake_message

    interaction.guild = guild if guild is not None else MagicMock(id=GUILD_ID, members=[])
    if guild is None:
        interaction.guild.get_role.return_value = None

    interaction.channel = MagicMock()
    interaction.channel.send = AsyncMock()

    return interaction


class TestCustomIdRoundTrip:
    def test_custom_id_matches_template(self) -> None:
        button = RsvpButton(event_id="abc123XYZ0", status="yes")
        assert button.item.custom_id == "ev:rsvp:abc123XYZ0:yes"
        assert _TEMPLATE.match(button.item.custom_id)

    def test_template_parses_event_id_and_status_groups(self) -> None:
        match = _TEMPLATE.match("ev:rsvp:abc123XYZ0:maybe")
        assert match is not None
        assert match["event_id"] == "abc123XYZ0"
        assert match["status"] == "maybe"

    async def test_from_custom_id_reconstructs_button(self) -> None:
        match = _TEMPLATE.match("ev:rsvp:abc123XYZ0:no")
        assert match is not None
        button = await RsvpButton.from_custom_id(MagicMock(), MagicMock(), match)
        assert button.event_id == "abc123XYZ0"
        assert button.status == "no"

    def test_all_three_statuses_produce_valid_custom_ids(self) -> None:
        for status in RSVP_STATUSES:
            button = RsvpButton(event_id="e1", status=status)
            assert _TEMPLATE.match(button.item.custom_id)


class TestBuildEventControlsView:
    def test_creates_three_buttons(self) -> None:
        view = build_event_controls_view("e1", [], [])
        assert len(view.children) == 3

    def test_buttons_cover_all_statuses(self) -> None:
        view = build_event_controls_view("e1", [], [])
        statuses = {child.status for child in view.children}
        assert statuses == set(RSVP_STATUSES)

    def test_buttons_are_persistent(self) -> None:
        """timeout=None 是持久化的必要條件之一。"""
        view = build_event_controls_view("e1", [], [])
        assert view.timeout is None

    def test_no_position_select_when_no_role_slots(self) -> None:
        """M8：沒設定職位的活動只有三顆按鈕，這個功能加進來之前的行為不變。"""
        view = build_event_controls_view("e1", [], [])
        assert len(view.children) == 3

    def test_adds_position_select_when_role_slots_given(self) -> None:
        slot = {"id": "s1", "position": "MT", "sort": 0}
        view = build_event_controls_view("e1", [slot], [])
        assert len(view.children) == 4


class TestCallback:
    async def test_records_rsvp_in_database(self, db) -> None:
        event_id = await _create_event(db)
        button = RsvpButton(event_id=event_id, status="yes")
        interaction = _make_interaction()

        await button.callback(interaction)

        rows = await repo.list_rsvps(event_id, GUILD_ID)
        assert rows[0]["user_id"] == str(USER_ID)
        assert rows[0]["status"] == "yes"

    async def test_sends_ephemeral_confirmation(self, db) -> None:
        event_id = await _create_event(db)
        button = RsvpButton(event_id=event_id, status="maybe")
        interaction = _make_interaction()

        await button.callback(interaction)

        interaction.response.send_message.assert_awaited_once()
        args, kwargs = interaction.response.send_message.call_args
        assert kwargs["ephemeral"] is True
        assert "待定" in args[0]

    async def test_refreshes_the_public_announcement(self, db) -> None:
        event_id = await _create_event(db)
        button = RsvpButton(event_id=event_id, status="yes")
        interaction = _make_interaction()

        await button.callback(interaction)

        interaction.message.edit.assert_awaited_once()
        _, kwargs = interaction.message.edit.call_args
        embed = kwargs["embed"]
        assert any(f.name.startswith("✅ 參加") for f in embed.fields)

    async def test_nonexistent_event_shows_error_and_skips_refresh(self, db) -> None:
        button = RsvpButton(event_id="does-not-exist", status="yes")
        interaction = _make_interaction()

        await button.callback(interaction)

        args, _ = interaction.response.send_message.call_args
        assert "找不到" in args[0]
        interaction.message.edit.assert_not_awaited()

    async def test_no_guild_id_is_a_silent_noop(self, db) -> None:
        """理論上不會發生（按鈕只出現在伺服器頻道），但防禦性地確保不拋例外。"""
        button = RsvpButton(event_id="e1", status="yes")
        interaction = _make_interaction()
        interaction.guild_id = None

        await button.callback(interaction)  # 不應拋例外

        interaction.response.send_message.assert_not_awaited()

    async def test_revote_updates_existing_row(self, db) -> None:
        event_id = await _create_event(db)
        interaction = _make_interaction()

        await RsvpButton(event_id=event_id, status="maybe").callback(interaction)
        await RsvpButton(event_id=event_id, status="no").callback(interaction)

        rows = await repo.list_rsvps(event_id, GUILD_ID)
        assert len(rows) == 1
        assert rows[0]["status"] == "no"


class TestCancelledEvent:
    """M7：/event cancel 會把公告卡片的按鈕重繪成 disabled，但這裡是最後一道
    防呆——就算 Discord 端還沒收到那次編輯，已取消的活動也不該再被 RSVP。"""

    async def test_rejects_rsvp_on_cancelled_event(self, db) -> None:
        event_id = await _create_event(db)
        await repo.cancel_event(event_id, GUILD_ID)
        button = RsvpButton(event_id=event_id, status="yes")
        interaction = _make_interaction()

        await button.callback(interaction)

        args, kwargs = interaction.response.send_message.call_args
        assert "取消" in args[0]
        assert kwargs["ephemeral"] is True
        assert await repo.list_rsvps(event_id, GUILD_ID) == []

    async def test_does_not_refresh_announcement_for_cancelled_event(self, db) -> None:
        event_id = await _create_event(db)
        await repo.cancel_event(event_id, GUILD_ID)
        button = RsvpButton(event_id=event_id, status="yes")
        interaction = _make_interaction()

        await button.callback(interaction)

        interaction.message.edit.assert_not_awaited()


class TestRefreshAnnouncementResilience:
    async def test_message_edit_failure_does_not_raise(self, db) -> None:
        event_id = await _create_event(db)
        button = RsvpButton(event_id=event_id, status="yes")
        interaction = _make_interaction()
        interaction.message.edit = AsyncMock(
            side_effect=discord.HTTPException(MagicMock(status=404), "not found")
        )

        await button.callback(interaction)  # 不應拋例外，只記 log

    async def test_missing_guild_skips_refresh_without_raising(self, db) -> None:
        event_id = await _create_event(db)
        button = RsvpButton(event_id=event_id, status="yes")
        interaction = _make_interaction()
        interaction.guild = None

        await button.callback(interaction)

        interaction.message.edit.assert_not_awaited()


class TestRestrictRsvp:
    """restrict_rsvp 開啟時，只有落在邀請名單展開後的成員能回覆。"""

    async def test_uninvited_user_is_rejected(self, db) -> None:
        event_id = await _create_event(db, restrict_rsvp=True, user_ids=[999])
        button = RsvpButton(event_id=event_id, status="yes")
        interaction = _make_interaction()  # interaction.user.id == USER_ID，不在 [999] 裡

        await button.callback(interaction)

        args, kwargs = interaction.response.send_message.call_args
        assert "僅限受邀對象" in args[0]
        assert kwargs["ephemeral"] is True
        assert await repo.list_rsvps(event_id, GUILD_ID) == []

    async def test_uninvited_user_does_not_trigger_refresh(self, db) -> None:
        event_id = await _create_event(db, restrict_rsvp=True, user_ids=[999])
        button = RsvpButton(event_id=event_id, status="yes")
        interaction = _make_interaction()

        await button.callback(interaction)

        interaction.message.edit.assert_not_awaited()

    async def test_explicitly_invited_user_is_allowed(self, db) -> None:
        event_id = await _create_event(db, restrict_rsvp=True, user_ids=[USER_ID])
        button = RsvpButton(event_id=event_id, status="yes")
        interaction = _make_interaction()

        await button.callback(interaction)

        rows = await repo.list_rsvps(event_id, GUILD_ID)
        assert rows[0]["user_id"] == str(USER_ID)

    async def test_user_invited_via_role_is_allowed(self, db) -> None:
        event_id = await _create_event(db, restrict_rsvp=True, role_ids=[555])
        button = RsvpButton(event_id=event_id, status="yes")

        role = SimpleNamespace(id=555, members=[SimpleNamespace(id=USER_ID, bot=False)])
        guild = MagicMock(id=GUILD_ID)
        guild.get_role.side_effect = lambda rid: role if rid == 555 else None
        interaction = _make_interaction(guild=guild)

        await button.callback(interaction)

        rows = await repo.list_rsvps(event_id, GUILD_ID)
        assert rows[0]["user_id"] == str(USER_ID)

    async def test_unrestricted_event_allows_anyone(self, db) -> None:
        """對照組：restrict_rsvp 沒開的話，沒被邀請的人一樣能回覆。"""
        event_id = await _create_event(db, restrict_rsvp=False, user_ids=[999])
        button = RsvpButton(event_id=event_id, status="yes")
        interaction = _make_interaction()

        await button.callback(interaction)

        rows = await repo.list_rsvps(event_id, GUILD_ID)
        assert rows[0]["user_id"] == str(USER_ID)

    async def test_missing_guild_on_restricted_event_treated_as_no_pool(self, db) -> None:
        """理論上不會發生，但 guild 拿不到時不該讓沒被邀請的人矇混過關。"""
        event_id = await _create_event(db, restrict_rsvp=True, user_ids=[USER_ID])
        button = RsvpButton(event_id=event_id, status="yes")
        interaction = _make_interaction()
        interaction.guild = None

        await button.callback(interaction)

        assert await repo.list_rsvps(event_id, GUILD_ID) == []


class TestClearsPositionOnNonYesRsvp:
    """M8：位置選擇疊加在「參加」狀態之上，RSVP 改成非「參加」要連帶清空
    位置選擇；讓出的名額若是確定名額，候補佇列裡最早報名的人要自動遞補，
    並在頻道收到通知。"""

    async def _make_slot(self, event_id: str, position: str = "D1") -> str:
        await repo.set_event_role_slots(event_id, GUILD_ID, [position])
        slots = await repo.list_event_role_slots(event_id, GUILD_ID)
        return slots[0]["id"]

    async def test_switching_to_maybe_clears_position(self, db) -> None:
        event_id = await _create_event(db)
        slot_id = await self._make_slot(event_id)
        await repo.upsert_rsvp(event_id, GUILD_ID, USER_ID, "yes")
        await repo.set_role_signup(event_id, GUILD_ID, USER_ID, slot_id, "武士")

        await RsvpButton(event_id=event_id, status="maybe").callback(_make_interaction())

        assert await repo.list_event_role_signups(event_id, GUILD_ID) == []

    async def test_switching_to_no_clears_position(self, db) -> None:
        event_id = await _create_event(db)
        slot_id = await self._make_slot(event_id)
        await repo.upsert_rsvp(event_id, GUILD_ID, USER_ID, "yes")
        await repo.set_role_signup(event_id, GUILD_ID, USER_ID, slot_id, "武士")

        await RsvpButton(event_id=event_id, status="no").callback(_make_interaction())

        assert await repo.list_event_role_signups(event_id, GUILD_ID) == []

    async def test_promotes_waitlist_and_notifies_channel(self, db) -> None:
        event_id = await _create_event(db)
        slot_id = await self._make_slot(event_id)
        await repo.upsert_rsvp(event_id, GUILD_ID, USER_ID, "yes")
        await repo.upsert_rsvp(event_id, GUILD_ID, OTHER_USER_ID, "yes")
        await repo.set_role_signup(event_id, GUILD_ID, USER_ID, slot_id, "武士")
        await repo.set_role_signup(event_id, GUILD_ID, OTHER_USER_ID, slot_id, "忍者")
        interaction = _make_interaction()

        await RsvpButton(event_id=event_id, status="no").callback(interaction)

        signups = {
            s["user_id"]: s["waitlisted"]
            for s in await repo.list_event_role_signups(event_id, GUILD_ID)
        }
        assert signups[str(OTHER_USER_ID)] == 0
        interaction.channel.send.assert_awaited_once()
        _, kwargs = interaction.channel.send.call_args
        assert str(OTHER_USER_ID) in kwargs["content"]

    async def test_no_op_when_no_position_was_selected(self, db) -> None:
        """對照組：本來就沒選職位的話，改 RSVP 不該出任何錯或多發通知。"""
        event_id = await _create_event(db)
        await self._make_slot(event_id)
        await repo.upsert_rsvp(event_id, GUILD_ID, USER_ID, "yes")
        interaction = _make_interaction()

        await RsvpButton(event_id=event_id, status="maybe").callback(interaction)

        interaction.channel.send.assert_not_awaited()

    async def test_yes_status_does_not_clear_position(self, db) -> None:
        """對照組：重複按「參加」（例如原本就是 yes）不該清空既有的位置選擇。"""
        event_id = await _create_event(db)
        slot_id = await self._make_slot(event_id)
        await repo.upsert_rsvp(event_id, GUILD_ID, USER_ID, "yes")
        await repo.set_role_signup(event_id, GUILD_ID, USER_ID, slot_id, "武士")

        await RsvpButton(event_id=event_id, status="yes").callback(_make_interaction())

        signups = await repo.list_event_role_signups(event_id, GUILD_ID)
        assert len(signups) == 1
