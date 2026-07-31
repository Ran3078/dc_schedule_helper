"""RSVP 統計：把邀請名單與已回覆名單合併，算出參加／待定／不參加／未回覆四類。

「未回覆」只從邀請名單裡扣掉已回覆的人 —— 但誰能回覆完全不受邀請名單限制：
任何看得到公告訊息的人都能按按鈕表態，就算沒被明確標記。邀請名單的唯一用途
是拿來算「還有誰沒回覆」，不是拿來限制誰能參加。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import discord

from src.domain.invitees import expand_invited_members

Row = dict[str, Any]


@dataclass(frozen=True, slots=True)
class RsvpSummary:
    yes: list[int] = field(default_factory=list)
    maybe: list[int] = field(default_factory=list)
    no: list[int] = field(default_factory=list)
    # 邀請名單裡尚未回覆的人。沒有邀請名單（M2 略過參加對象）時這裡自然是
    # 空清單，跟「邀請名單裡的人都回覆了」無法區分 —— 兩種情況下都不顯示
    # 「未回覆」欄位是合理的行為，不需要額外的旗標去分辨。
    no_response: list[int] = field(default_factory=list)


def build_rsvp_summary(
    guild: discord.Guild, invitees: list[Row], rsvps: list[Row]
) -> RsvpSummary:
    invited_pool = expand_invited_members(guild, invitees)
    responded: dict[int, str] = {int(r["user_id"]): r["status"] for r in rsvps}

    yes = [uid for uid, status in responded.items() if status == "yes"]
    maybe = [uid for uid, status in responded.items() if status == "maybe"]
    no = [uid for uid, status in responded.items() if status == "no"]
    no_response = sorted(invited_pool - responded.keys())

    return RsvpSummary(yes=yes, maybe=maybe, no=no, no_response=no_response)
