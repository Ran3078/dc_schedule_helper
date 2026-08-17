# Review instructions

這個檔案只影響 Claude Code GitHub Action 的 PR 審查標準，不影響一般開發
（一般規則寫在 `PROCESS.md`／`README.md`）。

## Always check

- 每個查詢的 `guild_id` 是不是必填參數、有沒有出現在 WHERE 子句裡（見
  README「多伺服器」章節）——這是本專案最容易寫錯、也最難事後補救的
  一條，抓到直接標 Important。
- 操作子表（`rsvps`／`poll_votes`／`poll_options`／`event_invitees`／
  `reminders`）前有沒有先用 `owned_event()`／`owned_poll()` 確認母體屬於
  當前伺服器——子表沒有自己的 `guild_id` 欄位，少了這層檢查就是跨伺服器
  資料外洩。
- 新的 migration 是不是可重複執行（`CREATE TABLE IF NOT EXISTS` 等），
  因為每次 deploy 都會重跑一遍。
- DB 存取有沒有繞過 `src/db/engine.py` 直接 import `libsql`——同步驅動
  直接呼叫會卡住 asyncio 事件迴圈，導致 gateway 心跳逾時被 Discord 斷線。
- 唯一鍵衝突有沒有靠例外型別判斷——libsql 0.1.11 拋的是普通 `ValueError`
  而非 DBAPI `IntegrityError`，這樣判斷不可靠。

## What Important means here

只有真的會炸資料或炸連線的才算 Important：跨伺服器資料外洩、活動/投票
寫壞、asyncio 事件迴圈被卡住。風格、命名、小重構一律 Nit 或不提。

## Cap the nits

單次審查最多列 5 個 Nit，超過的用「還有 N 個類似」在摘要裡帶過，不要每個
都留 inline comment。
