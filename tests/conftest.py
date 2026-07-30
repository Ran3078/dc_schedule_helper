"""測試共用 fixture。

測試一律跑在本機 SQLite 檔上 —— libsql 對本機路徑就是普通 SQLite，
所以測試不需要真的 Turso 憑證，也不會碰到雲端資料。
"""

from __future__ import annotations

import os

import pytest


def _set_test_env(db_path: str) -> None:
    os.environ.update(
        DISCORD_TOKEN="test-token",
        DISCORD_APP_ID="123456789012345678",
        GUILD_ID="987654321098765432",
        TURSO_DATABASE_URL=db_path,
        TURSO_AUTH_TOKEN="",
        DEFAULT_TZ="Asia/Taipei",
        LOG_LEVEL="WARNING",
    )


@pytest.fixture
async def db(tmp_path):
    """乾淨的、已套用 migration 的資料庫。每個測試各自獨立。"""
    _set_test_env(str(tmp_path / "test.db"))

    from src.config import get_settings
    from src.db import engine
    from src.db.migrate import run_migrations

    get_settings.cache_clear()
    await engine.close()

    await run_migrations()
    yield engine
    await engine.close()
    get_settings.cache_clear()
