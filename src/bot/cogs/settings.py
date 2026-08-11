"""`/settings`／`/timezone`：讓 `guild_settings`／`user_prefs` 這兩張表變成
使用者自己能調，不用再直接改資料庫（`PROCESS.md` 記錄過的已知限制，這輪補上）。

`PLAN.md` §8 原始專案結構規劃把這兩個指令放同一個檔案，這裡沿用。`/settings`
本身是平面指令（`channel`／`tz`／`reminders`... 都是同一個指令底下的參數，不是
子指令），所以用普通 `commands.Cog`；`/timezone set <tz>` 讀起來就是「群組 +
子指令」的樣子，跟 `/event`／`/poll` 一樣用 `GroupCog`。

`guild_settings.locale` 這輪刻意不開放——沒有任何 i18n 基礎建設，所有訊息都是
寫死的繁體中文字串，開放這個欄位只會誤導使用者以為調了有效果。
"""

from __future__ import annotations

import logging
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from discord import app_commands
from discord.ext import commands

from src.db import repo
from src.domain.reminders import parse_default_reminders

log = logging.getLogger(__name__)


def _validate_tz(tz: str) -> str | None:
    """回傳錯誤訊息；合法時回傳 None。"""
    try:
        ZoneInfo(tz)
    except ZoneInfoNotFoundError:
        return f"「{tz}」不是合法的時區字串，要用 IANA 格式，例如 Asia/Taipei。"
    return None


class Settings(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="settings", description="調整伺服器設定")
    @app_commands.describe(
        channel="公告頻道（選填，設定後 /event create、/poll create 都會發到這裡）",
        tz="伺服器預設時區，例如 Asia/Taipei（選填）",
        reminders="預設提醒，提前幾分鐘、逗號分隔，例如 1440,60,10（選填）",
        organizer_role="限定能建立/管理活動的身分組（選填）",
        clear_organizer_role="清空上面那個限制，恢復成所有人皆可（選填，預設否）",
        everyone_ping="是否允許 @everyone 通知（選填）",
        sync_native_events="是否同步到 Discord 原生「活動」分頁（選填）",
    )
    @app_commands.guild_only()
    async def settings(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
        tz: str | None = None,
        reminders: str | None = None,
        organizer_role: discord.Role | None = None,
        clear_organizer_role: bool = False,
        everyone_ping: bool | None = None,
        sync_native_events: bool | None = None,
    ) -> None:
        # 邏輯拆到 _settings_impl：方便用假 interaction 直接測，不用真的觸發
        # send_message（比照 events.py／polls.py 的 _impl 拆法）。
        await self._settings_impl(
            interaction,
            channel,
            tz,
            reminders,
            organizer_role,
            clear_organizer_role,
            everyone_ping,
            sync_native_events,
        )

    async def _settings_impl(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None,
        tz: str | None,
        reminders: str | None,
        organizer_role: discord.Role | None,
        clear_organizer_role: bool,
        everyone_ping: bool | None,
        sync_native_events: bool | None,
    ) -> None:
        assert interaction.guild_id is not None  # guild_only() 保證

        if tz is not None:
            error = _validate_tz(tz)
            if error:
                await interaction.response.send_message(error, ephemeral=True)
                return

        fields: dict[str, object] = {}
        lines: list[str] = []

        if channel is not None:
            fields["announce_channel_id"] = str(channel.id)
            lines.append(f"公告頻道 → {channel.mention}")
        if tz is not None:
            fields["default_tz"] = tz
            lines.append(f"預設時區 → `{tz}`")
        if reminders is not None:
            fields["default_reminders"] = reminders
            parsed = parse_default_reminders(reminders)
            lines.append(
                f"預設提醒 → 開始前 {parsed} 分鐘"
                if parsed
                else "預設提醒 → （解析不出任何有效值，請確認格式，例如 1440,60,10）"
            )
        if clear_organizer_role:
            fields["organizer_role_id"] = None
            lines.append("管理員身分組 → 已清空（所有人皆可建立/管理活動）")
        elif organizer_role is not None:
            fields["organizer_role_id"] = str(organizer_role.id)
            lines.append(f"管理員身分組 → {organizer_role.mention}")
        if everyone_ping is not None:
            fields["allow_everyone_ping"] = int(everyone_ping)
            lines.append(f"@everyone 通知 → {'開啟' if everyone_ping else '關閉'}")
        if sync_native_events is not None:
            fields["sync_native_events"] = int(sync_native_events)
            lines.append(f"同步原生活動分頁 → {'開啟' if sync_native_events else '關閉'}")

        if not fields:
            await interaction.response.send_message(
                "沒有帶任何要調整的參數，設定沒有變動。", ephemeral=True
            )
            return

        # 正常情況下 on_guild_join／on_ready 早就建好這一列，這裡自癒一次
        # （比照 cogs/_shared.guild_tz 同樣的防禦理由），ensure_guild 是
        # INSERT OR IGNORE，不會覆寫已經存在的設定。
        await repo.ensure_guild(interaction.guild_id, "Asia/Taipei")
        await repo.update_guild_settings(interaction.guild_id, **fields)

        await interaction.response.send_message(
            "✅ 設定已更新：\n" + "\n".join(f"・{line}" for line in lines), ephemeral=True
        )


class Timezone(commands.GroupCog, group_name="timezone", group_description="個人時區"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="set", description="設定你自己的時區（只影響你輸入時間時怎麼解讀）")
    @app_commands.describe(tz="IANA 時區字串，例如 Asia/Taipei、Asia/Tokyo、America/Los_Angeles")
    async def set_tz(self, interaction: discord.Interaction, tz: str) -> None:
        await self._set_impl(interaction, tz)

    async def _set_impl(self, interaction: discord.Interaction, tz: str) -> None:
        error = _validate_tz(tz)
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return

        await repo.set_user_tz(interaction.user.id, tz)
        await interaction.response.send_message(
            f"✅ 已將你的時區設成 `{tz}`。之後你打的時間、挑的時段都會用這個時區解讀，"
            "不影響已經建立好的活動。",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Settings(bot))
    await bot.add_cog(Timezone(bot))
