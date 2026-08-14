"""活動相關的 Modal。

Discord 的互動限制：Modal 只能作為 interaction 的**第一個**回應（不能先 defer
再彈 Modal）。因此 `/event create` 的流程分幾步：

1. 先驗證 title/time/location/duration 這些指令參數，驗證失敗就直接 ephemeral
   回覆錯誤 —— 這時還沒彈 Modal，使用者可以直接修正指令重打，不必多按一次。
2. 驗證通過才彈出這個 Modal 收活動內容。
3. 時間若還沒定案（指令留空 time），先經過
   `views_datetime.DateTimePickerView` 選日期時間；時間確定後一律進
   `views_invitees.InviteePickerView` 選要 tag 的人／身分組（全程選填，
   可直接略過）。
4. 最後才顯示只有使用者自己看得到的預覽（見 `views.ConfirmEventView`），
   按「發布」才會真的寫入資料庫並公開發文。這一步是為了攔截打錯字：時間/
   地點/內容/邀請對象有誤時按「取消」即可，不會留下任何資料庫痕跡，也不會
   弄髒頻道。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord

log = logging.getLogger(__name__)

Row = dict[str, Any]

# 跟 cogs/events.py 的 MAX_TITLE_LENGTH 同一個數字——這裡不 import 那邊的常數，
# 是為了避免 modals.py ↔ cogs/events.py 形成循環參照（cogs/events.py 本來就會
# import 這個檔案的 PendingEvent）。
_MAX_TITLE_LENGTH = 200


@dataclass(frozen=True, slots=True)
class PendingEvent:
    """`/event create` 的指令參數已驗證完成，只差活動內容還沒收集。

    `starts_at_utc` 為 `None` 代表使用者在指令裡沒有直接打時間，改用
    `views_datetime.DateTimePickerView` 事後補上（見該檔案開頭的說明：
    Discord 沒有原生日期選擇元件，這是用下拉選單模擬出來的替代方案）。

    `duration_minutes` 存的是分鐘數而非提前算好的 `ends_at_utc`，因為
    `starts_at_utc` 確定之前根本無從計算結束時間 —— 這正是走月曆挑選器
    那條路徑時的情況。
    """

    guild_id: int
    channel_id: int
    creator_id: int
    title: str
    tz: str
    location: str | None
    duration_minutes: int | None
    starts_at_utc: int | None = None

    @property
    def ends_at_utc(self) -> int | None:
        if self.starts_at_utc is None or self.duration_minutes is None:
            return None
        return self.starts_at_utc + self.duration_minutes * 60_000

    def with_start(self, starts_at_utc: int) -> PendingEvent:
        return replace(self, starts_at_utc=starts_at_utc)


def build_preview_row(
    event_id: str,
    pending: PendingEvent,
    description: str | None,
    *,
    restrict_rsvp: bool = False,
) -> dict[str, Any]:
    """組出 build_event_embed() 需要的 dict 形狀，但資料尚未寫入資料庫。

    modals.py（時間已知）、views_datetime.py（月曆挑選完成後）、
    views_invitees.py（決定要不要限制回覆對象後）都要組同一種預覽用的假
    event row，這裡共用一份，避免多處各寫一次、日後改欄位漏掉一邊。

    restrict_rsvp 預設 False：這個決定要到 InviteePickerView 那一步才會
    知道，modals.py／views_datetime.py 呼叫時都還沒決定，用預設值即可
    （反正這兩處產生的預覽後面都還會再經過一次 InviteePickerView）。
    """
    return {
        "id": event_id,
        "title": pending.title,
        "starts_at_utc": pending.starts_at_utc,
        "ends_at_utc": pending.ends_at_utc,
        "location": pending.location,
        "description": description,
        "creator_id": pending.creator_id,
        "status": "scheduled",
        "restrict_rsvp": int(restrict_rsvp),
    }


class EventDescriptionModal(discord.ui.Modal, title="活動內容（選填）"):
    description_input: discord.ui.TextInput[EventDescriptionModal] = discord.ui.TextInput(
        label="活動內容",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=1000,
        placeholder="例如：打完第三章，記得先補給裝備",
    )

    def __init__(self, pending: PendingEvent, *, event_id: str) -> None:
        super().__init__()
        self.pending = pending
        self.event_id = event_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        # 延遲匯入避免模組間的循環參照
        # （views_datetime.py / views_invitees.py 都需要這個檔案的 PendingEvent
        # 型別，此檔案又需要它們的 View 類別）。
        from src.bot.views_datetime import DateTimePickerView
        from src.bot.views_invitees import InviteePickerView
        from src.db import repo

        description = self.description_input.value.strip() or None

        if self.pending.starts_at_utc is None:
            # 使用者在指令裡沒有直接打時間，改用月曆／時間挑選器補上
            # （見 views_datetime.py 開頭：Discord 沒有原生日期選擇元件）。
            picker = DateTimePickerView(
                pending=self.pending, description=description, event_id=self.event_id
            )
            await interaction.response.send_message(
                content="請選擇活動的日期與時間：",
                embed=picker.build_embed(),
                view=picker,
                ephemeral=True,
            )
            picker.message = await interaction.original_response()
            return

        # 時間已經確定，接著選要 tag 的人／身分組。
        # allow_everyone_ping 現查現用：這個旗標只影響「要不要顯示 @everyone
        # 切換按鈕」，不值得為此塞進 PendingEvent 混淆它「使用者打了什麼」的
        # 語意，兩個呼叫點（這裡與 DateTimePickerView）各自查一次即可。
        settings = await repo.get_guild_settings(self.pending.guild_id)
        allow_everyone = bool(settings and settings["allow_everyone_ping"])

        invitee_picker = InviteePickerView(
            pending=self.pending,
            description=description,
            event_id=self.event_id,
            allow_everyone_ping=allow_everyone,
        )
        await interaction.response.send_message(
            content="請選擇要標記的參加對象（選填，可直接按下一步略過）：",
            embed=invitee_picker.build_embed(),
            view=invitee_picker,
            ephemeral=True,
        )
        invitee_picker.message = await interaction.original_response()

    async def on_error(
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        log.exception("建立活動時發生未預期錯誤", exc_info=error)
        message = "建立活動時發生錯誤，請稍後再試一次。"
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


class EventEditModal(discord.ui.Modal, title="編輯活動"):
    """`/event edit` 用：四個欄位（標題/時間/地點/內容）全部用目前的值預先
    帶好——`default=` 是建構參數，不是建構後才賦值，不會踩到
    `modals_poll.PollDetailsModal` 已經記過的 `.label` deprecation 那個坑
    （這裡也沒有動態換 label，純粹是每個 instance 的 default 值不同，本來
    就得在 `__init__` 動態建）。

    使用者沒改某個欄位、原樣送出時，`time_input` 的 `default` 是用
    `lib/timeparse.parse_datetime` 認得的格式（`%Y-%m-%d %H:%M`）格式化的，
    重新解析回去會是同一個 epoch，不會因為「沒改」而變成一個新的時間。
    """

    def __init__(self, *, event: Row, event_id: str, guild_id: int, tz: str) -> None:
        super().__init__()
        self.event_id = event_id
        self.guild_id = guild_id
        self.tz = tz

        try:
            zone = ZoneInfo(tz)
        except ZoneInfoNotFoundError:
            zone = ZoneInfo("Asia/Taipei")
        start_text = datetime.fromtimestamp(
            event["starts_at_utc"] / 1000, tz=zone
        ).strftime("%Y-%m-%d %H:%M")

        self.title_input: discord.ui.TextInput[EventEditModal] = discord.ui.TextInput(
            label="標題",
            style=discord.TextStyle.short,
            required=True,
            max_length=_MAX_TITLE_LENGTH,
            default=event["title"],
        )
        self.add_item(self.title_input)

        self.time_input: discord.ui.TextInput[EventEditModal] = discord.ui.TextInput(
            label="時間",
            style=discord.TextStyle.short,
            required=True,
            default=start_text,
            placeholder="2026-08-01 20:00",
        )
        self.add_item(self.time_input)

        self.location_input: discord.ui.TextInput[EventEditModal] = discord.ui.TextInput(
            label="地點（選填）",
            style=discord.TextStyle.short,
            required=False,
            default=event.get("location") or None,
        )
        self.add_item(self.location_input)

        self.description_input: discord.ui.TextInput[EventEditModal] = discord.ui.TextInput(
            label="內容（選填）",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=1000,
            default=event.get("description") or None,
        )
        self.add_item(self.description_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        from src.bot.embeds import build_event_embed
        from src.bot.native_events import sync_edit
        from src.db import repo
        from src.domain.rsvp import build_rsvp_summary
        from src.lib.timeparse import TimeParseError, parse_datetime

        title = self.title_input.value.strip()
        if not title:
            await interaction.response.send_message("活動標題不能是空的。", ephemeral=True)
            return

        try:
            starts_at_utc = parse_datetime(self.time_input.value, self.tz)
        except TimeParseError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        location = self.location_input.value.strip() or None
        description = self.description_input.value.strip() or None

        ok = await repo.update_event(
            self.event_id,
            self.guild_id,
            title=title,
            starts_at_utc=starts_at_utc,
            location=location,
            description=description,
        )
        if not ok:
            await interaction.response.send_message(
                "找不到這個活動，可能已被刪除。", ephemeral=True
            )
            return

        event_row = await repo.owned_event(self.event_id, self.guild_id)
        assert event_row is not None  # 剛剛才更新成功
        invitees = await repo.list_event_invitees(self.event_id, self.guild_id)
        rsvps = await repo.list_rsvps(self.event_id, self.guild_id)
        role_slots = await repo.list_event_role_slots(self.event_id, self.guild_id)
        role_signups = await repo.list_event_role_signups(self.event_id, self.guild_id)
        summary = (
            build_rsvp_summary(interaction.guild, invitees, rsvps)
            if interaction.guild is not None
            else None
        )

        # 重繪公告卡片：跟 views_rsvp.RsvpButton._refresh_announcement 同一種
        # 「編輯已存在的公告訊息」手法，只是這裡沒有現成的 interaction.message
        # 可用（觸發編輯的是 Modal 這個獨立 interaction，不是按在公告訊息上的
        # 元件），改用 interaction.client 找頻道、憑 message_id 抓訊息來編輯。
        # 沒帶 view=：/event edit 不會動到職位選單本身，只有 embed 需要帶上
        # role_slots/role_signups（否則已設定過職位的活動會在這次重繪後
        # 憑空少掉那幾個欄位——embed 是整包替換，不是只補丁）。
        if event_row["message_id"] and event_row["channel_id"]:
            channel = interaction.client.get_channel(int(event_row["channel_id"]))
            if channel is None:
                try:
                    channel = await interaction.client.fetch_channel(
                        int(event_row["channel_id"])
                    )
                except discord.HTTPException:
                    channel = None
            if channel is not None:
                try:
                    message = await channel.fetch_message(int(event_row["message_id"]))
                    await message.edit(
                        embed=build_event_embed(
                            event_row, invitees, summary, role_slots, role_signups,
                            interaction.guild,
                        )
                    )
                except discord.HTTPException:
                    log.warning("編輯活動 %s 後更新公告訊息失敗", self.event_id, exc_info=True)

        if event_row["discord_event_id"] and interaction.guild is not None:
            await sync_edit(interaction.guild, int(event_row["discord_event_id"]), event_row)

        await interaction.response.send_message("✅ 活動已更新。", ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.exception("編輯活動時發生未預期錯誤", exc_info=error)
        message = "編輯活動時發生錯誤，請稍後再試一次。"
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
