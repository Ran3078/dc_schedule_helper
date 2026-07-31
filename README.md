# dc_schedule

Discord 行事曆排程機器人。自架取代 Sesh / Apollo / Raid-Helper。**支援多伺服器**，每個伺服器有獨立的設定與資料。


**目前進度：M0（骨架與部署管線）完成。** 可用指令：`/ping`。

---

## 技術棧

| 項目 | 選擇 |
|------|------|
| Runtime | Python 3.12+ |
| Discord | discord.py 2.7 |
| 資料庫 | Turso（libSQL），官方 `libsql` 驅動 + 手寫 SQL |
| HTTP | `aiohttp`（discord.py 已帶入，不需額外套件） |
| 部署 | Render 免費 Web Service + 外部 cron 保活 |

沒有 ORM —— schema 只有 9 張小表，全部走原生 SQL。原本評估的 `sqlalchemy-libsql`
其最新版仍依賴已棄用的 `libsql-experimental`，故不採用。

---

## 本機開發

```bash
python -m venv .venv
.venv\Scripts\activate           # Windows
pip install -r requirements-dev.txt

cp .env.example .env             # 填入實際值
python -m src.main
```

測試與 lint：

```bash
pytest                           # 跑在本機 SQLite 檔上，不需 Turso 憑證
ruff check .
```

> **VS Code**：記得把 Python interpreter 選成 `.venv\Scripts\python.exe`，
> 否則編輯器會誤報「套件未安裝」。

---

## 首次設定

### 1. Discord 應用程式

1. [Developer Portal](https://discord.com/developers/applications) → New Application
2. **General Information** → 複製 Application ID → `DISCORD_APP_ID`
3. **Bot** → Reset Token → 複製 → `DISCORD_TOKEN`
4. **Bot** → Privileged Gateway Intents → 開啟 **SERVER MEMBERS INTENT**
   - 這是必要的：要把「參加對象」裡的角色展開成成員清單、算出誰還沒回覆，都需要成員快取
   - 伺服器數 <100 無需審核，直接開就好
   - **不需要** MESSAGE CONTENT INTENT —— 本 bot 全走 slash 指令，不讀任何聊天內容
5. **OAuth2 → URL Generator**：
   - Scopes：**必須同時勾 `bot` 和 `applications.commands`**
   - Bot Permissions：`View Channels`、`Send Messages`、`Embed Links`、
     `Read Message History`、`Add Reactions`、`Create Public Threads`、
     `Send Messages in Threads`、`Mention Everyone`（要 @everyone 才需要）、
     `Manage Events`（同步原生活動分頁需要）

   > ⚠️ **只勾 `applications.commands` 是最容易踩的坑**：指令會被裝進伺服器、
   > `/ping` 也能用，但 **bot 本身沒有加入伺服器**，不會出現在成員清單裡。
   > 症狀是 `bot.guilds` 為空、`on_ready` 建不出 `guild_settings`，
   > 且無法發訊息 / tag 人 / 展開角色成員 / 建立原生活動。
   >
   > 快速產生正確連結（把 `<APP_ID>` 換成你的 Application ID）：
   > ```
   > https://discord.com/oauth2/authorize?client_id=<APP_ID>&scope=bot+applications.commands&permissions=317827796032
   > ```

6. 用產生的連結把 bot 邀進伺服器（可邀進多個，每個伺服器資料獨立）
   - 邀請後在伺服器成員清單裡應該看得到 bot，看不到就是 scope 少勾了
7. （選填）Discord 設定 → 進階 → 開啟開發者模式 → 右鍵你的主要伺服器 → 複製伺服器 ID
   → `DEV_GUILD_ID`。作用見下方「多伺服器」段落

### 2. Turso

**用 Dashboard（Windows 建議走這條）**

Turso CLI 官方只支援 WSL，沒有原生 Windows 版本，所以直接用網頁比較快：

1. [Turso Dashboard](https://app.turso.tech) → `Create Database`
2. 名稱 `dc-schedule`，**Region 選 Singapore**（或最近的亞洲節點）
3. 進入該資料庫頁面，複製連線 URL（`libsql://...`）→ `TURSO_DATABASE_URL`
4. 產生 Auth Token → `TURSO_AUTH_TOKEN`（**只會顯示一次，立刻存好**）

> ⚠️ **資料庫要跟 Render 服務同區**。[render.yaml](render.yaml) 設的是 Singapore，
> 若資料庫開在別區，每次查詢都要跨區往返 —— 而本專案的 DB 存取是序列化的
> （單一連線加鎖，見 `src/db/engine.py`），延遲會直接累積成可感受的卡頓。

**用 CLI（macOS / Linux / WSL）**

```bash
turso db create dc-schedule --location sin
turso db show dc-schedule --url          # → TURSO_DATABASE_URL
turso db tokens create dc-schedule       # → TURSO_AUTH_TOKEN
```

**不論哪種方式，schema 都不必手動建立** —— bot 每次啟動會自動套用
`src/db/migrations/*.sql`，9 張表與索引會自己建好。

### 3. Render

1. 把這個 repo 推到 GitHub
2. Render → New → Blueprint → 選這個 repo（會讀 [render.yaml](render.yaml)）
3. 在 Dashboard 填入機密環境變數：`DISCORD_TOKEN`、`DISCORD_APP_ID`、
   `TURSO_DATABASE_URL`、`TURSO_AUTH_TOKEN`，以及選填的 `DEV_GUILD_ID`
4. Deploy

### 4. 保活設定（**必做，不是選配**）

Render 免費 Web Service 閒置 15 分鐘就休眠，休眠會切斷 Discord gateway 連線，
冷啟動要約 1 分鐘。到 [cron-job.org](https://cron-job.org)（免費）建立一個任務：

- URL：`https://<你的服務>.onrender.com/healthz`
- 間隔：每 10 分鐘

### ⚠️ 免費額度沒有餘裕

Render 免費方案是 **750 instance hours / 月 / workspace**，而全月常駐 31 天 = 744 小時。

- **同一個 workspace 不能再有其他免費服務**，否則會超額被停
- 若覺得偶爾斷線太煩，升級 Background Worker（$7/mo）即可拿掉保活 hack

---

## 端點

| 路徑 | 用途 |
|------|------|
| `/healthz` | 只證明進程活著，不碰 DB。給 Render health check 與保活 cron 用（啟動初期 gateway 還沒連上時也要回 200，否則 Render 會誤判 deploy 失敗） |
| `/readyz` | 深度檢查：gateway 連線狀態 + DB 往返延遲。排查問題用，異常時回 503 |

---

## 架構重點

進場改程式前先讀這幾條，都是踩過的坑：

1. **DB 存取一律走 `src/db/engine.py` 的 async 介面。**
   Turso 的 Python 驅動是同步的，直接在 handler 裡呼叫會阻塞 asyncio 事件迴圈，
   導致 gateway 心跳超時被 Discord 斷線。engine 內部用 `asyncio.to_thread` +
   單一連線加鎖處理。禁止在 cog / view / domain 層 import `libsql`。

2. **不要用記憶體排程器，也不要用 View 的 timeout collector。**
   Render 免費方案會重啟進程（deploy、休眠喚醒），記憶體狀態一律會丟。
   排程真相在 `reminders` 表，按鈕用持久化 View（`timeout=None` + 固定 `custom_id`）。

3. **時間一律存 UTC epoch 毫秒**，顯示用 Discord 時間戳 `<t:epoch:F>`。
   Discord 客戶端會自動換算成每個人的本地時區，不必自己算。

4. **唯一鍵衝突不要靠例外型別判斷。** libsql 0.1.11 拋的是普通 `ValueError`
   而非 DBAPI `IntegrityError`。需要 upsert 就用 `INSERT OR IGNORE` /
   `ON CONFLICT DO UPDATE`。

5. **Windows 本機開發需要 `tzdata` 套件。** Linux 有系統時區資料庫，Windows 沒有，
   少了它 `ZoneInfo("Asia/Taipei")` 會直接拋錯。已列在 requirements.txt。

6. **每個查詢都必須以 `guild_id` 為界。** 見下方「多伺服器」段落 —— 這是本專案最容易
   寫錯、也最難事後補救的一條。

---

## 多伺服器

Bot 可同時服務多個伺服器，每個伺服器有獨立的 `guild_settings`（公告頻道、時區、
預設提醒時距、誰能開活動、是否允許 @everyone）與獨立的活動 / 投票資料。
加入新伺服器時 `on_guild_join` 會自動建立預設設定。

### ★ 寫程式時必須遵守的紀律

**每一個查詢都必須以 `guild_id` 為界。** 這類漏洞不會讓程式報錯，只會安靜地把別的
伺服器的活動列給你看 —— 靠人工 review 很難抓，所以規則要硬。

1. 讀取活動 / 投票的函式，`guild_id` 一律是**必填參數**，且必須出現在 WHERE 子句。
   **不要提供「不分伺服器」的查詢版本** —— 那種函式一旦存在，早晚會有人誤用。
2. `guild_id` 一律來自 `interaction.guild_id`，**絕不從設定檔取**。
   `DEV_GUILD_ID` 只用於指令同步，與資料查詢完全無關。
3. 操作子表（`rsvps` / `poll_votes` / `poll_options` / `event_invitees` / `reminders`）
   前，必須先確認其母體屬於當前伺服器 —— 用 [src/db/repo.py](src/db/repo.py) 的
   `owned_event()` / `owned_poll()`。子表沒有 `guild_id` 欄位，只靠母體界定範圍，
   少了這層檢查，A 伺服器的人就能用猜到的 ID 改 B 伺服器的資料。
4. Discord ID 是 64-bit 整數，DB 欄位型別是 TEXT。傳入前一律 `str()`，
   否則 `WHERE guild_id = 123` 與存進去的 `'123'` 比不出結果。repo 層已代為處理。
5. 需要伺服器情境的指令要加 `@app_commands.guild_only()` —— 否則在 DM 中呼叫時
   `interaction.guild_id` 會是 `None`。

每新增一個查詢函式，就在 [tests/test_multi_guild.py](tests/test_multi_guild.py) 補一條
對應的隔離測試。

### 指令同步的取捨

多伺服器必須用 global 註冊，但 Discord 對 global 指令有快取，改動後**最長要等 1 小時**
才在各伺服器生效。填了 `DEV_GUILD_ID` 之後，會對該伺服器額外做一次 guild-scoped
同步（即時生效），開發時就不必等。開發伺服器會同時有 global 與 guild 兩份註冊，
Discord 的行為是 guild-scoped 優先，不會出現重複指令。

### 兩個規模天花板

- **超過 100 個伺服器**：Server Members Intent 需要向 Discord 申請審核
- **成員快取吃 RAM**：Render 免費方案只有 512MB。幾十個伺服器就要評估改用
  `chunk_guilds_at_startup=False` + 按需 fetch

---

## 新增 migration

在 `src/db/migrations/` 放 `002_xxx.sql`，開機時自動套用並記錄在 `_migrations` 表。

Migration **必須可重複執行**（`CREATE TABLE IF NOT EXISTS` 等），因為 Render 每次
deploy 都會跑一遍。切語句是用單純的分號分割，所以檔案內不要出現 trigger、
`BEGIN...END`，或字串常值裡的分號。
