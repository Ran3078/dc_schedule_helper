"""ConfirmEventView 測試：`/event create` 的預覽確認流程。

重點是「取消不寫入任何資料」與「確認才真的建立活動並記錄公告訊息 ID」，
這是本次加入的攔截打錯字機制，寫錯了會讓使用者的取消操作變成沒作用。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

from src.bot.modals import PendingEvent
from src.bot.views import ConfirmEventView
from src.bot.views_rsvp import RsvpButton
from src.db import repo
from src.lib.clock import now_ms
from src.lib.ids import new_id

GUILD_ID = 111111111111111111
CHANNEL_ID = 222222222222222222
USER_ID = 333333333333333333


def _make_pending(**overrides) -> PendingEvent:
    defaults = dict(
        guild_id=GUILD_ID,
        channel_id=CHANNEL_ID,
        creator_id=USER_ID,
        title="週五團練",
        starts_at_utc=now_ms() + 3_600_000,
        duration_minutes=None,
        tz="Asia/Taipei",
        location=None,
    )
    return PendingEvent(**{**defaults, **overrides})


def _make_interaction(*, sent_message_id: int = 999999, guild=None) -> MagicMock:
    """組一個假的 discord.Interaction，只提供本模組用得到的屬性。

    guild 預設是空白 MagicMock：MagicMock 的 __iter__ 預設回傳空迭代器，
    所以 role.members 這類展開不會拋例外，只是會展開成空集合 —— 對不檢查
    RSVP 統計內容的測試無害。要驗證未回覆名單正確展開時才需要傳入真正配置
    過 get_role/members 的 guild。

    `create_scheduled_event` 是 AsyncMock：`_confirm_impl` 現在（M6）一律會
    嘗試同步原生活動，沒有這個大部分測試會在 await 一個普通 MagicMock 時炸掉
    （這裡的假 guild_settings 查不到列，`sync_native_events` 判斷式因此預設
    當作「開啟」）。想測試「同步失敗」的情境才需要另外覆寫這個屬性。

    `guild.get_channel` 預設指回 `interaction.channel`：`_confirm_impl`
    （M7 起）改用 `_resolve_channel(interaction, pending.channel_id)` 找公告
    頻道，不是直接信任 `interaction.channel`——多數測試裡 `pending.channel_id`
    就是 `CHANNEL_ID`，這裡讓 `get_channel(CHANNEL_ID)` 也回同一個假頻道，
    行為才會跟改動前一致。想測試「公告頻道跟指令所在頻道不同」才需要另外
    覆寫這個屬性。
    """
    interaction = MagicMock()
    interaction.response = AsyncMock()
    # is_done() 在真正的 discord.py 裡是同步方法（回傳 bool），
    # 若讓 AsyncMock 自動生成屬性會變成協程，"not is_done()" 恆為 False。
    interaction.response.is_done = MagicMock(return_value=False)

    sent_message = MagicMock()
    sent_message.id = sent_message_id
    sent_message.jump_url = f"https://discord.com/channels/{GUILD_ID}/{CHANNEL_ID}/{sent_message_id}"

    interaction.channel = AsyncMock()
    interaction.channel.send = AsyncMock(return_value=sent_message)
    # _resolve_channel 的備援路徑（guild 拿不到頻道，或 guild 本身是 None）
    # 會落到 interaction.client.fetch_channel——不管這次測試有沒有用到，
    # 都先接好，不然會在 await 一個沒配置的 MagicMock 屬性時炸掉。
    interaction.client.fetch_channel = AsyncMock(return_value=interaction.channel)

    if guild is not None:
        interaction.guild = guild
    else:
        interaction.guild = MagicMock(id=GUILD_ID)
        fake_scheduled_event = MagicMock(id=555555)
        interaction.guild.create_scheduled_event = AsyncMock(return_value=fake_scheduled_event)
        interaction.guild.get_channel = MagicMock(return_value=interaction.channel)

    return interaction


class TestCancel:
    async def test_cancel_writes_nothing_to_database(self, db) -> None:
        pending = _make_pending()
        view = ConfirmEventView(event_id=new_id(), pending=pending, description="測試內容")
        interaction = _make_interaction()

        await view._cancel_impl(interaction)

        assert await repo.count_guilds() == 0  # 更直接：確認 events 表沒有任何列
        rows = await db.query_all("SELECT * FROM events")
        assert rows == []

    async def test_cancel_edits_message_and_disables_buttons(self, db) -> None:
        pending = _make_pending()
        view = ConfirmEventView(event_id=new_id(), pending=pending, description=None)
        interaction = _make_interaction()

        await view._cancel_impl(interaction)

        interaction.response.edit_message.assert_awaited_once()
        _, kwargs = interaction.response.edit_message.call_args
        assert "取消" in kwargs["content"]
        assert all(child.disabled for child in view.children)

    async def test_cancel_does_not_touch_channel(self, db) -> None:
        """取消不該發任何公開訊息 —— 這是整個確認流程存在的意義。"""
        pending = _make_pending()
        view = ConfirmEventView(event_id=new_id(), pending=pending, description=None)
        interaction = _make_interaction()

        await view._cancel_impl(interaction)

        interaction.channel.send.assert_not_awaited()


class TestConfirm:
    async def test_confirm_creates_event_in_database(self, db) -> None:
        event_id = new_id()
        pending = _make_pending(title="確認測試活動", location="語音頻道")
        view = ConfirmEventView(event_id=event_id, pending=pending, description="打完補給")
        interaction = _make_interaction()

        await view._confirm_impl(interaction)

        row = await repo.owned_event(event_id, GUILD_ID)
        assert row is not None
        assert row["title"] == "確認測試活動"
        assert row["location"] == "語音頻道"
        assert row["description"] == "打完補給"

    async def test_confirm_posts_public_message_and_records_id(self, db) -> None:
        event_id = new_id()
        pending = _make_pending()
        view = ConfirmEventView(event_id=event_id, pending=pending, description=None)
        interaction = _make_interaction(sent_message_id=555)

        await view._confirm_impl(interaction)

        interaction.channel.send.assert_awaited_once()
        row = await repo.owned_event(event_id, GUILD_ID)
        assert row["message_id"] == "555"

    async def test_confirm_shows_jump_url_on_success(self, db) -> None:
        pending = _make_pending()
        view = ConfirmEventView(event_id=new_id(), pending=pending, description=None)
        interaction = _make_interaction()

        await view._confirm_impl(interaction)

        _, kwargs = interaction.response.edit_message.call_args
        assert "https://discord.com/channels" in kwargs["content"]

    async def test_confirm_is_scoped_to_pending_guild(self, db) -> None:
        """事件必須寫進 pending.guild_id 指定的伺服器，不是隨便一個。"""
        event_id = new_id()
        other_guild = 999999999999999999
        pending = _make_pending(guild_id=other_guild)
        view = ConfirmEventView(event_id=event_id, pending=pending, description=None)
        interaction = _make_interaction()

        await view._confirm_impl(interaction)

        assert await repo.owned_event(event_id, GUILD_ID) is None
        assert await repo.owned_event(event_id, other_guild) is not None

    async def test_confirm_when_send_fails_still_reports_event_id(self, db) -> None:
        """發訊息失敗時，活動仍已建立 —— 訊息要告訴使用者怎麼找回它，而不是假裝失敗。"""
        event_id = new_id()
        pending = _make_pending()
        view = ConfirmEventView(event_id=event_id, pending=pending, description=None)
        interaction = _make_interaction()
        interaction.channel.send = AsyncMock(return_value=None)

        await view._confirm_impl(interaction)

        assert await repo.owned_event(event_id, GUILD_ID) is not None
        _, kwargs = interaction.response.edit_message.call_args
        assert event_id in kwargs["content"]

    async def test_confirm_disables_buttons_after_success(self, db) -> None:
        pending = _make_pending()
        view = ConfirmEventView(event_id=new_id(), pending=pending, description=None)
        interaction = _make_interaction()

        await view._confirm_impl(interaction)

        assert all(child.disabled for child in view.children)

    async def test_confirm_defers_before_doing_any_work(self, db) -> None:
        """實測踩過的坑：整條路徑要跑好幾次 DB 往返＋兩次 Discord API 呼叫
        （發公告、同步原生活動），bot 剛啟動、Turso 連線還沒暖機時很容易
        超過 Discord 對 interaction 的 3 秒初次回應期限，逾期後
        interaction token 直接失效（`404 Unknown interaction`），不是能
        重試的那種錯誤——一開始就 defer 才是正解。"""
        pending = _make_pending()
        view = ConfirmEventView(event_id=new_id(), pending=pending, description=None)
        interaction = _make_interaction()

        await view._confirm_impl(interaction)

        interaction.response.defer.assert_awaited_once()

    async def test_confirm_falls_back_to_message_edit_when_interaction_expired(
        self, db
    ) -> None:
        """就算 defer 沒能挽救（interaction token 已經失效），最後回報
        結果的那一步也不該把整個確認流程炸掉——改用訊息本身編輯（bot
        token，不受 interaction token 有效期限制）。"""
        pending = _make_pending()
        view = ConfirmEventView(event_id=new_id(), pending=pending, description=None)
        interaction = _make_interaction()
        interaction.response.edit_message = AsyncMock(
            side_effect=discord.NotFound(MagicMock(status=404), "Unknown interaction")
        )
        final_message = AsyncMock()
        view.message = final_message

        await view._confirm_impl(interaction)  # 不應拋例外

        final_message.edit.assert_awaited_once()
        _, kwargs = final_message.edit.call_args
        assert "已發布" in kwargs["content"]


class TestConfirmWithInvitees:
    """user_ids/role_ids/tag_everyone 是 InviteePickerView 交接過來的邀請對象。
    這裡守的是「Tag 人的能力」端到端：資料庫要寫對，發布訊息要真的帶會觸發
    推播的 content，且 allowed_mentions 白名單要精確等於這次選定的對象。
    """

    async def test_writes_invitees_atomically_with_event(self, db) -> None:
        event_id = new_id()
        pending = _make_pending()
        view = ConfirmEventView(
            event_id=event_id, pending=pending, description=None,
            user_ids=[111, 222], role_ids=[333],
        )
        interaction = _make_interaction()

        await view._confirm_impl(interaction)

        invitees = await repo.list_event_invitees(event_id, GUILD_ID)
        users = {i["target_id"] for i in invitees if i["target_type"] == "user"}
        roles = {i["target_id"] for i in invitees if i["target_type"] == "role"}
        assert users == {"111", "222"}
        assert roles == {"333"}

    async def test_sends_content_with_mentions(self, db) -> None:
        pending = _make_pending()
        view = ConfirmEventView(
            event_id=new_id(), pending=pending, description=None,
            user_ids=[111], role_ids=[222],
        )
        interaction = _make_interaction()

        await view._confirm_impl(interaction)

        _, kwargs = interaction.channel.send.call_args
        assert kwargs["content"] == "<@111> <@&222>"

    async def test_allowed_mentions_whitelist_matches_selection(self, db) -> None:
        pending = _make_pending()
        view = ConfirmEventView(
            event_id=new_id(), pending=pending, description=None,
            user_ids=[111], role_ids=[222],
        )
        interaction = _make_interaction()

        await view._confirm_impl(interaction)

        _, kwargs = interaction.channel.send.call_args
        allowed = kwargs["allowed_mentions"]
        assert [o.id for o in allowed.users] == [111]
        assert [o.id for o in allowed.roles] == [222]
        assert allowed.everyone is False

    async def test_tag_everyone_writes_everyone_row_and_allows_it(self, db) -> None:
        event_id = new_id()
        pending = _make_pending()
        view = ConfirmEventView(
            event_id=event_id, pending=pending, description=None, tag_everyone=True
        )
        interaction = _make_interaction()

        await view._confirm_impl(interaction)

        invitees = await repo.list_event_invitees(event_id, GUILD_ID)
        assert invitees[0]["target_type"] == "everyone"
        _, kwargs = interaction.channel.send.call_args
        assert kwargs["content"] == "@everyone"
        assert kwargs["allowed_mentions"].everyone is True

    async def test_no_invitees_sends_no_content(self, db) -> None:
        """沒選任何人是合法情境（全程選填）：content 該是 None，不是空字串。"""
        pending = _make_pending()
        view = ConfirmEventView(event_id=new_id(), pending=pending, description=None)
        interaction = _make_interaction()

        await view._confirm_impl(interaction)

        _, kwargs = interaction.channel.send.call_args
        assert kwargs["content"] is None

    async def test_embed_shows_invitees_after_publish(self, db) -> None:
        pending = _make_pending()
        view = ConfirmEventView(
            event_id=new_id(), pending=pending, description=None, user_ids=[111]
        )
        interaction = _make_interaction()

        await view._confirm_impl(interaction)

        _, kwargs = interaction.channel.send.call_args
        field = next(f for f in kwargs["embed"].fields if "邀請對象" in f.name)
        assert "<@111>" in field.value


class TestNativeEventSync:
    """M6：發布活動時同步建立原生 Scheduled Event（`guild_settings.sync_native_events`
    預設開啟）。"""

    async def test_syncs_by_default_and_records_discord_event_id(self, db) -> None:
        event_id = new_id()
        pending = _make_pending()
        view = ConfirmEventView(event_id=event_id, pending=pending, description=None)
        interaction = _make_interaction()

        await view._confirm_impl(interaction)

        interaction.guild.create_scheduled_event.assert_awaited_once()
        row = await repo.owned_event(event_id, GUILD_ID)
        assert row["discord_event_id"] == "555555"

    async def test_skips_sync_when_guild_setting_disabled(self, db) -> None:
        await repo.ensure_guild(GUILD_ID, "Asia/Taipei")
        await db.execute(
            "UPDATE guild_settings SET sync_native_events = 0 WHERE guild_id = ?",
            (str(GUILD_ID),),
        )
        event_id = new_id()
        pending = _make_pending()
        view = ConfirmEventView(event_id=event_id, pending=pending, description=None)
        interaction = _make_interaction()

        await view._confirm_impl(interaction)

        interaction.guild.create_scheduled_event.assert_not_awaited()
        row = await repo.owned_event(event_id, GUILD_ID)
        assert row["discord_event_id"] is None

    async def test_sync_failure_does_not_affect_publish_confirmation_text(self, db) -> None:
        """同步原生活動是附加功能，失敗不該讓使用者以為活動本身建立失敗了。"""
        event_id = new_id()
        pending = _make_pending()
        view = ConfirmEventView(event_id=event_id, pending=pending, description=None)
        interaction = _make_interaction()
        interaction.guild.create_scheduled_event = AsyncMock(
            side_effect=discord.HTTPException(MagicMock(status=403), "forbidden")
        )

        await view._confirm_impl(interaction)  # 不應拋例外

        row = await repo.owned_event(event_id, GUILD_ID)
        assert row["discord_event_id"] is None
        _, kwargs = interaction.response.edit_message.call_args
        assert "https://discord.com/channels" in kwargs["content"]


class TestTimeout:
    async def test_on_timeout_edits_stored_message_reference(self, db) -> None:
        """逾時清理走 self.message.edit，不是 interaction —— 此時互動早已過期。"""
        pending = _make_pending()
        view = ConfirmEventView(event_id=new_id(), pending=pending, description=None)
        fake_message = AsyncMock()
        view.message = fake_message

        await view.on_timeout()

        fake_message.edit.assert_awaited_once()
        _, kwargs = fake_message.edit.call_args
        assert "逾時" in kwargs["content"]
        assert all(child.disabled for child in view.children)

    async def test_on_timeout_without_message_does_not_raise(self, db) -> None:
        """理論上 message 一定會在 modal.on_submit 後被設好，但防禦性地確保 None 不炸。"""
        pending = _make_pending()
        view = ConfirmEventView(event_id=new_id(), pending=pending, description=None)
        assert view.message is None

        await view.on_timeout()  # 不應拋例外


class TestConfirmAttachesRsvp:
    """發布公告時要附上 RSVP 按鈕，且第一版 embed 就該顯示初始統計
    （✅ 參加（0）／⏳ 未回覆），不必等第一次有人按按鈕才出現。
    """

    async def test_publishes_with_rsvp_view(self, db) -> None:
        event_id = new_id()
        pending = _make_pending()
        view = ConfirmEventView(event_id=event_id, pending=pending, description=None)
        interaction = _make_interaction()

        await view._confirm_impl(interaction)

        _, kwargs = interaction.channel.send.call_args
        rsvp_view = kwargs["view"]
        assert len(rsvp_view.children) == 3
        assert all(isinstance(child, RsvpButton) for child in rsvp_view.children)
        assert all(child.event_id == event_id for child in rsvp_view.children)

    async def test_initial_embed_shows_zero_yes(self, db) -> None:
        pending = _make_pending()
        view = ConfirmEventView(event_id=new_id(), pending=pending, description=None)
        interaction = _make_interaction()

        await view._confirm_impl(interaction)

        _, kwargs = interaction.channel.send.call_args
        embed = kwargs["embed"]
        yes_field = next(f for f in embed.fields if f.name.startswith("✅ 參加"))
        assert "（0）" in yes_field.name

    async def test_initial_embed_reflects_invited_pool_as_no_response(self, db) -> None:
        """發布當下邀請名單裡的人都還沒回覆，第一版 embed 就該把他們列進未回覆。"""
        event_id = new_id()
        pending = _make_pending()
        view = ConfirmEventView(
            event_id=event_id, pending=pending, description=None, user_ids=[111, 222]
        )
        guild = SimpleNamespace(
            id=GUILD_ID,
            get_role=lambda rid: None,
            members=[],
            create_scheduled_event=AsyncMock(return_value=MagicMock(id=555555)),
        )
        interaction = _make_interaction(guild=guild)
        # _resolve_channel 找頻道靠 guild.get_channel；這裡指回 interaction.channel
        # 讓 channel.send 的斷言還是打在同一個假物件上（見 _make_interaction 的
        # 預設 guild 分支同樣的作法）。
        guild.get_channel = lambda channel_id: interaction.channel

        await view._confirm_impl(interaction)

        _, kwargs = interaction.channel.send.call_args
        embed = kwargs["embed"]
        no_response_field = next(f for f in embed.fields if "未回覆" in f.name)
        assert "<@111>" in no_response_field.value
        assert "<@222>" in no_response_field.value

    async def test_no_rsvp_fields_shown_when_guild_unavailable(self, db) -> None:
        """interaction.guild 理論上一定有值，但 None 時不該讓發布整個炸掉，
        只是不顯示 RSVP 統計欄位。"""
        pending = _make_pending()
        view = ConfirmEventView(event_id=new_id(), pending=pending, description=None)
        interaction = _make_interaction()
        interaction.guild = None

        await view._confirm_impl(interaction)

        _, kwargs = interaction.channel.send.call_args
        embed = kwargs["embed"]
        assert not any("參加" in f.name for f in embed.fields)


class TestConfirmWithRestrictRsvp:
    """restrict_rsvp 是從 InviteePickerView 帶過來的第三個政策旗標
    （跟 user_ids/role_ids/tag_everyone 同一批），要一路寫進 DB 並反映在
    發布後的公告卡片上。
    """

    async def test_writes_restrict_rsvp_to_database(self, db) -> None:
        event_id = new_id()
        pending = _make_pending()
        view = ConfirmEventView(
            event_id=event_id, pending=pending, description=None, restrict_rsvp=True
        )
        interaction = _make_interaction()

        await view._confirm_impl(interaction)

        row = await repo.owned_event(event_id, GUILD_ID)
        assert row["restrict_rsvp"] == 1

    async def test_defaults_to_unrestricted(self, db) -> None:
        event_id = new_id()
        pending = _make_pending()
        view = ConfirmEventView(event_id=event_id, pending=pending, description=None)
        interaction = _make_interaction()

        await view._confirm_impl(interaction)

        row = await repo.owned_event(event_id, GUILD_ID)
        assert row["restrict_rsvp"] == 0

    async def test_published_embed_shows_restrict_hint(self, db) -> None:
        event_id = new_id()
        pending = _make_pending()
        view = ConfirmEventView(
            event_id=event_id,
            pending=pending,
            description=None,
            user_ids=[111],
            restrict_rsvp=True,
        )
        interaction = _make_interaction()

        await view._confirm_impl(interaction)

        _, kwargs = interaction.channel.send.call_args
        field = next(f for f in kwargs["embed"].fields if "邀請對象" in f.name)
        assert "僅限以下對象回覆" in field.name


class TestConfirmSchedulesDefaultReminders:
    """發布活動時要自動排定預設提醒（預設只在開始前 5 分鐘提醒一次，可由
    guild_settings 的 default_reminders 調整），不必使用者另外操作 —— 這是
    M4 的核心承諾。
    """

    async def test_schedules_default_five_minute_reminder(self, db) -> None:
        pending = _make_pending(starts_at_utc=now_ms() + 3_600_000)
        view = ConfirmEventView(event_id=new_id(), pending=pending, description=None)
        interaction = _make_interaction()

        await view._confirm_impl(interaction)

        rows = await db.query_all(
            "SELECT offset_min FROM reminders WHERE event_id = ?", (view.event_id,)
        )
        assert [r["offset_min"] for r in rows] == [5]

    async def test_offset_that_would_fire_in_the_past_is_skipped(self, db) -> None:
        """活動 2 分鐘後就開始，「提前 5 分鐘」排出來會是過去的時間，該跳過不排。"""
        pending = _make_pending(starts_at_utc=now_ms() + 2 * 60_000)
        view = ConfirmEventView(event_id=new_id(), pending=pending, description=None)
        interaction = _make_interaction()

        await view._confirm_impl(interaction)

        rows = await db.query_all(
            "SELECT offset_min FROM reminders WHERE event_id = ?", (view.event_id,)
        )
        assert rows == []

    async def test_uses_guild_specific_default_reminders_setting(self, db) -> None:
        await repo.ensure_guild(GUILD_ID, "Asia/Taipei")
        await db.execute(
            "UPDATE guild_settings SET default_reminders = ? WHERE guild_id = ?",
            ("30,5", str(GUILD_ID)),
        )
        pending = _make_pending(starts_at_utc=now_ms() + 3_600_000)
        view = ConfirmEventView(event_id=new_id(), pending=pending, description=None)
        interaction = _make_interaction()

        await view._confirm_impl(interaction)

        rows = await db.query_all(
            "SELECT offset_min FROM reminders WHERE event_id = ? ORDER BY offset_min",
            (view.event_id,),
        )
        assert [r["offset_min"] for r in rows] == [5, 30]


class TestPositionsIntegration:
    """M8：`/ff14_recruit` 帶 `positions` 時，`_confirm_impl` 要一併寫入
    `event_role_slots` 並在公告 embed/view 帶出來；`positions=()`（現有
    `/event create` 的預設情況）要完全不受影響——這是最重要的回歸測試。"""

    async def test_positions_empty_by_default_writes_no_role_slots(self, db) -> None:
        """對照組：/event create 沒有 positions 參數可給，view 建構時就是
        預設值，行為要跟這個功能加進來之前一致。"""
        event_id = new_id()
        pending = _make_pending()
        view = ConfirmEventView(event_id=event_id, pending=pending, description=None)
        interaction = _make_interaction()

        await view._confirm_impl(interaction)

        assert await repo.list_event_role_slots(event_id, GUILD_ID) == []
        _, kwargs = interaction.channel.send.call_args
        assert len(kwargs["view"].children) == 3  # 只有 RSVP 三顆按鈕，沒有職位選單

    async def test_positions_given_writes_role_slots(self, db) -> None:
        event_id = new_id()
        pending = _make_pending()
        view = ConfirmEventView(
            event_id=event_id, pending=pending, description=None,
            positions=["MT", "ST", "D1"],
        )
        interaction = _make_interaction()

        await view._confirm_impl(interaction)

        slots = await repo.list_event_role_slots(event_id, GUILD_ID)
        assert [s["position"] for s in slots] == ["MT", "ST", "D1"]

    async def test_positions_given_includes_role_fields_in_embed_and_view(self, db) -> None:
        event_id = new_id()
        pending = _make_pending()
        view = ConfirmEventView(
            event_id=event_id, pending=pending, description=None, positions=["MT"],
        )
        interaction = _make_interaction()

        await view._confirm_impl(interaction)

        _, kwargs = interaction.channel.send.call_args
        embed = kwargs["embed"]
        assert any("MT" in f.name for f in embed.fields)
        assert len(kwargs["view"].children) == 4  # 3 顆 RSVP 按鈕 + PositionSelect
