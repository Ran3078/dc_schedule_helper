"""活動同步到 Discord 原生「活動」分頁（M6）的純邏輯。

不碰 DB／Discord，方便單元測試。實際呼叫 Discord API 的部分在
`bot/native_events.py`（`sync_create`），Discord 互動事件的雙向同步在
`bot/cogs/native_events.py`。
"""

from __future__ import annotations

# 沒填 duration（ends_at_utc 是 None）時，原生活動預設抓開始後 2 小時——
# Discord 的 EXTERNAL 活動類型強制要求結束時間，不能讓使用者被迫填一個
# 我們自己標成「選填」的欄位。
DEFAULT_DURATION_MINUTES = 120

# 活動沒填地點時的預設值——EXTERNAL 活動類型的 location 欄位是必填、
# 不能是空字串，我們自己的 events.location 卻是選填，需要一個保底文字。
DEFAULT_LOCATION_TEXT = "詳見活動公告"


def resolve_native_end_time(starts_at_utc: int, ends_at_utc: int | None) -> int:
    """原生活動的結束時間：有 `ends_at_utc` 就直接用，沒有就抓開始後
    `DEFAULT_DURATION_MINUTES` 分鐘。"""
    if ends_at_utc is not None:
        return ends_at_utc
    return starts_at_utc + DEFAULT_DURATION_MINUTES * 60_000


def resolve_native_location(location: str | None) -> str:
    """原生活動的地點：有填就用原值，沒填就用固定的保底文字。"""
    return location or DEFAULT_LOCATION_TEXT
