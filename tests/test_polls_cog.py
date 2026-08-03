"""煙霧測試：/poll cog 能不能被 discord.py 正常載入。

不測互動行為（那些在 test_polls_views.py／test_polls_repo.py），只抓
`@app_commands.describe` / `@app_commands.rename` 這類裝飾器在載入當下就會
炸掉的錯誤——例如 describe() 用了跟 Python 參數名不一致的 key（曾經真的
在手動啟動 bot 時炸過一次：`rename(poll_id="id")` 卻在 describe() 裡寫
`id=...`，這種錯誤不會被 test_polls_views.py 那種直接呼叫 callback 的測試
抓到，只有實際載入 cog、讓 discord.py 解析簽名時才會浮現）。
"""

from __future__ import annotations

import discord
from discord.ext import commands


async def test_polls_cog_loads_without_error() -> None:
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.default())
    await bot.load_extension("src.bot.cogs.polls")
    assert bot.get_cog("Polls") is not None
