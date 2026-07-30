"""時間取得。集中在一處，測試才好固定時間。"""

from __future__ import annotations

from datetime import UTC, datetime


def now_ms() -> int:
    """現在時間，UTC epoch 毫秒。

    專案內所有時間欄位都用這個單位 —— 見 001_init.sql 的欄位慣例說明。
    """
    return int(datetime.now(UTC).timestamp() * 1000)
