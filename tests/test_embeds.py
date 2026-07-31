"""活動 Embed 建構測試。只驗證內容組裝正確，不需要連線 Discord。"""

from __future__ import annotations

from src.bot.embeds import build_event_embed, build_event_list_embed
from src.domain.rsvp import RsvpSummary

BASE_EVENT = {
    "id": "evt0000001",
    "title": "週五團練",
    "starts_at_utc": 1754049600000,
    "ends_at_utc": None,
    "location": None,
    "description": None,
    "creator_id": "123456789",
    "status": "scheduled",
}


def _event(**overrides):
    return {**BASE_EVENT, **overrides}


class TestBuildEventEmbed:
    def test_includes_title(self) -> None:
        embed = build_event_embed(_event(title="週五團練"))
        assert "週五團練" in embed.title

    def test_includes_start_and_relative_timestamp(self) -> None:
        embed = build_event_embed(_event())
        time_field = next(f for f in embed.fields if "時間" in f.name)
        assert "<t:1754049600:F>" in time_field.value
        assert "<t:1754049600:R>" in time_field.value

    def test_omits_end_time_when_not_set(self) -> None:
        embed = build_event_embed(_event(ends_at_utc=None))
        time_field = next(f for f in embed.fields if "時間" in f.name)
        assert "結束" not in time_field.value

    def test_includes_end_time_when_set(self) -> None:
        embed = build_event_embed(_event(ends_at_utc=1754056800000))
        time_field = next(f for f in embed.fields if "時間" in f.name)
        assert "結束" in time_field.value
        assert "<t:1754056800:t>" in time_field.value

    def test_omits_location_field_when_not_set(self) -> None:
        embed = build_event_embed(_event(location=None))
        assert not any("地點" in f.name for f in embed.fields)

    def test_includes_location_field_when_set(self) -> None:
        embed = build_event_embed(_event(location="語音頻道"))
        loc_field = next(f for f in embed.fields if "地點" in f.name)
        assert loc_field.value == "語音頻道"

    def test_omits_description_field_when_not_set(self) -> None:
        embed = build_event_embed(_event(description=None))
        assert not any("內容" in f.name for f in embed.fields)

    def test_includes_description_field_when_set(self) -> None:
        embed = build_event_embed(_event(description="打完第三章"))
        desc_field = next(f for f in embed.fields if "內容" in f.name)
        assert desc_field.value == "打完第三章"

    def test_includes_creator_mention(self) -> None:
        embed = build_event_embed(_event(creator_id="123456789"))
        creator_field = next(f for f in embed.fields if "發起人" in f.name)
        assert creator_field.value == "<@123456789>"

    def test_footer_contains_event_id(self) -> None:
        embed = build_event_embed(_event(id="abc123"))
        assert "abc123" in embed.footer.text

    def test_cancelled_event_shows_strikethrough_and_marker(self) -> None:
        embed = build_event_embed(_event(status="cancelled"))
        assert "~~" in embed.title
        assert "已取消" in embed.title

    def test_omits_invitee_field_when_none_given(self) -> None:
        embed = build_event_embed(_event(), invitees=None)
        assert not any("邀請對象" in f.name for f in embed.fields)

    def test_omits_invitee_field_when_empty_list(self) -> None:
        embed = build_event_embed(_event(), invitees=[])
        assert not any("邀請對象" in f.name for f in embed.fields)

    def test_shows_user_and_role_mentions(self) -> None:
        invitees = [
            {"target_type": "user", "target_id": "111"},
            {"target_type": "role", "target_id": "222"},
        ]
        embed = build_event_embed(_event(), invitees=invitees)
        field = next(f for f in embed.fields if "邀請對象" in f.name)
        assert "<@111>" in field.value
        assert "<@&222>" in field.value

    def test_shows_everyone_mention(self) -> None:
        invitees = [{"target_type": "everyone", "target_id": "999"}]
        embed = build_event_embed(_event(), invitees=invitees)
        field = next(f for f in embed.fields if "邀請對象" in f.name)
        assert field.value == "@everyone"

    def test_invitee_field_shows_restrict_hint_when_restricted(self) -> None:
        invitees = [{"target_type": "user", "target_id": "111"}]
        embed = build_event_embed(_event(restrict_rsvp=1), invitees=invitees)
        field = next(f for f in embed.fields if "邀請對象" in f.name)
        assert "僅限以下對象回覆" in field.name

    def test_invitee_field_omits_restrict_hint_when_not_restricted(self) -> None:
        invitees = [{"target_type": "user", "target_id": "111"}]
        embed = build_event_embed(_event(restrict_rsvp=0), invitees=invitees)
        field = next(f for f in embed.fields if "邀請對象" in f.name)
        assert "僅限以下對象回覆" not in field.name

    def test_missing_restrict_rsvp_key_does_not_crash(self) -> None:
        """舊測試資料或尚未跑過 002 migration 的 row 可能沒有這個欄位，
        用 .get() 讀取要有防呆，不能整個 embed 建構失敗。"""
        invitees = [{"target_type": "user", "target_id": "111"}]
        embed = build_event_embed(_event(), invitees=invitees)  # BASE_EVENT 沒有這個 key
        assert any("邀請對象" in f.name for f in embed.fields)


class TestRsvpFields:
    def test_omits_all_rsvp_fields_when_summary_not_given(self) -> None:
        embed = build_event_embed(_event(), rsvp_summary=None)
        assert not any("參加" in f.name for f in embed.fields)

    def test_yes_field_always_shown_even_when_empty(self) -> None:
        """參加是主要欄位，就算 0 人也該顯示，讓使用者知道這個功能存在。"""
        embed = build_event_embed(_event(), rsvp_summary=RsvpSummary())
        field = next(f for f in embed.fields if f.name.startswith("✅ 參加"))
        assert "（0）" in field.name
        assert field.value == "（無）"

    def test_maybe_no_and_no_response_hidden_when_empty(self) -> None:
        embed = build_event_embed(_event(), rsvp_summary=RsvpSummary())
        assert not any("待定" in f.name for f in embed.fields)
        assert not any(f.name.startswith("❌ 不參加") for f in embed.fields)
        assert not any("未回覆" in f.name for f in embed.fields)

    def test_shows_all_four_buckets_when_populated(self) -> None:
        summary = RsvpSummary(yes=[1, 2], maybe=[3], no=[4], no_response=[5, 6, 7])
        embed = build_event_embed(_event(), rsvp_summary=summary)

        yes_field = next(f for f in embed.fields if f.name.startswith("✅ 參加"))
        assert "（2）" in yes_field.name
        assert "<@1>" in yes_field.value and "<@2>" in yes_field.value

        maybe_field = next(f for f in embed.fields if "待定" in f.name)
        assert "<@3>" in maybe_field.value

        no_field = next(f for f in embed.fields if f.name.startswith("❌ 不參加"))
        assert "<@4>" in no_field.value

        no_response_field = next(f for f in embed.fields if "未回覆" in f.name)
        assert "（3）" in no_response_field.name
        assert all(f"<@{uid}>" in no_response_field.value for uid in (5, 6, 7))

    def test_truncates_when_mention_list_exceeds_field_limit(self) -> None:
        """單一 embed 欄位硬上限 1024 字元，超過要截斷並註明剩餘人數，不能讓整個
        API 呼叫因超字數失敗。"""
        many_ids = list(range(1_000_000, 1_000_300))  # 300 人，遠超過塞得下的量
        summary = RsvpSummary(yes=many_ids)
        embed = build_event_embed(_event(), rsvp_summary=summary)

        yes_field = next(f for f in embed.fields if f.name.startswith("✅ 參加"))
        assert len(yes_field.value) <= 1024
        assert "…等" in yes_field.value


class TestBuildEventListEmbed:
    def test_empty_list_shows_placeholder_message(self) -> None:
        embed = build_event_list_embed([], title="測試")
        assert "沒有" in embed.description

    def test_lists_each_event_with_id(self) -> None:
        events = [_event(id="evt1", title="活動一"), _event(id="evt2", title="活動二")]
        embed = build_event_list_embed(events, title="測試")
        assert "活動一" in embed.description
        assert "活動二" in embed.description
        assert "evt1" in embed.description
        assert "evt2" in embed.description

    def test_shows_location_inline_when_present(self) -> None:
        events = [_event(location="語音頻道")]
        embed = build_event_list_embed(events, title="測試")
        assert "語音頻道" in embed.description

    def test_uses_provided_title(self) -> None:
        embed = build_event_list_embed([], title="🙋 我建立的活動")
        assert embed.title == "🙋 我建立的活動"
