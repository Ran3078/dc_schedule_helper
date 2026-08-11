"""`cogs/_shared.is_organizer` 測試：`guild_settings.organizer_role_id` 這個
一直沒人用的欄位，M7 起真的接上權限管制。"""

from __future__ import annotations

from types import SimpleNamespace

from src.bot.cogs._shared import is_organizer


def _member(*role_ids: int) -> SimpleNamespace:
    return SimpleNamespace(roles=[SimpleNamespace(id=rid) for rid in role_ids])


class TestIsOrganizer:
    def test_no_settings_row_allows_everyone(self) -> None:
        assert is_organizer(_member(), None) is True

    def test_unset_role_id_allows_everyone(self) -> None:
        assert is_organizer(_member(), {"organizer_role_id": None}) is True

    def test_member_with_the_role_is_allowed(self) -> None:
        member = _member(111, 222)
        assert is_organizer(member, {"organizer_role_id": "222"}) is True

    def test_member_without_the_role_is_denied(self) -> None:
        member = _member(111)
        assert is_organizer(member, {"organizer_role_id": "222"}) is False

    def test_member_with_no_roles_is_denied_when_role_required(self) -> None:
        assert is_organizer(_member(), {"organizer_role_id": "222"}) is False
