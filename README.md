# dc_schedule

Discord 行事曆排程機器人，個人私服自用。自架取代 Sesh / Apollo / Raid-Helper。

完整規劃見 [PLAN.md](PLAN.md)。

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
   - Scopes：`bot`、`applications.commands`
   - Bot Permissions：`Send Messages`、`Embed Links`、`Read Message History`、
     `Create Public Threads`、`Mention Everyone`（要 @everyone 才需要）、
     `Manage Events`（同步原生活動分頁需要）
6. 用產生的連結把 bot 邀進你的伺服器
7. Discord 設定 → 進階 → 開啟開發者模式 → 右鍵伺服器 → 複製伺服器 ID → `GUILD_ID`

### 2. Turso

```bash
turso db create dc-schedule
turso db show dc-schedule --url          # → TURSO_DATABASE_URL
turso db tokens create dc-schedule       # → TURSO_AUTH_TOKEN
```

Schema 不必手動建立 —— 每次啟動會自動套用 `src/db/migrations/*.sql`。

### 3. Render

1. 把這個 repo 推到 GitHub
2. Render → New → Blueprint → 選這個 repo（會讀 [render.yaml](render.yaml)）
3. 在 Dashboard 填入 5 個機密環境變數：`DISCORD_TOKEN`、`DISCORD_APP_ID`、
   `GUILD_ID`、`TURSO_DATABASE_URL`、`TURSO_AUTH_TOKEN`
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

6. **指令是 guild-scoped 註冊**（用 `GUILD_ID`）。更新即時生效；global 註冊有最長
   1 小時的傳播延遲。

---

## 新增 migration

在 `src/db/migrations/` 放 `002_xxx.sql`，開機時自動套用並記錄在 `_migrations` 表。

Migration **必須可重複執行**（`CREATE TABLE IF NOT EXISTS` 等），因為 Render 每次
deploy 都會跑一遍。切語句是用單純的分號分割，所以檔案內不要出現 trigger、
`BEGIN...END`，或字串常值裡的分號。
