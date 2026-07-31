"""mention 白名單測試。

這裡守的是「Tag 人的能力」最基本的正確性：content 字串要包含實際會觸發
推播的 mention，allowed_mentions 白名單要精確等於這次選定的對象 —— 不多
（不該讓 content 裡任何看起來像 mention 的文字都能發通知），也不少（選了
就該真的通知到）。
"""

from __future__ import annotations

from src.lib.mentions import build_allowed_mentions, build_mention_content, invitee_rows


class TestBuildMentionContent:
    def test_empty_selection_returns_none(self) -> None:
        """channel.send 不接受空字串 content，空清單要回傳 None 而非 ""。"""
        assert build_mention_content([], []) is None

    def test_users_only(self) -> None:
        assert build_mention_content(["111", "222"], []) == "<@111> <@222>"

    def test_roles_only(self) -> None:
        assert build_mention_content([], ["333"]) == "<@&333>"

    def test_users_and_roles_combined(self) -> None:
        assert build_mention_content(["111"], ["333"]) == "<@111> <@&333>"

    def test_everyone_flag_prepends_at_everyone(self) -> None:
        content = build_mention_content(["111"], [], tag_everyone=True)
        assert content == "@everyone <@111>"

    def test_everyone_alone_is_not_none(self) -> None:
        assert build_mention_content([], [], tag_everyone=True) == "@everyone"

    def test_accepts_int_ids(self) -> None:
        """Discord 給的 ID 是 int（UserSelect.values 回傳的 Member.id），不能要求呼叫端先轉字串。"""
        assert build_mention_content([111, 222], []) == "<@111> <@222>"


class TestBuildAllowedMentions:
    def test_whitelist_matches_exactly_the_given_ids(self) -> None:
        allowed = build_allowed_mentions(["111"], ["333"])
        assert [o.id for o in allowed.users] == [111]
        assert [o.id for o in allowed.roles] == [333]

    def test_everyone_defaults_to_false(self) -> None:
        """預設不能推播 @everyone/@here —— 這是安全性的關鍵防線。"""
        allowed = build_allowed_mentions(["111"], [])
        assert allowed.everyone is False

    def test_everyone_true_only_when_explicitly_requested(self) -> None:
        allowed = build_allowed_mentions([], [], tag_everyone=True)
        assert allowed.everyone is True

    def test_does_not_mention_replied_user(self) -> None:
        """公告訊息不是回覆任何人，這個旗標理論上不影響行為，但明確關閉比依賴預設值保險。"""
        allowed = build_allowed_mentions([], [])
        assert allowed.replied_user is False

    def test_empty_selection_still_blocks_everyone(self) -> None:
        allowed = build_allowed_mentions([], [])
        assert allowed.users == []
        assert allowed.roles == []
        assert allowed.everyone is False


class TestInviteeRows:
    def test_produces_rows_matching_db_shape(self) -> None:
        rows = invitee_rows(["111"], ["333"])
        assert rows == [
            {"target_type": "user", "target_id": "111"},
            {"target_type": "role", "target_id": "333"},
        ]

    def test_empty_input_produces_empty_list(self) -> None:
        assert invitee_rows([], []) == []
