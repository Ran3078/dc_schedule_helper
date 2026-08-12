"""`/ff14_recruit` 的內容 Modal（M8）。

比照 `modals.py` 的 `EventDescriptionModal`——是它的 FF14 版本，理由同
`modals_poll.py` 跟 `modals.py` 分檔：FF14 招募這條路徑專屬的欄位（職位
複選）不該混進 `/event create` 共用的 Modal 裡。

職位複選用 `discord.ui.Label` 包 `discord.ui.Select`（discord.py 2.6 起
支援把 Select 這類元件放進 Modal，不再只能塞 TextInput），一個 Modal
同時收活動內容跟職位設定，不用像原本設計那樣拆成「Modal 收文字 → 另一個
View 選位置」兩步。
"""

from __future__ import annotations

import logging

import discord

from src.bot.modals import PendingEvent
from src.domain.roles import POSITIONS, sort_positions

log = logging.getLogger(__name__)


class Ff14RecruitModal(discord.ui.Modal, title="FF14 團本招募內容"):
    def __init__(self, pending: PendingEvent, *, event_id: str) -> None:
        super().__init__()
        self.pending = pending
        self.event_id = event_id

        self.description_input: discord.ui.TextInput[Ff14RecruitModal] = discord.ui.TextInput(
            label="活動內容（選填）",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=1000,
            placeholder="例如：打完第三章，記得先補給裝備",
        )
        self.add_item(self.description_input)

        self.position_select: discord.ui.Select[Ff14RecruitModal] = discord.ui.Select(
            placeholder="選擇這場要開的職位（至少 1 個）",
            min_values=1,
            max_values=len(POSITIONS),
            options=[discord.SelectOption(label=p, value=p) for p in POSITIONS],
        )
        self.add_item(
            discord.ui.Label(text="職位名額", component=self.position_select)
        )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        # 延遲匯入避免模組間的循環參照，理由同 modals.EventDescriptionModal。
        from src.bot.views_datetime import DateTimePickerView
        from src.bot.views_invitees import InviteePickerView
        from src.db import repo

        description = self.description_input.value.strip() or None
        positions = sort_positions(self.position_select.values)

        if self.pending.starts_at_utc is None:
            # 使用者在指令裡沒有直接打時間，改用月曆／時間挑選器補上
            # （見 views_datetime.py 開頭：Discord 沒有原生日期選擇元件）。
            picker = DateTimePickerView(
                pending=self.pending,
                description=description,
                event_id=self.event_id,
                positions=positions,
            )
            await interaction.response.send_message(
                content="請選擇活動的日期與時間：",
                embed=picker.build_embed(),
                view=picker,
                ephemeral=True,
            )
            picker.message = await interaction.original_response()
            return

        settings = await repo.get_guild_settings(self.pending.guild_id)
        allow_everyone = bool(settings and settings["allow_everyone_ping"])

        invitee_picker = InviteePickerView(
            pending=self.pending,
            description=description,
            event_id=self.event_id,
            allow_everyone_ping=allow_everyone,
            positions=positions,
        )
        await interaction.response.send_message(
            content="請選擇要標記的參加對象（選填，可直接按下一步略過）：",
            embed=invitee_picker.build_embed(),
            view=invitee_picker,
            ephemeral=True,
        )
        invitee_picker.message = await interaction.original_response()

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.exception("建立 FF14 招募活動時發生未預期錯誤", exc_info=error)
        message = "建立活動時發生錯誤，請稍後再試一次。"
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
