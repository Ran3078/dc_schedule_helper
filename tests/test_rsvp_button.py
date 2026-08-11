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

from src.bot.views_rsvp import _TEMPLATE, RSVP_STATUSES, RsvpButton, build_rsvp_view
from src.db import repo
from src.lib.clock import now_ms
from src.lib.ids import new_id

GUILD_ID = 111111111111111111
CHANNEL_ID = 222222222222222222
USER_ID = 333333333333333333


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


class TestBuildRsvpView:
    def test_creates_three_buttons(self) -> None:
        view = build_rsvp_view("e1")
        assert len(view.children) == 3

    def test_buttons_cover_all_statuses(self) -> None:
        view = build_rsvp_view("e1")
        statuses = {child.status for child in view.children}
        assert statuses == set(RSVP_STATUSES)

    def test_buttons_are_persistent(self) -> None:
        """timeout=None 是持久化的必要條件之一。"""
        view = build_rsvp_view("e1")
        assert view.timeout is None


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
