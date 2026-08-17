"""每週活動清單（M9）的純邏輯：算「最近一次週日 00:00」是什麼時候。不碰
DB、不碰 Discord API，方便單獨測試。

週期性任務判斷「這週發過了沒」的作法：拿伺服器當地時間現在的時刻，算出
「最近一次週日 00:00」（可能是今天，如果 `now_local` 剛好落在週日；也可能
是本週稍早），跟 `guild_settings.last_weekly_digest_at` 比較——上次發送時間
早於這個邊界，代表這週還沒發過。
"""

from __future__ import annotations

from datetime import datetime, timedelta


def most_recent_sunday_midnight(now_local: datetime) -> datetime:
    """算出 `now_local` 所在時區「最近一次週日 00:00」。

    `datetime.weekday()`：週一=0、週二=1...週日=6。`now_local` 本身若是
    週日，`days_since_sunday` 算出來是 0，回傳今天 00:00（而不是往前推
    整整一週）——這是刻意的：任務迴圈本來就是每 5 分鐘跑一次，週日一過
    00:00 就該算出「今天 00:00」這個邊界，才能立刻判斷出「這週還沒發」。
    """
    days_since_sunday = (now_local.weekday() - 6) % 7
    return (now_local - timedelta(days=days_since_sunday)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
