"""活動同步到 Discord 原生「活動」分頁（M6）。

`guild.create_scheduled_event()` 建立的一律是 `EXTERNAL` 類型（純文字地點）
——這是唯一符合本專案「地點是自由文字，不是選一個語音/展示台頻道」設計的
類型。`EXTERNAL` 類型 Discord 強制要求 `location` 與 `end_time` 都要有值，
沒填的部分用 `domain.native_events` 的預設值補上（見該檔案的說明）。

同步失敗（權限不足、Discord API 錯誤）一律 log 後放棄，不讓這個附加功能拖垮
活動本身的建立/發布——這是本專案一貫的錯誤處理精神（見 `views.py`／
`scheduler.py` 其他處的 `try/except HTTPException`）。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import discord

from src.domain.native_events import resolve_native_end_time, resolve_native_location

log = logging.getLogger(__name__)

Row = dict[str, Any]

# Discord 對這幾個欄位的字數硬上限：活動名稱／地點都是 100 字元，比我們自己
# 的 events.title（MAX_TITLE_LENGTH=200）更嚴，這裡要再截一次；描述已經受
# EventDescriptionModal 的 max_length=1000 限制，跟 Discord 的上限一致，不用再截。
_MAX_NAME_LEN = 100
_MAX_LOCATION_LEN = 100


async def sync_create(guild: discord.Guild, event: Row) -> int | None:
    """建立這個活動對應的原生 Scheduled Event，回傳它的 ID；失敗回傳 None。"""
    start = datetime.fromtimestamp(event["starts_at_utc"] / 1000, tz=UTC)
    end_epoch = resolve_native_end_time(event["starts_at_utc"], event["ends_at_utc"])
    end = datetime.fromtimestamp(end_epoch / 1000, tz=UTC)

    # description 的預設值是 discord.py 的 MISSING sentinel，不是 None——
    # 傳 None 會讓 payload 帶 "description": null 送出去，跟「乾脆不要有
    # description 這個欄位」是兩回事，這裡沒活動說明時就完全不傳這個關鍵字。
    kwargs: dict[str, Any] = {}
    if event.get("description"):
        kwargs["description"] = event["description"]

    try:
        scheduled = await guild.create_scheduled_event(
            name=event["title"][:_MAX_NAME_LEN],
            start_time=start,
            end_time=end,
            entity_type=discord.EntityType.external,
            privacy_level=discord.PrivacyLevel.guild_only,
            location=resolve_native_location(event["location"])[:_MAX_LOCATION_LEN],
            **kwargs,
        )
    except discord.HTTPException:
        log.warning("活動 %s 同步原生活動失敗", event["id"], exc_info=True)
        return None

    return scheduled.id


async def _resolve_scheduled_event(
    guild: discord.Guild, discord_event_id: int
) -> discord.ScheduledEvent | None:
    """先吃快取（`get_scheduled_event`），沒有才補一次 API 呼叫
    （`fetch_scheduled_event`）——理由同 `scheduler.py`／`polls.py` 的
    `_resolve_channel`。找不到（例如使用者自己手動把原生活動刪了）回傳 None，
    呼叫端當成「沒什麼好同步的」處理，不是錯誤。
    """
    scheduled = guild.get_scheduled_event(discord_event_id)
    if scheduled is not None:
        return scheduled
    try:
        return await guild.fetch_scheduled_event(discord_event_id)
    except discord.HTTPException:
        return None


async def sync_edit(guild: discord.Guild, discord_event_id: int, event: Row) -> bool:
    """活動編輯後同步更新對應的原生 Scheduled Event。找不到／API 失敗都只
    log，回傳 False——不影響活動本身的編輯已經成功這件事。"""
    scheduled = await _resolve_scheduled_event(guild, discord_event_id)
    if scheduled is None:
        return False

    start = datetime.fromtimestamp(event["starts_at_utc"] / 1000, tz=UTC)
    end_epoch = resolve_native_end_time(event["starts_at_utc"], event["ends_at_utc"])
    end = datetime.fromtimestamp(end_epoch / 1000, tz=UTC)

    kwargs: dict[str, Any] = {}
    if event.get("description"):
        kwargs["description"] = event["description"]

    try:
        await scheduled.edit(
            name=event["title"][:_MAX_NAME_LEN],
            start_time=start,
            end_time=end,
            location=resolve_native_location(event["location"])[:_MAX_LOCATION_LEN],
            **kwargs,
        )
    except discord.HTTPException:
        log.warning("活動 %s 同步更新原生活動失敗", event["id"], exc_info=True)
        return False
    return True


async def sync_cancel(guild: discord.Guild, discord_event_id: int) -> bool:
    """取消活動時一併取消對應的原生 Scheduled Event。"""
    scheduled = await _resolve_scheduled_event(guild, discord_event_id)
    if scheduled is None:
        return False

    try:
        await scheduled.cancel()
    except discord.HTTPException:
        log.warning("取消原生活動 %s 失敗", discord_event_id, exc_info=True)
        return False
    return True
