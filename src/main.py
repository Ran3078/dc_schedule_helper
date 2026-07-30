"""進程入口。

一個進程同時跑三件事，全在同一個 asyncio 事件迴圈上：
  1. Discord gateway 連線（discord.py）
  2. 保活用的 aiohttp HTTP server（/healthz）
  3. 提醒派送的 tasks.loop（M4 起，由 cog 帶入）

啟動順序刻意如此：先跑 migration，再開 HTTP server，最後連 gateway。
HTTP server 要早於 gateway 起來，Render 的 health check 才不會在冷啟動期間誤判失敗。
"""

from __future__ import annotations

import asyncio
import logging
import sys

from src.bot.client import ScheduleBot
from src.config import get_settings
from src.db import engine
from src.db.migrate import run_migrations
from src.http.health import start_health_server

log = logging.getLogger(__name__)


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,  # Render 從 stdout 收 log
    )
    # discord.py 的 INFO 太吵，只留警告
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("discord.http").setLevel(logging.WARNING)


async def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)

    log.info("啟動 dc_schedule bot")

    await run_migrations()

    bot = ScheduleBot(settings)
    runner = await start_health_server(bot, settings.port)

    try:
        await bot.start(settings.discord_token)
    finally:
        log.info("關閉中…")
        await bot.close()
        await runner.cleanup()
        await engine.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
