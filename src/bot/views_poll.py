"""投票的持久化下拉選單。

跟 `views_rsvp.py` 的 `RsvpButton` 一樣走 `discord.ui.DynamicItem`（訊息會存在
好幾天甚至好幾週，bot 一定會因為重啟而丟掉記憶體狀態，理由同該檔案開頭
註解）。這是本專案第一個**持久化 Select**——`views_invitees.py`／
`views_datetime.py` 裡的 Select 都是非持久化、`timeout=300` 的短命 View。

`custom_id` 格式：`poll:vote:<poll_id>`。跟 `RsvpButton` 一樣，`from_custom_id`
不需要重建完整的選項清單——discord.py 在呼叫 `callback` 前，會先用這次互動的
payload 呼叫 `_refresh_state()`，把「這次實際選了哪些值」灌進
`self.item.values`（透過 custom_id 對應，不管我們重建的 Select 物件本身帶不帶
`options`），所以 `callback` 只管讀 `self.item.values` 就好。
"""

from __future__ import annotations

import logging
import re

import discord

from src.bot.embeds import Row, build_poll_embed
from src.db import repo
from src.lib.ids import build_custom_id

log = logging.getLogger(__name__)

_VOTE_TEMPLATE = re.compile(r"^poll:vote:(?P<poll_id>[^:]+)$")

_RESULT_MESSAGES = {
    "locked": "這個投票不能改票，你已經投過了。",
    "closed": "這個投票已經截止了。",
    "not_found": "找不到這個投票，可能已被刪除。",
}


def _build_select(
    poll_id: str, options: list[Row], *, multi: bool, disabled: bool
) -> discord.ui.Select:
    return discord.ui.Select(
        custom_id=build_custom_id("poll", "vote", poll_id),
        placeholder="投票已截止" if disabled else "選擇你的答案",
        min_values=1 if options else 0,
        max_values=(len(options) if multi else 1) or 1,
        options=[discord.SelectOption(label=o["label"][:100], value=o["id"]) for o in options],
        disabled=disabled,
    )


class PollVoteSelect(discord.ui.DynamicItem[discord.ui.Select], template=_VOTE_TEMPLATE):
    def __init__(
        self, *, poll_id: str, options: list[Row], multi: bool, disabled: bool = False
    ) -> None:
        self.poll_id = poll_id
        super().__init__(_build_select(poll_id, options, multi=multi, disabled=disabled))

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item,
        match: re.Match[str],
        /,
    ) -> PollVoteSelect:
        # 不重建選項清單（見檔案開頭說明）——callback 只靠 self.item.values
        # 讀這次選了什麼，這裡的 options=[] 只是滿足 __init__ 的簽名。
        return cls(poll_id=match["poll_id"], options=[], multi=True)

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            # 理論上不會發生——下拉選單只會出現在公告訊息上，公告只會發在伺服器頻道。
            return

        poll = await repo.owned_poll(self.poll_id, interaction.guild_id)
        if poll is None:
            await interaction.response.send_message(_RESULT_MESSAGES["not_found"], ephemeral=True)
            return
        if poll["status"] != "open":
            await interaction.response.send_message(_RESULT_MESSAGES["closed"], ephemeral=True)
            return

        result = await repo.cast_vote(
            self.poll_id,
            interaction.guild_id,
            interaction.user.id,
            self.item.values,
            allow_change=bool(poll["allow_change"]),
        )
        if result != "ok":
            await interaction.response.send_message(
                _RESULT_MESSAGES.get(result, "投票失敗，請稍後再試。"), ephemeral=True
            )
            return

        await interaction.response.send_message("已記錄你的投票。", ephemeral=True)
        await self._refresh_announcement(interaction, poll)

    async def _refresh_announcement(self, interaction: discord.Interaction, poll: Row) -> None:
        message = interaction.message
        if message is None:
            return

        options = await repo.list_poll_options(self.poll_id, poll["guild_id"])
        votes = await repo.list_poll_votes(self.poll_id, poll["guild_id"])
        try:
            await message.edit(embed=build_poll_embed(poll, options, votes))
        except discord.HTTPException:
            log.warning("更新投票 %s 的公告訊息失敗", self.poll_id, exc_info=True)


def build_poll_vote_view(
    poll_id: str, options: list[Row], *, multi: bool, disabled: bool = False
) -> discord.ui.View:
    """建立投票公告要附帶的下拉選單。只在**發布**與 **/poll close** 時呼叫
    （比照 `views_rsvp.build_rsvp_view` 只在發布時呼叫一次）——之後的持久性
    完全靠 `PollVoteSelect` 的 `DynamicItem` 機制，重啟不需要重新附加。

    `disabled=True` 給 `/poll close` 用：關閉投票後重繪一個 disabled 版本
    edit 上去，讓使用者一眼看出已經不能再投了；`callback` 裡的 `status`
    檢查則是防呆備援，防止使用者手上還留著舊訊息快取硬點。
    """
    view = discord.ui.View(timeout=None)
    view.add_item(PollVoteSelect(poll_id=poll_id, options=options, multi=multi, disabled=disabled))
    return view
