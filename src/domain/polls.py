"""投票的純邏輯：選項字串解析、票數統計。

不碰 DB／Discord，方便單元測試。實際的資料庫寫入在 repo.py，Discord 互動在
bot/views_poll.py 與 bot/cogs/polls.py。
"""

from __future__ import annotations

import re
from typing import Any

Row = dict[str, Any]

# Discord Select 元件單一訊息最多 25 個選項，這是硬上限，不是我們選的數字。
MIN_OPTIONS = 2
MAX_OPTIONS = 25

# 選項分隔字元：換行（Modal 多行文字框一行一個選項，主要輸入方式）或 `|`
# （沿用早期單行指令參數的寫法，兩種混用也允許）。
_SEPARATOR_RE = re.compile(r"[|\n]+")


def split_options(raw: str) -> list[str]:
    """把多行或用 `|` 分隔的字串切成選項清單。

    去除每個選項前後的空白，並丟掉切出來是空字串的項目（例如相鄰的分隔符號、
    字串頭尾多打的換行/`|`、文字框裡的空白行），不逐一報錯 —— 呼叫端只需要對
    「切完剩幾個」做數量檢查即可。
    """
    return [part.strip() for part in _SEPARATOR_RE.split(raw) if part.strip()]


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


def all_voter_ids(votes: list[Row]) -> list[int]:
    """這個投票裡所有投過票的人，不分投給哪個選項，已去重排序。

    用在時段投票關閉、自動建立活動時決定參加對象——誰投了票就代表誰在意
    這個活動，不分他投的是不是最後獲勝的那個時段。
    """
    return sorted({int(v["user_id"]) for v in votes})


def pick_winning_time_slot(
    options: list[Row], tally: PollTally
) -> tuple[Row | None, str, list[Row]]:
    """時段投票關閉時，挑出票數最高的選項。

    回傳 `(獲勝的 option 或 None, 原因代碼, 並列最高票的選項清單)`：

    - `"ok"`：唯一最高票 → `(winner, "ok", [winner])`
    - `"no_votes"`：完全沒人投票（或根本沒有選項）→ `(None, "no_votes", [])`
    - `"tie"`：最高票有 2 個以上選項並列 → `(None, "tie", 那些並列的選項)`——
      刻意不自動選一個（例如挑排最前面那個）。平票時系統幫大家「隨便選一個」
      比不自動建立更容易引發爭議，交回給使用者自己判斷、手動 `/event create`。
    """
    if not options:
        return None, "no_votes", []

    max_count = max(len(tally.get(o["id"], [])) for o in options)
    if max_count == 0:
        return None, "no_votes", []

    top = [o for o in options if len(tally.get(o["id"], [])) == max_count]
    if len(top) > 1:
        return None, "tie", top
    return top[0], "ok", top
