"""ID 產生與 Discord custom_id 編解碼。"""

from __future__ import annotations

import secrets

# 去掉容易看錯的字元（0/O、1/l/I），方便使用者在 /event info <id> 手打
_ALPHABET = "23456789abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ"


def new_id(size: int = 10) -> str:
    """nanoid 風格短 ID。10 碼在本專案規模下碰撞機率可忽略。"""
    return "".join(secrets.choice(_ALPHABET) for _ in range(size))


# ── Discord custom_id ────────────────────────────────────────────────────
# Discord 的 custom_id 上限 100 字元。持久化 View 重啟後沒有記憶體狀態可用，
# 所有必要資訊都得編在 custom_id 裡，例如 "ev:rsvp:aB3dE5fG7h:yes"。

SEP = ":"
MAX_CUSTOM_ID = 100


def build_custom_id(*parts: str) -> str:
    cid = SEP.join(parts)
    if len(cid) > MAX_CUSTOM_ID:
        raise ValueError(f"custom_id 超過 {MAX_CUSTOM_ID} 字元上限: {cid!r}")
    if any(SEP in p for p in parts):
        raise ValueError(f"custom_id 的組成部分不可含分隔符 {SEP!r}: {parts!r}")
    return cid


def parse_custom_id(custom_id: str) -> list[str]:
    return custom_id.split(SEP)
