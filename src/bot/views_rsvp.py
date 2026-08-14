"""RSVP 按鈕與職位選單 —— 這是**持久化**元件，用 `DynamicItem` 而非普通 `View`。

公告訊息可能存在好幾天甚至好幾週，這段期間 bot 一定會因為 Render 的休眠／
deploy 重啟過好幾次。`DynamicItem` 的關鍵好處：Discord 每次互動都會把訊息
當下的元件結構整包送回來，我們的 callback 是靠 `custom_id` 比對 regex 樣板
觸發的（而非某個記憶體裡的 `View` 物件），所以完全不用在啟動時「重新綁定」
舊訊息 —— 只要在 `setup_hook` 呼叫一次
`bot.add_dynamic_items(RsvpButton, PositionSelect)` 註冊這兩個 class，之後
不管訊息是十分鐘前發的還是十天前發的，按鈕/選單都一樣有效。

（跟 M0–M2 那些 `ConfirmEventView`／`DateTimePickerView`／`InviteePickerView`
完全不同層級 —— 那些是短命的確認流程，`timeout=300` 就夠了；這裡是要撐過
bot 重啟、活動整個生命週期都要能用的按鈕/選單。`views_role_job.JobPickerView`
是選職位流程裡唯一的例外——它是 `PositionSelect` 選定位置後的**同一次
使用者操作**延續，不需要撐過重啟，走短命 View 那一套。）

`custom_id` 格式沿用 `lib/ids.py` 的慣例：`ev:rsvp:<event_id>:<status>`／
`ev:pos:<event_id>`（M8 新增，見 domain/roles.py 的位置報名說明）。
"""

from __future__ import annotations

import logging
import re

import discord

from src.bot.embeds import Row, build_event_embed
from src.db import repo
from src.domain.invitees import expand_invited_members
from src.domain.roles import compute_position_counts
from src.domain.rsvp import build_rsvp_summary
from src.lib.ids import build_custom_id
from src.lib.mentions import build_allowed_mentions, build_mention_content

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


async def _refresh_announcement(interaction: discord.Interaction, event: Row) -> None:
    """RSVP 按鈕／職位選單共用的重繪邏輯：兩者都是公告訊息本身的元件，
    互動一定帶著 `interaction.message`，跟 `views_role_job.JobPickerView`
    那種要自己找頻道/訊息的 ephemeral 流程不同（見該檔案的說明）。

    embed 跟 view 一起重繪：職位選單的選項標籤帶著即時人數
    （`「D1（1/1）」`），任何一次 RSVP 或職位異動都可能讓這些數字過期，
    一律整包重建比只挑著更新更不容易漏掉某個路徑。
    """
    guild = interaction.guild
    message = interaction.message
    if guild is None or message is None:
        return

    invitees = await repo.list_event_invitees(event["id"], guild.id)
    rsvps = await repo.list_rsvps(event["id"], guild.id)
    role_slots = await repo.list_event_role_slots(event["id"], guild.id)
    role_signups = await repo.list_event_role_signups(event["id"], guild.id)
    summary = build_rsvp_summary(guild, invitees, rsvps)

    try:
        await message.edit(
            embed=build_event_embed(event, invitees, summary, role_slots, role_signups),
            view=build_event_controls_view(event["id"], role_slots, role_signups),
        )
    except discord.HTTPException:
        log.warning("更新活動 %s 的公告訊息失敗", event["id"], exc_info=True)


async def notify_promotion(
    channel: discord.abc.Messageable, position: str, job: str, user_id: str | int
) -> None:
    """位置候補遞補通知——確定名額的人讓出位置（換位置／RSVP 改成非
    「參加」／自己取消位置選擇）時，候補佇列裡最早報名的人自動遞補上去，
    在活動頻道 tag 通知。`views_rsvp.py`（RsvpButton／PositionSelect）跟
    `views_role_job.JobPickerView` 都會觸發這個通知，放在這裡公用。
    """
    content = build_mention_content([user_id], [], tag_everyone=False)
    text = f"{content}\n🎉 遞補上「{position}」位置（{job}）！"
    try:
        await channel.send(
            content=text,
            allowed_mentions=build_allowed_mentions([user_id], [], tag_everyone=False),
        )
    except discord.HTTPException:
        log.warning("遞補通知傳送失敗（位置 %s）", position, exc_info=True)


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

        # M8：職位選擇疊加在「參加」狀態之上，RSVP 改成非「參加」要連帶
        # 清空職位選擇，讓出的名額若是確定名額就觸發候補遞補——不然會出現
        # 「不參加但佔著一個 MT 名額」的怪狀態。
        if self.status != "yes":
            removed = await repo.remove_role_signup(
                self.event_id, interaction.guild_id, interaction.user.id
            )
            if removed is not None and not removed["waitlisted"] and interaction.channel:
                promoted = await repo.promote_next_waitlisted(removed["role_slot_id"])
                if promoted is not None:
                    role_slots = await repo.list_event_role_slots(
                        self.event_id, interaction.guild_id
                    )
                    slot = next(
                        (s for s in role_slots if s["id"] == removed["role_slot_id"]), None
                    )
                    if slot is not None:
                        await notify_promotion(
                            interaction.channel, slot["position"], promoted["job"],
                            promoted["user_id"],
                        )

        # 先給按按鈕的人一個立即的確認 —— 這一步只依賴上面幾次 DB 讀寫，
        # 在 3 秒互動期限內綽綽有餘。重新整理公告訊息（下面）還要再撈
        # invitees/rsvps 兩次 DB，晚一點更新也沒關係，使用者已經先拿到回饋了。
        await interaction.response.send_message(
            f"已記錄你的回覆：{_EMOJIS[self.status]} {_LABELS[self.status]}", ephemeral=True
        )
        await _refresh_announcement(interaction, event)


_POSITION_TEMPLATE = re.compile(r"^ev:pos:(?P<event_id>[^:]+)$")
_CLEAR_VALUE = "_none"


def _position_option_label(slot: Row, counts: dict[str, tuple[int, int]]) -> str:
    confirmed, waitlisted = counts.get(slot["id"], (0, 0))
    if confirmed >= 1 and waitlisted > 0:
        status = f"候補 {waitlisted} 人"
    elif confirmed >= 1:
        status = "1/1"
    else:
        status = "空"
    return f"{slot['position']}（{status}）"


def _build_position_select(
    event_id: str, role_slots: list[Row], role_signups: list[Row], *, disabled: bool
) -> discord.ui.Select:
    counts = compute_position_counts(role_signups)
    options = [
        discord.SelectOption(label=_position_option_label(slot, counts), value=slot["id"])
        for slot in role_slots
    ]
    options.append(discord.SelectOption(label="❌ 取消我的位置", value=_CLEAR_VALUE))
    return discord.ui.Select(
        custom_id=build_custom_id("ev", "pos", event_id),
        placeholder="選擇你的位置（需先按「參加」）",
        min_values=1,
        max_values=1,
        options=options[:25],
        disabled=disabled,
    )


class PositionSelect(discord.ui.DynamicItem[discord.ui.Select], template=_POSITION_TEMPLATE):
    """FF14 位置報名（M8）：疊加在 RSVP 按鈕之上，只有「參加」的人能選。
    選定位置後彈出 ephemeral 的 `views_role_job.JobPickerView` 繼續選具體
    職業——這裡的 callback 只負責位置本身（含「取消我的位置」）。
    """

    def __init__(
        self,
        *,
        event_id: str,
        role_slots: list[Row] | None = None,
        role_signups: list[Row] | None = None,
        disabled: bool = False,
    ) -> None:
        self.event_id = event_id
        super().__init__(
            _build_position_select(
                event_id, role_slots or [], role_signups or [], disabled=disabled
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item,
        match: re.Match[str],
        /,
    ) -> PositionSelect:
        # 不重建選項清單（見 views_poll.PollVoteSelect 同樣的說明）——
        # callback 只靠 self.item.values 讀這次選了什麼，接下來的邏輯全部
        # 重新查 DB，不依賴這裡的 options。
        return cls(event_id=match["event_id"])

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            return

        event = await repo.owned_event(self.event_id, interaction.guild_id)
        if event is None:
            await interaction.response.send_message(
                "找不到這個活動，可能已被取消或刪除。", ephemeral=True
            )
            return
        if event["status"] != "scheduled":
            await interaction.response.send_message("這個活動已經取消了。", ephemeral=True)
            return

        status = await repo.get_rsvp_status(
            self.event_id, interaction.guild_id, interaction.user.id
        )
        if status != "yes":
            await interaction.response.send_message(
                "請先按「參加」再選擇位置。", ephemeral=True
            )
            return

        value = self.item.values[0]
        if value == _CLEAR_VALUE:
            await self._clear_selection(interaction, event)
            return

        role_slots = await repo.list_event_role_slots(self.event_id, interaction.guild_id)
        slot = next((s for s in role_slots if s["id"] == value), None)
        if slot is None:
            await interaction.response.send_message(
                "這個位置已經不存在了，請重新整理活動公告後再試一次。", ephemeral=True
            )
            return

        # 延遲匯入避免循環參照：views_role_job 需要這個模組的
        # build_event_controls_view／notify_promotion。
        from src.bot.views_role_job import JobPickerView

        picker = JobPickerView(
            event_id=self.event_id, role_slot_id=value, position=slot["position"]
        )
        await interaction.response.send_message(
            content=f"你選的是 **{slot['position']}**，請選擇你要打的職業：",
            view=picker,
            ephemeral=True,
        )
        picker.message = await interaction.original_response()

    async def _clear_selection(self, interaction: discord.Interaction, event: Row) -> None:
        assert interaction.guild_id is not None
        removed = await repo.remove_role_signup(
            self.event_id, interaction.guild_id, interaction.user.id
        )
        if removed is None:
            await interaction.response.send_message("你目前沒有選擇任何位置。", ephemeral=True)
            return

        if not removed["waitlisted"] and interaction.channel:
            promoted = await repo.promote_next_waitlisted(removed["role_slot_id"])
            if promoted is not None:
                role_slots = await repo.list_event_role_slots(
                    self.event_id, interaction.guild_id
                )
                slot = next((s for s in role_slots if s["id"] == removed["role_slot_id"]), None)
                if slot is not None:
                    await notify_promotion(
                        interaction.channel, slot["position"], promoted["job"],
                        promoted["user_id"],
                    )

        await interaction.response.send_message("已取消你的位置選擇。", ephemeral=True)
        await _refresh_announcement(interaction, event)


def build_event_controls_view(
    event_id: str,
    role_slots: list[Row],
    role_signups: list[Row],
    *,
    disabled: bool = False,
) -> discord.ui.View:
    """建立活動公告要附帶的控制元件：RSVP 三顆按鈕永遠都在；`role_slots`
    非空才多加一列 `PositionSelect`（M8）。只在**發布**、**設定/清空職位**
    與 **/event cancel** 時呼叫（比照舊版 `build_rsvp_view` 只在需要重繪
    整個 View 時呼叫）——之後按鈕/選單的持久性完全靠 `DynamicItem` 機制，
    bot 重啟不需要重新附加。

    `disabled=True` 給 `/event cancel` 用：取消活動後重繪一個全部
    disabled 的版本 edit 上去，視覺上明確表示不能再互動了；
    `RsvpButton`／`PositionSelect` 的 callback 裡各自的狀態檢查是最後一道
    防呆備援。
    """
    view = discord.ui.View(timeout=None)
    for status in RSVP_STATUSES:
        view.add_item(RsvpButton(event_id=event_id, status=status, disabled=disabled))
    if role_slots:
        view.add_item(
            PositionSelect(
                event_id=event_id,
                role_slots=role_slots,
                role_signups=role_signups,
                disabled=disabled,
            )
        )
    return view
