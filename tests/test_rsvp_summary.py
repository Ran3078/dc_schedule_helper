"""domain.rsvp.build_rsvp_summary 測試。

重點：「未回覆」只從邀請名單裡扣掉已回覆的人；誰能回覆完全不受邀請名單
限制 —— 沒被明確邀請的人一樣可以按按鈕表態，並且要正確被算進對應分類。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from src.domain.rsvp import build_rsvp_summary


def _guild(*, members: list | None = None) -> MagicMock:
    guild = MagicMock()
    guild.get_role.return_value = None
    guild.members = members or []
    return guild


def _rsvp(user_id: str, status: str) -> dict:
    return {"user_id": user_id, "status": status}


class TestBuckets:
    def test_buckets_by_status(self) -> None:
        rsvps = [_rsvp("111", "yes"), _rsvp("222", "maybe"), _rsvp("333", "no")]
        summary = build_rsvp_summary(_guild(), [], rsvps)
        assert summary.yes == [111]
        assert summary.maybe == [222]
        assert summary.no == [333]

    def test_no_rsvps_gives_empty_buckets(self) -> None:
        summary = build_rsvp_summary(_guild(), [], [])
        assert summary.yes == []
        assert summary.maybe == []
        assert summary.no == []
        assert summary.no_response == []


class TestNoResponse:
    def test_invited_but_not_responded_counts_as_no_response(self) -> None:
        invitees = [{"target_type": "user", "target_id": "111"}]
        summary = build_rsvp_summary(_guild(), invitees, [])
        assert summary.no_response == [111]

    def test_invited_and_responded_is_not_no_response(self) -> None:
        invitees = [{"target_type": "user", "target_id": "111"}]
        rsvps = [_rsvp("111", "yes")]
        summary = build_rsvp_summary(_guild(), invitees, rsvps)
        assert summary.no_response == []
        assert summary.yes == [111]

    def test_no_invitee_list_means_no_no_response_tracking(self) -> None:
        """M2 略過參加對象時，沒有邀請名單可算「未回覆」——不是有 0 人沒回覆，
        是這個概念根本不適用，兩者用同一個空清單表示（見 domain/rsvp.py 的說明）。
        """
        summary = build_rsvp_summary(_guild(), [], [])
        assert summary.no_response == []

    def test_response_from_uninvited_person_does_not_appear_in_no_response(self) -> None:
        """沒被邀請的人回覆了，不該讓「未回覆」清單出現負數或奇怪的結果。"""
        invitees = [{"target_type": "user", "target_id": "111"}]
        rsvps = [_rsvp("999", "yes")]  # 999 不在邀請名單裡
        summary = build_rsvp_summary(_guild(), invitees, rsvps)
        assert summary.no_response == [111]
        assert summary.yes == [999]


class TestUninvitedCanStillRespond:
    def test_uninvited_person_yes_is_counted(self) -> None:
        """誰能回覆不受邀請名單限制：任何看得到公告的人都能按按鈕表態。"""
        summary = build_rsvp_summary(_guild(), [], [_rsvp("555", "yes")])
        assert summary.yes == [555]


class TestRoleExpansionIntegration:
    def test_no_response_expands_role_members(self) -> None:
        members = [SimpleNamespace(id=1, bot=False), SimpleNamespace(id=2, bot=False)]
        role = SimpleNamespace(id=777, members=members)
        guild = MagicMock()
        guild.get_role.side_effect = lambda rid: role if rid == 777 else None
        guild.members = []

        invitees = [{"target_type": "role", "target_id": "777"}]
        summary = build_rsvp_summary(guild, invitees, [_rsvp("1", "yes")])

        assert summary.yes == [1]
        assert summary.no_response == [2]
