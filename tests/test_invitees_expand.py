"""domain.invitees.expand_invited_members 測試。

角色展開需要 discord.Guild 的成員快取，這裡用假物件模擬 —— 只要有
`get_role(id)` 與 `members` 屬性，不需要真的連 Discord。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from src.domain.invitees import expand_invited_members


def _member(user_id: int, *, bot: bool = False) -> SimpleNamespace:
    return SimpleNamespace(id=user_id, bot=bot)


def _role(role_id: int, members: list[SimpleNamespace]) -> SimpleNamespace:
    return SimpleNamespace(id=role_id, members=members)


def _guild(*, roles: dict[int, SimpleNamespace] | None = None, members: list | None = None):
    guild = MagicMock()
    roles = roles or {}
    guild.get_role.side_effect = lambda rid: roles.get(rid)
    guild.members = members or []
    return guild


class TestUserInvitees:
    def test_user_target_adds_the_id(self) -> None:
        guild = _guild()
        pool = expand_invited_members(guild, [{"target_type": "user", "target_id": "111"}])
        assert pool == {111}

    def test_multiple_users(self) -> None:
        guild = _guild()
        rows = [
            {"target_type": "user", "target_id": "111"},
            {"target_type": "user", "target_id": "222"},
        ]
        assert expand_invited_members(guild, rows) == {111, 222}


class TestRoleInvitees:
    def test_role_expands_to_its_members(self) -> None:
        role = _role(555, [_member(111), _member(222)])
        guild = _guild(roles={555: role})
        pool = expand_invited_members(guild, [{"target_type": "role", "target_id": "555"}])
        assert pool == {111, 222}

    def test_role_excludes_bots(self) -> None:
        role = _role(555, [_member(111), _member(999, bot=True)])
        guild = _guild(roles={555: role})
        pool = expand_invited_members(guild, [{"target_type": "role", "target_id": "555"}])
        assert pool == {111}

    def test_unknown_role_id_is_silently_skipped(self) -> None:
        """角色可能在建立活動後被刪除 —— get_role 回 None 不該讓整個展開炸掉。"""
        guild = _guild(roles={})
        pool = expand_invited_members(guild, [{"target_type": "role", "target_id": "999"}])
        assert pool == set()


class TestEveryoneInvitee:
    def test_everyone_expands_to_all_guild_members(self) -> None:
        guild = _guild(members=[_member(111), _member(222)])
        pool = expand_invited_members(guild, [{"target_type": "everyone", "target_id": "g1"}])
        assert pool == {111, 222}

    def test_everyone_excludes_bots(self) -> None:
        guild = _guild(members=[_member(111), _member(999, bot=True)])
        pool = expand_invited_members(guild, [{"target_type": "everyone", "target_id": "g1"}])
        assert pool == {111}


class TestCombined:
    def test_user_role_and_overlap_deduplicates(self) -> None:
        """同一個人同時被明確標記又落在某個角色底下，只該算一次（set 天生去重）。"""
        role = _role(555, [_member(111), _member(222)])
        guild = _guild(roles={555: role})
        rows = [
            {"target_type": "user", "target_id": "111"},
            {"target_type": "role", "target_id": "555"},
        ]
        assert expand_invited_members(guild, rows) == {111, 222}

    def test_empty_invitees_produces_empty_pool(self) -> None:
        assert expand_invited_members(_guild(), []) == set()
