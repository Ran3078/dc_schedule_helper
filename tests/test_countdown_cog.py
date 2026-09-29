"""`Countdown` cog 測試——`/countdown create`／`list`／`cancel` 的驗證與權限，
以及 `countdown_tick`／`_maybe_send_countdown` 的每日發送判斷。比照
`test_ff14_recruit.py`（指令 impl 測法）與 `test_weekly_digest_cog.py`
（tick 測法）的既有慣例。
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import discord

from src.bot.cogs.countdown import Countdown
from src.bot.views_countdown import CountdownTimePickerView
from src.db import repo
from src.lib.clock import now_ms

GUILD_ID = 111111111111111111
CHANNEL_ID = 222222222222222222
CREATOR_ID = 333333333333333333
OTHER_USER_ID = 444444444444444444
ORGANIZER_ROLE_ID = 555555555555555555
OTHER_CHANNEL_ID = 666666666666666666

NOW = now_ms()
DAY = 24 * 3_600_000
TPE = ZoneInfo("Asia/Taipei")


async def _restrict_to_organizer_role(db) -> None:
    await repo.ensure_guild(GUILD_ID, "Asia/Taipei")
    await repo.update_guild_settings(GUILD_ID, organizer_role_id=str(ORGANIZER_ROLE_ID))


def _make_member(user_id: int, role_ids: tuple[int, ...] = ()) -> MagicMock:
    member = MagicMock(spec=discord.Member)
    member.id = user_id
    member.roles = [MagicMock(id=rid) for rid in role_ids]
    return member


def _make_channel(channel_id: int = CHANNEL_ID) -> MagicMock:
    channel = MagicMock()
    channel.id = channel_id
    channel.mention = f"<#{channel_id}>"
    channel.send = AsyncMock()
    return channel


def _make_bot() -> MagicMock:
    bot = MagicMock()
    bot.settings = None  # 見 guild_tz 的 self-heal 分支說明
    return bot


def _make_interaction(
    *, user: MagicMock | None = None, channel: MagicMock | None = None
) -> MagicMock:
    interaction = MagicMock()
    interaction.guild_id = GUILD_ID
    interaction.user = user or _make_member(CREATOR_ID)
    interaction.response = AsyncMock()
    interaction.channel = channel or _make_channel()

    fake_message = MagicMock()
    fake_message.id = 999
    interaction.original_response = AsyncMock(return_value=fake_message)
    return interaction


def _extract_picker(interaction: MagicMock) -> CountdownTimePickerView:
    """`_create_impl` 驗證通過後改開 `CountdownTimePickerView`（下拉選單挑
    每天發送時間），不再直接寫入 DB——見這輪確認過的產品決策：時間跟
    `/event create` 一樣改用選單，不讓使用者手打 HH:MM。"""
    _, kwargs = interaction.response.send_message.call_args
    return kwargs["view"]


async def _finish_via_picker(
    picker: CountdownTimePickerView, *, hour: int = 9, minute: int = 0
) -> None:
    picker.selected_hour = hour
    picker.selected_minute = minute
    confirm_interaction = MagicMock()
    confirm_interaction.response = AsyncMock()
    await picker._on_confirm(confirm_interaction)


async def _create_countdown_via_cog(
    cog: Countdown,
    interaction: MagicMock,
    *,
    title: str = "退伍倒數",
    mode: str = "countdown",
    value: str = "2027-06-15",
    suffix: str | None = None,
    content: str | None = None,
    channel: MagicMock | None = None,
    hour: int = 9,
    minute: int = 0,
) -> None:
    """`TestListImpl`／`TestCancelImpl` 只在意「已經有一筆倒數存在」，不在意
    建立過程本身（那是 `TestCreateImpl` 的職責），走完整個兩段式流程
    （建立 → 選時間 → 確認）拿到最終寫進 DB 的那筆。"""
    await cog._create_impl(interaction, title, mode, value, suffix, content, channel)
    picker = _extract_picker(interaction)
    await _finish_via_picker(picker, hour=hour, minute=minute)


class TestCreateImpl:
    async def test_organizer_role_required_when_configured(self, db) -> None:
        await _restrict_to_organizer_role(db)
        cog = Countdown(_make_bot())
        interaction = _make_interaction(user=_make_member(OTHER_USER_ID))

        await cog._create_impl(
            interaction, "退伍倒數", "countdown", "2027-06-15", None, None, None
        )

        interaction.response.send_message.assert_awaited_once()
        args, kwargs = interaction.response.send_message.call_args
        assert "特定身分組" in args[0]
        assert kwargs["ephemeral"] is True

    async def test_organizer_role_member_is_allowed(self, db) -> None:
        await _restrict_to_organizer_role(db)
        cog = Countdown(_make_bot())
        interaction = _make_interaction(
            user=_make_member(OTHER_USER_ID, role_ids=(ORGANIZER_ROLE_ID,))
        )

        await cog._create_impl(
            interaction, "退伍倒數", "countdown", "2027-06-15", None, None, None
        )

        _, kwargs = interaction.response.send_message.call_args
        assert isinstance(kwargs["view"], CountdownTimePickerView)

    async def test_empty_title_is_rejected(self, db) -> None:
        cog = Countdown(_make_bot())
        interaction = _make_interaction()

        await cog._create_impl(interaction, "   ", "countdown", "2027-06-15", None, None, None)

        args, _ = interaction.response.send_message.call_args
        assert "不能是空的" in args[0]

    async def test_title_too_long_is_rejected(self, db) -> None:
        cog = Countdown(_make_bot())
        interaction = _make_interaction()

        await cog._create_impl(
            interaction, "太" * 201, "countdown", "2027-06-15", None, None, None
        )

        args, _ = interaction.response.send_message.call_args
        assert "太長了" in args[0]

    async def test_invalid_date_is_rejected(self, db) -> None:
        cog = Countdown(_make_bot())
        interaction = _make_interaction()

        await cog._create_impl(interaction, "退伍倒數", "countdown", "不是日期", None, None, None)

        args, _ = interaction.response.send_message.call_args
        assert "無法解析" in args[0]

    async def test_day_count_shortcut_opens_picker(self, db) -> None:
        """懶得算確切日期時可以直接打「還剩幾天」的數字。"""
        cog = Countdown(_make_bot())
        interaction = _make_interaction()

        await cog._create_impl(interaction, "退伍倒數", "countdown", "500", None, None, None)

        _, kwargs = interaction.response.send_message.call_args
        assert isinstance(kwargs["view"], CountdownTimePickerView)

    async def test_valid_input_creates_countdown_after_picking_time(self, db) -> None:
        cog = Countdown(_make_bot())
        interaction = _make_interaction()

        await cog._create_impl(
            interaction, "退伍倒數", "countdown", "2027-06-15", "天", "加油", None
        )
        picker = _extract_picker(interaction)
        await _finish_via_picker(picker, hour=9, minute=0)

        rows = await repo.list_active_countdowns_in_guild(GUILD_ID)
        assert len(rows) == 1
        assert rows[0]["title"] == "退伍倒數"
        assert rows[0]["content"] == "加油"
        assert rows[0]["suffix"] == "天"
        assert rows[0]["mode"] == "countdown"
        assert rows[0]["send_hour"] == 9
        assert rows[0]["send_minute"] == 0

    async def test_countup_mode_creates_countdown_with_anchor_in_past(self, db) -> None:
        """正數模式：輸入「目前已經第 12 天」，錨點要反推成「今天－12 天」。"""
        from src.domain.countdown import days_elapsed

        cog = Countdown(_make_bot())
        interaction = _make_interaction()

        await cog._create_impl(
            interaction, "沒有小名的日子第", "countup", "12", "天", None, None
        )
        picker = _extract_picker(interaction)
        assert picker.mode == "countup"
        await _finish_via_picker(picker, hour=9, minute=0)

        rows = await repo.list_active_countdowns_in_guild(GUILD_ID)
        assert rows[0]["mode"] == "countup"
        elapsed = days_elapsed(rows[0]["target_date_utc"], datetime.now(TPE))
        assert elapsed == 12

    async def test_countup_mode_rejects_invalid_day_count(self, db) -> None:
        cog = Countdown(_make_bot())
        interaction = _make_interaction()

        await cog._create_impl(
            interaction, "沒有小名的日子第", "countup", "不是天數", None, None, None
        )

        args, _ = interaction.response.send_message.call_args
        assert "不是合法的天數" in args[0]

    async def test_defaults_to_current_channel_when_not_specified(self, db) -> None:
        cog = Countdown(_make_bot())
        channel = _make_channel(CHANNEL_ID)
        interaction = _make_interaction(channel=channel)

        await cog._create_impl(
            interaction, "退伍倒數", "countdown", "2027-06-15", None, None, None
        )
        picker = _extract_picker(interaction)
        await _finish_via_picker(picker)

        rows = await repo.list_active_countdowns_in_guild(GUILD_ID)
        assert rows[0]["channel_id"] == str(CHANNEL_ID)

    async def test_uses_explicit_channel_when_specified(self, db) -> None:
        cog = Countdown(_make_bot())
        interaction = _make_interaction()
        explicit_channel = _make_channel(OTHER_CHANNEL_ID)

        await cog._create_impl(
            interaction, "退伍倒數", "countdown", "2027-06-15", None, None, explicit_channel
        )
        picker = _extract_picker(interaction)
        await _finish_via_picker(picker)

        rows = await repo.list_active_countdowns_in_guild(GUILD_ID)
        assert rows[0]["channel_id"] == str(OTHER_CHANNEL_ID)

    async def test_blank_content_becomes_none(self, db) -> None:
        cog = Countdown(_make_bot())
        interaction = _make_interaction()

        await cog._create_impl(
            interaction, "退伍倒數", "countdown", "2027-06-15", None, "   ", None
        )
        picker = _extract_picker(interaction)
        await _finish_via_picker(picker)

        rows = await repo.list_active_countdowns_in_guild(GUILD_ID)
        assert rows[0]["content"] is None

    async def test_blank_suffix_becomes_none(self, db) -> None:
        cog = Countdown(_make_bot())
        interaction = _make_interaction()

        await cog._create_impl(
            interaction, "退伍倒數", "countdown", "2027-06-15", "   ", None, None
        )
        picker = _extract_picker(interaction)
        await _finish_via_picker(picker)

        rows = await repo.list_active_countdowns_in_guild(GUILD_ID)
        assert rows[0]["suffix"] is None


class TestListImpl:
    async def test_no_countdowns_message(self, db) -> None:
        cog = Countdown(_make_bot())
        interaction = _make_interaction()

        await cog._list_impl(interaction)

        args, _ = interaction.response.send_message.call_args
        assert "沒有進行中" in args[0]

    async def test_lists_active_countdown_with_days_remaining(self, db) -> None:
        cog = Countdown(_make_bot())
        create_interaction = _make_interaction()
        await _create_countdown_via_cog(cog, create_interaction)

        list_interaction = _make_interaction()
        await cog._list_impl(list_interaction)

        args, _ = list_interaction.response.send_message.call_args
        assert "退伍倒數" in args[0]
        assert "09:00" in args[0]

    async def test_lists_countup_with_elapsed_days_rendered(self, db) -> None:
        cog = Countdown(_make_bot())
        create_interaction = _make_interaction()
        await _create_countdown_via_cog(
            cog, create_interaction, title="沒有小名的日子第", mode="countup", value="12",
            suffix="天",
        )

        list_interaction = _make_interaction()
        await cog._list_impl(list_interaction)

        args, _ = list_interaction.response.send_message.call_args
        assert "沒有小名的日子第12天" in args[0]


class TestCancelImpl:
    async def test_not_found(self, db) -> None:
        cog = Countdown(_make_bot())
        interaction = _make_interaction()

        await cog._cancel_impl(interaction, "nope")

        args, _ = interaction.response.send_message.call_args
        assert "找不到" in args[0]

    async def test_creator_can_cancel(self, db) -> None:
        cog = Countdown(_make_bot())
        create_interaction = _make_interaction(user=_make_member(CREATOR_ID))
        await _create_countdown_via_cog(cog, create_interaction)
        countdown_id = (await repo.list_active_countdowns_in_guild(GUILD_ID))[0]["id"]

        cancel_interaction = _make_interaction(user=_make_member(CREATOR_ID))
        await cog._cancel_impl(cancel_interaction, countdown_id)

        args, _ = cancel_interaction.response.send_message.call_args
        assert "已取消" in args[0]

    async def test_non_creator_non_organizer_is_denied(self, db) -> None:
        await _restrict_to_organizer_role(db)
        cog = Countdown(_make_bot())
        create_interaction = _make_interaction(
            user=_make_member(CREATOR_ID, role_ids=(ORGANIZER_ROLE_ID,))
        )
        await _create_countdown_via_cog(cog, create_interaction)
        countdown_id = (await repo.list_active_countdowns_in_guild(GUILD_ID))[0]["id"]

        cancel_interaction = _make_interaction(user=_make_member(OTHER_USER_ID))
        await cog._cancel_impl(cancel_interaction, countdown_id)

        args, _ = cancel_interaction.response.send_message.call_args
        assert "只有建立者或管理員身分組" in args[0]

    async def test_organizer_role_member_can_cancel_others_countdown(self, db) -> None:
        cog = Countdown(_make_bot())
        create_interaction = _make_interaction(user=_make_member(CREATOR_ID))
        await _create_countdown_via_cog(cog, create_interaction)
        countdown_id = (await repo.list_active_countdowns_in_guild(GUILD_ID))[0]["id"]
        await repo.update_guild_settings(GUILD_ID, organizer_role_id=str(ORGANIZER_ROLE_ID))

        cancel_interaction = _make_interaction(
            user=_make_member(OTHER_USER_ID, role_ids=(ORGANIZER_ROLE_ID,))
        )
        await cog._cancel_impl(cancel_interaction, countdown_id)

        args, _ = cancel_interaction.response.send_message.call_args
        assert "已取消" in args[0]

    async def test_already_cancelled_gives_clear_message(self, db) -> None:
        cog = Countdown(_make_bot())
        create_interaction = _make_interaction(user=_make_member(CREATOR_ID))
        await _create_countdown_via_cog(cog, create_interaction)
        countdown_id = (await repo.list_active_countdowns_in_guild(GUILD_ID))[0]["id"]
        await repo.cancel_countdown(countdown_id, GUILD_ID)

        cancel_interaction = _make_interaction(user=_make_member(CREATOR_ID))
        await cog._cancel_impl(cancel_interaction, countdown_id)

        args, _ = cancel_interaction.response.send_message.call_args
        assert "已經結束或取消" in args[0]


def _make_cog(*, channel=None, fetch_channel_result=None) -> tuple[Countdown, MagicMock]:
    bot = MagicMock()
    bot.get_channel = MagicMock(return_value=channel)
    if fetch_channel_result is not None:
        bot.fetch_channel = AsyncMock(return_value=fetch_channel_result)
    else:
        bot.fetch_channel = AsyncMock(
            side_effect=discord.NotFound(MagicMock(status=404), "channel not found")
        )
    return Countdown(bot), bot


async def _create_countdown(db, **overrides) -> str:
    from src.lib.ids import new_id

    countdown_id = new_id()
    defaults = dict(
        countdown_id=countdown_id,
        guild_id=GUILD_ID,
        channel_id=CHANNEL_ID,
        creator_id=CREATOR_ID,
        title="退伍倒數",
        content=None,
        target_date_utc=NOW + 30 * DAY,
        tz="Asia/Taipei",
        send_hour=9,
        send_minute=0,
    )
    await repo.create_countdown(**{**defaults, **overrides})
    return countdown_id


class TestMaybeSendCountdown:
    async def test_sends_at_or_after_send_time(self, db) -> None:
        # send_hour/send_minute 設成 00:00，不管現在系統時間是幾點都一定
        # 已經過了，不用 monkeypatch 系統時鐘也能穩定測到「該發送」這個分支。
        countdown_id = await _create_countdown(
            db, send_hour=0, send_minute=0, target_date_utc=NOW + 10 * DAY
        )
        channel = _make_channel()
        cog, _ = _make_cog(channel=channel)
        countdown = await repo.owned_countdown(countdown_id, GUILD_ID)

        await cog._maybe_send_countdown(countdown)

        channel.send.assert_awaited_once()
        row = await repo.owned_countdown(countdown_id, GUILD_ID)
        assert row["last_sent_utc"] is not None

    async def test_does_not_resend_same_day(self, db) -> None:
        countdown_id = await _create_countdown(db, send_hour=0, send_minute=0)
        await repo.mark_countdown_sent(countdown_id, now_ms(), completed=False)
        channel = _make_channel()
        cog, _ = _make_cog(channel=channel)
        countdown = await repo.owned_countdown(countdown_id, GUILD_ID)

        await cog._maybe_send_countdown(countdown)

        channel.send.assert_not_awaited()

    async def test_before_send_time_does_not_send(self, db) -> None:
        countdown_id = await _create_countdown(db, send_hour=23, send_minute=59)
        channel = _make_channel()
        cog, _ = _make_cog(channel=channel)
        countdown = await repo.owned_countdown(countdown_id, GUILD_ID)

        await cog._maybe_send_countdown(countdown)

        channel.send.assert_not_awaited()

    async def test_missing_channel_skips_without_raising(self, db) -> None:
        countdown_id = await _create_countdown(db, send_hour=0, send_minute=0)
        cog, _ = _make_cog(channel=None, fetch_channel_result=None)
        countdown = await repo.owned_countdown(countdown_id, GUILD_ID)

        await cog._maybe_send_countdown(countdown)  # 不應拋例外

        row = await repo.owned_countdown(countdown_id, GUILD_ID)
        assert row["last_sent_utc"] is None

    async def test_send_failure_does_not_mark_sent(self, db) -> None:
        countdown_id = await _create_countdown(db, send_hour=0, send_minute=0)
        channel = _make_channel()
        channel.send = AsyncMock(
            side_effect=discord.HTTPException(MagicMock(status=403), "forbidden")
        )
        cog, _ = _make_cog(channel=channel)
        countdown = await repo.owned_countdown(countdown_id, GUILD_ID)

        await cog._maybe_send_countdown(countdown)  # 不應拋例外

        row = await repo.owned_countdown(countdown_id, GUILD_ID)
        assert row["last_sent_utc"] is None

    async def test_reaching_target_date_marks_completed(self, db) -> None:
        """發送當天的訊息後自動停止——這是這輪確認過的產品決策。"""
        countdown_id = await _create_countdown(
            db, send_hour=0, send_minute=0, target_date_utc=NOW
        )
        channel = _make_channel()
        cog, _ = _make_cog(channel=channel)
        countdown = await repo.owned_countdown(countdown_id, GUILD_ID)

        await cog._maybe_send_countdown(countdown)

        row = await repo.owned_countdown(countdown_id, GUILD_ID)
        assert row["status"] == "completed"

    async def test_not_yet_target_date_stays_active(self, db) -> None:
        countdown_id = await _create_countdown(
            db, send_hour=0, send_minute=0, target_date_utc=NOW + 10 * DAY
        )
        channel = _make_channel()
        cog, _ = _make_cog(channel=channel)
        countdown = await repo.owned_countdown(countdown_id, GUILD_ID)

        await cog._maybe_send_countdown(countdown)

        row = await repo.owned_countdown(countdown_id, GUILD_ID)
        assert row["status"] == "active"

    async def test_countup_mode_never_completes(self, db) -> None:
        """正數模式永遠累加、只能手動取消——就算 elapsed 已經很大也不會
        自動 completed（跟倒數模式的自動停止不同，見這輪確認過的產品決策）。
        """
        countdown_id = await _create_countdown(
            db, send_hour=0, send_minute=0, mode="countup", target_date_utc=NOW - 999 * DAY
        )
        channel = _make_channel()
        cog, _ = _make_cog(channel=channel)
        countdown = await repo.owned_countdown(countdown_id, GUILD_ID)

        await cog._maybe_send_countdown(countdown)

        row = await repo.owned_countdown(countdown_id, GUILD_ID)
        assert row["status"] == "active"
        channel.send.assert_awaited_once()

    async def test_countup_mode_renders_elapsed_in_embed(self, db) -> None:
        countdown_id = await _create_countdown(
            db,
            send_hour=0,
            send_minute=0,
            mode="countup",
            title="沒有小名的日子第",
            suffix="天",
            target_date_utc=NOW - 12 * DAY,
        )
        channel = _make_channel()
        cog, _ = _make_cog(channel=channel)
        countdown = await repo.owned_countdown(countdown_id, GUILD_ID)

        await cog._maybe_send_countdown(countdown)

        _, kwargs = channel.send.call_args
        assert "沒有小名的日子第12天" in kwargs["embed"].title


class TestCountdownTick:
    async def test_only_touches_active_countdowns(self, db) -> None:
        active_id = await _create_countdown(db, send_hour=0, send_minute=0)
        cancelled_id = await _create_countdown(db, send_hour=0, send_minute=0)
        await repo.cancel_countdown(cancelled_id, GUILD_ID)

        channel = _make_channel()
        cog, _ = _make_cog(channel=channel)

        await Countdown.countdown_tick.coro(cog)  # 略過 @tasks.loop 包裝

        channel.send.assert_awaited_once()
        active_row = await repo.owned_countdown(active_id, GUILD_ID)
        assert active_row["last_sent_utc"] is not None
