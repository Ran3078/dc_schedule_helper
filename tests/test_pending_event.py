"""PendingEvent 的計算欄位測試。

`ends_at_utc` 是 property 而非儲存欄位 —— 因為在走月曆挑選器那條路徑時，
`starts_at_utc` 在使用者選定時間之前是 None，這時候完全無從算出結束時間。
"""

from __future__ import annotations

from src.bot.modals import PendingEvent

BASE_KWARGS = dict(
    guild_id=1,
    channel_id=2,
    creator_id=3,
    title="測試活動",
    tz="Asia/Taipei",
    location=None,
)


class TestEndsAtUtc:
    def test_none_when_starts_at_utc_not_set(self) -> None:
        pending = PendingEvent(**BASE_KWARGS, duration_minutes=60, starts_at_utc=None)
        assert pending.ends_at_utc is None

    def test_none_when_duration_not_set(self) -> None:
        pending = PendingEvent(**BASE_KWARGS, duration_minutes=None, starts_at_utc=1_000_000)
        assert pending.ends_at_utc is None

    def test_computed_when_both_present(self) -> None:
        pending = PendingEvent(**BASE_KWARGS, duration_minutes=90, starts_at_utc=1_000_000)
        assert pending.ends_at_utc == 1_000_000 + 90 * 60_000


class TestWithStart:
    def test_sets_starts_at_utc(self) -> None:
        pending = PendingEvent(**BASE_KWARGS, duration_minutes=None, starts_at_utc=None)
        updated = pending.with_start(5_000_000)
        assert updated.starts_at_utc == 5_000_000

    def test_original_instance_is_untouched(self) -> None:
        """frozen dataclass：with_start 必須回傳新物件，不能動到原本的 pending。"""
        pending = PendingEvent(**BASE_KWARGS, duration_minutes=None, starts_at_utc=None)
        pending.with_start(5_000_000)
        assert pending.starts_at_utc is None

    def test_other_fields_are_preserved(self) -> None:
        pending = PendingEvent(**BASE_KWARGS, duration_minutes=30, starts_at_utc=None)
        updated = pending.with_start(5_000_000)
        assert updated.title == pending.title
        assert updated.duration_minutes == 30
        assert updated.ends_at_utc == 5_000_000 + 30 * 60_000
