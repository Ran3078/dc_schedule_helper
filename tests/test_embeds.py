"""活動 Embed 建構測試。只驗證內容組裝正確，不需要連線 Discord。"""

from __future__ import annotations

from typing import ClassVar
from unittest.mock import MagicMock

from src.bot.embeds import (
    build_event_embed,
    build_event_list_embed,
    build_poll_embed,
    build_reminder_embed,
)
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


class TestPositionFields:
    """M8：職位報名欄位。"""

    def _slot(self, **overrides):
        return {"id": "slot1", "event_id": "evt0000001", "position": "D1", "sort": 0, **overrides}

    def test_no_fields_when_role_slots_not_given(self) -> None:
        """既有活動（沒設定位置）不受這個功能影響——這是最重要的回歸測試。"""
        embed = build_event_embed(_event())
        assert not any("MT" in f.name or "D1" in f.name for f in embed.fields)

    def test_no_fields_when_role_slots_empty(self) -> None:
        embed = build_event_embed(_event(), role_slots=[], role_signups=[])
        assert not any("D1" in f.name for f in embed.fields)

    def test_shows_position_with_no_signups(self) -> None:
        embed = build_event_embed(_event(), role_slots=[self._slot()], role_signups=[])
        field = next(f for f in embed.fields if "D1" in f.name)
        assert "（0/1）" in field.name
        assert "尚無人選" in field.value

    def test_shows_confirmed_signup_with_job(self) -> None:
        signups = [{"role_slot_id": "slot1", "user_id": "111", "job": "武士", "waitlisted": 0}]
        embed = build_event_embed(_event(), role_slots=[self._slot()], role_signups=signups)
        field = next(f for f in embed.fields if "D1" in f.name)
        assert "（1/1）" in field.name
        assert "<@111>" in field.value
        assert "武士" in field.value

    def test_shows_waitlist_alongside_confirmed(self) -> None:
        signups = [
            {
                "role_slot_id": "slot1",
                "user_id": "111",
                "job": "武士",
                "waitlisted": 0,
                "signed_up_at": 1,
            },
            {
                "role_slot_id": "slot1",
                "user_id": "222",
                "job": "忍者",
                "waitlisted": 1,
                "signed_up_at": 2,
            },
        ]
        embed = build_event_embed(_event(), role_slots=[self._slot()], role_signups=signups)
        field = next(f for f in embed.fields if "D1" in f.name)
        assert "（1/1）" in field.name
        assert "<@111>" in field.value
        assert "候補" in field.value
        assert "<@222>" in field.value

    def test_multiple_slots_each_get_their_own_field(self) -> None:
        slots = [
            self._slot(id="s1", position="MT", sort=0),
            self._slot(id="s2", position="D1", sort=1),
        ]
        embed = build_event_embed(_event(), role_slots=slots, role_signups=[])
        names = [f.name for f in embed.fields]
        assert any("MT" in n for n in names)
        assert any("D1" in n for n in names)

    def test_role_fields_appear_before_invitees_field(self) -> None:
        invitees = [{"target_type": "user", "target_id": "999"}]
        embed = build_event_embed(
            _event(), invitees=invitees, role_slots=[self._slot()], role_signups=[]
        )
        names = [f.name for f in embed.fields]
        role_index = next(i for i, n in enumerate(names) if "D1" in n)
        invitee_index = next(i for i, n in enumerate(names) if "邀請對象" in n)
        assert role_index < invitee_index


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


class TestBuildReminderEmbed:
    BASE_REMINDER: ClassVar[dict] = {
        "title": "週五團練",
        "starts_at_utc": 1754049600000,
        "location": None,
        "message_id": None,
        "channel_id": None,
        "guild_id": None,
    }

    def _reminder(self, **overrides):
        return {**self.BASE_REMINDER, **overrides}

    def test_title_includes_event_title(self) -> None:
        embed = build_reminder_embed(self._reminder(title="週五團練"))
        assert "週五團練" in embed.title
        assert "⏰" in embed.title

    def test_description_includes_relative_timestamp(self) -> None:
        embed = build_reminder_embed(self._reminder())
        assert "<t:1754049600:R>" in embed.description

    def test_omits_location_when_not_set(self) -> None:
        embed = build_reminder_embed(self._reminder(location=None))
        assert "📍" not in embed.description

    def test_includes_location_when_set(self) -> None:
        embed = build_reminder_embed(self._reminder(location="語音頻道"))
        assert "📍 語音頻道" in embed.description

    def test_omits_jump_link_when_message_id_missing(self) -> None:
        embed = build_reminder_embed(
            self._reminder(message_id=None, channel_id="c1", guild_id="g1")
        )
        assert "查看活動公告" not in embed.description

    def test_includes_jump_link_when_all_ids_present(self) -> None:
        embed = build_reminder_embed(
            self._reminder(message_id="m1", channel_id="c1", guild_id="g1")
        )
        assert "https://discord.com/channels/g1/c1/m1" in embed.description
        assert "查看活動公告" in embed.description


BASE_POLL = {
    "id": "poll000001",
    "question": "晚餐吃什麼",
    "kind": "generic",
    "multi": 0,
    "anonymous": 0,
    "allow_change": 1,
    "closes_at": None,
    "status": "open",
    "description": None,
}


def _poll(**overrides):
    return {**BASE_POLL, **overrides}


class TestBuildPollEmbed:
    def test_generic_option_shows_label_as_is(self) -> None:
        options = [{"id": "o1", "label": "火鍋", "meta": None}]
        embed = build_poll_embed(_poll(), options, [])
        assert any(f.name.startswith("火鍋") for f in embed.fields)

    def test_time_slot_option_renders_discord_timestamp_from_meta_not_raw_label(self) -> None:
        """回歸測試：option['label'] 是給 Select 選單用的純文字（例如
        "8/3（一）04:00"），embed 欄位名稱要從 meta 現算 Discord 時間戳，不能
        直接沿用那個純文字 label——否則 embed 少了「依檢視者時區自動換算」
        的效果（雖然不像 Select 選項那樣整串原始字元跑出來，但仍然是退步）。
        """
        options = [{"id": "o1", "label": "8/3（一）04:00", "meta": "1785700800000"}]
        embed = build_poll_embed(_poll(kind="time_slot"), options, [])
        field_names = [f.name for f in embed.fields]
        assert any("<t:1785700800:F>" in name for name in field_names)
        assert not any(name.startswith("8/3（一）04:00") for name in field_names)

    def test_time_slot_option_without_meta_falls_back_to_label(self) -> None:
        """理論上不會發生（time_slot 選項一定有 meta），但防禦性地確保不會
        因為缺 meta 就整個 embed 組不出來。"""
        options = [{"id": "o1", "label": "沒有 meta", "meta": None}]
        embed = build_poll_embed(_poll(kind="time_slot"), options, [])
        assert any(f.name.startswith("沒有 meta") for f in embed.fields)

    def test_omits_description_field_when_not_set(self) -> None:
        embed = build_poll_embed(_poll(description=None), [], [])
        assert not any(f.name == "📝 說明" for f in embed.fields)

    def test_includes_description_field_when_set(self) -> None:
        embed = build_poll_embed(_poll(description="這次要約平日還是假日晚上"), [], [])
        field = next(f for f in embed.fields if f.name == "📝 說明")
        assert field.value == "這次要約平日還是假日晚上"

    def test_anonymous_hides_voter_mentions(self) -> None:
        options = [{"id": "o1", "label": "A", "meta": None}]
        votes = [{"option_id": "o1", "user_id": "111"}]
        embed = build_poll_embed(_poll(anonymous=1), options, votes)
        field = next(f for f in embed.fields if f.name.startswith("A"))
        assert "111" not in field.value
        assert "匿名" in field.value

    def test_closed_poll_shows_marker_in_title(self) -> None:
        embed = build_poll_embed(_poll(status="closed"), [], [])
        assert "已截止" in embed.title


def _make_guild(*, members: dict[int, str] | None = None, roles: dict[int, str] | None = None):
    """假的 discord.Guild：`get_member`/`get_role` 依傳入的對照表回傳假物件，
    查不到的 ID 回傳 None（模擬「不在本機成員快取裡」）。"""
    guild = MagicMock()

    def _get_member(uid: int):
        if members and uid in members:
            return MagicMock(display_name=members[uid])
        return None

    def _get_role(rid: int):
        if roles and rid in roles:
            # 注意：MagicMock(name=...) 的 name= 是保留給 mock 自己的 repr
            # 名稱用，不會變成 .name 屬性——要用建構後賦值才能設 .name。
            role = MagicMock()
            role.name = roles[rid]
            return role
        return None

    guild.get_member.side_effect = _get_member
    guild.get_role.side_effect = _get_role
    return guild


class TestGuildAwareNames:
    """embed 欄位裡的 `<@id>` mention 在不同 Discord 客戶端解析結果不一致
    （見 embeds.py `_display_name` 的說明），改用伺服器暱稱純文字顯示，
    guild 沒給或成員不在快取裡時才退回原本的 mention 標記。"""

    def test_no_guild_falls_back_to_mentions(self) -> None:
        """對照組：guild 不給（預設 None）行為要跟這個功能加進來之前完全
        一樣——這是最重要的回歸測試，本檔案其餘既有測試都仰賴這個假設。"""
        summary = RsvpSummary(yes=[111])
        embed = build_event_embed(_event(), rsvp_summary=summary)
        field = next(f for f in embed.fields if f.name.startswith("✅ 參加"))
        assert field.value == "<@111>"

    def test_rsvp_summary_shows_display_name_when_member_cached(self) -> None:
        guild = _make_guild(members={111: "拉麵"})
        summary = RsvpSummary(yes=[111])
        embed = build_event_embed(_event(), rsvp_summary=summary, guild=guild)
        field = next(f for f in embed.fields if f.name.startswith("✅ 參加"))
        assert field.value == "@拉麵"
        assert "<@111>" not in field.value

    def test_rsvp_summary_falls_back_to_mention_when_member_not_cached(self) -> None:
        """guild 有給，但這個使用者不在本機成員快取裡（例如已離開伺服器）
        ——退回 mention 標記，至少讓 Discord 自己盡力解析。"""
        guild = _make_guild(members={})
        summary = RsvpSummary(yes=[999])
        embed = build_event_embed(_event(), rsvp_summary=summary, guild=guild)
        field = next(f for f in embed.fields if f.name.startswith("✅ 參加"))
        assert field.value == "<@999>"

    def test_multiple_names_are_separated_by_dun_hao(self) -> None:
        guild = _make_guild(members={111: "拉麵", 222: "看看"})
        summary = RsvpSummary(yes=[111, 222])
        embed = build_event_embed(_event(), rsvp_summary=summary, guild=guild)
        field = next(f for f in embed.fields if f.name.startswith("✅ 參加"))
        assert field.value == "@拉麵、@看看"

    def test_creator_field_shows_display_name(self) -> None:
        guild = _make_guild(members={123456789: "拉麵"})
        embed = build_event_embed(_event(creator_id="123456789"), guild=guild)
        creator_field = next(f for f in embed.fields if "發起人" in f.name)
        assert creator_field.value == "@拉麵"

    def test_invitee_user_shows_display_name(self) -> None:
        guild = _make_guild(members={111: "拉麵"})
        invitees = [{"target_type": "user", "target_id": "111"}]
        embed = build_event_embed(_event(), invitees=invitees, guild=guild)
        field = next(f for f in embed.fields if "邀請對象" in f.name)
        assert field.value == "@拉麵"

    def test_invitee_role_shows_role_name(self) -> None:
        guild = _make_guild(roles={222: "幹部"})
        invitees = [{"target_type": "role", "target_id": "222"}]
        embed = build_event_embed(_event(), invitees=invitees, guild=guild)
        field = next(f for f in embed.fields if "邀請對象" in f.name)
        assert field.value == "@幹部"

    def test_invitee_role_falls_back_to_mention_when_not_cached(self) -> None:
        guild = _make_guild(roles={})
        invitees = [{"target_type": "role", "target_id": "222"}]
        embed = build_event_embed(_event(), invitees=invitees, guild=guild)
        field = next(f for f in embed.fields if "邀請對象" in f.name)
        assert field.value == "<@&222>"

    def test_invitee_everyone_unaffected(self) -> None:
        guild = _make_guild()
        invitees = [{"target_type": "everyone", "target_id": "999"}]
        embed = build_event_embed(_event(), invitees=invitees, guild=guild)
        field = next(f for f in embed.fields if "邀請對象" in f.name)
        assert field.value == "@everyone"

    def test_role_slot_signup_shows_display_name(self) -> None:
        guild = _make_guild(members={111: "拉麵"})
        slot = {"id": "s1", "event_id": "e1", "position": "D1", "sort": 0}
        signups = [{"role_slot_id": "s1", "user_id": "111", "job": "武士", "waitlisted": 0}]
        embed = build_event_embed(_event(), role_slots=[slot], role_signups=signups, guild=guild)
        field = next(f for f in embed.fields if "D1" in f.name)
        assert "@拉麵（武士）" in field.value
        assert "<@111>" not in field.value

    def test_poll_vote_shows_display_name(self) -> None:
        guild = _make_guild(members={111: "拉麵"})
        options = [{"id": "o1", "label": "A", "meta": None}]
        votes = [{"option_id": "o1", "user_id": "111"}]
        embed = build_poll_embed(_poll(), options, votes, guild)
        field = next(f for f in embed.fields if f.name.startswith("A"))
        assert field.value == "@拉麵"
