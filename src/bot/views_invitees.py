"""參加對象挑選器 —— `/event create` 流程裡，時間確定後、正式預覽發布前的一步。

用 Discord 原生的 `UserSelect` / `RoleSelect` 元件，各自最多可選 25 個目標
（Discord 元件本身的上限）。這一步全程選填：不選任何人也能直接按「下一步」
略過，活動一樣能建立，只是公告訊息不會 tag 任何人。

`@everyone` 只有在該伺服器的 `guild_settings.allow_everyone_ping` 開啟時才會
顯示切換按鈕 —— 沒開的話連按鈕都不會出現，而不是顯示了卻按下去沒作用；
這樣使用者不會被一個「看起來能用但其實被擋下來」的選項搞混。

這裡還有一個「僅限受邀對象回覆」的切換：Discord 沒有原生 checkbox 可以放在
一般訊息上（`discord.ui.Checkbox`／`CheckboxGroup` 只能用在 Modal 裡），所以
跟 @everyone 切換一樣，用按鈕模擬開關。開啟後，公告的 RSVP 按鈕只接受落在
邀請名單展開後的成員（見 domain/invitees.py 與 views_rsvp.py），其他人按了
會被拒絕、不寫入任何東西。
"""

from __future__ import annotations

import logging

import discord

from src.bot.embeds import build_event_embed
from src.bot.modals import PendingEvent, build_preview_row
from src.lib.mentions import invitee_rows

log = logging.getLogger(__name__)


class InviteePickerView(discord.ui.View):
    """1 個 UserSelect + 1 個 RoleSelect + 政策切換列（@everyone／僅限受邀）
    + 動作列（下一步／取消）。

    元件權重：2 個 select 各權重 5 = 10，政策切換列最多 2 個按鈕權重 2，
    動作列 2 個按鈕權重 2，合計 14，遠低於 25 的上限。用 4 個列（0–3），
    留第 5 列（row=4）給未來若還要加東西。
    """

    def __init__(
        self,
        *,
        pending: PendingEvent,
        description: str | None,
        event_id: str,
        allow_everyone_ping: bool,
    ) -> None:
        super().__init__(timeout=300)  # 5 分鐘沒動作就作廢，避免預覽訊息無限期卡著
        self.pending = pending
        self.description = description
        self.event_id = event_id
        self.message: discord.Message | None = None

        self.selected_user_ids: list[int] = []
        self.selected_role_ids: list[int] = []
        self.tag_everyone = False
        self.restrict_rsvp = False

        self.user_select: discord.ui.UserSelect = discord.ui.UserSelect(
            placeholder="選擇要標記的成員（選填，最多 25 位）",
            min_values=0,
            max_values=25,
            row=0,
        )
        self.user_select.callback = self._on_user_select
        self.add_item(self.user_select)

        self.role_select: discord.ui.RoleSelect = discord.ui.RoleSelect(
            placeholder="選擇要標記的身分組（選填，最多 25 個）",
            min_values=0,
            max_values=25,
            row=1,
        )
        self.role_select.callback = self._on_role_select
        self.add_item(self.role_select)

        self.everyone_button: discord.ui.Button | None = None
        if allow_everyone_ping:
            self.everyone_button = discord.ui.Button(
                label="@everyone：關", style=discord.ButtonStyle.secondary, row=2
            )
            self.everyone_button.callback = self._on_toggle_everyone
            self.add_item(self.everyone_button)

        self.restrict_button: discord.ui.Button = discord.ui.Button(
            label="🔓 開放所有人回覆", style=discord.ButtonStyle.secondary, row=2
        )
        self.restrict_button.callback = self._on_toggle_restrict
        self.add_item(self.restrict_button)

        self.next_button: discord.ui.Button = discord.ui.Button(
            label="下一步", style=discord.ButtonStyle.success, emoji="➡️", row=3
        )
        self.next_button.callback = self._on_next
        self.add_item(self.next_button)

        self.cancel_button: discord.ui.Button = discord.ui.Button(
            label="取消", style=discord.ButtonStyle.secondary, emoji="❌", row=3
        )
        self.cancel_button.callback = self._on_cancel
        self.add_item(self.cancel_button)

    def build_embed(self) -> discord.Embed:
        preview_row = build_preview_row(self.event_id, self.pending, self.description)
        embed = build_event_embed(preview_row)

        summary_parts: list[str] = []
        if self.tag_everyone:
            summary_parts.append("@everyone")
        summary_parts.extend(f"<@{uid}>" for uid in self.selected_user_ids)
        summary_parts.extend(f"<@&{rid}>" for rid in self.selected_role_ids)
        summary = (
            " ".join(summary_parts) if summary_parts else "（尚未選擇任何人，可直接下一步略過）"
        )
        field_name = "🔔 目前選擇的邀請對象"
        if self.restrict_rsvp:
            field_name += "（僅限以下對象回覆）"
        embed.add_field(name=field_name, value=summary, inline=False)

        return embed

    async def _rerender(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def _on_user_select(self, interaction: discord.Interaction) -> None:
        self.selected_user_ids = [member.id for member in self.user_select.values]
        await self._rerender(interaction)

    async def _on_role_select(self, interaction: discord.Interaction) -> None:
        self.selected_role_ids = [role.id for role in self.role_select.values]
        await self._rerender(interaction)

    async def _on_toggle_everyone(self, interaction: discord.Interaction) -> None:
        self.tag_everyone = not self.tag_everyone
        assert self.everyone_button is not None
        self.everyone_button.label = f"@everyone：{'開' if self.tag_everyone else '關'}"
        self.everyone_button.style = (
            discord.ButtonStyle.danger if self.tag_everyone else discord.ButtonStyle.secondary
        )
        await self._rerender(interaction)

    async def _on_toggle_restrict(self, interaction: discord.Interaction) -> None:
        self.restrict_rsvp = not self.restrict_rsvp
        self.restrict_button.label = (
            "🔒 僅限受邀對象回覆" if self.restrict_rsvp else "🔓 開放所有人回覆"
        )
        self.restrict_button.style = (
            discord.ButtonStyle.primary if self.restrict_rsvp else discord.ButtonStyle.secondary
        )
        await self._rerender(interaction)

    async def _on_cancel(self, interaction: discord.Interaction) -> None:
        for child in self.children:
            child.disabled = True  # type: ignore[attr-defined]
        self.stop()
        await interaction.response.edit_message(
            content="已取消，活動未建立。", embed=None, view=self
        )

    async def _on_next(self, interaction: discord.Interaction) -> None:
        # 延遲匯入避免 views_invitees.py ↔ views.py 的循環參照。
        from src.bot.views import ConfirmEventView

        self.stop()

        confirm_view = ConfirmEventView(
            event_id=self.event_id,
            pending=self.pending,
            description=self.description,
            user_ids=self.selected_user_ids,
            role_ids=self.selected_role_ids,
            tag_everyone=self.tag_everyone,
            restrict_rsvp=self.restrict_rsvp,
        )
        preview_row = build_preview_row(
            self.event_id, self.pending, self.description, restrict_rsvp=self.restrict_rsvp
        )
        preview_invitees = invitee_rows(self.selected_user_ids, self.selected_role_ids)
        if self.tag_everyone:
            preview_invitees.append(
                {"target_type": "everyone", "target_id": str(self.pending.guild_id)}
            )

        await interaction.response.edit_message(
            content="請確認活動內容，按「發布」才會公開發文；按「取消」則不會建立任何資料：",
            embed=build_event_embed(preview_row, preview_invitees),
            view=confirm_view,
        )
        confirm_view.message = await interaction.original_response()

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True  # type: ignore[attr-defined]
        if self.message is not None:
            try:
                await self.message.edit(
                    content="⌛ 已逾時未操作，活動未建立。請重新使用 `/event create`。",
                    embed=None,
                    view=self,
                )
            except discord.HTTPException:
                pass  # 訊息可能已被使用者刪除，逾時清理失敗不影響任何資料正確性
