# PROCESS.md — 專案交接文件

> 給接手這個專案的下一位 AI／開發者看的。目的是讓你不用重新爬一次對話記錄
> 就能知道「為什麼是這樣做」，尤其是那些踩過坑才學到的細節。
>
> 最後更新：2026-08-03（M0–M5 完成）

---

## 這是什麼專案

Discord 行事曆排程機器人，**個人／小型伺服器自用**，目標是取代 Sesh／
Apollo／Raid-Helper 這類商業 bot（它們的核心功能都鎖在 $3.5–5/mo 付費牆
後面）。使用者需求原文列在 [PLAN.md](PLAN.md)（**這份檔案故意不進版控**，
是本機用的規劃草稿，`.gitignore` 裡排除了它——如果你在 repo 裡找不到它，
去問使用者要，不要嘗試從 git 歷史挖，舊版本已經被移除且不完整）。

核心需求（已全部實作，含投票）：
1. Tag 人的能力（`@user`／`@role`／`@everyone`）
2. 安排參加對象（User Select／Role Select）
3. 時間必填、地點與內容選填
4. 投票開關 + 單選／複選

## 技術棧與部署

| 項目 | 選擇 | 備註 |
|------|------|------|
| 語言 | Python 3.13 | discord.py 生態選的，非我方案唯一選項 |
| Discord | discord.py 2.7 | `app_commands`／`ui.Modal,Select`／`ui.DynamicItem`／`discord.Poll` |
| 資料庫 | Turso (libSQL)，官方 `libsql` 驅動 | **無 ORM**，手寫 SQL（見下方「為什麼沒有 ORM」） |
| HTTP | `aiohttp`（discord.py 自帶） | 只用來做 `/healthz` 保活端點 |
| 部署 | Render 免費 Web Service | 750 instance hours/月，需要外部 cron 保活 |
| 保活 | cron-job.org 每 10 分鐘 ping `/healthz` | Render 免費方案閒置 15 分鐘會休眠 |

**尚未部署到 Render**——整個開發階段都在本機用真實 Turso 資料庫測試
（`.env` 裡的憑證是本機開發用，還沒填進 Render 的環境變數）。部署步驟完整
寫在 [README.md](README.md)。

---

## 目前完成度：M0–M5

對照 [PLAN.md](PLAN.md) 的里程碑表：

| 里程碑 | 狀態 | 內容 |
|--------|------|------|
| M0 骨架 | ✅ 完成 | DB 層、migration runner、`/healthz`、`/ping`、多伺服器支援 |
| M1 活動核心 | ✅ 完成 | `/event create`／`list`／`info`，時間解析，Embed |
| M2 參加對象＋Tag | ✅ 完成 | UserSelect/RoleSelect、mention 白名單、`restrict_rsvp` 切換 |
| M3 RSVP | ✅ 完成 | 參加/待定/不參加按鈕（`DynamicItem` 持久化） |
| M4 提醒 | ✅ 完成 | `reminders` 表 + 30 秒排程迴圈 + 逾期補償 |
| M5 投票 | ✅ 完成 | `/poll create|close|results`，單選/複選，`DynamicItem` 持久化下拉選單 |
| M6 原生活動同步 | ❌ 未開始 | 同步到 Discord「活動」分頁 |
| M7 收尾指令 | ❌ 未開始 | `/event edit`／`cancel`／`invite`／`ping`、`/settings`、`/timezone` |

**測試：391 個全過，ruff 乾淨。** M0–M4 都在本機（用開發者的私人 Discord
伺服器 + 真實 Turso 資料庫）手動驗證過，不是只有單元測試。**M5（投票，含
時段投票自動建立活動）目前只驗證過測試套件與 import／啟動正常，還沒有人
在真實 Discord 上手動點過 `/poll create` 走一輪完整流程**——這是下一個該
做的驗證，不是「假設它能用」。

> **關於 M5 的實作來源**：M5 的程式碼是分兩次、在不同的對話空檔被寫入
> repo 的，都不是「當下這次對話」直接寫的，但每次接手後都有照樣審查、
> 跑測試、跑 lint、對照這份文件的紀律逐條檢查過，不是照單全收：
> 1. 第一批（`src/domain/polls.py`／`views_poll.py`／`cogs/polls.py`／
>    `cogs/_shared.py` 的初版）做出 `/poll create|close|results` 基本功能，
>    選項用單行 `|` 分隔輸入。
> 2. 第二批補上「時段投票（`kind=time_slot`）關閉時自動挑最高票時段建立
>    正式活動」——這是 [PLAN.md](PLAN.md) §6 Phase 2 第 1 項規劃的招牌
>    功能，原本沒做，補完過程記在 [Revise.md](Revise.md)（**這份檔案不
>    進版控**，`.gitignore` 現在排除所有 `.md` 只留 README，若找不到
>    去問使用者要）。同一次也把選項輸入從單行 `|` 分隔改成 Modal 多行
>    文字框（`modals_poll.py`），理由跟 `/event create` 用 Modal 收活動
>    內容一樣。
>
> 兩批都遵守了既有紀律（多伺服器範圍界定、`DynamicItem` 持久化、原子交易、
> 樂觀鎖），且第二批明確採用了 Revise.md 裡寫的設計決策（平票/沒人投票都
> 不自動建立、參加對象＝全部投票者不分投給哪個選項）。**你接手後第一件事
> 最好還是自己動手測一次 `/poll create` 走完整流程（含 `kind=time_slot`
> 關閉後自動建立活動那條路徑），不要只憑「測試過了」就假設沒問題**——這
> 條提醒本身也已經被驗證過一次還不夠，值得繼續留著。

### 目前實際存在的指令

```
/ping                          健康檢查（gateway + DB 延遲）
/event create title time? location? duration?
                                建立活動，time 留空會跳出日期時間挑選器
/event list [scope] [limit]    列出活動（upcoming/mine/all）
/event info <event_id>         查看單一活動詳情
/poll create question multi? anonymous? allow_change? closes? kind?
                                建立投票，送出後彈 Modal 收選項（多行文字框，
                                一行一個，2～25 個）。kind=time_slot 時選項
                                會被解析成時間、顯示 Discord 時間戳
/poll close <id>               關閉投票（僅建立者可操作）。若是 time_slot
                                投票，會挑最高票時段自動建立正式活動並發布
                                公告（平票／沒人投票／獲勝選項時間資料損毀
                                都刻意不自動建立，回覆訊息請使用者自行
                                `/event create`）。參加對象＝這個投票裡所有
                                投過票的人，不分投給哪個選項
/poll results <id>              查看投票結果
```

**沒有** `/event edit`、`/event cancel`、`/event invite`、`/event ping`、
`/settings`、`/timezone` —— 這些是 M7 的範圍，還沒做。

### `/event create` 的完整互動流程

這是本專案最複雜的一條互動鏈，串了 5 個 View／Modal，交接前務必搞懂：

```
/event create (title 必填, time 選填, location/duration 選填)
  │
  ├─ time 有填 → 直接驗證解析
  ├─ time 沒填 → 標記為「稍後用挑選器決定」
  │
  ▼
EventDescriptionModal（Modal，收活動內容，選填）
  │
  ├─ starts_at_utc 還是 None → 顯示 DateTimePickerView
  │     （14 天捲動視窗選日期 + 小時/分鐘/持續時間下拉選單）
  │     確認後 → 進 InviteePickerView
  │
  └─ starts_at_utc 已知 → 直接進 InviteePickerView
        │
        ▼
      InviteePickerView（UserSelect + RoleSelect +
                          @everyone 切換[需 allow_everyone_ping] +
                          僅限受邀對象回覆 切換）
        │ 全程選填，可直接「下一步」略過
        ▼
      ConfirmEventView（發布前最後預覽，防手滑打錯字）
        │
        ├─ 取消 → 資料庫完全沒寫入痕跡
        └─ 發布 → repo.create_event() 原子寫入：
              events + event_invitees + reminders（三者同一次 commit）
              → 頻道發公告（帶 RsvpButton 三顆按鈕）
```

**這條鏈上每個 View 都是非持久化的**（`timeout=300`），只有最終公告訊息上
的 RSVP 按鈕（`RsvpButton`，見下方）是持久化的。

---

## 關鍵架構決策（新開工前務必先讀）

### 1. 沒有 ORM

原本評估 SQLAlchemy + `sqlalchemy-libsql`，查證後發現該 dialect 最新版
（0.2.0, 2025-05）仍依賴**已棄用歸檔**的 `libsql-experimental`。改用官方
`libsql` 驅動 + 手寫 SQL，全部 CRUD 集中在 [src/db/repo.py](src/db/repo.py)。

### 2. 同步 DB 驅動 × asyncio

Turso 的官方 Python 驅動（`libsql`）是**同步**的，discord.py 是 asyncio。
[src/db/engine.py](src/db/engine.py) 用 `asyncio.to_thread()` 包裝所有 DB
呼叫，並用單一連線 + `threading.Lock` 序列化存取。**任何新的 DB 操作都要
走這一層，不要在別處直接 import `libsql`。**

**libsql 0.1.11 的已知怪癖**：唯一鍵衝突拋的是普通 `ValueError`，不是
DBAPI 標準的 `IntegrityError`。不要用例外型別判斷衝突，要用
`INSERT OR IGNORE` 或 `INSERT ... ON CONFLICT DO UPDATE`。

### 3. 多伺服器紀律（★ 最容易寫錯的一條）

本 bot 支援多伺服器。**每一個查詢都必須以 `guild_id` 為界**，規則詳列在
[src/db/repo.py](src/db/repo.py) 檔案開頭的大註解，簡述：

- 讀取活動／投票的函式，`guild_id` 一律必填且進 WHERE 子句
- `guild_id` 一律來自 `interaction.guild_id`，不要從設定檔或全域變數取
- 子表（`rsvps`／`event_invitees`／`reminders` 等）沒有自己的 `guild_id`
  欄位，操作前要先確認母體（活動）屬於當前伺服器（`owned_event()` 或
  JOIN events）
- **唯一例外**：`list_due_reminders()`——排程迴圈是系統層級背景工作，
  本來就該一次撈全部伺服器的到期提醒，這是刻意的設計，不是漏洞

### 4. 持久化 vs 非持久化 View 要分清楚

- **非持久化**（`ConfirmEventView`、`DateTimePickerView`、
  `InviteePickerView`）：生命週期幾分鐘，`timeout=300` 就夠，不需要撐過
  bot 重啟。
- **持久化**（`RsvpButton`）：訊息會存在好幾天甚至幾週，中間 bot 一定會
  因為 Render 休眠／redeploy 重啟好幾次。用 `discord.ui.DynamicItem`
  （不是舊式 `View` + `add_view()`）——custom_id 帶 event_id
  （格式 `ev:rsvp:<event_id>:<status>`），Discord 每次互動都把訊息當下
  的元件結構整包送回來，靠 regex 樣板比對重建，完全不需要在啟動時
  「重新綁定」舊訊息。只要在 `setup_hook` 呼叫一次
  `bot.add_dynamic_items(RsvpButton)` 註冊 class 即可。

  **M5 做投票按鈕時，如果也要撐過重啟，一樣用 `DynamicItem`，不要用
  `add_view()`。**

### 5. `discord.ext.tasks` 的例外處理有陷阱（★ 已經咬過一次）

`@tasks.loop` 預設只對一小組網路類例外（`OSError`／
`discord.ConnectionClosed`／`aiohttp.ClientError`／`asyncio.TimeoutError`）
自動重試。**其他任何例外（包括 DB 驅動拋出的普通 `ValueError`）會被記一次
log 後讓整個背景工作永久停止**，不是暫停一輪，是徹底死掉，要等進程重啟
才會恢復。

這在正式使用中真的發生過一次：Turso 連線串流閒置逾時，libsql 拋
`ValueError`，`reminder_tick` 整個排程死掉，導致當晚的提醒全部漏發。
修法在 [src/bot/cogs/scheduler.py](src/bot/cogs/scheduler.py)：**整個
`@tasks.loop` 方法的 body 都要包在自己的 try/except 裡**，不能只包部分
邏輯、依賴 `tasks.loop` 的預設重試機制。日後如果要加新的 `tasks.loop`
背景工作，套用同一個模式。

### 6. Discord 元件限制（做 UI 之前要知道的天花板）

- 一個操作列最多 **5 個按鈕**，一則訊息最多 5 列（row 0–4）。這是 Discord
  API 本身的硬限制，查過原始碼確認過，Components V2 也沒放寬。
- **沒有原生日期選擇元件**。`DateTimePickerView` 是用「14 天捲動視窗下拉
  選單」模擬出來的，不是抄來的元件。
- `discord.ui.Checkbox`／`RadioGroup` 只能用在 **Modal** 裡，不能放在一般
  訊息上。`InviteePickerView` 的「@everyone」「僅限受邀對象回覆」都是用
  **按鈕模擬開關**（點一下切換 label／style），不是真正的 checkbox。
- `UserSelect`／`RoleSelect` 是 Discord 原生的可搜尋元件，`max_values`
  （上限 25）限制的是「一次能選幾個」，不是「能搜尋/瀏覽幾個」——伺服器
  再多人都能透過打字搜尋，這不是我們要處理的問題。

### 7. Mention 安全性

[src/lib/mentions.py](src/lib/mentions.py) 統一處理：`content` 裡放實際
會觸發推播的 `<@id>`／`<@&id>`／`@everyone`，`allowed_mentions` 明確列出
白名單（不是讓 Discord 解析 content 字串裡任何看起來像 mention 的東西）。
`@everyone` 預設關閉，只有 `guild_settings.allow_everyone_ping` 開啟時
才允許。**任何新增會發訊息的功能，都要走這個模組，不要自己組 mention
字串。**

### 8. 提醒的 tag 對象邏輯

[src/bot/cogs/scheduler.py](src/bot/cogs/scheduler.py) 的
`_process_reminder()` 裡，提醒訊息 tag 的對象 = 活動邀請名單 **聯集**
已經 RSVP「參加」或「待定」的人。這是事後加的：一開始只 tag 邀請名單，
結果活動沒選邀請對象時提醒完全不會通知任何人。現在就算建立活動時忘記選
邀請對象，只要有人自己按過參加/待定，提醒還是會點名到他們。

---

## 資料庫 Schema 現況

四個 migration 檔，**依序套用，不要修改已存在的檔案**（Render 每次
deploy 都會重跑 migration，已套用的靠 `_migrations` 表跳過，改舊檔案對
已部署的資料庫沒有追溯效果）：

- `001_init.sql`：9 張表的完整初始 schema（`guild_settings`／
  `user_prefs`／`events`／`event_invitees`／`rsvps`／`polls`／
  `poll_options`／`poll_votes`／`reminders`）。
- `002_restrict_rsvp.sql`：`events` 加 `restrict_rsvp` 欄位（用
  `ALTER TABLE ADD COLUMN`，SQLite 不支援直接改欄位預設值，這點在檔案
  註解裡有寫）。
- `003_default_reminder_five_min.sql`：把 `guild_settings` 裡還停在舊
  預設值（`'1440,60,10'`）的伺服器回填成新預設值（`'5'`）。
- `004_polls_creator_id.sql`：`polls` 表加 `creator_id`（001 當初設計
  `polls` 時漏加，`events` 一開始就有這欄）。`/poll close` 靠這個判斷
  誰能關閉投票。

新增 migration 一律用 `00N_描述.sql`，內容要對 `CREATE TABLE` 用
`IF NOT EXISTS`；`ALTER TABLE` 沒有這個語法，冪等性靠 migration runner
的 `_migrations` 追蹤表保證（每個檔名只套用一次）。

---

## 專案結構速查

```
src/
├── main.py                 進程入口：migration → HTTP server → gateway
├── config.py                pydantic-settings，含 token 格式驗證
├── db/
│   ├── engine.py            ★ libsql 連線 + asyncio.to_thread 包裝
│   ├── migrate.py           migration runner
│   ├── migrations/          001~004（見上）
│   └── repo.py              ★ 所有 DB CRUD，多伺服器紀律寫在檔頭
├── domain/                  純邏輯，不碰 DB／Discord API，方便單元測試
│   ├── invitees.py          角色展開成實際成員（需要 discord.Guild）
│   ├── rsvp.py               RsvpSummary + 參加/待定/不參加/未回覆分類
│   ├── reminders.py          CSV 解析 + 逾期補償規則
│   └── polls.py               選項字串解析 + 票數統計
├── bot/
│   ├── client.py             Bot 子類，intents，指令同步，DynamicItem 註冊
│   ├── embeds.py             所有 Embed builder（活動/提醒/投票卡片/清單）
│   ├── modals.py             PendingEvent dataclass + 活動內容 Modal
│   ├── views.py              ConfirmEventView（非持久化）
│   ├── views_datetime.py     DateTimePickerView（非持久化）
│   ├── views_invitees.py     InviteePickerView（非持久化）
│   ├── views_rsvp.py         RsvpButton（★ 持久化，DynamicItem）
│   ├── views_poll.py          PollVoteSelect（★ 持久化，DynamicItem）
│   └── cogs/
│       ├── meta.py           /ping
│       ├── events.py         /event create|list|info
│       ├── polls.py           /poll create|close|results
│       ├── _shared.py         跨 cog 共用的 guild_tz() 輔助函式
│       └── scheduler.py      ★ 30 秒提醒排程迴圈
├── lib/
│   ├── clock.py               now_ms()，統一時間來源方便測試注入
│   ├── ids.py                 nanoid 短 ID + custom_id 編解碼
│   ├── mentions.py            ★ mention 白名單，見上方第 7 點
│   └── timeparse.py           固定格式時間解析 + Discord 時間戳
└── http/
    └── health.py               /healthz（保活）、/readyz（深度檢查）

tests/                        372 個測試，跑在本機 SQLite 檔上，不需要
                               真實 Turso 憑證（見 conftest.py 的 db fixture）
```

---

## 已知問題／技術債

1. **Turso 資料庫區域跟 Render 不同區**——資料庫開在東京
   (`ap-northeast-1`)，但 `render.yaml` 設定服務在新加坡。目前資料量小
   感覺不出差異，但正式有流量後每次查詢會多繞一趟。之前有跟使用者提過，
   使用者選擇先不處理。

2. **背景任務追蹤在對話時段跨越時會遺失，而且會重複啟動進程**——這不是
   單一事件，是**確認會重複發生**的模式：已經遇過至少兩次，背景啟動的
   bot 進程在對話恢復後被系統判定「找不到完成紀錄」，結果變成兩個一模
   一樣的進程同一秒自動起來，同時搶同一份 Turso 資料、同一個 Discord
   token。**每次要重啟本機 bot（不管是接手當下第一次啟動，還是後續任何
   一次重啟）之前，都先用**
   ```powershell
   Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like '*src.main*' }
   ```
   **檢查有沒有殘留或重複的 `src.main` 進程，有的話先 `Stop-Process -Force`
   全部清掉，確認乾淨後才啟動新的一個。不要假設「我只呼叫了一次啟動指令」
   等於「只會有一個進程在跑」。**

3. **還沒部署到 Render**，只在本機測試過。部署前記得：
   - 在 Render 環境變數填 5 個機密（`DISCORD_TOKEN`／`DISCORD_APP_ID`／
     `TURSO_DATABASE_URL`／`TURSO_AUTH_TOKEN`／選填的 `DEV_GUILD_ID`）
   - 設定 cron-job.org 保活（README 有步驟）
   - 確認 Render 服務類型是免費 **Web Service**，不是 Background Worker
     （免費方案沒有 Background Worker）

4. **`/settings` 指令還不存在**，所以 `guild_settings` 裡的
   `default_reminders`／`allow_everyone_ping`／`organizer_role_id` 等欄位
   目前只能手動改資料庫調整，使用者沒有介面可以自己調。這是 M7 的範圍。

5. **候補名單／名額上限**（`events.capacity` 欄位已存在但完全沒用到）是
   Phase 2 功能，使用者當初明確表示不進 MVP。

---

## 下一步建議順序

依照 [PLAN.md](PLAN.md) 的規劃，接下來是：

0. **先手動驗證一次 M5**——見上方「關於 M5 的實作來源」那段。跑
   `/poll create` 建一個投票、投一票、`/poll close`、`/poll results`，
   確認整條路徑在真實 Discord 上真的沒問題，不要只信測試套件。

1. **M6 原生活動同步**——`guild.create_scheduled_event()`，讓活動出現在
   Discord「活動」分頁，換取免費的手機推播。注意：`EXTERNAL` entity
   type（純文字地點）Discord 會強制要求填結束時間，沒填 duration 時要
   預設抓 2 小時，不要讓使用者被迫填一個「選填」的欄位。

2. **M7 收尾指令**——`/event edit`／`cancel`／`invite`／`ping`、
   `/settings`、`/timezone`。這些是讓現有功能「可調整」，不是新功能。

開始任何一項之前，建議：
- 讀一遍這份文件的「關鍵架構決策」章節
- 跑一次 `pytest -q` 確認起點是綠的
- 跟使用者確認他們的優先順序有沒有變

---

## 開發／測試/部署速查

```bash
# 本機開發
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env   # 填入實際值（Discord token、Turso 憑證）
python -m src.main

# 測試（不需要真實 Turso 憑證，跑在本機 SQLite 檔上）
pytest -q
ruff check .
```

完整的 Discord 應用程式設定、Turso 建立、Render 部署步驟都在
[README.md](README.md)，這裡不重複。

---

## Git 慣例

- commit message 用 heredoc 避免格式跑掉，結尾加
  `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`
- 只在使用者明確要求時才 commit／push（這條專案裡使用者有明確要求「記得
  commit 和 push」，所以每個里程碑完成後都有推）
- `PLAN.md` **故意不進版控**，`.gitignore` 裡排除了它——不要重新加回去，
  除非使用者要求
