"""提醒排定的純邏輯：從 `guild_settings.default_reminders` 這種逗號分隔字串
解析出提前幾分鐘提醒，以及判斷一個逾期提醒該補發還是該放棄。

實際的資料庫寫入在 repo.py（`create_event` 的 `reminder_offsets_min` 參數），
排程迴圈在 bot/cogs/scheduler.py —— 這裡只放不碰資料庫、不碰 Discord API
的計算規則，方便單獨測試。
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# 提醒逾時多久就放棄補發、只在活動仍未開始時才發「即將開始」通知。
# 15 分鐘是刻意選的緩衝：比這個還舊的提醒多半代表 bot 停機了好一陣子，
# 這時候「活動前 X 分鐘」這句話已經失真，不如乾脆不發。
OVERDUE_GRACE_MS = 15 * 60_000

DEFAULT_REMINDERS_CSV = "1440,60,10"  # 1 天、1 小時、10 分鐘前


def parse_default_reminders(csv: str | None) -> list[int]:
    """解析 "1440,60,10" 這種逗號分隔字串成分鐘數清單。

    對壞資料寬容：非數字、負數、0 的項目直接跳過並記警告，而不是讓整個
    活動建立流程因為 guild_settings 裡一個打錯的設定值而失敗。空字串或
    None 一律退回預設值，而不是「不排任何提醒」——沒有提醒通常不是使用者
    的本意，比較可能是設定被清空了。
    """
    if not csv or not csv.strip():
        csv = DEFAULT_REMINDERS_CSV

    offsets: list[int] = []
    for token in csv.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            minutes = int(token)
        except ValueError:
            log.warning("default_reminders 裡有無法解析的項目，已略過：%r", token)
            continue
        if minutes <= 0:
            log.warning("default_reminders 裡有非正數的項目，已略過：%r", minutes)
            continue
        offsets.append(minutes)

    return offsets


def is_overdue(*, fire_at_utc: int, now_ms: int) -> bool:
    return (now_ms - fire_at_utc) > OVERDUE_GRACE_MS


def should_skip_overdue_reminder(*, fire_at_utc: int, starts_at_utc: int, now_ms: int) -> bool:
    """逾期提醒該不該直接放棄不發。

    規則：逾期，且活動本身也已經開始（或已結束）—— 才放棄。逾期但活動還沒
    開始的話，仍然值得補發一次「即將開始」通知（見 scheduler.py 如何組訊息：
    用 Discord 動態時間戳 <t:...:R>，不管實際送達時間多晚，顯示的相對時間
    永遠是正確的，不需要為這個情境另外寫一套文案）。
    """
    return is_overdue(fire_at_utc=fire_at_utc, now_ms=now_ms) and starts_at_utc <= now_ms
