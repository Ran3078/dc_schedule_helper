"""投票的純邏輯：選項字串解析、票數統計。

不碰 DB／Discord，方便單元測試。實際的資料庫寫入在 repo.py，Discord 互動在
bot/views_poll.py 與 bot/cogs/polls.py。
"""

from __future__ import annotations

from typing import Any

Row = dict[str, Any]

# Discord Select 元件單一訊息最多 25 個選項，這是硬上限，不是我們選的數字。
MIN_OPTIONS = 2
MAX_OPTIONS = 25


def split_options(raw: str) -> list[str]:
    """把 `"A|B|C"` 這種用 `|` 分隔的字串切成選項清單。

    去除每個選項前後的空白，並丟掉切出來是空字串的項目（例如相鄰的
    `||` 或字串頭尾多打的 `|`），不逐一報錯 —— 呼叫端只需要對「切完剩幾個」
    做數量檢查即可。
    """
    return [part.strip() for part in raw.split("|") if part.strip()]


PollTally = dict[str, list[int]]


def build_tally(options: list[Row], votes: list[Row]) -> PollTally:
    """把 `poll_votes` 的列依 `option_id` 分組成 user_id 清單。

    沒人投的 option 也會是 key（對應空清單），這樣 embed 才能穩定顯示每個
    選項的「0 票」，不必另外判斷 key 存不存在。
    """
    tally: PollTally = {option["id"]: [] for option in options}
    for vote in votes:
        option_id = vote["option_id"]
        if option_id in tally:
            tally[option_id].append(int(vote["user_id"]))
    return tally
