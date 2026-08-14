"""職業挑選（M8）——`views_rsvp.PositionSelect` 選定位置後的延續。

短命 View（`timeout=300`），跟 `views_datetime.DateTimePickerView`／
`views_invitees.InviteePickerView` 同一個層級：這一步只是**同一次使用者
操作**的延續，不需要撐過 bot 重啟，不走 `DynamicItem` 那一套。
"""

from __future__ import annotations

import logging

import discord

from src.bot.embeds import Row, build_event_embed
from src.bot.views_rsvp import build_event_controls_view, notify_promotion
from src.db import repo
from src.domain.roles import JOBS_BY_POSITION
from src.domain.rsvp import build_rsvp_summary

log = logging.getLogger(__name__)


class _JobSelect(discord.ui.Select):
    def __init__(self, picker: JobPickerView) -> None:
        self.picker = picker
        options = [
            discord.SelectOption(label=job, value=job)
            for job in JOBS_BY_POSITION[picker.position]
        ]
        super().__init__(placeholder="選擇你的職業", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.picker.on_pick(interaction, self.values[0])


class JobPickerView(discord.ui.View):
    def __init__(self, *, event_id: str, role_slot_id: str, position: str) -> None:
        super().__init__(timeout=300)  # 5 分鐘沒動作就作廢，避免預覽訊息無限期卡著
        self.event_id = event_id
        self.role_slot_id = role_slot_id
        self.position = position
        self.message: discord.Message | None = None
        self.add_item(_JobSelect(self))

    async def on_pick(self, interaction: discord.Interaction, job: str) -> None:
        if interaction.guild_id is None:
            return
        guild_id = interaction.guild_id

        # 若使用者原本已經選了別的位置（換位置的情境），先移除舊的、視情況
        # 觸發舊位置的候補遞補——理由同 views_rsvp.RsvpButton.callback 對
        # 非「參加」狀態的處理。
        previous = await repo.remove_role_signup(self.event_id, guild_id, interaction.user.id)
        if (
            previous is not None
            and previous["role_slot_id"] != self.role_slot_id
            and not previous["waitlisted"]
            and interaction.channel
        ):
            promoted = await repo.promote_next_waitlisted(previous["role_slot_id"])
            if promoted is not None:
                role_slots = await repo.list_event_role_slots(self.event_id, guild_id)
                slot = next(
                    (s for s in role_slots if s["id"] == previous["role_slot_id"]), None
                )
                if slot is not None:
                    await notify_promotion(
                        interaction.channel, slot["position"], promoted["job"],
                        promoted["user_id"],
                    )

        row = await repo.set_role_signup(
            self.event_id, guild_id, interaction.user.id, self.role_slot_id, job
        )
        if row is None:
            await interaction.response.send_message(
                "這個位置已經不存在了，請重新整理活動公告後再試一次。", ephemeral=True
            )
            return

        if row["waitlisted"]:
            text = f"「{self.position}」目前已滿，已將你加入候補名單（{job}）。"
        else:
            text = f"已將你的位置設為 {self.position}（{job}）。"

        for child in self.children:
            if isinstance(child, discord.ui.Item):
                child.disabled = True  # type: ignore[attr-defined]
        self.stop()
        await interaction.response.edit_message(content=text, view=self)

        await self._refresh_public_announcement(interaction, guild_id)

    async def _refresh_public_announcement(
        self, interaction: discord.Interaction, guild_id: int
    ) -> None:
        """跟 `views_rsvp._refresh_announcement` 不同：這裡沒有
        `interaction.message` 可用（觸發的是 ephemeral 職業選單，不是公告
        本身），改用 event 的 `message_id`/`channel_id` 去抓，比照
        `modals.EventEditModal.on_submit` 的手法。
        """
        event: Row | None = await repo.owned_event(self.event_id, guild_id)
        if event is None or not event["message_id"] or not event["channel_id"]:
            return

        channel = interaction.client.get_channel(int(event["channel_id"]))
        if channel is None:
            try:
                channel = await interaction.client.fetch_channel(int(event["channel_id"]))
            except discord.HTTPException:
                return
        if channel is None:
            return

        invitees = await repo.list_event_invitees(self.event_id, guild_id)
        rsvps = await repo.list_rsvps(self.event_id, guild_id)
        role_slots = await repo.list_event_role_slots(self.event_id, guild_id)
        role_signups = await repo.list_event_role_signups(self.event_id, guild_id)
        summary = (
            build_rsvp_summary(interaction.guild, invitees, rsvps)
            if interaction.guild is not None
            else None
        )

        try:
            message = await channel.fetch_message(int(event["message_id"]))
            await message.edit(
                embed=build_event_embed(event, invitees, summary, role_slots, role_signups),
                view=build_event_controls_view(self.event_id, role_slots, role_signups),
            )
        except discord.HTTPException:
            log.warning("選擇職業後更新活動 %s 公告訊息失敗", self.event_id, exc_info=True)

    async def on_timeout(self) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Item):
                child.disabled = True  # type: ignore[attr-defined]
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass  # 訊息可能已被使用者刪除，逾時清理失敗不影響任何資料正確性
