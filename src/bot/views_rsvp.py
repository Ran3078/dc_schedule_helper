"""RSVP 按鈕 —— 這是**持久化**元件，用 `DynamicItem` 而非普通 `View`。

公告訊息可能存在好幾天甚至好幾週，這段期間 bot 一定會因為 Render 的休眠／
deploy 重啟過好幾次。`DynamicItem` 的關鍵好處：Discord 每次互動都會把訊息
當下的元件結構整包送回來，我們的 callback 是靠 `custom_id` 比對 regex 樣板
觸發的（而非某個記憶體裡的 `View` 物件），所以完全不用在啟動時「重新綁定」
舊訊息 —— 只要在 `setup_hook` 呼叫一次 `bot.add_dynamic_items(RsvpButton)`
註冊這個 class，之後不管訊息是十分鐘前發的還是十天前發的，按鈕都一樣有效。

（跟 M0–M2 那些 `ConfirmEventView`／`DateTimePickerView`／`InviteePickerView`
完全不同層級 —— 那些是短命的確認流程，`timeout=300` 就夠了；這裡是要撐過
bot 重啟、活動整個生命週期都要能用的按鈕。）

`custom_id` 格式沿用 `lib/ids.py` 的慣例：`ev:rsvp:<event_id>:<status>`。
"""

from __future__ import annotations

import logging
import re

import discord

from src.bot.embeds import Row, build_event_embed
from src.db import repo
from src.domain.invitees import expand_invited_members
from src.domain.rsvp import build_rsvp_summary
from src.lib.ids import build_custom_id

log = logging.getLogger(__name__)

_TEMPLATE = re.compile(r"^ev:rsvp:(?P<event_id>[^:]+):(?P<status>yes|maybe|no)$")

_LABELS = {"yes": "參加", "maybe": "待定", "no": "不參加"}
_EMOJIS = {"yes": "✅", "maybe": "❔", "no": "❌"}
_STYLES = {
    "yes": discord.ButtonStyle.success,
    "maybe": discord.ButtonStyle.secondary,
    "no": discord.ButtonStyle.danger,
}

RSVP_STATUSES = ("yes", "maybe", "no")


class RsvpButton(discord.ui.DynamicItem[discord.ui.Button], template=_TEMPLATE):
    def __init__(self, *, event_id: str, status: str, disabled: bool = False) -> None:
        self.event_id = event_id
        self.status = status
        super().__init__(
            discord.ui.Button(
                label=_LABELS[status],
                emoji=_EMOJIS[status],
                style=_STYLES[status],
                custom_id=build_custom_id("ev", "rsvp", event_id, status),
                disabled=disabled,
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item,
        match: re.Match[str],
        /,
    ) -> RsvpButton:
        return cls(event_id=match["event_id"], status=match["status"])

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            # 理論上不會發生 —— 按鈕只會出現在公告訊息上，公告只會發在伺服器頻道。
            return

        # 先查一次活動：一方面確認活動還存在，一方面要知道 restrict_rsvp
        # 旗標才能決定要不要做邀請名單檢查。upsert_rsvp 內部也會再檢查一次
        # 存在性（WHERE EXISTS），這裡重複檢查是刻意的 belt-and-suspenders，
        # 不是效能問題（Turso 單次查詢在我們的規模下可忽略不計）。
        event = await repo.owned_event(self.event_id, interaction.guild_id)
        if event is None:
            await interaction.response.send_message(
                "找不到這個活動，可能已被取消或刪除。", ephemeral=True
            )
            return

        if event["status"] != "scheduled":
            # 活動已取消：/event cancel 會把公告卡片的按鈕重繪成 disabled，
            # 但 Discord 端如果剛好還沒收到那次編輯（或使用者手上是舊快取），
            # 這裡是最後一道防線，不讓已取消的活動還能被 RSVP。
            await interaction.response.send_message("這個活動已經取消了。", ephemeral=True)
            return

        if event["restrict_rsvp"]:
            guild = interaction.guild
            invitees = await repo.list_event_invitees(self.event_id, interaction.guild_id)
            invited_pool = expand_invited_members(guild, invitees) if guild else set()
            if interaction.user.id not in invited_pool:
                await interaction.response.send_message(
                    "這個活動僅限受邀對象回覆，你不在邀請名單上。", ephemeral=True
                )
                return

        ok = await repo.upsert_rsvp(
            self.event_id, interaction.guild_id, interaction.user.id, self.status
        )
        if not ok:
            # 極罕見的競態：上面查到活動存在，但寫入前被取消/刪除了。
            await interaction.response.send_message(
                "找不到這個活動，可能已被取消或刪除。", ephemeral=True
            )
            return

        # 先給按按鈕的人一個立即的確認 —— 這一步只依賴上面幾次 DB 讀寫，
        # 在 3 秒互動期限內綽綽有餘。重新整理公告訊息（下面）還要再撈
        # invitees/rsvps 兩次 DB，晚一點更新也沒關係，使用者已經先拿到回饋了。
        await interaction.response.send_message(
            f"已記錄你的回覆：{_EMOJIS[self.status]} {_LABELS[self.status]}", ephemeral=True
        )
        await self._refresh_announcement(interaction, event)

    async def _refresh_announcement(self, interaction: discord.Interaction, event: Row) -> None:
        guild = interaction.guild
        message = interaction.message
        if guild is None or message is None:
            return

        invitees = await repo.list_event_invitees(self.event_id, guild.id)
        rsvps = await repo.list_rsvps(self.event_id, guild.id)
        summary = build_rsvp_summary(guild, invitees, rsvps)

        try:
            await message.edit(embed=build_event_embed(event, invitees, summary))
        except discord.HTTPException:
            log.warning("更新活動 %s 的公告訊息失敗", self.event_id, exc_info=True)


def build_rsvp_view(event_id: str, *, disabled: bool = False) -> discord.ui.View:
    """建立活動公告要附帶的 RSVP 按鈕列。只在**第一次發布**時呼叫一次
    （見 views.ConfirmEventView._confirm_impl）—— 之後按鈕的持久性完全靠
    上面 RsvpButton 的 DynamicItem 機制，不需要重新附加任何東西，bot 重啟
    也不用重新呼叫這個函式去「補回」舊訊息的按鈕。

    `disabled=True` 給 `/event cancel` 用：取消活動後重繪一個全部 disabled
    的版本 edit 上去，視覺上明確表示不能再 RSVP 了（比照
    `views_poll.build_poll_vote_view` 的 `disabled` 參數同樣的用法）；
    `RsvpButton.callback` 裡的活動狀態檢查是最後一道防呆備援。
    """
    view = discord.ui.View(timeout=None)
    for status in RSVP_STATUSES:
        view.add_item(RsvpButton(event_id=event_id, status=status, disabled=disabled))
    return view
