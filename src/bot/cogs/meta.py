"""診斷用指令。/ping 同時驗證 gateway 與 DB，是 M0 的驗收工具。"""

from __future__ import annotations

import logging
import math
import time

import discord
from discord import app_commands
from discord.ext import commands

from src.db import engine, repo

log = logging.getLogger(__name__)


class Meta(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="ping", description="檢查 bot 與資料庫是否正常")
    async def ping(self, interaction: discord.Interaction) -> None:
        # 先 defer：DB 往返可能超過 Discord 的 3 秒回應期限。
        # ephemeral 讓診斷訊息只有自己看得到，不吵到頻道。
        await interaction.response.defer(ephemeral=True, thinking=True)

        gateway_ms = None if math.isnan(self.bot.latency) else round(self.bot.latency * 1000, 1)

        started = time.perf_counter()
        try:
            await engine.ping()
            db_ms: float | None = round((time.perf_counter() - started) * 1000, 1)
            db_line = f"✅ {db_ms} ms"
        except Exception:
            log.exception("/ping 的 DB 檢查失敗")
            db_line = "❌ 連線失敗（詳見 log）"

        embed = discord.Embed(title="🏓 Pong", colour=discord.Colour.blurple())
        embed.add_field(
            name="Gateway",
            value=f"✅ {gateway_ms} ms" if gateway_ms is not None else "⏳ 尚未連線",
            inline=True,
        )
        embed.add_field(name="資料庫（Turso）", value=db_line, inline=True)
        embed.add_field(name="服務中的伺服器", value=f"{len(self.bot.guilds)} 個", inline=True)

        # 順手驗證多伺服器設定有正確建立
        if interaction.guild_id is not None:
            settings = await repo.get_guild_settings(interaction.guild_id)
            embed.add_field(
                name="本伺服器設定",
                value=(
                    f"✅ 時區 `{settings['default_tz']}`"
                    if settings
                    else "⚠️ 尚未建立（重啟 bot 或重新邀請可修復）"
                ),
                inline=False,
            )

        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Meta(bot))
