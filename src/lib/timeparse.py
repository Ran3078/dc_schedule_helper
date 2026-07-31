"""時間輸入解析與 Discord 時間戳格式化。

MVP 只收固定格式的時間輸入（自然語言解析留待 Phase 2，見 PLAN.md §6）。
支援：
    2026-08-01 20:00   2026/08/01 20:00   （完整年份）
    8/1 20:00          8-1 20:00          （省略年份，用當前年份，若已過則捲到明年）

所有解析結果一律回傳 **UTC epoch 毫秒**（見 001_init.sql 的欄位慣例），顯示時
一律交給 Discord 動態時間戳 `<t:epoch:F>`，由 Discord 客戶端自動換算成每個人的
本地時區 —— 這裡完全不需要、也不應該自己算「對方在哪個時區看到幾點」。
"""

from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# 有年份的格式優先比對，避免 "2026-08-01" 被省略年份的規則誤吃。
_FULL_DATE_FORMATS = ("%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M")

# 省略年份的格式改用 regex 而非 strptime：Python 3.13 起，strptime 解析
# 「沒給年份」的日期會發 DeprecationWarning（3.15 起行為將變更，且對 2/29
# 這類閏年日期的預設年份處理本來就有歧義）。年份由呼叫端決定，這裡不需要
# strptime 幫忙猜年份，直接用 regex 抓月日時分即可。
_MONTH_DAY_RE = re.compile(r"^(\d{1,2})[/-](\d{1,2})\s+(\d{1,2}):(\d{2})$")

SUPPORTED_FORMATS_HELP = (
    "支援的時間格式：\n"
    "・2026-08-01 20:00（含年份）\n"
    "・2026/08/01 20:00\n"
    "・8/1 20:00（省略年份，用今年；若已過則自動抓明年）\n"
    "・8-1 20:00"
)


class TimeParseError(ValueError):
    """使用者輸入的時間格式無法解析。訊息可直接顯示給使用者看。"""


def _resolve_tz(tz_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError as exc:
        # 理論上不該發生：guild_settings.default_tz 在 config.py 已驗證過。
        # 若真的發生，代表資料被外部改壞，訊息要指向資料而非使用者輸入。
        raise TimeParseError(
            f"伺服器時區設定 {tz_name!r} 無效，請聯絡管理員用 /settings 修正"
        ) from exc


def parse_datetime(text: str, tz_name: str, *, now: datetime | None = None) -> int:
    """把使用者輸入的時間字串解析成 UTC epoch 毫秒。

    Args:
        text: 使用者輸入，例如 "8/1 20:00"。
        tz_name: 解析基準時區（該伺服器的 default_tz，或使用者的個人覆寫）。
        now: 供測試注入固定的「現在時間」；未指定則用系統時間。

    Raises:
        TimeParseError: 格式不符或年月日不合法（例如 2 月 30 日）。
    """
    cleaned = text.strip()
    if not cleaned:
        raise TimeParseError(f"時間不能是空的。\n{SUPPORTED_FORMATS_HELP}")

    tz = _resolve_tz(tz_name)
    reference = now.astimezone(tz) if now is not None else datetime.now(tz)

    for fmt in _FULL_DATE_FORMATS:
        try:
            naive = datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
        localized = naive.replace(tzinfo=tz)
        return int(localized.timestamp() * 1000)

    match = _MONTH_DAY_RE.match(cleaned)
    if match:
        month, day, hour, minute = (int(g) for g in match.groups())
        try:
            candidate = datetime(reference.year, month, day, hour, minute, tzinfo=tz)
        except ValueError as exc:
            # 月/日/時/分本身不合法（例如 13 月、2/30、25 時）
            raise TimeParseError(f"「{text}」不是合法的日期時間：{exc}") from exc

        # 省略年份時直觀的解讀是「未來最近的那一次」：日期已過就當作明年。
        # 5 分鐘緩衝避免「現在正好是這個時間」被誤判成已過去。
        if candidate.timestamp() < reference.timestamp() - 300:
            candidate = candidate.replace(year=reference.year + 1)
        return int(candidate.timestamp() * 1000)

    raise TimeParseError(f"無法解析時間「{text}」。\n{SUPPORTED_FORMATS_HELP}")


_DURATION_RE = re.compile(r"^\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?\s*$", re.IGNORECASE)


def parse_duration_minutes(text: str) -> int:
    """解析活動時長，例如 "2h"、"90m"、"1h30m"。回傳分鐘數。"""
    match = _DURATION_RE.match(text)
    if not match or not any(match.groups()):
        raise TimeParseError(f"無法解析時長「{text}」，範例：2h、90m、1h30m")

    hours, minutes = match.groups()
    return int(hours or 0) * 60 + int(minutes or 0)


def discord_timestamp(epoch_ms: int, style: str = "F") -> str:
    """Discord 動態時間戳，客戶端會自動換算成檢視者的本地時區。

    style: f=簡短日期時間 F=完整 d=簡短日期 D=完整日期 t=簡短時間 T=完整時間 R=相對時間
    """
    return f"<t:{epoch_ms // 1000}:{style}>"
