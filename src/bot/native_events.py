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
