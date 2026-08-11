"""NativeEvents cog 測試：Discord 原生活動卡片「有興趣／取消有興趣」跟我們
自己的 RSVP 表雙向同步（M6 的另一半，`sync_create` 的測試在
`test_native_events_sync.py`）。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from src.bot.cogs.native_events import NativeEvents
from src.db import repo
from src.lib.clock import now_ms
from src.lib.ids import new_id

GUILD_ID = 111111111111111111
CHANNEL_ID = 222222222222222222
USER_ID = 333333333333333333
DISCORD_EVENT_ID = 444444444444444444


async def _create_linked_event(db, *, message_id: int | None = 555, **kwargs) -> str:
    """建一個活動，並比照 sync_create 成功後的狀態，把它連結到一個假的原生
    Scheduled Event ID（`DISCORD_EVENT_ID`）。"""
    event_id = new_id()
    await repo.create_event(
        event_id=event_id,
        guild_id=GUILD_ID,
        channel_id=CHANNEL_ID,
        creator_id=USER_ID,
        title="週五團練",
        starts_at_utc=now_ms() + 3_600_000,
        tz="Asia/Taipei",
        **kwargs,
    )
    await repo.set_event_discord_id(event_id, GUILD_ID, DISCORD_EVENT_ID)
    if message_id is not None:
        await repo.set_event_message(event_id, GUILD_ID, message_id)
    return event_id


def _make_channel() -> MagicMock:
    channel = MagicMock()
    channel.fetch_message = AsyncMock(return_value=AsyncMock())
    return channel


def _make_bot(channel: MagicMock | None) -> MagicMock:
    bot = MagicMock()
    bot.get_channel.return_value = channel
    bot.fetch_channel = AsyncMock(return_value=channel)
    return bot


def _make_scheduled_event(*, discord_event_id: int = DISCORD_EVENT_ID) -> MagicMock:
    event = MagicMock()
    event.id = discord_event_id
    event.guild = MagicMock(id=GUILD_ID)
    return event


def _make_user(*, user_id: int = USER_ID, bot: bool = False) -> SimpleNamespace:
    return SimpleNamespace(id=user_id, bot=bot)


class TestOnScheduledEventUserAdd:
    async def test_records_yes_rsvp_for_linked_event(self, db) -> None:
        event_id = await _create_linked_event(db)
        cog = NativeEvents(_make_bot(_make_channel()))

        await cog.on_scheduled_event_user_add(_make_scheduled_event(), _make_user())

        rows = await repo.list_rsvps(event_id, GUILD_ID)
        assert rows[0]["user_id"] == str(USER_ID)
        assert rows[0]["status"] == "yes"

    async def test_refreshes_announcement_embed(self, db) -> None:
        await _create_linked_event(db)
        channel = _make_channel()
        cog = NativeEvents(_make_bot(channel))

        await cog.on_scheduled_event_user_add(_make_scheduled_event(), _make_user())

        channel.fetch_message.assert_awaited_once()

    async def test_ignores_unknown_discord_event_id(self, db) -> None:
        """使用者自己在 Discord 手動開的原生活動，不是我們建立的，不該被同步。"""
        cog = NativeEvents(_make_bot(_make_channel()))

        await cog.on_scheduled_event_user_add(
            _make_scheduled_event(discord_event_id=999999999), _make_user()
        )

        assert await db.query_all("SELECT * FROM rsvps") == []

    async def test_ignores_bot_users(self, db) -> None:
        await _create_linked_event(db)
        cog = NativeEvents(_make_bot(_make_channel()))

        await cog.on_scheduled_event_user_add(_make_scheduled_event(), _make_user(bot=True))

        assert await db.query_all("SELECT * FROM rsvps") == []

    async def test_overwrites_existing_maybe_or_no_status(self, db) -> None:
        event_id = await _create_linked_event(db)
        await repo.upsert_rsvp(event_id, GUILD_ID, USER_ID, "no")
        cog = NativeEvents(_make_bot(_make_channel()))

        await cog.on_scheduled_event_user_add(_make_scheduled_event(), _make_user())

        rows = await repo.list_rsvps(event_id, GUILD_ID)
        assert rows[0]["status"] == "yes"


class TestOnScheduledEventUserRemove:
    async def test_reverts_to_no_response(self, db) -> None:
        """取消有興趣不是明確表態不參加，還原成未回覆（刪掉那一列），
        不是改成 status='no'。"""
        event_id = await _create_linked_event(db)
        await repo.upsert_rsvp(event_id, GUILD_ID, USER_ID, "yes")
        cog = NativeEvents(_make_bot(_make_channel()))

        await cog.on_scheduled_event_user_remove(_make_scheduled_event(), _make_user())

        assert await repo.list_rsvps(event_id, GUILD_ID) == []

    async def test_refreshes_announcement_embed(self, db) -> None:
        event_id = await _create_linked_event(db)
        await repo.upsert_rsvp(event_id, GUILD_ID, USER_ID, "yes")
        channel = _make_channel()
        cog = NativeEvents(_make_bot(channel))

        await cog.on_scheduled_event_user_remove(_make_scheduled_event(), _make_user())

        channel.fetch_message.assert_awaited_once()

    async def test_no_existing_rsvp_does_not_raise(self, db) -> None:
        await _create_linked_event(db)
        cog = NativeEvents(_make_bot(_make_channel()))

        await cog.on_scheduled_event_user_remove(
            _make_scheduled_event(), _make_user()
        )  # 不應拋例外


class TestRefreshResilience:
    async def test_missing_message_id_skips_refresh_without_raising(self, db) -> None:
        await _create_linked_event(db, message_id=None)
        channel = _make_channel()
        cog = NativeEvents(_make_bot(channel))

        await cog.on_scheduled_event_user_add(_make_scheduled_event(), _make_user())

        channel.fetch_message.assert_not_awaited()

    async def test_channel_resolution_failure_does_not_raise(self, db) -> None:
        await _create_linked_event(db)
        cog = NativeEvents(_make_bot(None))

        await cog.on_scheduled_event_user_add(_make_scheduled_event(), _make_user())  # 不應拋例外
