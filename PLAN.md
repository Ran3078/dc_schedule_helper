# Discord 行事曆排程機器人 — 規劃書

> 用途：自用為主，但**支援多伺服器**（每個伺服器設定與資料獨立）
> 技術棧：Python + discord.py / Turso / Render
> 最後更新：2026-07-30（決策已定案，M0 程式完成）
>
> 開發者導向的架構重點與部署步驟見 [README.md](README.md)。

---

## 1. 需求盤點

### 1.1 你明確要求的（MVP 必做）

| # | 需求 | 規格決定 |
|---|------|----------|
| 1 | Tag 人的能力 | 支援 `@user` / `@role` / `@everyone`（可在設定關閉 everyone）。公告訊息 mention、提醒 mention、DM 私訊三種通道 |
| 2 | 安排參加對象 | 建立活動後用 User Select / Role Select 元件挑選（Discord 元件單次上限 25 個），可標記「必到 / 選到」 |
| 3 | 時間（必填） | MVP 收固定格式：`2026-08-01 20:00`、`8/1 20:00`、`8-1 20:00`；一律存 UTC epoch 毫秒 + 原始時區欄位。自然語言（`明天 20:00`、`+2h`）留待 Phase 2 |
| 4 | 地點（選填） | 純文字，可放語音頻道連結或實體地址 |
| 5 | 活動內容（選填） | 多行文字，用 Modal 輸入避免 slash 指令單行限制 |
| 6 | 投票 開/關 + 單選/複選 | 預設自製投票（無選項上限、可改票、可匿名、票數入庫）；`native:true` 改用 Discord 原生 Poll |

### 1.2 時區處理（隱含需求，但一定要做對）

- **所有時間存 UTC epoch（毫秒，INTEGER）**，額外存建立時使用的 IANA 時區（預設 `Asia/Taipei`）。
- 顯示一律用 Discord 動態時間戳 `<t:1754049600:F>` / `<t:...:R>`。**Discord 客戶端會自動用每個人的本地時區渲染**，等於免費拿到多時區支援，不必自己算。
- 使用者可用 `/timezone set` 覆寫個人時區，只影響「輸入解析」（他打 `20:00` 是哪個 20:00）。

---

## 2. 技術選型

> 已定案並實作完成（M0）。使用者選擇 Python。

| 層 | 選擇 | 理由 |
|----|------|------|
| 語言 | Python 3.13（Render 上 3.13） | 使用者選擇 |
| Discord SDK | discord.py 2.7 | `app_commands`、`ui.Modal/Select`、`ScheduledEvent`(2.0+)、`discord.Poll`(2.4+) 皆支援 |
| 資料庫 | Turso (libSQL) + 官方 `libsql` 驅動 | 免費額度極寬：100 DB / 5GB / 5億讀 / 1千萬寫 每月。本專案用量約為額度的萬分之一 |
| ORM / Migration | **無 ORM**，原生 SQL + 編號 migration 檔 | 見 §2.3 |
| 時間 | `zoneinfo`（+ Windows 需 `tzdata`）| MVP 只收固定格式時間；自然語言解析留待 Phase 2 |
| HTTP | `aiohttp` | **已是 discord.py 的相依套件**，不必多裝 FastAPI/Flask，且共用同一事件迴圈 |
| 排程 | `discord.ext.tasks` 30 秒輪詢 DB | **不用記憶體排程** — 進程重啟會遺失。狀態全在 Turso，重啟後自動補發 |
| 設定 | `pydantic-settings` | 環境變數缺失時開機即失敗，而非跑到一半才炸 |

### 2.1 為什麼不用 serverless / HTTP Interactions

Discord 的 HTTP Interactions 模式要求 **3 秒內回應**，Render 免費方案冷啟動約 60 秒 → 直接 timeout，Discord 連 endpoint 驗證都不會過。而且一旦設定 Interactions Endpoint URL，**gateway 就完全失效**（兩者互斥），會失去投票事件、成員離開事件等推播。

→ 結論：**走 gateway（WebSocket）常駐模式**。

### 2.2 Intent 與權限

因為全部走 slash command，**不需要 Message Content Intent**。Bot 不讀任何人的聊天內容。

但**需要 Server Members Intent**（同為特權 intent）：把「參加對象」裡的角色展開成成員清單、算出誰還沒回覆，都需要成員快取。伺服器數 <100 無需審核，Developer Portal 直接開啟即可。

- Scopes：`bot`, `applications.commands`
- Permissions：`Send Messages`, `Embed Links`, `Read Message History`, `Create Public Threads`, `Mention Everyone`(若要 @everyone), `Manage Events`(同步原生活動)

### 2.3 為什麼不用 ORM

原本評估 SQLAlchemy + `sqlalchemy-libsql`，但查證後發現該 dialect 最新版（0.2.0, 2025-05）**仍依賴已棄用歸檔的 `libsql-experimental`**。既然 migration 本來就決定手寫 SQL，索性拿掉 ORM 層：不依賴落後的 dialect、少三個相依套件、Render 冷啟動更快（冷啟動時間在本專案直接影響可用性）。

schema 只有 9 張小表、查詢都很簡單，ORM 帶來的價值有限。

### 2.4 關鍵約束：同步 DB 驅動 × asyncio

Turso 的 Python 驅動是**同步**的，discord.py 跑在 asyncio 上。直接在 handler 裡呼叫會阻塞事件迴圈，導致 gateway 心跳超時被 Discord 斷線。

處理方式（已實作於 [src/db/engine.py](src/db/engine.py)）：對外只暴露 `async def` 介面，內部一律 `asyncio.to_thread()`；sqlite3-like 連線非 thread-safe，故用「單一連線 + `threading.Lock`」把 DB 存取序列化。以本專案用量完全足夠，且日後 Turso 推出 async 驅動時只需改 engine 內層。

### 2.5 驅動已知行為（實測）

`libsql` 0.1.11 對唯一鍵衝突拋的是**普通 `ValueError`**（訊息含 `UNIQUE constraint failed`），而非 DBAPI 的 `IntegrityError`。因此不可用例外型別判斷衝突，upsert 一律走 SQL 層的 `INSERT OR IGNORE` / `ON CONFLICT DO UPDATE`。已有測試釘住此行為。

---

## 3. 架構

```
                        ┌──────────────────────────┐
   外部 cron            │  Render Web Service (free)│
 cron-job.org  ──ping──▶│                           │
  每 10 分鐘            │  ┌─────────────────────┐  │
                        │  │ Express /healthz    │  │  ← 唯一目的：防休眠
                        │  ├─────────────────────┤  │
                        │  │ discord.js Client   │◀─┼──WSS──▶ Discord Gateway
                        │  │  · 指令 router      │  │
                        │  │  · 元件 router      │  │
                        │  ├─────────────────────┤  │
                        │  │ Scheduler tick 30s  │  │
                        │  └──────────┬──────────┘  │
                        └─────────────┼─────────────┘
                                      │ libSQL over HTTPS
                                      ▼
                              ┌───────────────┐
                              │  Turso (free) │
                              └───────────────┘
```

**Scheduler tick 邏輯**（每 30 秒）：

```sql
SELECT * FROM reminders
WHERE sent_at IS NULL AND fire_at_utc <= :now
ORDER BY fire_at_utc LIMIT 50;
```

發送後才寫 `sent_at`。若進程在中途死掉，最壞情況是重複發一次 — 用 `UPDATE ... WHERE sent_at IS NULL` 的回傳 rowcount 當樂觀鎖即可避免。

**逾期補償**：若 bot 停機 2 小時後復活，會撈到一堆過期 reminder。規則 = 超過 `fire_at_utc + 15 分鐘` 的直接標記 `skipped` 不發（避免半夜噴一串已經開始的活動提醒），但活動本身尚未開始者仍發一則「即將開始」。

---

## 4. 資料模型（SQLite / Turso）

```sql
-- 伺服器層級設定
CREATE TABLE guild_settings (
  guild_id            TEXT PRIMARY KEY,
  default_tz          TEXT    NOT NULL DEFAULT 'Asia/Taipei',
  announce_channel_id TEXT,
  default_reminders   TEXT    NOT NULL DEFAULT '1440,60,10',  -- 分鐘，逗號分隔
  allow_everyone_ping INTEGER NOT NULL DEFAULT 0,
  organizer_role_id   TEXT,            -- 誰能開活動，NULL = 所有人
  locale              TEXT    NOT NULL DEFAULT 'zh-TW',
  sync_native_events  INTEGER NOT NULL DEFAULT 1
);

-- 使用者個人偏好
CREATE TABLE user_prefs (
  user_id  TEXT PRIMARY KEY,
  tz       TEXT,
  dm_optout INTEGER NOT NULL DEFAULT 0
);

-- 活動主表
CREATE TABLE events (
  id                TEXT PRIMARY KEY,          -- nanoid(10)
  guild_id          TEXT NOT NULL,
  channel_id        TEXT NOT NULL,
  message_id        TEXT,                      -- 公告訊息，供編輯更新
  thread_id         TEXT,
  creator_id        TEXT NOT NULL,
  title             TEXT NOT NULL,
  starts_at_utc     INTEGER NOT NULL,          -- 必填
  ends_at_utc       INTEGER,
  tz                TEXT NOT NULL,
  location          TEXT,                      -- 選填
  description       TEXT,                      -- 選填
  capacity          INTEGER,                   -- NULL = 不限
  status            TEXT NOT NULL DEFAULT 'scheduled',  -- scheduled|cancelled|completed
  discord_event_id  TEXT,                      -- 原生 Scheduled Event 連動
  rrule             TEXT,                      -- 重複規則，Phase 3
  parent_event_id   TEXT,                      -- 重複活動的母體
  created_at        INTEGER NOT NULL,
  updated_at        INTEGER NOT NULL
);
CREATE INDEX idx_events_guild_start ON events(guild_id, starts_at_utc);
CREATE INDEX idx_events_status ON events(status, starts_at_utc);

-- 參加對象（被安排的人 / 角色）
CREATE TABLE event_invitees (
  event_id    TEXT NOT NULL,
  target_type TEXT NOT NULL,   -- user | role | everyone
  target_id   TEXT NOT NULL,   -- everyone 時填 guild_id
  required    INTEGER NOT NULL DEFAULT 0,  -- 1=必到
  PRIMARY KEY (event_id, target_type, target_id)
);

-- 出席回覆
CREATE TABLE rsvps (
  event_id     TEXT NOT NULL,
  user_id      TEXT NOT NULL,
  status       TEXT NOT NULL,   -- yes | no | maybe | waitlist
  note         TEXT,
  responded_at INTEGER NOT NULL,
  PRIMARY KEY (event_id, user_id)
);

-- 投票
CREATE TABLE polls (
  id           TEXT PRIMARY KEY,
  guild_id     TEXT NOT NULL,
  event_id     TEXT,            -- NULL = 獨立投票
  channel_id   TEXT NOT NULL,
  message_id   TEXT,
  question     TEXT NOT NULL,
  kind         TEXT NOT NULL DEFAULT 'generic',  -- generic | time_slot
  multi        INTEGER NOT NULL DEFAULT 0,       -- 0=單選 1=複選
  max_choices  INTEGER,                          -- 複選上限，NULL=不限
  anonymous    INTEGER NOT NULL DEFAULT 0,
  allow_change INTEGER NOT NULL DEFAULT 1,
  closes_at    INTEGER,
  status       TEXT NOT NULL DEFAULT 'open',     -- open | closed
  created_at   INTEGER NOT NULL
);

CREATE TABLE poll_options (
  id       TEXT PRIMARY KEY,
  poll_id  TEXT NOT NULL,
  label    TEXT NOT NULL,
  emoji    TEXT,
  sort     INTEGER NOT NULL,
  meta     TEXT             -- time_slot 時放 epoch
);

CREATE TABLE poll_votes (
  poll_id   TEXT NOT NULL,
  option_id TEXT NOT NULL,
  user_id   TEXT NOT NULL,
  voted_at  INTEGER NOT NULL,
  PRIMARY KEY (poll_id, option_id, user_id)
);
CREATE INDEX idx_votes_poll_user ON poll_votes(poll_id, user_id);

-- 提醒佇列（排程真相來源）
CREATE TABLE reminders (
  id            TEXT PRIMARY KEY,
  event_id      TEXT NOT NULL,
  fire_at_utc   INTEGER NOT NULL,
  offset_min    INTEGER NOT NULL,
  audience      TEXT NOT NULL DEFAULT 'invitees', -- invitees|yes|maybe|no_response|channel
  channel_dm    TEXT NOT NULL DEFAULT 'channel',  -- channel|dm|both
  sent_at       INTEGER,
  state         TEXT NOT NULL DEFAULT 'pending'   -- pending|sent|skipped|failed
);
CREATE INDEX idx_reminders_due ON reminders(state, fire_at_utc);
```

單選投票的「單選」用 SQL 保證：投票時先 `DELETE FROM poll_votes WHERE poll_id=? AND user_id=?` 再 insert；複選則只 toggle 單一 option。

---

## 5. 指令設計

### 5.1 MVP 指令

```
/event create
    title      (必填, string)
    time       (必填, string)    ← 自然語言解析
    duration   (選填, string)    ← "2h" / "90m"
    location   (選填, string)
    poll       (選填, boolean)   ← 開啟投票
    poll_mode  (選填, choice)    ← single | multi
  → 回一個 ephemeral Modal 收「活動內容」（多行）
  → 再回一個帶 User Select + Role Select 的訊息挑參加對象
  → 確認後才發公告

/event list    [scope: upcoming|mine|all] [limit]
/event info    <id>
/event edit    <id>          → Modal 改標題/時間/地點/內容
/event cancel  <id> [reason] → 通知所有已回覆 yes 的人
/event invite  <id>          → 追加參加對象
/event remind  <id> <offset> → 追加提醒（如 "30m"）
/event ping    <id> [filter: no_response|yes|maybe]  ← 催未回覆的人

/poll create   <question> <options: 用 | 分隔> [multi] [anonymous] [closes]
/poll close    <id>
/poll results  <id>

/timezone set  <tz>
/settings      channel | tz | reminders | organizer_role | everyone_ping
```

### 5.2 互動元件

**活動公告卡片**（Embed + 按鈕）：

```
┌────────────────────────────────────────┐
│ 📅  週五團練                            │
│ ────────────────────────────────────── │
│ 🕐  2026/08/01 (五) 20:00  ·  3 小時後  │  ← <t:...:F> + <t:...:R>
│ 📍  語音頻道 #遊戲室                     │
│ 📝  打完第三章，記得先補給                │
│                                        │
│ ✅ 參加 (4/8)   @A @B @C @D             │
│ ❔ 待定 (1)      @E                     │
│ ❌ 不參加 (2)    @F @G                  │
│ ⏳ 未回覆 (3)    @H @I @J               │
│                                        │
│ [✅ 參加] [❔ 待定] [❌ 不參加] [⋯ 更多]  │
└────────────────────────────────────────┘
@here @團練組
```

- `custom_id` 格式：`ev:rsvp:<eventId>:<status>` — 無狀態、進程重啟後按鈕照樣有效（**不要用記憶體 collector**）。
- 「未回覆」= invitees 展開後（role 展開成成員）減去已回覆者。
- 名額滿時 `✅ 參加` 自動轉 `⏳ 候補`。

**投票卡片**：單選用 String Select（`max_values=1`），複選用 `max_values=N`；或選項 ≤5 時用按鈕列。匿名模式只顯示票數不顯示頭像。

---

## 6. 競品對照與擴充功能規劃

調查了三大主流 bot 的差異，作為擴充優先序依據：

| 能力 | Sesh | Apollo | Raid-Helper | 本專案規劃 |
|------|------|--------|-------------|-----------|
| 基本活動 + RSVP | ✅ | ✅ | ✅ | **Phase 1** |
| 時區自動換算 | ✅ | ✅ | ✅ | **Phase 1**（用 Discord 時間戳，免費） |
| 提醒推播 | ✅ | ✅ | ✅ | **Phase 1** |
| 原生活動分頁同步（手機推播） | ❌ | 部分 | 部分 | **Phase 1**（M6，已確認納入） |
| 找共同時間投票 | ✅ 招牌 | ❌ | ❌ | **Phase 2** |
| 職位/名額（2坦6輸出） | ❌ | ✅ 招牌 | ✅ 最強 | **Phase 3** |
| 重複活動 | ✅ | ✅ | ✅ | **Phase 3** |
| 候補名單 | 部分 | ✅ | ✅ | **Phase 2**（使用者確認不進 MVP） |
| 自然語言建立 | ✅ (付費) | ❌ | ❌ | **Phase 2**（使用者確認不進 MVP） |
| API / Webhook | Pro 付費 | ❌ | 部分 | **Phase 4** |
| 出席統計 / 放鳥紀錄 | ❌ | 部分 | ✅ | **Phase 4** |
| 月曆檢視 | 部分 | ✅ | ✅ | **Phase 3** |
| 價格 | $5/mo | $5/mo | $3.5/mo | 自架 $0–7/mo |

### Phase 2 — 好用度躍升（建議做完 Phase 1 立刻做）

1. **時段投票 → 自動建立活動**（Sesh 的招牌功能）
   `/availability 週五團練 slots:"8/1 20:00, 8/2 20:00, 8/3 14:00"` → 發複選投票 → `/availability close` 取最高票時段直接生成正式活動並沿用參加對象。這是三大 bot 裡最實用、且你目前需求的自然延伸。
2. **自然語言建立** — `dateparser` 支援中文，`「下週五晚上八點 團練」` 直接開活動。開源即免費，Sesh 這功能要 $5/mo。
3. **候補名單 + 名額上限** — `capacity` 欄位已在 schema 預留；有人退出自動把候補第一位遞補並 DM 通知。
4. **未回覆自動催** — 活動前 24h 對 `no_response` 的人發一次 DM。

### Phase 3 — 進階排程

6. **重複活動（RRULE）** — `rrule` / `parent_event_id` 欄位已預留。策略：只實體化未來 4 週的實例，由 tick loop 滾動生成，避免無限膨脹。
7. **職位名額報名** — 新增 `signup_roles(event_id, name, emoji, capacity, sort)`，RSVP 改為選職位。MMO / 桌遊分工用。
8. **月曆檢視** — `/calendar 2026-08` 用 Embed 畫 ASCII 月曆，或直接產圖。
9. **ICS / webcal 訂閱** — aiohttp server 已在跑，加 `GET /ics/{token}` 回 iCalendar，可直接訂閱進 Google Calendar / iOS 行事曆。**這是自架才有的殺手級功能**，三大 bot 都做得很差。
10. **活動討論 thread** — 建立活動時自動開 thread，結束後自動封存。

### Phase 4 — 資料與運維

11. **出席統計** — 活動結束後標記實到，累積個人出席率 / 放鳥次數，`/stats @user`。
12. **CSV / JSON 匯出**。
13. **Web dashboard** — Render Static Site + 同一個 aiohttp server 開 read-only API，手機上看月曆比 Discord 舒服。
14. **稽核紀錄 + 軟刪除** — 誰改了什麼活動。
15. **i18n（zh-TW / en）** — 訊息字串抽到 `src/i18n/`，一開始就抽比後補容易。

### 明確不做（避免範圍膨脹）

- 多伺服器 SaaS 化、公開上架、付費牆
- 語音自動點名
- LLM 對話式介面（`dateparser` 已足夠，且要額外 API 成本）

---

## 7. 部署方案

### 7.1 三個選項與成本

| 方案 | 月成本 | 可靠度 | 說明 |
|------|--------|--------|------|
| **A. Render Free Web Service + 外部保活** | **$0** | 中 | 免費額度 750 instance hours/月；全月常駐 31 天 = 744h，**塞得進但幾乎零餘裕**。同 workspace 若有其他免費服務會超額。閒置 15 分鐘休眠 → 需外部 cron 每 10 分鐘 ping `/healthz` |
| **B. Render Background Worker** | **$7** | 高 | 真正常駐、無休眠、不需保活 hack。Render 的 cron job 也是付費（$1/mo 起），Worker 更直接 |
| **C. Oracle Cloud Always Free ARM VM** | $0 | 中高 | 4 OCPU / 24GB RAM 永久免費，但註冊審核常被拒、閒置實例可能被回收 |

**建議**：先用 **A** 把功能做出來驗證，穩定後若覺得偶爾斷線煩人再升 **B**（$7/mo 還是比 Sesh + Apollo 兩個付費加起來便宜，而且功能全歸你）。

Turso 免費方案完全夠：本專案一個月的讀寫量約在 10 萬 rows 量級，額度是 5 億讀 / 1 千萬寫。

### 7.2 A 方案落地細節

實際設定見 [render.yaml](render.yaml)（已建立）。要點：`runtime: python`、`plan: free`、`region: singapore`、`startCommand: python -m src.main`、`healthCheckPath: /healthz`，5 個機密環境變數設 `sync: false` 需在 Dashboard 手動填。

Migration 不放在 `buildCommand`，而是在 `src/main.py` 啟動時自動套用 —— build 階段拿不到 `sync: false` 的環境變數。

保活：註冊 cron-job.org（免費），每 10 分鐘 GET `https://<app>.onrender.com/healthz`。

`/healthz` 與 `/readyz` 刻意分開：Render 的 health check 走 `/healthz`，它只證明進程活著、不碰 DB、不管 gateway 是否已連上 —— 啟動初期 gateway 尚未連線，若此時回 503 會讓 Render 誤判 deploy 失敗。深度檢查放在 `/readyz`。

**指令註冊用 global**（多伺服器必須如此），另以選填的 `DEV_GUILD_ID` 對開發伺服器額外做一次 guild-scoped 同步換取即時生效 —— global 有最長 1 小時快取，開發期間每改一次指令定義都要等，很痛。詳見 §11。

**Render 免費方案的已知坑**：
- 每次 deploy 會重啟 → gateway 重連（幾秒，可接受，因為排程狀態在 DB）
- 冷啟動慢 → 保活 cron 是必須，不是選配
- 沒有持久磁碟 → 所有狀態必須在 Turso（架構已如此）

---

## 8. 專案結構

`✅` = M0 已建立，其餘為後續里程碑。

```
dc_schedule/
├── PLAN.md                      ✅ 本文件
├── README.md                    ✅ 部署步驟與架構重點
├── render.yaml                  ✅
├── pyproject.toml               ✅ pytest / ruff 設定
├── requirements.txt             ✅
├── requirements-dev.txt         ✅
├── .env.example                 ✅
├── .gitignore                   ✅
├── src/
│   ├── main.py                  ✅ 入口：migration → HTTP server → gateway
│   ├── config.py                ✅ pydantic-settings
│   ├── db/
│   │   ├── engine.py            ✅ ★ libsql + asyncio.to_thread + 加鎖
│   │   ├── migrate.py           ✅ 編號 SQL migration runner
│   │   ├── migrations/
│   │   │   └── 001_init.sql     ✅ 9 張表 + 索引
│   │   └── repo.py              ✅ ★ 多伺服器紀律 + guild_settings + 歸屬檢查
│   ├── bot/
│   │   ├── client.py            ✅ Bot 子類、intents、global+dev 指令同步、on_guild_join
│   │   ├── cogs/
│   │   │   ├── meta.py          ✅ /ping（gateway + DB 診斷）
│   │   │   ├── events.py           /event create|list|info|edit|cancel|invite|remind|ping
│   │   │   ├── polls.py            /poll create|close|results
│   │   │   ├── settings.py         /settings, /timezone
│   │   │   └── scheduler.py        tasks.loop 提醒派送
│   │   ├── views/                  持久化 View（RsvpView / PollView / InviteePicker）
│   │   ├── modals.py               活動內容 / 編輯 Modal
│   │   └── embeds.py               活動卡片、投票卡片 builder
│   ├── domain/
│   │   ├── events.py               活動業務規則
│   │   ├── rsvp.py                 出席（候補遞補留待 Phase 2）
│   │   ├── polls.py                投票單/複選規則
│   │   ├── invitees.py             role → 成員展開
│   │   ├── reminders.py            提醒排定 / 重排 / 逾期判定
│   │   └── native_events.py        Discord 原生活動同步
│   ├── lib/
│   │   ├── ids.py               ✅ 短 ID + custom_id 編解碼（含 100 字元上限檢查）
│   │   ├── clock.py             ✅ now_ms()，集中時間取得以便測試
│   │   ├── timeparse.py            時間解析 + Discord 時間戳
│   │   └── mentions.py             allowed_mentions 白名單
│   ├── i18n/                       zh_tw.py
│   └── http/
│       └── health.py            ✅ /healthz（保活）、/readyz（深度檢查）
└── tests/
    ├── conftest.py              ✅ 本機 SQLite fixture，不需 Turso 憑證
    ├── test_db.py               ✅ migration 冪等、rowcount 樂觀鎖、單選投票語意
    ├── test_config.py           ✅ dev_guild_id 選填語意、必填欄位、時區驗證
    ├── test_multi_guild.py      ✅ ★ 伺服器隔離（A 的活動不能出現在 B）
    ├── test_timeparse.py           解析與時區
    └── test_polls.py               單選/複選票數正確性
```

---

## 9. 實作里程碑

| Milestone | 內容 | 完成定義 | 狀態 |
|-----------|------|---------|------|
| **M0** 骨架 | git init、DB 層、migration runner、`/healthz`、`/ping`、render.yaml、測試骨架 | 私服打 `/ping` 有回應，閒置 30 分鐘後仍即時回應 | **程式完成，待部署驗證** |
| **M1** 活動核心 | `/event create`（時間必填、地點/內容選填）+ Modal + 公告 Embed + `/event list` `/event info` | 能建立活動並在頻道看到卡片 | 待做 |
| **M2** 參加對象 + Tag | User/Role Select 挑人、role 展開、`@` mention、`allowed_mentions` 白名單控制 | 建立活動時能 tag 指定的人與角色 | 待做 |
| **M3** RSVP | 參加/待定/不參加按鈕、未回覆計算、公告訊息即時更新 | 重啟 bot 後舊訊息按鈕仍可用 | 待做 |
| **M4** 提醒 | reminders 表 + `tasks.loop` + 逾期補償 + 預設 1天/1小時/10分 | 停機 2 小時後復活，提醒不重複、不亂噴 | 待做 |
| **M5** 投票 | 自製單選/複選投票、`/poll` 系列、票數統計、可綁定活動、`native:true` 選配 | 單選覆蓋前次、複選可 toggle | 待做 |
| **M6** 原生活動同步 | `guild.create_scheduled_event()` 同步 + 原生「有興趣」映射回 yes | 活動出現在「活動」分頁，取消時一併消失 | 待做 |
| **M7** 收尾 | `/event edit` `/event cancel` `/event invite` `/event ping` `/settings` `/timezone`、錯誤處理 | 全部 MVP 需求可用 | 待做 |
| **M8+** | 依 §6 Phase 2 順序擴充 | — | — |

M0–M7 涵蓋你列的所有需求。

**M6 的 API 細節**：填了文字地點的活動屬 `EXTERNAL` entity type，Discord **強制要求結束時間**。使用者沒填 `duration` 時預設 2 小時，不要讓「選填」變成被迫必填。

---

## 10. 已定案的決策

| 項目 | 決定 |
|------|------|
| 語言 | **Python + discord.py** |
| 投票 | **自製為主**（無選項上限、可改票、可匿名、票數入庫、可綁定活動），原生 `discord.Poll` 當 `native:true` 選配 |
| 部署 | **Render 免費 Web Service + cron-job.org 保活**（$0），穩定後可升 Background Worker $7/mo |
| 多伺服器 | **支援**。指令 global 註冊，`guild_settings` 每伺服器獨立，`on_guild_join` 自動建立。詳見 §11 |
| 原生活動分頁同步 | **納入 MVP**（M6）：單向同步 + 原生「有興趣」映射回 yes |
| 自然語言時間解析 | **不進 MVP**，Phase 2 再做。MVP 只收固定格式 |
| 名額上限 / 候補名單 | **不進 MVP**，Phase 2 再做。`capacity` 欄位已在 schema 預留 |
| git | 已 `git init` |

---

## 11. 多伺服器設計

資料模型從一開始就是多伺服器就緒的，因此開放多伺服器不需要改 schema：

- `guild_settings.guild_id` 是主鍵 —— 公告頻道、時區、預設提醒時距、誰能開活動、是否允許 @everyone，全部各伺服器獨立
- `events.guild_id`、`polls.guild_id` 皆 `NOT NULL`，並有 `(guild_id, starts_at_utc)` 複合索引
- 子表（`rsvps` / `poll_votes` / `poll_options` / `event_invitees` / `reminders`）不帶 `guild_id`，靠母體界定範圍

### 11.1 真正的成本在程式紀律，不在 schema

改成多伺服器只動了 2 個檔案，但**維持多伺服器正確性的成本分散在 M1–M7 的每一個查詢裡**。這類錯誤不會讓程式報錯，只會安靜地把別的伺服器的活動列出來 —— 靠人工 review 很難抓，所以規則寫死在 [src/db/repo.py](src/db/repo.py) 的模組註解與 [README.md](README.md)：

1. 讀取活動 / 投票的函式，`guild_id` 一律必填且必須進 WHERE。**不提供「不分伺服器」的查詢版本**
2. `guild_id` 一律來自 `interaction.guild_id`，絕不從設定檔取
3. 操作子表前必須先用 `owned_event()` / `owned_poll()` 確認母體歸屬 —— 否則 A 伺服器的人能用猜到的 ID 改 B 伺服器的資料
4. Discord ID 是整數、DB 欄位是 TEXT，傳入前一律 `str()`（repo 層已代為處理）
5. 需要伺服器情境的指令要加 `@app_commands.guild_only()`，否則 DM 中 `interaction.guild_id` 是 `None`

[tests/test_multi_guild.py](tests/test_multi_guild.py) 用「A 的活動不能出現在 B」這類測試把上述規則釘住。每新增一個查詢函式就補一條對應的隔離測試。

### 11.2 指令同步的取捨

多伺服器必須 global 註冊，但 Discord 對 global 指令有快取，改動後最長 1 小時才在各伺服器生效。因此 `DEV_GUILD_ID`（選填）會對指定伺服器額外做一次 guild-scoped 同步換取即時生效。開發伺服器會同時有兩份註冊，Discord 的行為是 guild-scoped 優先，不會重複。

### 11.3 兩個規模天花板

| 門檻 | 影響 |
|------|------|
| 100 個伺服器 | Server Members Intent 需向 Discord 申請審核 |
| 數十個伺服器 | 成員快取吃 RAM，Render 免費方案只有 512MB。屆時改 `chunk_guilds_at_startup=False` + 按需 fetch |

另外 Discord 原生活動上限 100 個是**每伺服器**計算，所以 M6 不受多伺服器影響。

---

## 附錄：參考來源

- [Apollo vs Sesh 功能對照](https://peakbot.pro/blog/apollo-vs-sesh-discord-event-bot-2026)
- [Best Discord Event Bots 2026](https://peakbot.pro/blog/best-discord-event-bots-2026)
- [Best Discord Scheduling Bots for Gaming](https://supatimer.com/en/guides/best-discord-scheduling-bots)
- [Render 定價與免費方案限制](https://www.srvrlss.io/provider/render/)
- [Render Free Tier 750 小時 / Cron Job 說明](https://unanswered.io/guide/render-free-tier-details)
- [Turso 定價](https://turso.tech/pricing)
- [Turso Developer Plan 額度](https://turso.tech/blog/turso-cloud-debuts-the-new-developer-plan)
- [Discord HTTP Bots vs Gateway](https://carbon.buape.com/concepts/http-bots)
- [Discord Guild Scheduled Event API](https://discord.com/developers/docs/resources/guild-scheduled-event)
- [Discord 原生投票上限（10 選項）](https://alternativeto.net/news/2024/4/discord-launches-new-in-app-poll-creation-feature-with-up-to-10-answer-options)
- [免費 24/7 Discord bot 託管實測](https://clawdhost.net/blog/best-discord-bot-hosting-2026/)
