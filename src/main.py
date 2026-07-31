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

import discord

from src.bot.client import ScheduleBot
from src.config import get_settings
from src.db import engine
from src.db.migrate import run_migrations
from src.http.health import start_health_server

log = logging.getLogger(__name__)


def setup_logging(level: str) -> None:
    # Windows console 預設是 cp950，中文 log 會變成亂碼。
    # Render 跑 Linux 本來就是 UTF-8，這行只影響本機開發。
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

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
    except discord.LoginFailure:
        # Discord 對錯誤的 token 只回一句 "Improper token has been passed."，
        # 加上 30 行 traceback 反而蓋掉重點。這裡直接給可執行的排查步驟。
        log.error(
            "Discord 登入失敗：token 無效。請依序確認：\n"
            "  1. 是不是複製到 Application ID（純數字）或 Client Secret？"
            "bot token 是三段以 '.' 分隔的字串\n"
            "  2. 是不是在 Developer Portal 按過 Reset Token？舊 token 會立刻失效\n"
            "  3. 貼進環境變數時前後是否混到空白、換行或引號\n"
            "  → Developer Portal → Bot → Reset Token 重新取得並更新 DISCORD_TOKEN"
        )
        raise SystemExit(1) from None
    except discord.PrivilegedIntentsRequired:
        log.error(
            "Discord 拒絕連線：未開啟必要的特權 intent。\n"
            "  → Developer Portal → Bot → Privileged Gateway Intents\n"
            "  → 開啟 SERVER MEMBERS INTENT（展開角色成員、計算未回覆者需要）"
        )
        raise SystemExit(1) from None
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
