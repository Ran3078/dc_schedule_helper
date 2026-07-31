"""互動元件（View）。

這裡放的是**非持久化**的 View：生命週期只到使用者確認/取消/逾時為止（最多幾分鐘），
不需要在 bot 重啟後仍然可用。這跟 M3 起的 RSVP／投票按鈕不同 —— 那些訊息會存在
好幾天，必須是持久化 View（`timeout=None` + 固定 `custom_id` + `bot.add_view()`
註冊）。混淆這兩種會導致重啟後按鈕失效，或讓短命的確認對話框長期佔用記憶體，
所以在此明確區分。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import discord

from src.bot.embeds import build_event_embed
from src.bot.modals import PendingEvent
from src.bot.views_rsvp import build_rsvp_view
from src.db import repo
from src.domain.reminders import parse_default_reminders
from src.domain.rsvp import RsvpSummary, build_rsvp_summary
from src.lib.mentions import build_allowed_mentions, build_mention_content

log = logging.getLogger(__name__)


class ConfirmEventView(discord.ui.View):
    """`/event create` 送出內容後、正式公開發布前的確認步驟。

    目的是攔截打錯字：使用者可以在只有自己看得到的預覽裡確認時間/地點/內容
    是否正確，按「取消」就什麼都不會發生，資料庫完全沒有寫入痕跡。

    `user_ids` / `role_ids` / `tag_everyone` 是從 InviteePickerView 帶過來的
    邀請對象；「發布」按下去才會一併寫進 `event_invitees`，並在公告訊息的
    content 裡加上實際會觸發推播的 mention（純 embed 不會通知任何人）。

    `restrict_rsvp` 同樣從 InviteePickerView 帶過來：True 時 RsvpButton
    只接受落在邀請名單展開後的成員（見 views_rsvp.py），其他人按了會被拒絕。
    """

    def __init__(
        self,
        *,
        event_id: str,
        pending: PendingEvent,
        description: str | None,
        user_ids: Sequence[int] = (),
        role_ids: Sequence[int] = (),
        tag_everyone: bool = False,
        restrict_rsvp: bool = False,
    ) -> None:
        super().__init__(timeout=300)  # 5 分鐘沒確認就作廢，避免預覽訊息無限期卡著
        self.event_id = event_id
        self.pending = pending
        self.description = description
        self.user_ids = list(user_ids)
        self.role_ids = list(role_ids)
        self.tag_everyone = tag_everyone
        self.restrict_rsvp = restrict_rsvp
        self.message: discord.Message | None = None

    async def _finish(self, interaction: discord.Interaction | None, content: str) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Item):
                child.disabled = True  # type: ignore[attr-defined]
        self.stop()

        if interaction is not None and not interaction.response.is_done():
            await interaction.response.edit_message(content=content, embed=None, view=self)
        elif self.message is not None:
            try:
                await self.message.edit(content=content, embed=None, view=self)
            except discord.HTTPException:
                log.warning("編輯確認訊息失敗（可能已被使用者刪除）", exc_info=True)

    @discord.ui.button(label="發布", style=discord.ButtonStyle.success, emoji="✅")
    async def confirm(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        # 邏輯拆到 _confirm_impl：discord.ui.button 裝飾器會把方法包裝成 UI
        # 元件描述子，測試時不方便直接呼叫；拆出來的版本是普通 async 方法，
        # 可以在不啟動真正 Discord 連線的情況下用假的 interaction 直接測試。
        await self._confirm_impl(interaction)

    async def _confirm_impl(self, interaction: discord.Interaction) -> None:
        pending = self.pending

        # 預設提醒（1天/1小時/10分前，可由 /settings 調整）在活動建立當下
        # 就一併排定，而不是等使用者另外操作 —— 這是 M4 的核心承諾：建活動
        # 就自動有提醒，不用額外一步。
        guild_settings = await repo.get_guild_settings(pending.guild_id)
        reminder_offsets = parse_default_reminders(
            guild_settings["default_reminders"] if guild_settings else None
        )

        await repo.create_event(
            event_id=self.event_id,
            guild_id=pending.guild_id,
            channel_id=pending.channel_id,
            creator_id=pending.creator_id,
            title=pending.title,
            starts_at_utc=pending.starts_at_utc,
            ends_at_utc=pending.ends_at_utc,
            tz=pending.tz,
            location=pending.location,
            description=self.description,
            user_ids=self.user_ids,
            role_ids=self.role_ids,
            tag_everyone=self.tag_everyone,
            restrict_rsvp=self.restrict_rsvp,
            reminder_offsets_min=reminder_offsets,
        )
        event_row = await repo.owned_event(self.event_id, pending.guild_id)
        assert event_row is not None, "剛寫入的活動查不到，DB 層有問題"
        invitees = await repo.list_event_invitees(self.event_id, pending.guild_id)

        # 剛發布時還沒有任何人回覆（rsvps=[]），但先算一次摘要讓公告一開始
        # 就顯示「✅ 參加（0）」「⏳ 未回覆（N）」，不必等第一次按按鈕才出現。
        # interaction.guild 理論上一定有值（這條路徑全走 guild_only 指令），
        # 這裡防禦性處理 None 只是不讓型別不合預期時整個發布動作炸掉。
        rsvp_summary: RsvpSummary | None = None
        if interaction.guild is not None:
            rsvp_summary = build_rsvp_summary(interaction.guild, invitees, rsvps=[])

        message = None
        channel = interaction.channel
        # 用 duck typing 而非 isinstance(..., discord.abc.Messageable)：
        # 兩者在正常情況下等價（能收到指令互動的頻道一定能發訊息），但前者
        # 讓測試可以用簡單的假物件替代，不必仿造 Discord 內部的頻道類別階層。
        if channel is not None and hasattr(channel, "send"):
            try:
                message = await channel.send(
                    content=build_mention_content(
                        self.user_ids, self.role_ids, tag_everyone=self.tag_everyone
                    ),
                    embed=build_event_embed(event_row, invitees, rsvp_summary),
                    allowed_mentions=build_allowed_mentions(
                        self.user_ids, self.role_ids, tag_everyone=self.tag_everyone
                    ),
                    view=build_rsvp_view(self.event_id),
                )
            except discord.HTTPException:
                log.exception("活動 %s 已建立，但發布公告訊息失敗", self.event_id)

        if message is not None:
            await repo.set_event_message(self.event_id, pending.guild_id, message.id)
            await self._finish(interaction, f"✅ 活動已發布：{message.jump_url}")
        else:
            await self._finish(
                interaction,
                f"✅ 活動已建立（ID `{self.event_id}`），但公告訊息發送失敗，"
                f"請用 `/event info {self.event_id}` 查看。",
            )

    @discord.ui.button(label="取消", style=discord.ButtonStyle.secondary, emoji="❌")
    async def cancel(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        await self._cancel_impl(interaction)

    async def _cancel_impl(self, interaction: discord.Interaction) -> None:
        # 尚未寫入資料庫，這裡什麼都不用清 —— 直接關閉預覽即可。
        await self._finish(interaction, "已取消，活動未建立。")

    async def on_timeout(self) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Item):
                child.disabled = True  # type: ignore[attr-defined]
        if self.message is not None:
            try:
                await self.message.edit(
                    content="⌛ 已逾時未確認，活動未建立。請重新使用 `/event create`。",
                    embed=None,
                    view=self,
                )
            except discord.HTTPException:
                pass  # 訊息可能已被使用者刪除，逾時清理失敗不影響任何資料正確性
