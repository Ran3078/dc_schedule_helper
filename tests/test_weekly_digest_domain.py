"""domain/weekly_digest.py 純邏輯測試：算「最近一次週日 00:00」。"""

from __future__ import annotations

from datetime import datetime

from src.domain.weekly_digest import most_recent_sunday_midnight


class TestMostRecentSundayMidnight:
    def test_sunday_returns_today_at_midnight(self) -> None:
        # 2026-08-16 是週日
        now = datetime(2026, 8, 16, 13, 45, 30)
        assert most_recent_sunday_midnight(now) == datetime(2026, 8, 16, 0, 0, 0)

    def test_sunday_exactly_at_midnight_returns_itself(self) -> None:
        now = datetime(2026, 8, 16, 0, 0, 0)
        assert most_recent_sunday_midnight(now) == datetime(2026, 8, 16, 0, 0, 0)

    def test_monday_returns_previous_sunday(self) -> None:
        now = datetime(2026, 8, 17, 9, 0, 0)  # 週一
        assert most_recent_sunday_midnight(now) == datetime(2026, 8, 16, 0, 0, 0)

    def test_saturday_returns_previous_sunday(self) -> None:
        now = datetime(2026, 8, 22, 23, 59, 59)  # 週六
        assert most_recent_sunday_midnight(now) == datetime(2026, 8, 16, 0, 0, 0)

    def test_wednesday_returns_previous_sunday(self) -> None:
        now = datetime(2026, 8, 19, 12, 0, 0)  # 週三
        assert most_recent_sunday_midnight(now) == datetime(2026, 8, 16, 0, 0, 0)

    def test_crosses_month_boundary(self) -> None:
        now = datetime(2026, 9, 1, 10, 0, 0)  # 2026-09-01 是週二
        assert most_recent_sunday_midnight(now) == datetime(2026, 8, 30, 0, 0, 0)

    def test_crosses_year_boundary(self) -> None:
        now = datetime(2027, 1, 1, 10, 0, 0)  # 2027-01-01 是週五
        assert most_recent_sunday_midnight(now) == datetime(2026, 12, 27, 0, 0, 0)

    def test_strips_time_of_day(self) -> None:
        now = datetime(2026, 8, 16, 23, 59, 59, 999999)
        result = most_recent_sunday_midnight(now)
        assert result.hour == 0
        assert result.minute == 0
        assert result.second == 0
        assert result.microsecond == 0
