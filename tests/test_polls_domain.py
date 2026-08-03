"""domain/polls.py 純邏輯測試：選項字串解析、票數統計。"""

from __future__ import annotations

from src.domain.polls import MAX_OPTIONS, MIN_OPTIONS, build_tally, split_options


class TestSplitOptions:
    def test_splits_on_pipe(self) -> None:
        assert split_options("A|B|C") == ["A", "B", "C"]

    def test_strips_whitespace(self) -> None:
        assert split_options(" A | B |C ") == ["A", "B", "C"]

    def test_drops_empty_segments(self) -> None:
        assert split_options("A||B|") == ["A", "B"]

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
