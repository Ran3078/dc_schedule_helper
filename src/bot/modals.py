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
from typing import Any

import discord

log = logging.getLogger(__name__)


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
