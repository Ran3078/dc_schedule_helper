"""邀請對象展開：把 `event_invitees` 的列（user/role/everyone）展開成
實際會被算進「未回覆」名單的成員 ID 集合。

展開身分組需要 `discord.Guild` 的成員快取，這正是本專案一開始就要求開啟
Server Members Intent 的原因（見 bot/client.py）——沒有這個 intent，
`role.members` 會是空的，「未回覆」永遠算不出人。
"""

from __future__ import annotations

from typing import Any

import discord

Row = dict[str, Any]


def expand_invited_members(guild: discord.Guild, invitees: list[Row]) -> set[int]:
    """展開後排除 bot 帳號 —— 尤其是 @everyone 展開時，不排除的話會把伺服器
    裡的其他 bot 也算進「未回覆」名單，那沒有意義（bot 不會按 RSVP 按鈕）。
    """
    pool: set[int] = set()
    for row in invitees:
        target_type = row["target_type"]
        if target_type == "user":
            pool.add(int(row["target_id"]))
        elif target_type == "role":
            role = guild.get_role(int(row["target_id"]))
            if role is not None:
                pool.update(member.id for member in role.members if not member.bot)
        elif target_type == "everyone":
            pool.update(member.id for member in guild.members if not member.bot)
    return pool
