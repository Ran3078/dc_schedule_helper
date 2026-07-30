"""Migration runner。

刻意不用 Alembic：schema 小、單人開發，而 Alembic 在 SQLite 上改欄位要走 batch mode，
複雜度不划算。這裡就是「編號 SQL 檔 + 已套用紀錄表」。

新增 migration：在 migrations/ 放 `002_xxx.sql`，開機自動套用。
Migration 必須寫成可重複執行（IF NOT EXISTS 等），因為 Render 每次 deploy 都會跑。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from src.db import engine

log = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

_SCHEMA_TABLE = """
CREATE TABLE IF NOT EXISTS _migrations (
  name       TEXT PRIMARY KEY,
  applied_at INTEGER NOT NULL
)
"""

# 去掉整行 `-- 註解`（本專案的 migration 不含字串內的 -- ，故此簡化是安全的）
_LINE_COMMENT = re.compile(r"^\s*--.*$", re.MULTILINE)


def _split_statements(sql: str) -> list[str]:
    """把 .sql 檔切成單一語句。

    libsql 的 execute() 一次只吃一句。這裡用單純的分號切割 —— 前提是 migration 檔
    不含 trigger / BEGIN...END 或字串內的分號。若日後真的需要，改用 executescript()
    或改寫本函式，並在該 migration 檔頂端註明。
    """
    without_comments = _LINE_COMMENT.sub("", sql)
    return [s.strip() for s in without_comments.split(";") if s.strip()]


def _discover() -> list[Path]:
    files = sorted(MIGRATIONS_DIR.glob("*.sql"), key=lambda p: p.name)
    if not files:
        log.warning("migrations 目錄是空的: %s", MIGRATIONS_DIR)
    return files


async def run_migrations() -> None:
    await engine.execute(_SCHEMA_TABLE)

    rows = await engine.query_all("SELECT name FROM _migrations")
    applied = {row["name"] for row in rows}

    pending = [p for p in _discover() if p.name not in applied]
    if not pending:
        log.info("Migration 皆已套用（共 %d 個）", len(applied))
        return

    for path in pending:
        statements = _split_statements(path.read_text(encoding="utf-8"))
        log.info("套用 migration %s（%d 個語句）", path.name, len(statements))

        def _apply(conn: Any, _stmts: list[str] = statements, _name: str = path.name) -> None:
            for stmt in _stmts:
                conn.execute(stmt)
            conn.execute(
                "INSERT INTO _migrations (name, applied_at) VALUES (?, unixepoch() * 1000)",
                (_name,),
            )
            conn.commit()

        await engine.run(_apply)
        log.info("Migration %s 完成", path.name)
