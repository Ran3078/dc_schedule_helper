"""提醒派送排程 —— 每 30 秒輪詢一次到期的提醒。

刻意不用記憶體排程（例如把「下一個要發的提醒」存在某個 list 或 asyncio
定時器裡）—— Render 免費方案會因為休眠／deploy 頻繁重啟進程，記憶體狀態
一律會丟。真相全部在 `reminders` 表，這個迴圈每次醒來都重新從 DB 查
「現在該發哪些」，重啟後自動接續，不需要任何額外的復原邏輯。

逾期補償規則見 domain/reminders.py；重複發送的防護見 repo.claim_reminder()
的說明（先搶鎖再送出，而非送出後才標記）。
"""

from __future__ import annotations

import logging

import discord
from discord.ext import commands, tasks

from src.bot.embeds import build_reminder_embed
from src.db import repo
from src.db.repo import Row
from src.domain.reminders import should_skip_overdue_reminder
from src.lib.clock import now_ms
from src.lib.mentions import build_allowed_mentions, build_mention_content

log = logging.getLogger(__name__)

_TICK_SECONDS = 30
_BATCH_LIMIT = 50


class Scheduler(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.reminder_tick.start()

    async def cog_unload(self) -> None:
        self.reminder_tick.cancel()

    @tasks.loop(seconds=_TICK_SECONDS)
    async def reminder_tick(self) -> None:
        # ★ 這整個方法的例外都必須在這裡攔截，不能讓任何東西逃出去。
        #
        # discord.ext.tasks 的 @tasks.loop 預設只對一小組網路類例外
        # （OSError／ConnectionClosed／aiohttp.ClientError／TimeoutError）
        # 自動重試；其他例外（包括 Turso 連線瞬斷時 libsql 拋出的普通
        # ValueError）會被記一次 log 後直接讓整個迴圈永久停止 —— 不是
        # 暫停一輪，是這個 task 徹底結束，之後再也不會醒來，直到整個
        # bot 進程重啟為止。DB 連線瞬斷是背景排程遇得到的常態，不是
        # 罕見狀況，用 bare except 把「這一輪失敗」和「這個功能死掉」
        # 徹底分開，是這裡最重要的一條防線。
        try:
            now = now_ms()
            due = await repo.list_due_reminders(now, limit=_BATCH_LIMIT)
        except Exception:
            log.exception("撈取到期提醒失敗，這一輪跳過，下一輪（30 秒後）再試")
            return

        for reminder in due:
            try:
                await self._process_reminder(reminder, now)
            except Exception:
                # 單一提醒處理失敗不該讓這一輪其他提醒都發不出去。
                log.exception("處理提醒 %s 時發生未預期錯誤", reminder["id"])

    @reminder_tick.before_loop
    async def _before_reminder_tick(self) -> None:
        # 等 gateway 連上、guild/channel 快取就緒再開始第一輪，避免一開機
        # 就因為快取是空的而找不到頻道、誤判成「頻道被刪除」。
        await self.bot.wait_until_ready()

    async def _process_reminder(self, reminder: Row, now: int) -> None:
        if should_skip_overdue_reminder(
            fire_at_utc=reminder["fire_at_utc"],
            starts_at_utc=reminder["starts_at_utc"],
            now_ms=now,
        ):
            await repo.skip_reminder(reminder["id"])
            return

        # 先搶鎖再發送：萬一發送後、標記前進程就當掉，寧可漏發一次提醒，
        # 也不要因為重啟後重新處理同一列而對整個頻道重複 tag 一次所有人。
        claimed = await repo.claim_reminder(reminder["id"])
        if not claimed:
            return  # 理論上不會發生（單一 asyncio 迴圈不重疊執行），純防禦

        channel = await self._resolve_channel(int(reminder["channel_id"]))
        if channel is None:
            log.warning(
                "提醒 %s 找不到頻道 %s（可能已被刪除或 bot 被移出），無法發送",
                reminder["id"],
                reminder["channel_id"],
            )
            await repo.mark_reminder_failed(reminder["id"])
            return

        invitees = await repo.list_event_invitees(reminder["event_id"], reminder["guild_id"])
        user_ids = {int(i["target_id"]) for i in invitees if i["target_type"] == "user"}
        role_ids = [int(i["target_id"]) for i in invitees if i["target_type"] == "role"]
        tag_everyone = any(i["target_type"] == "everyone" for i in invitees)

        # 已經表態「參加」或「待定」的人，就算當初建立活動時沒被明確邀請
        # （例如揪辦活動的人忘記選邀請對象），提醒還是該點名到他們 —— 誰
        # 能回覆本來就不受邀請名單限制（見 domain/rsvp.py），提醒對象同理
        # 不該只看邀請名單，不然「有人主動說要去，卻收不到提醒」會很怪。
        rsvps = await repo.list_rsvps(reminder["event_id"], reminder["guild_id"])
        user_ids |= {int(r["user_id"]) for r in rsvps if r["status"] in ("yes", "maybe")}
        user_ids_list = sorted(user_ids)

        try:
            await channel.send(
                content=build_mention_content(
                    user_ids_list, role_ids, tag_everyone=tag_everyone
                ),
                embed=build_reminder_embed(reminder),
                allowed_mentions=build_allowed_mentions(
                    user_ids_list, role_ids, tag_everyone=tag_everyone
                ),
            )
        except discord.HTTPException:
            log.warning("提醒 %s 已標記送出，但實際發送失敗", reminder["id"], exc_info=True)
            await repo.mark_reminder_failed(reminder["id"])

    async def _resolve_channel(self, channel_id: int) -> discord.abc.Messageable | None:
        # 優先吃快取；快取沒有（例如 bot 剛重啟、還沒收滿頻道快取）才打 API 補一次。
        channel = self.bot.get_channel(channel_id)
        if channel is not None:
            return channel
        try:
            return await self.bot.fetch_channel(channel_id)
        except discord.HTTPException:
            return None


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Scheduler(bot))
