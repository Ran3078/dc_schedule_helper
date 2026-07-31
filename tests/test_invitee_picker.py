"""InviteePickerView 測試 —— `/event create` 流程裡挑選要 tag 的人／身分組。

重點：
・@everyone 切換按鈕只有在該伺服器允許時才會出現（見 allow_everyone_ping）
・整步全程選填，不選任何人也能直接「下一步」略過
・「下一步」正確把選擇交接給 ConfirmEventView，此時仍未寫入任何資料庫紀錄
   （真正寫入要等 ConfirmEventView 自己的「發布」按鈕）
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from src.bot.modals import PendingEvent
from src.bot.views import ConfirmEventView
from src.bot.views_invitees import InviteePickerView
from src.db import repo

GUILD_ID = 111111111111111111
CHANNEL_ID = 222222222222222222
USER_ID = 333333333333333333


def _make_pending(**overrides) -> PendingEvent:
    from src.lib.clock import now_ms

    defaults = dict(
        guild_id=GUILD_ID,
        channel_id=CHANNEL_ID,
        creator_id=USER_ID,
        title="週五團練",
        tz="Asia/Taipei",
        location=None,
        duration_minutes=None,
        starts_at_utc=now_ms() + 3_600_000,
    )
    return PendingEvent(**{**defaults, **overrides})


def _make_interaction() -> MagicMock:
    interaction = MagicMock()
    interaction.response = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=False)

    fake_message = MagicMock()
    fake_message.id = 555
    interaction.original_response = AsyncMock(return_value=fake_message)
    return interaction


def _view(*, allow_everyone_ping: bool = False, **pending_overrides) -> InviteePickerView:
    return InviteePickerView(
        pending=_make_pending(**pending_overrides),
        description=None,
        event_id="e1",
        allow_everyone_ping=allow_everyone_ping,
    )


class TestEveryoneToggleVisibility:
    def test_hidden_when_not_allowed(self) -> None:
        view = _view(allow_everyone_ping=False)
        assert view.everyone_button is None
        assert not any(
            getattr(child, "label", "") == "@everyone：關" for child in view.children
        )

    def test_shown_when_allowed(self) -> None:
        view = _view(allow_everyone_ping=True)
        assert view.everyone_button is not None
        assert view.everyone_button in view.children


class TestRestrictToggle:
    """「僅限受邀對象回覆」切換 —— Discord 沒有原生 checkbox，這裡是用按鈕
    模擬開關（跟 @everyone 切換同樣的作法，見 views_invitees.py 開頭說明）。
    """

    def test_defaults_to_open(self) -> None:
        view = _view()
        assert view.restrict_rsvp is False
        assert view.restrict_button.label == "🔓 開放所有人回覆"

    async def test_toggle_flips_state_and_label(self) -> None:
        view = _view()
        interaction = _make_interaction()

        await view._on_toggle_restrict(interaction)
        assert view.restrict_rsvp is True
        assert view.restrict_button.label == "🔒 僅限受邀對象回覆"

        await view._on_toggle_restrict(interaction)
        assert view.restrict_rsvp is False
        assert view.restrict_button.label == "🔓 開放所有人回覆"

    def test_button_always_present_regardless_of_everyone_setting(self) -> None:
        """跟 @everyone 切換不同：這個功能不需要伺服器額外授權，永遠顯示。"""
        assert _view(allow_everyone_ping=False).restrict_button is not None
        assert _view(allow_everyone_ping=True).restrict_button is not None

    async def test_embed_shows_restrict_hint_when_toggled_on(self) -> None:
        view = _view()
        interaction = _make_interaction()
        view.user_select._values = [SimpleNamespace(id=111)]
        await view._on_user_select(interaction)

        await view._on_toggle_restrict(interaction)

        field = next(f for f in view.build_embed().fields if "目前選擇的邀請對象" in f.name)
        assert "僅限以下對象回覆" in field.name


class TestSelection:
    async def test_selecting_users_updates_state(self) -> None:
        view = _view()
        interaction = _make_interaction()
        view.user_select._values = [SimpleNamespace(id=111), SimpleNamespace(id=222)]

        await view._on_user_select(interaction)

        assert view.selected_user_ids == [111, 222]
        interaction.response.edit_message.assert_awaited_once()

    async def test_selecting_roles_updates_state(self) -> None:
        view = _view()
        interaction = _make_interaction()
        view.role_select._values = [SimpleNamespace(id=333)]

        await view._on_role_select(interaction)

        assert view.selected_role_ids == [333]

    async def test_toggle_everyone_flips_state_and_label(self) -> None:
        view = _view(allow_everyone_ping=True)
        interaction = _make_interaction()
        assert view.tag_everyone is False

        await view._on_toggle_everyone(interaction)
        assert view.tag_everyone is True
        assert view.everyone_button.label == "@everyone：開"

        await view._on_toggle_everyone(interaction)
        assert view.tag_everyone is False
        assert view.everyone_button.label == "@everyone：關"

    def test_embed_shows_placeholder_when_nothing_selected(self) -> None:
        view = _view()
        embed = view.build_embed()
        field = next(f for f in embed.fields if "目前選擇" in f.name)
        assert "尚未選擇" in field.value

    async def test_embed_reflects_current_selection(self) -> None:
        view = _view()
        interaction = _make_interaction()
        view.user_select._values = [SimpleNamespace(id=111)]
        await view._on_user_select(interaction)

        field = next(f for f in view.build_embed().fields if "目前選擇" in f.name)
        assert "<@111>" in field.value


class TestCancel:
    async def test_cancel_disables_all_children(self) -> None:
        view = _view()
        await view._on_cancel(_make_interaction())
        assert all(child.disabled for child in view.children)  # type: ignore[attr-defined]

    async def test_cancel_edits_message_with_cancellation_notice(self) -> None:
        view = _view()
        interaction = _make_interaction()
        await view._on_cancel(interaction)
        _, kwargs = interaction.response.edit_message.call_args
        assert "取消" in kwargs["content"]

    async def test_cancel_writes_nothing_to_database(self, db) -> None:
        view = _view()
        await view._on_cancel(_make_interaction())
        assert await repo.count_guilds() == 0


class TestNext:
    async def test_next_hands_off_to_confirm_event_view(self, db) -> None:
        view = _view(title="交接測試")
        interaction = _make_interaction()
        view.user_select._values = [SimpleNamespace(id=111)]
        await view._on_user_select(interaction)
        view.role_select._values = [SimpleNamespace(id=222)]
        await view._on_role_select(interaction)

        await view._on_next(interaction)

        _, kwargs = interaction.response.edit_message.call_args
        confirm_view = kwargs["view"]
        assert isinstance(confirm_view, ConfirmEventView)
        assert confirm_view.pending.title == "交接測試"
        assert confirm_view.user_ids == [111]
        assert confirm_view.role_ids == [222]

    async def test_next_without_any_selection_still_proceeds(self, db) -> None:
        """全程選填：什麼都不選也能直接下一步。"""
        view = _view()
        interaction = _make_interaction()

        await view._on_next(interaction)

        _, kwargs = interaction.response.edit_message.call_args
        assert isinstance(kwargs["view"], ConfirmEventView)
        assert kwargs["view"].user_ids == []
        assert kwargs["view"].role_ids == []

    async def test_next_carries_everyone_flag(self, db) -> None:
        view = _view(allow_everyone_ping=True)
        interaction = _make_interaction()
        await view._on_toggle_everyone(interaction)

        await view._on_next(interaction)

        _, kwargs = interaction.response.edit_message.call_args
        assert kwargs["view"].tag_everyone is True

    async def test_next_carries_restrict_flag(self, db) -> None:
        view = _view()
        interaction = _make_interaction()
        await view._on_toggle_restrict(interaction)

        await view._on_next(interaction)

        _, kwargs = interaction.response.edit_message.call_args
        assert kwargs["view"].restrict_rsvp is True

    async def test_next_without_toggling_restrict_defaults_to_false(self, db) -> None:
        view = _view()
        interaction = _make_interaction()

        await view._on_next(interaction)

        _, kwargs = interaction.response.edit_message.call_args
        assert kwargs["view"].restrict_rsvp is False

    async def test_next_does_not_write_to_database_yet(self, db) -> None:
        view = _view()
        interaction = _make_interaction()
        view.user_select._values = [SimpleNamespace(id=111)]
        await view._on_user_select(interaction)

        await view._on_next(interaction)

        assert await repo.count_guilds() == 0

    async def test_next_preview_shows_selected_mentions(self, db) -> None:
        view = _view()
        interaction = _make_interaction()
        view.user_select._values = [SimpleNamespace(id=111)]
        await view._on_user_select(interaction)

        await view._on_next(interaction)

        _, kwargs = interaction.response.edit_message.call_args
        field = next(f for f in kwargs["embed"].fields if "邀請對象" in f.name)
        assert "<@111>" in field.value


class TestTimeout:
    async def test_on_timeout_edits_stored_message(self) -> None:
        view = _view()
        fake_message = AsyncMock()
        view.message = fake_message

        await view.on_timeout()

        fake_message.edit.assert_awaited_once()
        _, kwargs = fake_message.edit.call_args
        assert "逾時" in kwargs["content"]
