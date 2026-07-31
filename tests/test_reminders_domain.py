"""domain.reminders 的純邏輯測試：CSV 解析與逾期判斷規則。"""

from __future__ import annotations

from src.domain.reminders import (
    OVERDUE_GRACE_MS,
    is_overdue,
    parse_default_reminders,
    should_skip_overdue_reminder,
)

NOW = 1_785_000_000_000


class TestParseDefaultReminders:
    def test_parses_comma_separated_minutes(self) -> None:
        assert parse_default_reminders("1440,60,10") == [1440, 60, 10]

    def test_tolerates_internal_whitespace(self) -> None:
        assert parse_default_reminders(" 1440 , 60 , 10 ") == [1440, 60, 10]

    def test_none_falls_back_to_default(self) -> None:
        assert parse_default_reminders(None) == [1440, 60, 10]

    def test_empty_string_falls_back_to_default(self) -> None:
        assert parse_default_reminders("") == [1440, 60, 10]

    def test_whitespace_only_falls_back_to_default(self) -> None:
        assert parse_default_reminders("   ") == [1440, 60, 10]

    def test_skips_non_numeric_tokens(self) -> None:
        assert parse_default_reminders("60,abc,10") == [60, 10]

    def test_skips_zero_and_negative(self) -> None:
        assert parse_default_reminders("60,0,-10,30") == [60, 30]

    def test_skips_empty_tokens_from_trailing_comma(self) -> None:
        assert parse_default_reminders("60,10,") == [60, 10]

    def test_single_value(self) -> None:
        assert parse_default_reminders("30") == [30]

    def test_all_invalid_tokens_yields_empty_list(self) -> None:
        """跟「沒傳值」不同：使用者刻意把設定清成全部無效值，視為「不要提醒」。"""
        assert parse_default_reminders("0,-5,abc") == []


class TestIsOverdue:
    def test_within_grace_window_is_not_overdue(self) -> None:
        assert is_overdue(fire_at_utc=NOW - 1000, now_ms=NOW) is False

    def test_exactly_at_grace_boundary_is_not_overdue(self) -> None:
        assert is_overdue(fire_at_utc=NOW - OVERDUE_GRACE_MS, now_ms=NOW) is False

    def test_past_grace_window_is_overdue(self) -> None:
        assert is_overdue(fire_at_utc=NOW - OVERDUE_GRACE_MS - 1, now_ms=NOW) is True

    def test_future_fire_time_is_not_overdue(self) -> None:
        assert is_overdue(fire_at_utc=NOW + 60_000, now_ms=NOW) is False


class TestShouldSkipOverdueReminder:
    def test_not_overdue_is_never_skipped(self) -> None:
        assert (
            should_skip_overdue_reminder(
                fire_at_utc=NOW - 1000, starts_at_utc=NOW - 1000, now_ms=NOW
            )
            is False
        )

    def test_overdue_but_event_not_started_is_not_skipped(self) -> None:
        """活動還沒開始，就算提醒本身逾期了，也該補發一次「即將開始」。"""
        assert (
            should_skip_overdue_reminder(
                fire_at_utc=NOW - OVERDUE_GRACE_MS - 1,
                starts_at_utc=NOW + 3_600_000,
                now_ms=NOW,
            )
            is False
        )

    def test_overdue_and_event_already_started_is_skipped(self) -> None:
        """避免半夜補發一串早就開始（甚至結束）的活動提醒。"""
        assert (
            should_skip_overdue_reminder(
                fire_at_utc=NOW - OVERDUE_GRACE_MS - 1,
                starts_at_utc=NOW - 1000,
                now_ms=NOW,
            )
            is True
        )

    def test_overdue_and_event_starting_exactly_now_is_skipped(self) -> None:
        assert (
            should_skip_overdue_reminder(
                fire_at_utc=NOW - OVERDUE_GRACE_MS - 1, starts_at_utc=NOW, now_ms=NOW
            )
            is True
        )
