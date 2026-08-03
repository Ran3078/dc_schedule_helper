"""domain/polls.py 純邏輯測試：選項字串解析、票數統計。"""

from __future__ import annotations

from src.domain.polls import (
    MAX_OPTIONS,
    MIN_OPTIONS,
    all_voter_ids,
    build_tally,
    pick_winning_time_slot,
    split_options,
)


class TestSplitOptions:
    def test_splits_on_pipe(self) -> None:
        assert split_options("A|B|C") == ["A", "B", "C"]

    def test_strips_whitespace(self) -> None:
        assert split_options(" A | B |C ") == ["A", "B", "C"]

    def test_drops_empty_segments(self) -> None:
        assert split_options("A||B|") == ["A", "B"]

    def test_splits_on_newline(self) -> None:
        """Modal 的多行文字框主要走這條——一行一個選項。"""
        assert split_options("火鍋\n燒肉\n拉麵") == ["火鍋", "燒肉", "拉麵"]

    def test_drops_blank_lines(self) -> None:
        assert split_options("A\n\nB\n\n") == ["A", "B"]

    def test_mixed_pipe_and_newline_separators(self) -> None:
        assert split_options("A|B\nC") == ["A", "B", "C"]

    def test_empty_string_yields_empty_list(self) -> None:
        assert split_options("") == []

    def test_single_option_without_pipe(self) -> None:
        assert split_options("只有一個") == ["只有一個"]


class TestConstants:
    def test_min_and_max_reflect_discord_select_limits(self) -> None:
        assert MIN_OPTIONS == 2
        assert MAX_OPTIONS == 25


class TestBuildTally:
    def test_options_with_no_votes_get_empty_list(self) -> None:
        options = [{"id": "o1"}, {"id": "o2"}]
        assert build_tally(options, []) == {"o1": [], "o2": []}

    def test_groups_votes_by_option(self) -> None:
        options = [{"id": "o1"}, {"id": "o2"}]
        votes = [
            {"option_id": "o1", "user_id": "111"},
            {"option_id": "o1", "user_id": "222"},
            {"option_id": "o2", "user_id": "333"},
        ]
        tally = build_tally(options, votes)
        assert tally["o1"] == [111, 222]
        assert tally["o2"] == [333]

    def test_multi_select_one_user_can_appear_in_several_options(self) -> None:
        options = [{"id": "o1"}, {"id": "o2"}]
        votes = [
            {"option_id": "o1", "user_id": "111"},
            {"option_id": "o2", "user_id": "111"},
        ]
        tally = build_tally(options, votes)
        assert tally["o1"] == [111]
        assert tally["o2"] == [111]

    def test_ignores_votes_for_unknown_option(self) -> None:
        options = [{"id": "o1"}]
        votes = [{"option_id": "ghost", "user_id": "111"}]
        assert build_tally(options, votes) == {"o1": []}


class TestAllVoterIds:
    def test_dedups_across_options(self) -> None:
        votes = [
            {"option_id": "o1", "user_id": "111"},
            {"option_id": "o2", "user_id": "111"},
            {"option_id": "o1", "user_id": "222"},
        ]
        assert all_voter_ids(votes) == [111, 222]

    def test_no_votes_yields_empty_list(self) -> None:
        assert all_voter_ids([]) == []

    def test_sorted_ascending(self) -> None:
        votes = [{"option_id": "o1", "user_id": str(uid)} for uid in (300, 100, 200)]
        assert all_voter_ids(votes) == [100, 200, 300]


class TestPickWinningTimeSlot:
    def test_unique_highest_vote_wins(self) -> None:
        options = [{"id": "o1", "label": "A"}, {"id": "o2", "label": "B"}]
        tally = {"o1": [111], "o2": [111, 222]}

        winner, reason, top = pick_winning_time_slot(options, tally)

        assert reason == "ok"
        assert winner == options[1]
        assert top == [options[1]]

    def test_no_votes_at_all(self) -> None:
        options = [{"id": "o1", "label": "A"}, {"id": "o2", "label": "B"}]
        tally = {"o1": [], "o2": []}

        winner, reason, top = pick_winning_time_slot(options, tally)

        assert winner is None
        assert reason == "no_votes"
        assert top == []

    def test_no_options_counts_as_no_votes(self) -> None:
        winner, reason, top = pick_winning_time_slot([], {})
        assert winner is None
        assert reason == "no_votes"
        assert top == []

    def test_tie_returns_all_tied_options(self) -> None:
        options = [
            {"id": "o1", "label": "A"},
            {"id": "o2", "label": "B"},
            {"id": "o3", "label": "C"},
        ]
        tally = {"o1": [111], "o2": [222], "o3": []}

        winner, reason, top = pick_winning_time_slot(options, tally)

        assert winner is None
        assert reason == "tie"
        assert top == [options[0], options[1]]

    def test_missing_option_in_tally_counts_as_zero_votes(self) -> None:
        options = [{"id": "o1", "label": "A"}, {"id": "o2", "label": "B"}]
        tally = {"o1": [111]}  # o2 沒有 key，等同 0 票

        winner, reason, _ = pick_winning_time_slot(options, tally)

        assert reason == "ok"
        assert winner == options[0]
