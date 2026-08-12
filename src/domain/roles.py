"""FF14 團本職位報名（M8）的純邏輯：位置代碼／職業對照表、選擇排序、
確定/候補人數統計。不碰 DB、不碰 Discord API，方便單獨測試。

八個固定位置代碼——`MT`／`ST`（坦克）、`H1`／`H2`（治療）、`D1`／`D2`
（近戰輸出）、`D3`（遠程物理輸出）、`D4`（遠程魔法輸出）——每個位置名額
固定 1 人，不像 M5 投票或 M2 邀請對象那樣是自由文字；這裡刻意不做成通用
「角色名稱+名額」機制，直接對應使用者實際的 FF14 團隊配置慣例。

職業表依 FF14 官方繁體中文版 7.0（黃金的遺產）版本頁核對，含該版本新增的
蝮蛇（Viper，近戰）與繪靈法師（Pictomancer，遠程魔法）。之後改版有新職業
要手動更新這裡——沒有自動同步官方資料的機制。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

Row = dict[str, Any]

POSITIONS: tuple[str, ...] = ("MT", "ST", "H1", "H2", "D1", "D2", "D3", "D4")

# D2 比 D1 多開放遠程魔法職業（使用者明確要求的配置彈性：近戰位置不夠時
# 可以用遠程魔法頂 D2），D1 維持純近戰。這不是遊戲規則本身的限制，是這個
# 伺服器慣用的分工方式。
JOBS_BY_POSITION: dict[str, tuple[str, ...]] = {
    "MT": ("騎士", "戰士", "暗黑騎士", "絕槍戰士"),
    "ST": ("騎士", "戰士", "暗黑騎士", "絕槍戰士"),
    "H1": ("白魔法師", "占星術士"),
    "H2": ("學者", "賢者"),
    "D1": ("武僧", "龍騎士", "忍者", "武士", "鐮刀客", "蝮蛇"),
    "D2": (
        "武僧", "龍騎士", "忍者", "武士", "鐮刀客", "蝮蛇",
        "黑魔法師", "召喚師", "赤魔法師", "繪靈法師",
    ),
    "D3": ("吟遊詩人", "機工士", "舞者"),
    "D4": ("黑魔法師", "召喚師", "赤魔法師", "繪靈法師"),
}

def sort_positions(positions: Iterable[str]) -> list[str]:
    """把 `Ff14RecruitModal` 職位下拉選單複選出來的結果（Discord 回傳的
    順序不保證跟 `POSITIONS` 一致）歸一成 `POSITIONS` 原本的順序，讓公告
    卡片上的欄位順序穩定，不會因為使用者這次勾選的順序不同而跳動。順便
    去重——Select 多選理論上不會出現重複值，這裡防禦性地處理。

    不驗證代碼是否合法：`Select` 的選項清單本身就是白名單（見
    `modals_ff14.Ff14RecruitModal`），送出的值不可能是清單以外的字串，
    不需要在這裡重複檢查。
    """
    return sorted(set(positions), key=POSITIONS.index)


def compute_position_counts(signups: list[Row]) -> dict[str, tuple[int, int]]:
    """把 `event_role_signups` 的列依 `role_slot_id` 分組，算出
    (確定人數, 候補人數)。純函式，不碰 DB——`PositionSelect` 組選項標籤
    跟 `build_event_embed` 顯示都靠這個。

    確定人數理論上不會超過 1（`repo.set_role_signup` 保證同一個
    `role_slot_id` 最多一個 `waitlisted=0` 的列），這裡不假設這個不變量、
    照樣數出實際人數，避免資料萬一不一致時這個純函式本身也跟著算錯。
    """
    counts: dict[str, tuple[int, int]] = {}
    for row in signups:
        slot_id = row["role_slot_id"]
        confirmed, waitlisted = counts.get(slot_id, (0, 0))
        if row["waitlisted"]:
            waitlisted += 1
        else:
            confirmed += 1
        counts[slot_id] = (confirmed, waitlisted)
    return counts
