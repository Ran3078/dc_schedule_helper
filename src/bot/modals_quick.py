"""按鈕觸發的快速建立 Modal（M9）：@提及選單、每週活動清單的「新增行程」
按鈕共用。

跟指令觸發的既有 Modal（`EventDescriptionModal`／`Ff14RecruitModal`／
`modals_poll.PollDetailsModal`）不同：按鈕點下去沒有 slash 指令參數可以先
收，標題／地點這些原本是指令參數的欄位，這裡全部要塞進 Modal 本身
（Discord 限制一個 Modal 最多 5 個元件），驗證（`cogs._shared
.validate_event_draft`）也因此挪到 Modal 的 `on_submit` 裡做，不是像
`Events._create_impl` 那樣在指令層就做完。

時間刻意不當成 Modal 欄位收——按鈕觸發的使用者多半是為了避開打指令，
讓他們手打時間字串反而容易打錯格式卡關，固定走 `DateTimePickerView`
（下拉選單挑）比較符合「不用打指令」這個初衷。

驗證通過後直接重用既有的 `DateTimePickerView`／`InviteePickerView`／
FF14 職位選單／`build_poll_embed` 等下游元件，不重新發明一次。
"""

from __future__ import annotations

import logging
from dataclasses import replace

import discord

from src.bot.cogs._shared import DraftValidationError, Row, validate_event_draft
from src.bot.modals import PendingEvent
from src.domain.roles import POSITIONS, sort_positions

log = logging.getLogger(__name__)

_MAX_QUESTION_LENGTH = 200


async def _resolve_announce_channel(
    interaction: discord.Interaction, guild_settings: Row | None
) -> discord.abc.Messageable | None:
    """跟 `Events._resolve_announce_channel` 同一套邏輯，這裡沒有 `self.bot`
    可用（Modal／View 不是 cog），改用 `interaction.client`——兩者是同一個
    Bot 實例。理由同 `PROCESS.md` 既有慣例：頻道解析是每個呼叫端各自複製
    一份的小 glue，不硬拉共用抽象。
    """
    channel_id = guild_settings["announce_channel_id"] if guild_settings else None
    if channel_id:
        channel = interaction.client.get_channel(int(channel_id))
        if channel is None:
            try:
                channel = await interaction.client.fetch_channel(int(channel_id))
            except discord.HTTPException:
                channel = None
        if channel is not None:
            return channel
    return interaction.channel


class QuickEventModal(discord.ui.Modal, title="快速建立活動"):
    """時間故意不放進 Modal 的文字欄位——按鈕觸發的使用者多半不熟悉指令，
    讓他們手打時間字串反而容易打錯格式、卡在錯誤訊息。固定走下一步的
    `DateTimePickerView`（下拉選單挑日期/時間），跟指令版一樣支援選日期，
    但不用自己記格式。
    """

    def __init__(self) -> None:
        super().__init__()

        self.title_input: discord.ui.TextInput[QuickEventModal] = discord.ui.TextInput(
            label="活動標題", style=discord.TextStyle.short, required=True, max_length=200
        )
        self.add_item(self.title_input)

        self.location_input: discord.ui.TextInput[QuickEventModal] = discord.ui.TextInput(
            label="地點（選填）", style=discord.TextStyle.short, required=False
        )
        self.add_item(self.location_input)

        self.description_input: discord.ui.TextInput[QuickEventModal] = discord.ui.TextInput(
            label="內容（選填）",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=1000,
        )
        self.add_item(self.description_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        # 延遲匯入避免模組間的循環參照，理由同 modals.EventDescriptionModal。
        from src.bot.views_datetime import DateTimePickerView
        from src.db import repo
        from src.lib.ids import new_id

        draft = await validate_event_draft(
            interaction.client,
            interaction,
            title=self.title_input.value,
            time=None,  # 沒有時間欄位可填，一律留給 DateTimePickerView 挑
            location=self.location_input.value.strip() or None,
            duration=None,
        )
        if isinstance(draft, DraftValidationError):
            await interaction.response.send_message(draft.message, ephemeral=True)
            return

        guild_settings = await repo.get_guild_settings(draft.guild_id)
        channel = await _resolve_announce_channel(interaction, guild_settings)
        pending = replace(draft, channel_id=channel.id if channel is not None else draft.channel_id)
        description = self.description_input.value.strip() or None

        # draft.starts_at_utc 一定是 None（上面沒有傳 time），這裡固定走
        # DateTimePickerView，不會有 InviteePickerView 那條分支。
        picker = DateTimePickerView(pending=pending, description=description, event_id=new_id())
        await interaction.response.send_message(
            content="請選擇活動的日期與時間：",
            embed=picker.build_embed(),
            view=picker,
            ephemeral=True,
        )
        picker.message = await interaction.original_response()

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.exception("快速建立活動時發生未預期錯誤", exc_info=error)
        message = "建立活動時發生錯誤，請稍後再試一次。"
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


class _QuickFf14PositionSelect(discord.ui.Select):
    def __init__(self, picker: QuickFf14PositionPickerView) -> None:
        self.picker = picker
        super().__init__(
            placeholder="選擇這場要開的職位（至少 1 個）",
            min_values=1,
            max_values=len(POSITIONS),
            options=[discord.SelectOption(label=p, value=p) for p in POSITIONS],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.picker.on_pick(interaction, self.values)


class QuickFf14PositionPickerView(discord.ui.View):
    """`QuickFf14Modal` 驗證通過後的下一步：Discord 不允許 Modal submit 之後
    直接再開一個 Modal，職位選擇因此不能像 `Ff14RecruitModal` 那樣塞進同一個
    Modal，改用短命 View（比照 `views_datetime.DateTimePickerView` 同一種
    `timeout=300` 手法）。"""

    def __init__(self, *, pending: PendingEvent, guild_settings: Row | None) -> None:
        super().__init__(timeout=300)
        self.pending = pending
        self.guild_settings = guild_settings
        self.message: discord.Message | None = None
        self.add_item(_QuickFf14PositionSelect(self))

    async def on_pick(self, interaction: discord.Interaction, values: list[str]) -> None:
        from src.bot.views_datetime import DateTimePickerView
        from src.bot.views_invitees import InviteePickerView
        from src.lib.ids import new_id

        positions = sort_positions(values)

        if self.pending.starts_at_utc is None:
            picker = DateTimePickerView(
                pending=self.pending, description=None, event_id=new_id(), positions=positions
            )
            await interaction.response.edit_message(
                content="請選擇活動的日期與時間：", embed=picker.build_embed(), view=picker
            )
            picker.message = await interaction.original_response()
            return

        allow_everyone = bool(self.guild_settings and self.guild_settings["allow_everyone_ping"])
        invitee_picker = InviteePickerView(
            pending=self.pending,
            description=None,
            event_id=new_id(),
            allow_everyone_ping=allow_everyone,
            positions=positions,
        )
        await interaction.response.edit_message(
            content="請選擇要標記的參加對象（選填，可直接按下一步略過）：",
            embed=invitee_picker.build_embed(),
            view=invitee_picker,
        )
        invitee_picker.message = await interaction.original_response()

    async def on_timeout(self) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Item):
                child.disabled = True  # type: ignore[attr-defined]
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass  # 訊息可能已被使用者刪除，逾時清理失敗不影響任何資料正確性


class QuickFf14Modal(discord.ui.Modal, title="快速建立 FF14 招募"):
    """時間一樣不放進 Modal（理由同 `QuickEventModal`），固定走職位挑選 →
    `DateTimePickerView` 這條路。"""

    def __init__(self) -> None:
        super().__init__()

        self.title_input: discord.ui.TextInput[QuickFf14Modal] = discord.ui.TextInput(
            label="活動標題", style=discord.TextStyle.short, required=True, max_length=200
        )
        self.add_item(self.title_input)

        self.location_input: discord.ui.TextInput[QuickFf14Modal] = discord.ui.TextInput(
            label="地點（選填）", style=discord.TextStyle.short, required=False
        )
        self.add_item(self.location_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        from src.db import repo

        draft = await validate_event_draft(
            interaction.client,
            interaction,
            title=self.title_input.value,
            time=None,  # 沒有時間欄位可填，一律留給下一步的日期挑選器
            location=self.location_input.value.strip() or None,
            duration=None,
        )
        if isinstance(draft, DraftValidationError):
            await interaction.response.send_message(draft.message, ephemeral=True)
            return

        guild_settings = await repo.get_guild_settings(draft.guild_id)
        channel = await _resolve_announce_channel(interaction, guild_settings)
        pending = replace(draft, channel_id=channel.id if channel is not None else draft.channel_id)

        picker = QuickFf14PositionPickerView(pending=pending, guild_settings=guild_settings)
        await interaction.response.send_message(
            content="請選擇這場要開的職位：", view=picker, ephemeral=True
        )
        picker.message = await interaction.original_response()

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.exception("快速建立 FF14 招募時發生未預期錯誤", exc_info=error)
        message = "建立活動時發生錯誤，請稍後再試一次。"
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


class QuickPollModal(discord.ui.Modal, title="快速建立投票"):
    """只支援最常見的組合（單選、非匿名、可改票、無自動截止）——進階選項
    仍需要完整的 `/poll create`，這裡刻意不做到功能對等，換取「兩欄填一填
    就好」的低門檻。跟 `/poll create` 一樣不限定誰能建立投票（沒有
    `is_organizer` 權限檢查）。
    """

    def __init__(self) -> None:
        super().__init__()

        self.question_input: discord.ui.TextInput[QuickPollModal] = discord.ui.TextInput(
            label="投票問題", style=discord.TextStyle.short, required=True, max_length=200
        )
        self.add_item(self.question_input)

        self.options_input: discord.ui.TextInput[QuickPollModal] = discord.ui.TextInput(
            label="選項（每行一個，2～25 個）",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=2000,
            placeholder="每行填一個選項，例如：\n火鍋\n燒肉\n拉麵",
        )
        self.add_item(self.options_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        from src.bot.embeds import build_poll_embed
        from src.bot.views_poll import build_poll_vote_view
        from src.db import repo
        from src.domain.polls import MAX_OPTIONS, MIN_OPTIONS, split_options
        from src.lib.ids import new_id

        assert interaction.guild_id is not None  # guild_only() 保證（見 MentionMenuView）

        question = self.question_input.value.strip()
        if not question:
            await interaction.response.send_message("投票問題不能是空的。", ephemeral=True)
            return
        if len(question) > _MAX_QUESTION_LENGTH:
            await interaction.response.send_message(
                f"投票問題太長了（{len(question)} 字，上限 {_MAX_QUESTION_LENGTH} 字）。",
                ephemeral=True,
            )
            return

        raw_options = split_options(self.options_input.value)
        if not (MIN_OPTIONS <= len(raw_options) <= MAX_OPTIONS):
            await interaction.response.send_message(
                f"選項數量要在 {MIN_OPTIONS}～{MAX_OPTIONS} 個之間（目前 {len(raw_options)} 個）。",
                ephemeral=True,
            )
            return
        option_pairs = [(raw[:100], None) for raw in raw_options]

        poll_id = new_id()
        await repo.create_poll(
            poll_id=poll_id,
            guild_id=interaction.guild_id,
            channel_id=interaction.channel_id,
            creator_id=interaction.user.id,
            question=question,
            options=option_pairs,
            kind="generic",
            multi=False,
            anonymous=False,
            allow_change=True,
            closes_at=None,
        )

        poll = await repo.owned_poll(poll_id, interaction.guild_id)
        assert poll is not None  # 剛剛才在同一次交易寫入
        poll_options = await repo.list_poll_options(poll_id, interaction.guild_id)

        await interaction.response.send_message(
            embed=build_poll_embed(poll, poll_options, []),
            view=build_poll_vote_view(poll_id, poll_options, multi=False),
        )
        message = await interaction.original_response()
        await repo.set_poll_message(poll_id, interaction.guild_id, message.id)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.exception("快速建立投票時發生未預期錯誤", exc_info=error)
        message = "建立投票時發生錯誤，請稍後再試一次。"
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
