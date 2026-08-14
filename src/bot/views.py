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

from src.bot import native_events
from src.bot.embeds import Row, build_event_embed
from src.bot.modals import PendingEvent
from src.bot.views_rsvp import build_event_controls_view
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

    `positions` 是 M8（FF14 團本職位報名）的欄位，只有 `/ff14_recruit`
    那條路徑（`modals_ff14.Ff14RecruitModal` → `DateTimePickerView`／
    直接 → `InviteePickerView`）會帶非空值；`/event create` 一律是空
    tuple，行為完全不受影響。
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
        positions: Sequence[str] = (),
    ) -> None:
        super().__init__(timeout=300)  # 5 分鐘沒確認就作廢，避免預覽訊息無限期卡著
        self.event_id = event_id
        self.pending = pending
        self.description = description
        self.user_ids = list(user_ids)
        self.role_ids = list(role_ids)
        self.tag_everyone = tag_everyone
        self.restrict_rsvp = restrict_rsvp
        self.positions = list(positions)
        self.message: discord.Message | None = None

    async def _resolve_channel(
        self, interaction: discord.Interaction, channel_id: int
    ) -> discord.abc.Messageable | None:
        """公告要發到哪個頻道——多半跟 `interaction.channel` 是同一個，只有
        `guild_settings.announce_channel_id` 設定了、且跟指令所在頻道不同時
        才會不一樣。比照其他 cog 的 `_resolve_channel`：優先吃 guild 的頻道
        快取，沒有才補一次 API 呼叫。這裡沒有 `self.bot` 可用（`ConfirmEventView`
        是純 View，不是 cog），改用 `interaction.client`——兩者是同一個
        Bot 實例。
        """
        guild = interaction.guild
        channel = guild.get_channel(channel_id) if guild is not None else None
        if channel is not None:
            return channel
        try:
            return await interaction.client.fetch_channel(channel_id)
        except discord.HTTPException:
            return None

    async def _finish(self, interaction: discord.Interaction | None, content: str) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Item):
                child.disabled = True  # type: ignore[attr-defined]
        self.stop()

        if interaction is not None and not interaction.response.is_done():
            try:
                await interaction.response.edit_message(content=content, embed=None, view=self)
                return
            except discord.HTTPException:
                # `is_done()` 只代表「我們還沒呼叫過 interaction.response」，
                # 不代表 interaction token 還沒過期——`_confirm_impl` 那條路徑
                # 已經先 defer 過，理論上不會走到這裡；這裡是留給沒有 defer
                # 的呼叫端（例如 `_cancel_impl`）萬一剛好卡超過 3 秒的備援，
                # 直接改用 self.message.edit()（bot token，不受 interaction
                # token 有效期限制）。
                log.warning("interaction 回應失敗（可能已逾期），改用訊息本身編輯", exc_info=True)

        if self.message is not None:
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

        # 這條路徑要走好幾次 DB 往返（建活動＋M8 職位設定要再多 2～3 次）加上
        # 兩次 Discord API 呼叫（發公告訊息、同步原生活動），實測在 bot 剛
        # 啟動、Turso 連線還沒暖機時很容易超過 Discord 對 interaction 的
        # 3 秒初次回應期限——一旦超過，interaction token 直接失效
        # （`404 Unknown interaction`），不是「回應變慢」那種能重試的錯誤。
        # 一開始就 defer（元件互動預設是 deferred_message_update，畫面不會
        # 有任何「思考中」提示），把「必須 3 秒內回應」這個限制解決掉；
        # 之後 `_finish` 看到 `interaction.response.is_done()` 是 True，
        # 會改用 `self.message.edit(...)`——那是用 bot token 直接編輯訊息，
        # 不受 interaction token 有效期限制。
        try:
            await interaction.response.defer()
        except discord.HTTPException:
            log.warning("確認發布時 defer 失敗，稍後改用訊息本身編輯", exc_info=True)

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

        # M8：FF14 招募（/ff14_recruit）帶了職位設定才需要寫入，/event
        # create 這條路徑 self.positions 永遠是空清單，兩個查詢也不用跑。
        role_slots: list[Row] = []
        role_signups: list[Row] = []
        if self.positions:
            await repo.set_event_role_slots(self.event_id, pending.guild_id, self.positions)
            role_slots = await repo.list_event_role_slots(self.event_id, pending.guild_id)
            role_signups = await repo.list_event_role_signups(self.event_id, pending.guild_id)

        # 剛發布時還沒有任何人回覆（rsvps=[]），但先算一次摘要讓公告一開始
        # 就顯示「✅ 參加（0）」「⏳ 未回覆（N）」，不必等第一次按按鈕才出現。
        # interaction.guild 理論上一定有值（這條路徑全走 guild_only 指令），
        # 這裡防禦性處理 None 只是不讓型別不合預期時整個發布動作炸掉。
        rsvp_summary: RsvpSummary | None = None
        if interaction.guild is not None:
            rsvp_summary = build_rsvp_summary(interaction.guild, invitees, rsvps=[])

        message = None
        # pending.channel_id 是 Events._create_impl 那一步就定案的公告頻道
        # （見該方法的說明：guild_settings.announce_channel_id 設定了就不是
        # interaction 所在頻道，兩者這裡可能不同，一律照 pending.channel_id
        # 走，不是回頭用 interaction.channel）。
        channel = await self._resolve_channel(interaction, pending.channel_id)
        # 用 duck typing 而非 isinstance(..., discord.abc.Messageable)：
        # 兩者在正常情況下等價（能收到指令互動的頻道一定能發訊息），但前者
        # 讓測試可以用簡單的假物件替代，不必仿造 Discord 內部的頻道類別階層。
        if channel is not None and hasattr(channel, "send"):
            try:
                message = await channel.send(
                    content=build_mention_content(
                        self.user_ids, self.role_ids, tag_everyone=self.tag_everyone
                    ),
                    embed=build_event_embed(
                        event_row, invitees, rsvp_summary, role_slots, role_signups,
                        interaction.guild,
                    ),
                    allowed_mentions=build_allowed_mentions(
                        self.user_ids, self.role_ids, tag_everyone=self.tag_everyone
                    ),
                    view=build_event_controls_view(self.event_id, role_slots, role_signups),
                )
            except discord.HTTPException:
                log.exception("活動 %s 已建立，但發布公告訊息失敗", self.event_id)

        # 同步到 Discord 原生「活動」分頁（M6），換取免費的手機推播。這是
        # 附加功能，同步失敗只記 log，不影響上面公告發布成功/失敗的文案——
        # 使用者不該因為這個額外同步出包就以為活動本身建立失敗了。
        if interaction.guild is not None and (
            not guild_settings or guild_settings["sync_native_events"]
        ):
            discord_event_id = await native_events.sync_create(interaction.guild, event_row)
            if discord_event_id is not None:
                await repo.set_event_discord_id(self.event_id, pending.guild_id, discord_event_id)

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
