"""倒數提醒的純邏輯：算「還剩幾天」跟「今天該不該發」。不碰 DB、不碰
Discord API，方便單獨測試。

跟 `domain/weekly_digest.py` 同一種「真相在 DB、不用記憶體排程」精神：
`last_sent_utc` 記上次成功發送的時間點，任務迴圈每次醒來都重新判斷今天
發過了沒，bot 重啟/休眠喚醒後自動接續。差別是每個倒數各自有自己的每天
發送時間（`send_hour`/`send_minute`），不像 weekly_digest 全伺服器共用
固定的週日 00:00 邊界。
"""

from __future__ import annotations

from datetime import datetime


def days_remaining(target_date_utc: int, now_local: datetime) -> int:
    """算距離目標日期還剩幾天，以「日期」比較，不看時分秒。

    目標日期當天回傳 0，之後回傳負數（呼叫端據此判斷該不該停止發送，見
    這輪確認過的產品決策：發送當天的訊息後自動停止，不會變成負數一直發）。
    """
    target_local = datetime.fromtimestamp(target_date_utc / 1000, tz=now_local.tzinfo)
    return (target_local.date() - now_local.date()).days


def should_send_today(
    send_hour: int, send_minute: int, last_sent_utc: int | None, now_local: datetime
) -> bool:
    """今天這個倒數該不該發：現在的時刻已經到了每天固定的發送時間，且
    今天（依 `now_local` 所在時區的日期）還沒發過。

    `last_sent_utc` 是 `None`（從未發送過）一律視為「今天還沒發」。
    """
    if (now_local.hour, now_local.minute) < (send_hour, send_minute):
        return False
    if last_sent_utc is None:
        return True

    last_sent_local = datetime.fromtimestamp(last_sent_utc / 1000, tz=now_local.tzinfo)
    return last_sent_local.date() < now_local.date()
