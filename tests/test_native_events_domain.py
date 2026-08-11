"""domain/native_events.py 純邏輯測試：原生活動結束時間/地點的預設值。"""

from __future__ import annotations

from src.domain.native_events import (
    DEFAULT_DURATION_MINUTES,
    DEFAULT_LOCATION_TEXT,
    resolve_native_end_time,
    resolve_native_location,
)


class TestResolveNativeEndTime:
    def test_uses_given_end_time_when_present(self) -> None:
        assert resolve_native_end_time(1_000_000, 2_000_000) == 2_000_000

    def test_defaults_to_two_hours_after_start_when_missing(self) -> None:
        starts_at = 1_000_000
        expected = starts_at + DEFAULT_DURATION_MINUTES * 60_000
        assert resolve_native_end_time(starts_at, None) == expected


class TestResolveNativeLocation:
    def test_uses_given_location_when_present(self) -> None:
        assert resolve_native_location("語音頻道 #團練室") == "語音頻道 #團練室"

    def test_defaults_when_missing(self) -> None:
        assert resolve_native_location(None) == DEFAULT_LOCATION_TEXT

    def test_defaults_when_empty_string(self) -> None:
        assert resolve_native_location("") == DEFAULT_LOCATION_TEXT
