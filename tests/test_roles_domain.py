"""domain/roles.py 純邏輯測試：FF14 位置代碼／職業對照表、選擇排序、
確定/候補人數統計。"""

from __future__ import annotations

from src.domain.roles import (
    JOBS_BY_POSITION,
    POSITIONS,
    compute_position_counts,
    sort_positions,
)


class TestSortPositions:
    def test_reorders_to_canonical_position_order(self) -> None:
        """使用者複選的順序（Discord 不保證跟 POSITIONS 一致）要歸一成
        POSITIONS 原本的順序，公告卡片欄位順序才會穩定。"""
        assert sort_positions(["D1", "MT"]) == ["MT", "D1"]

    def test_deduplicates(self) -> None:
        assert sort_positions(["MT", "MT", "ST"]) == ["MT", "ST"]

    def test_empty_list_returns_empty_list(self) -> None:
        assert sort_positions([]) == []

    def test_all_eight_positions(self) -> None:
        assert sort_positions(reversed(POSITIONS)) == list(POSITIONS)


class TestJobsByPosition:
    def test_all_eight_positions_have_entries(self) -> None:
        assert set(JOBS_BY_POSITION.keys()) == set(POSITIONS)

    def test_every_entry_is_a_nonempty_tuple(self) -> None:
        for jobs in JOBS_BY_POSITION.values():
            assert isinstance(jobs, tuple)
            assert len(jobs) > 0

    def test_no_duplicate_jobs_within_a_position(self) -> None:
        for jobs in JOBS_BY_POSITION.values():
            assert len(jobs) == len(set(jobs))

    def test_d2_allows_everything_d1_allows_plus_ranged_magical(self) -> None:
        """D2 比 D1 多開放遠程魔法職業，是這個伺服器慣用的分工彈性設計。"""
        d1, d2 = set(JOBS_BY_POSITION["D1"]), set(JOBS_BY_POSITION["D2"])
        assert d1 < d2
        assert d2 - d1 == set(JOBS_BY_POSITION["D4"])

    def test_mt_and_st_share_the_same_tank_pool(self) -> None:
        assert JOBS_BY_POSITION["MT"] == JOBS_BY_POSITION["ST"]


class TestComputePositionCounts:
    def test_counts_confirmed_and_waitlisted_separately(self) -> None:
        signups = [
            {"role_slot_id": "s1", "waitlisted": 0},
            {"role_slot_id": "s1", "waitlisted": 1},
            {"role_slot_id": "s1", "waitlisted": 1},
        ]
        assert compute_position_counts(signups) == {"s1": (1, 2)}

    def test_separates_by_slot(self) -> None:
        signups = [
            {"role_slot_id": "s1", "waitlisted": 0},
            {"role_slot_id": "s2", "waitlisted": 0},
        ]
        assert compute_position_counts(signups) == {"s1": (1, 0), "s2": (1, 0)}

    def test_empty_list_returns_empty_dict(self) -> None:
        assert compute_position_counts([]) == {}
