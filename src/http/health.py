"""保活用的極簡 HTTP server。

存在的唯一理由：Render 免費 Web Service 閒置 15 分鐘就休眠，休眠會切斷 Discord
gateway 連線。外部 cron（cron-job.org / UptimeRobot 等）每 10 分鐘 GET /healthz
就能讓它保持醒著。

用 aiohttp 而非 FastAPI/Flask，因為 **aiohttp 本來就是 discord.py 的相依套件**，
不必多裝任何東西，也共用同一個事件迴圈。

端點設計刻意分成兩個：
  /healthz  —— 只證明「進程活著」，不碰 DB、不管 gateway 是否已連上。
               Render 的 health check 走這個：啟動初期 gateway 還沒連上，
               若這裡就回 503 會讓 Render 誤判 deploy 失敗。
  /readyz   —— 真正的深度檢查（gateway + DB 往返），給你自己排查問題用。
"""

from __future__ import annotations

import logging
import math
import time
from typing import TYPE_CHECKING

from aiohttp import web

from src.db import engine

if TYPE_CHECKING:
    from src.bot.client import ScheduleBot

log = logging.getLogger(__name__)

_STARTED_AT = time.monotonic()


def _uptime_seconds() -> int:
    return int(time.monotonic() - _STARTED_AT)


async def _healthz(_: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "uptime_s": _uptime_seconds()})


def _make_readyz(bot: ScheduleBot):
    async def _readyz(_: web.Request) -> web.Response:
        gateway_ok = bot.is_ready() and not bot.is_closed()

        db_ok = True
        db_latency_ms: float | None = None
        started = time.perf_counter()
        try:
            await engine.ping()
            db_latency_ms = round((time.perf_counter() - started) * 1000, 1)
        except Exception:
            log.exception("/readyz 的 DB 檢查失敗")
            db_ok = False

        payload = {
            "status": "ok" if (gateway_ok and db_ok) else "degraded",
            "uptime_s": _uptime_seconds(),
            "gateway": {
                "connected": gateway_ok,
                # 未連線時 bot.latency 是 nan，不能直接丟進 JSON
                "latency_ms": (
                    None if math.isnan(bot.latency) else round(bot.latency * 1000, 1)
                ),
            },
            "database": {"ok": db_ok, "latency_ms": db_latency_ms},
        }
        return web.json_response(payload, status=200 if payload["status"] == "ok" else 503)

    return _readyz


async def start_health_server(bot: ScheduleBot, port: int) -> web.AppRunner:
    app = web.Application()
    app.router.add_get("/healthz", _healthz)
    app.router.add_get("/readyz", _make_readyz(bot))
    app.router.add_get("/", _healthz)

    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    # 必須綁 0.0.0.0，Render 才連得進來
    site = web.TCPSite(runner, host="0.0.0.0", port=port)
    await site.start()
    log.info("保活 HTTP server 已啟動於 0.0.0.0:%d（/healthz, /readyz）", port)
    return runner
