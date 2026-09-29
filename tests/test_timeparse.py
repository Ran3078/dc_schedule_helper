"""時間解析測試。

這裡刻意固定 `now` 而不是用系統當下時間 —— 省略年份時的「捲到明年」邏輯
跟現在是幾月幾號高度相關，用固定時間點測試才能穩定重現年份邊界情況。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from src.lib.timeparse import (
    TimeParseError,
    discord_timestamp,
    parse_date,
    parse_datetime,
    parse_day_count,
    parse_duration_minutes,
    parse_target_date,
)

TPE = "Asia/Taipei"
# 固定「現在」：2026-07-30 12:00 台北時間
NOW = datetime(2026, 7, 30, 12, 0, tzinfo=ZoneInfo(TPE))


class TestFullDateFormats:
    def test_dash_format(self) -> None:
        ms = parse_datetime("2026-08-01 20:00", TPE, now=NOW)
        expected = datetime(2026, 8, 1, 20, 0, tzinfo=ZoneInfo(TPE))
        assert ms == int(expected.timestamp() * 1000)

    def test_slash_format(self) -> None:
        ms = parse_datetime("2026/08/01 20:00", TPE, now=NOW)
        expected = datetime(2026, 8, 1, 20, 0, tzinfo=ZoneInfo(TPE))
        assert ms == int(expected.timestamp() * 1000)

    def test_result_is_utc_epoch_not_local_seconds(self) -> None:
        """存的必須是真正的 UTC epoch，跨時區比對才不會出錯。"""
        ms = parse_datetime("2026-08-01 20:00", TPE, now=NOW)
        # 台北是 UTC+8，20:00 台北時間 = 12:00 UTC
        utc_dt = datetime(2026, 8, 1, 12, 0, tzinfo=ZoneInfo("UTC"))
        assert ms == int(utc_dt.timestamp() * 1000)


class TestMonthDayFormats:
    def test_slash_format_future_date_stays_this_year(self) -> None:
        """現在是 7/30，8/1 還沒到，應該解讀成今年。"""
        ms = parse_datetime("8/1 20:00", TPE, now=NOW)
        expected = datetime(2026, 8, 1, 20, 0, tzinfo=ZoneInfo(TPE))
        assert ms == int(expected.timestamp() * 1000)

    def test_dash_format_future_date_stays_this_year(self) -> None:
        ms = parse_datetime("8-1 20:00", TPE, now=NOW)
        expected = datetime(2026, 8, 1, 20, 0, tzinfo=ZoneInfo(TPE))
        assert ms == int(expected.timestamp() * 1000)

    def test_past_date_rolls_to_next_year(self) -> None:
        """現在是 7/30，1/1 已經過了，省略年份時應該理解成「明年的 1/1」。"""
        ms = parse_datetime("1/1 09:00", TPE, now=NOW)
        expected = datetime(2027, 1, 1, 9, 0, tzinfo=ZoneInfo(TPE))
        assert ms == int(expected.timestamp() * 1000)

    def test_near_now_within_buffer_is_not_rolled(self) -> None:
        """5 分鐘緩衝：現在正好是這個時間點附近，不該被誤判成「已過去」而跳到明年。"""
        ms = parse_datetime("7/30 12:00", TPE, now=NOW)
        expected = datetime(2026, 7, 30, 12, 0, tzinfo=ZoneInfo(TPE))
        assert ms == int(expected.timestamp() * 1000)

    def test_accepts_single_digit_month_and_day(self) -> None:
        ms = parse_datetime("8/1 20:00", TPE, now=NOW)
        ms_padded = parse_datetime("08/01 20:00", TPE, now=NOW)
        assert ms == ms_padded


class TestInvalidInput:
    def test_rejects_empty_string(self) -> None:
        with pytest.raises(TimeParseError):
            parse_datetime("   ", TPE, now=NOW)

    def test_rejects_garbage(self) -> None:
        with pytest.raises(TimeParseError, match="無法解析"):
            parse_datetime("下週五晚上八點", TPE, now=NOW)

    def test_rejects_impossible_date(self) -> None:
        """2 月 30 日不存在，strptime 會自己驗證曆法合法性。"""
        with pytest.raises(TimeParseError):
            parse_datetime("2026-02-30 20:00", TPE, now=NOW)

    def test_rejects_impossible_hour(self) -> None:
        with pytest.raises(TimeParseError):
            parse_datetime("2026-08-01 25:00", TPE, now=NOW)

    def test_error_message_lists_supported_formats(self) -> None:
        with pytest.raises(TimeParseError, match="2026-08-01 20:00"):
            parse_datetime("garbage", TPE, now=NOW)

    def test_invalid_guild_timezone_gives_actionable_message(self) -> None:
        """理論上不該發生（config.py 已驗證過），但資料若被外部改壞，訊息要指向資料。"""
        with pytest.raises(TimeParseError, match="/settings"):
            parse_datetime("2026-08-01 20:00", "Mars/Olympus_Mons", now=NOW)


class TestDurationParsing:
    def test_hours_only(self) -> None:
        assert parse_duration_minutes("2h") == 120

    def test_minutes_only(self) -> None:
        assert parse_duration_minutes("90m") == 90

    def test_hours_and_minutes(self) -> None:
        assert parse_duration_minutes("1h30m") == 90

    def test_case_insensitive(self) -> None:
        assert parse_duration_minutes("2H") == 120

    def test_half_day_and_full_day(self) -> None:
        """挑選器的「半天」「整天」選項沿用同一套解析，值要對得上（720/1440 分）。"""
        assert parse_duration_minutes("12h") == 720
        assert parse_duration_minutes("24h") == 1440

    def test_tolerates_internal_whitespace(self) -> None:
        assert parse_duration_minutes("1h 30m") == 90

    def test_rejects_empty_string(self) -> None:
        with pytest.raises(TimeParseError):
            parse_duration_minutes("")

    def test_rejects_garbage(self) -> None:
        with pytest.raises(TimeParseError, match="1h30m"):
            parse_duration_minutes("兩小時")


class TestDateParsing:
    """`countdown` 功能用：只有日期、沒有時分。跟 `parse_datetime` 的省略年份
    邏輯不同——這裡「今天」永遠算「還沒過去」，不用 5 分鐘緩衝（見
    parse_date 的說明）。
    """

    def test_dash_format(self) -> None:
        ms = parse_date("2027-06-15", TPE, now=NOW)
        expected = datetime(2027, 6, 15, tzinfo=ZoneInfo(TPE))
        assert ms == int(expected.timestamp() * 1000)

    def test_slash_format(self) -> None:
        ms = parse_date("2027/06/15", TPE, now=NOW)
        expected = datetime(2027, 6, 15, tzinfo=ZoneInfo(TPE))
        assert ms == int(expected.timestamp() * 1000)

    def test_month_day_future_date_stays_this_year(self) -> None:
        ms = parse_date("8/1", TPE, now=NOW)
        expected = datetime(2026, 8, 1, tzinfo=ZoneInfo(TPE))
        assert ms == int(expected.timestamp() * 1000)

    def test_month_day_past_date_rolls_to_next_year(self) -> None:
        """現在是 7/30，1/1 已經過了，捲到明年。"""
        ms = parse_date("1/1", TPE, now=NOW)
        expected = datetime(2027, 1, 1, tzinfo=ZoneInfo(TPE))
        assert ms == int(expected.timestamp() * 1000)

    def test_today_is_not_rolled_to_next_year(self) -> None:
        """今天這個日期本身永遠算「還沒過去」——跟 parse_datetime 用 5 分鐘
        緩衝不同，這裡直接比日期，今天絕對不該被捲到明年。"""
        ms = parse_date("7/30", TPE, now=NOW)
        expected = datetime(2026, 7, 30, tzinfo=ZoneInfo(TPE))
        assert ms == int(expected.timestamp() * 1000)

    def test_dash_month_day_format(self) -> None:
        ms = parse_date("8-1", TPE, now=NOW)
        expected = datetime(2026, 8, 1, tzinfo=ZoneInfo(TPE))
        assert ms == int(expected.timestamp() * 1000)

    def test_rejects_empty_string(self) -> None:
        with pytest.raises(TimeParseError):
            parse_date("   ", TPE, now=NOW)

    def test_rejects_garbage(self) -> None:
        with pytest.raises(TimeParseError, match="無法解析"):
            parse_date("下個月", TPE, now=NOW)

    def test_rejects_impossible_date(self) -> None:
        with pytest.raises(TimeParseError):
            parse_date("2027-02-30", TPE, now=NOW)


class TestTargetDateParsing:
    """`countdown` 功能用：目標日期可以打日期字串（見 TestDateParsing），
    也可以直接打「還剩幾天」的純數字——使用者懶得算確切日期時的捷徑。"""

    def test_pure_digits_means_days_from_now(self) -> None:
        ms = parse_target_date("5", TPE, now=NOW)
        expected = datetime(2026, 8, 4, tzinfo=ZoneInfo(TPE))  # 7/30 + 5 天
        assert ms == int(expected.timestamp() * 1000)

    def test_zero_days_means_today(self) -> None:
        ms = parse_target_date("0", TPE, now=NOW)
        expected = datetime(2026, 7, 30, tzinfo=ZoneInfo(TPE))
        assert ms == int(expected.timestamp() * 1000)

    def test_large_day_count(self) -> None:
        """實際使用情境：懶得算確切退伍日期，直接打還剩幾天。"""
        ms = parse_target_date("500", TPE, now=NOW)
        expected = datetime(2026, 7, 30, tzinfo=ZoneInfo(TPE)) + timedelta(days=500)
        assert ms == int(expected.timestamp() * 1000)

    def test_falls_back_to_date_parsing_for_non_digit_input(self) -> None:
        ms = parse_target_date("2027-06-15", TPE, now=NOW)
        expected = datetime(2027, 6, 15, tzinfo=ZoneInfo(TPE))
        assert ms == int(expected.timestamp() * 1000)

    def test_month_day_shortcut_still_works(self) -> None:
        ms = parse_target_date("8/1", TPE, now=NOW)
        expected = datetime(2026, 8, 1, tzinfo=ZoneInfo(TPE))
        assert ms == int(expected.timestamp() * 1000)

    def test_rejects_empty_string(self) -> None:
        with pytest.raises(TimeParseError):
            parse_target_date("   ", TPE, now=NOW)

    def test_rejects_garbage(self) -> None:
        with pytest.raises(TimeParseError, match="無法解析"):
            parse_target_date("下個月", TPE, now=NOW)

    def test_negative_number_is_not_treated_as_days(self) -> None:
        """負數不符合純數字 regex，會落到日期解析那條路，因為不是合法日期而報錯。"""
        with pytest.raises(TimeParseError):
            parse_target_date("-5", TPE, now=NOW)


class TestDayCountParsing:
    """`countdown` 正數模式用：「目前已經第幾天」的純數字輸入。"""

    def test_parses_positive_integer(self) -> None:
        assert parse_day_count("12") == 12

    def test_parses_large_number(self) -> None:
        assert parse_day_count("500") == 500

    def test_tolerates_surrounding_whitespace(self) -> None:
        assert parse_day_count("  12  ") == 12

    def test_rejects_zero(self) -> None:
        with pytest.raises(TimeParseError, match="大於 0"):
            parse_day_count("0")

    def test_rejects_negative(self) -> None:
        with pytest.raises(TimeParseError):
            parse_day_count("-5")

    def test_rejects_empty_string(self) -> None:
        with pytest.raises(TimeParseError):
            parse_day_count("   ")

    def test_rejects_non_digit(self) -> None:
        with pytest.raises(TimeParseError, match="正整數"):
            parse_day_count("十二")

    def test_rejects_decimal(self) -> None:
        with pytest.raises(TimeParseError):
            parse_day_count("12.5")


class TestDiscordTimestamp:
    def test_default_style_is_full(self) -> None:
        assert discord_timestamp(1754049600000) == "<t:1754049600:F>"

    def test_custom_style(self) -> None:
        assert discord_timestamp(1754049600000, "R") == "<t:1754049600:R>"

    def test_truncates_milliseconds_to_seconds(self) -> None:
        """Discord 時間戳語法是秒，不是毫秒 —— 忘記整除是很容易犯的錯。"""
        assert discord_timestamp(1754049600999, "F") == "<t:1754049600:F>"
