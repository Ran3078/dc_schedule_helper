"""`domain.countdown` 純邏輯測試——距離目標日期還剩幾天、今天該不該發。"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from src.domain.countdown import days_remaining, should_send_today

TPE = ZoneInfo("Asia/Taipei")


class TestDaysRemaining:
    def test_future_date(self) -> None:
        target = datetime(2027, 6, 15, tzinfo=TPE)
        now_local = datetime(2027, 6, 10, 8, 0, tzinfo=TPE)
        assert days_remaining(int(target.timestamp() * 1000), now_local) == 5

    def test_target_day_itself_is_zero(self) -> None:
        target = datetime(2027, 6, 15, tzinfo=TPE)
        now_local = datetime(2027, 6, 15, 23, 0, tzinfo=TPE)  # 當天任何時刻都算 0
        assert days_remaining(int(target.timestamp() * 1000), now_local) == 0

    def test_past_target_is_negative(self) -> None:
        target = datetime(2027, 6, 15, tzinfo=TPE)
        now_local = datetime(2027, 6, 18, 8, 0, tzinfo=TPE)
        assert days_remaining(int(target.timestamp() * 1000), now_local) == -3

    def test_ignores_time_of_day(self) -> None:
        """只比較日期，不看時分秒——目標日期存的是當天 00:00，現在時刻
        不管是幾點都不該影響「還剩幾天」的計算。"""
        target = datetime(2027, 6, 15, tzinfo=TPE)
        morning = datetime(2027, 6, 10, 0, 1, tzinfo=TPE)
        night = datetime(2027, 6, 10, 23, 59, tzinfo=TPE)
        assert days_remaining(int(target.timestamp() * 1000), morning) == days_remaining(
            int(target.timestamp() * 1000), night
        )


class TestShouldSendToday:
    def test_before_send_time_is_false(self) -> None:
        now_local = datetime(2027, 6, 10, 8, 59, tzinfo=TPE)
        assert should_send_today(9, 0, None, now_local) is False

    def test_at_send_time_with_no_prior_send_is_true(self) -> None:
        now_local = datetime(2027, 6, 10, 9, 0, tzinfo=TPE)
        assert should_send_today(9, 0, None, now_local) is True

    def test_after_send_time_with_no_prior_send_is_true(self) -> None:
        now_local = datetime(2027, 6, 10, 14, 30, tzinfo=TPE)
        assert should_send_today(9, 0, None, now_local) is True

    def test_already_sent_today_is_false(self) -> None:
        now_local = datetime(2027, 6, 10, 9, 5, tzinfo=TPE)
        last_sent = datetime(2027, 6, 10, 9, 0, tzinfo=TPE)
        assert should_send_today(9, 0, int(last_sent.timestamp() * 1000), now_local) is False

    def test_sent_yesterday_is_true_again_today(self) -> None:
        now_local = datetime(2027, 6, 10, 9, 5, tzinfo=TPE)
        last_sent = datetime(2027, 6, 9, 9, 0, tzinfo=TPE)
        assert should_send_today(9, 0, int(last_sent.timestamp() * 1000), now_local) is True
