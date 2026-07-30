"""Turso / libSQL 連線層。

★ 本模組是整個專案最關鍵的約束所在，動手改之前先讀完這段：

Turso 官方 Python 驅動 `libsql` 是**同步**的，而 discord.py 跑在 asyncio 事件迴圈上。
若在 command handler 裡直接呼叫 `conn.execute(...)`，HTTP 往返會**阻塞事件迴圈**，
導致 gateway 心跳超時、bot 被 Discord 斷線。

因此本模組的規則是：

1. 對外只暴露 `async def` 介面（`query_all` / `query_one` / `execute` / `execute_many`
   / `transaction`），內部一律透過 `asyncio.to_thread()` 丟到 worker thread 執行。
2. sqlite3-like 的連線物件並非 thread-safe，而 `to_thread` 會用 thread pool 的不同
   執行緒。這裡用「單一連線 + threading.Lock」把 DB 存取序列化：以本專案的用量
   （每分鐘幾十次查詢）完全足夠，且徹底消除所有 thread-safety 疑慮，也避免連線頻繁
   重建。
3. **禁止**在 cog / view / domain 層直接 import libsql 或碰 raw connection。

日後 Turso 若推出 async 驅動，只需改本模組內層，上層一行都不用動。

── 驅動已知行為（libsql 0.1.11，實測結果）─────────────────────────────
* 唯一鍵/主鍵衝突拋的是**普通的 `ValueError`**（訊息形如
  "UNIQUE constraint failed: t.a, t.b"），而**不是** DBAPI 的 `IntegrityError`；
  libsql 模組本身只暴露一個 `libsql.Error`。
  → 因此不要用例外型別去判斷衝突。需要 upsert 語意時一律走 SQL 層：
    `INSERT OR IGNORE`、`INSERT ... ON CONFLICT (...) DO UPDATE SET ...`。
* execute() 一次只吃一句 SQL，多句要自己切（見 migrate.py）。
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable, Iterable, Sequence
from typing import Any

import libsql

from src.config import get_settings

log = logging.getLogger(__name__)

Row = dict[str, Any]
Params = Sequence[Any] | None

_conn: Any = None
_lock = threading.Lock()


def _connect() -> Any:
    settings = get_settings()
    log.info("連線 Turso: %s", settings.turso_database_url)
    return libsql.connect(
        database=settings.turso_database_url,
        auth_token=settings.turso_auth_token,
    )


def _conn_locked() -> Any:
    """在已持有 _lock 的情況下取得連線（必要時建立）。"""
    global _conn
    if _conn is None:
        _conn = _connect()
    return _conn


def _rows_from_cursor(cur: Any) -> list[Row]:
    """把 cursor 結果轉成 dict list。

    libsql 回傳的是 tuple，用 cursor.description 取欄位名。若驅動未提供
    description（理論上不該發生），退回 index 當 key，至少不會炸掉。
    """
    fetched = cur.fetchall()
    if not fetched:
        return []
    description = getattr(cur, "description", None)
    if not description:
        return [{str(i): v for i, v in enumerate(row)} for row in fetched]
    columns = [d[0] for d in description]
    return [dict(zip(columns, row, strict=False)) for row in fetched]


def _run_sync[T](fn: Callable[[Any], T]) -> T:
    global _conn
    with _lock:
        conn = _conn_locked()
        try:
            return fn(conn)
        except Exception:
            # 連線可能已失效（Render 休眠喚醒、Turso 端斷線）。丟棄後下次重建。
            log.exception("DB 操作失敗，丟棄連線以便下次重建")
            _conn = None
            raise


async def run[T](fn: Callable[[Any], T]) -> T:
    """把一個接收 raw connection 的同步函式丟到 worker thread 執行。

    需要在單一交易內做多件事時使用，例如：

        def _swap(conn):
            conn.execute("DELETE FROM poll_votes WHERE poll_id=? AND user_id=?", (p, u))
            conn.execute("INSERT INTO poll_votes ... VALUES (?,?,?,?)", (...))
            conn.commit()

        await engine.run(_swap)
    """
    return await asyncio.to_thread(_run_sync, fn)


async def query_all(sql: str, params: Params = None) -> list[Row]:
    def _q(conn: Any) -> list[Row]:
        return _rows_from_cursor(conn.execute(sql, tuple(params or ())))

    return await run(_q)


async def query_one(sql: str, params: Params = None) -> Row | None:
    rows = await query_all(sql, params)
    return rows[0] if rows else None


async def query_scalar(sql: str, params: Params = None) -> Any:
    row = await query_one(sql, params)
    if row is None:
        return None
    return next(iter(row.values()), None)


async def execute(sql: str, params: Params = None) -> int:
    """執行單一寫入語句並 commit，回傳受影響列數。

    回傳值可當樂觀鎖用，例如提醒派送：
        UPDATE reminders SET state='sent' WHERE id=? AND state='pending'
    rowcount == 0 表示已被其他人搶走，不要重複發送。
    """

    def _e(conn: Any) -> int:
        cur = conn.execute(sql, tuple(params or ()))
        conn.commit()
        rowcount = getattr(cur, "rowcount", -1)
        return rowcount if isinstance(rowcount, int) else -1

    return await run(_e)


async def execute_many(sql: str, seq_params: Iterable[Sequence[Any]]) -> None:
    batch = [tuple(p) for p in seq_params]
    if not batch:
        return

    def _e(conn: Any) -> None:
        for params in batch:
            conn.execute(sql, params)
        conn.commit()

    await run(_e)


async def transaction(statements: Sequence[tuple[str, Params]]) -> None:
    """把多個語句包在同一次 commit 內。"""
    prepared = [(sql, tuple(params or ())) for sql, params in statements]

    def _t(conn: Any) -> None:
        for sql, params in prepared:
            conn.execute(sql, params)
        conn.commit()

    await run(_t)


async def ping() -> None:
    """健康檢查與 /ping 指令用的最小往返。"""
    await query_scalar("SELECT 1")


def _close_sync() -> None:
    """直接操作連線，不經過 _conn_locked()，避免關閉時反而重建一條新連線。"""
    global _conn
    with _lock:
        if _conn is None:
            return
        try:
            _conn.close()
        finally:
            _conn = None


async def close() -> None:
    try:
        await asyncio.to_thread(_close_sync)
    except Exception:
        log.warning("關閉 DB 連線時發生例外，忽略", exc_info=True)
