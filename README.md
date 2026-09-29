# dc_schedule

Discord 行事曆排程機器人。自架取代 Sesh / Apollo / Raid-Helper。**支援多伺服器**，每個伺服器有獨立的設定與資料。

---

## 功能／指令

| 指令 | 用途 |
|------|------|
| `/event create`／`edit`／`cancel`／`invite`／`ping`／`list`／`info` | 活動建立與管理，支援 RSVP、提醒排程、原生活動同步 |
| `/ff14_recruit` | 一次建立含職位名額的 FF14 團本招募活動 |
| `/poll create`／`close`／`results` | 投票（單選/複選、匿名、可設截止時間） |
| `/countdown create`／`list`／`cancel` | 自訂天數提醒——**倒數**模式每天發一次「還剩幾天」，到目標日期當天自動停止；**正數**模式每天遞增（像「沒發生意外第 N 天」看板），永遠累加到手動取消為止。訊息文字是「前綴＋天數＋後綴」（例如「退伍倒數500天」），不需要任何佔位符語法。倒數的目標日期可以打確切日期，也可以偷懶直接打「還剩幾天」；正數則直接打「目前已經第幾天」。每天發送時間一律用下拉選單挑，不用手打 |
| `/settings`、`/timezone set` | 伺服器與個人偏好設定（公告頻道、時區、提醒時距、身分組權限等） |
| `@提及機器人` | 跳出按鈕選單（建立活動／FF14 招募／建立投票／天數提醒／本週活動），不用先打指令 |
| 每週活動清單（`/settings weekly_digest:true` 開啟） | 每週日 00:00 自動發布未來 7 天活動預告，附「新增行程」按鈕 |

---

## 技術棧

| 項目 | 選擇 |
|------|------|
| Runtime | Python 3.12+ |
| Discord | discord.py 2.7 |
| 資料庫 | Turso（libSQL），官方 `libsql` 驅動 + 手寫 SQL |
| HTTP | `aiohttp`（discord.py 已帶入，不需額外套件） |
| 部署 | Oracle Cloud Always Free VM + systemd（見「首次設定」；Render 免費 Web Service + 外部 cron 保活是備選） |

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

### 3. 部署——自架 Always Free VM（推薦，永久免費、不休眠）

Render 免費方案有兩個先天限制：閒置 15 分鐘就休眠（要另外搭保活 cron 撐著，
見下方「選擇 Render」）、以及免費方案共用 IP，偶爾會被其他租戶連坐（實際
遇過一次 Discord 回傳「全域限速封鎖」，事後排查跟自己的請求頻率無關，
研判是共用 IP 被別的租戶拖累）。改用雲端服務商的「Always Free」永久免費
VM 就沒有這兩個問題——24 小時不休眠，代價是要自己顧一台 Linux VM（SSH、
systemd、防火牆），沒有 Render 那種按一鍵部署的體驗。兩個都是真永久免費、
不是試用，帳號審核**看運氣**，哪個過就用哪個：

| | Oracle Cloud Always Free | Google Cloud `e2-micro` Always Free |
|---|---|---|
| 規格 | 最高 4 OCPU／24GB RAM（ARM） | 1 vCPU（共享）／1GB RAM |
| 區域 | 不限 | 限 us-west1／us-central1／us-east1 三選一 |
| 對外流量 | 10TB/月 | 1GB/月（這個 bot 用量小，正常情況夠用） |
| 帳號審核 | 社群反應偏嚴、常被拒 | 相對容易過 |
| 特有風險 | 連續 7 天 CPU 使用率過低會被回收（先寄信警告，登入按「保留」即可） | 同一帳單帳戶只有第一台 e2-micro 算免費，多開一台就開始收費 |

兩者都需要信用卡驗證身分（不會真的扣款），Oracle 卡關的話換 Google 試，
兩邊帳號審核邏輯是獨立的。

#### 選項 A：Oracle Cloud Always Free

1. 到 [Oracle Cloud Free Tier](https://www.oracle.com/cloud/free/) 申請帳號
2. 建立運算執行個體：Shape 選 **Ampere（ARM）→ VM.Standard.A1.Flex**，
   OS 選 Ubuntu，OCPU／記憶體依需求調整（這個 bot 用 1 OCPU／6GB 綽綽有餘）
3. SSH 進去，照下方「共通安裝步驟」

VM 是 ARM 架構（aarch64）：Python 本身沒問題，但 `libsql` 這類 C extension
套件要留意有沒有現成的 aarch64 wheel——沒有的話 `pip install` 會退回原始碼
編譯，失敗率較高，部署前先實際跑一次 `pip install -r requirements.txt`
確認過得去。

#### 選項 B：Google Cloud `e2-micro` Always Free

1. 到 [console.cloud.google.com](https://console.cloud.google.com) 建立帳號、
   建一個新專案
2. 左側選單 → Compute Engine → 第一次使用會提示啟用 API（需要先綁定帳單
   帳戶，只要維持在 Always Free 額度內就不會扣款）
3. 建立執行個體：
   - **區域務必選 us-west1／us-central1／us-east1 其中一個**——其他區域
     不算 Always Free，會直接開始計費
   - 機器類型選 **e2-micro**
   - 開機磁碟選 Ubuntu（例如 24.04 LTS），磁碟大小 ≤ 30GB（Always Free
     額度上限）
   - 防火牆選項都不用勾——這個 bot 只需要對外連線，不需要任何人從外面
     連進來
4. 建好後直接在主控台點執行個體旁的 **SSH** 按鈕（瀏覽器內建終端機，不用
   自己管理金鑰），照下方「共通安裝步驟」

⚠️ **同一個帳單帳戶下，Always Free 只包含第一台符合資格的 `e2-micro`**——
不要手滑建第二台，會直接開始計費。

#### 共通安裝步驟

```bash
# Ubuntu 24.04 LTS 預設帶 Python 3.12，如果 apt 裡找不到 python3.13，
# 先加 deadsnakes PPA：
# sudo add-apt-repository ppa:deadsnakes/ppa && sudo apt update
sudo apt update && sudo apt install -y python3.13 python3.13-venv git
git clone <你的 repo URL>
cd dc_schedule_helper
python3.13 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # 填入 DISCORD_TOKEN 等機密值（見上面「1. Discord 應用程式」「2. Turso」）
```

用 systemd 顧 process，開機自動啟動、當掉自動重啟（等同 Render 免費幫你
做的事）：

```bash
sudo cp deploy/dc-schedule.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now dc-schedule
journalctl -u dc-schedule -f   # 看即時 log
```

`deploy/dc-schedule.service` 裡的路徑（`WorkingDirectory`／`ExecStart`／
`User`）預設抓 `/home/ubuntu/...`，跟實際部署路徑不同要記得改（GCP 的
Ubuntu 映像檔預設使用者名稱通常也是你 Google 帳號的名稱，不一定是
`ubuntu`，SSH 進去後用 `whoami` 確認）。

### 4. 保活設定——只有 Render 需要

VM（Oracle／GCP）不會像 Render 免費方案那樣閒置休眠，`/healthz`／`/readyz`
保留下來純粹給你自己（或 UptimeRobot 之類的服務）監控用，不是必要條件。
選擇下面「Render」路徑才需要做這一步。

### 選擇 Render（願意付費／想要一鍵部署體驗）

1. 把這個 repo 推到 GitHub
2. Render → New → Blueprint → 選這個 repo（會讀 [render.yaml](render.yaml)）
3. 在 Dashboard 填入機密環境變數：`DISCORD_TOKEN`、`DISCORD_APP_ID`、
   `TURSO_DATABASE_URL`、`TURSO_AUTH_TOKEN`，以及選填的 `DEV_GUILD_ID`
4. Deploy
5. **保活（免費方案必做）**：Render 免費 Web Service 閒置 15 分鐘就休眠，
   休眠會切斷 Discord gateway 連線，冷啟動要約 1 分鐘。到
   [cron-job.org](https://cron-job.org)（免費）建立一個任務：
   - URL：`https://<你的服務>.onrender.com/healthz`
   - 間隔：每 10 分鐘（間隔愈短，緩衝愈大，愈不容易被單次失敗的 ping 拖到睡著）

⚠️ **免費額度沒有餘裕**：Render 免費方案是 **750 instance hours / 月 /
workspace**，而全月常駐 31 天 = 744 小時，同一個 workspace 不能再有其他
免費服務，否則會超額被停。若覺得偶爾斷線太煩，升級 Background Worker
（$7/mo）即可拿掉保活 hack。

---

## 端點

| 路徑 | 用途 |
|------|------|
| `/healthz` | 只證明進程活著，不碰 DB。部署在 Render 時是保活 cron／health check 的必要端點；部署在 VM 上則純粹是可選的監控端點（啟動初期 gateway 還沒連上時也要回 200，否則 Render 會誤判 deploy 失敗） |
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
才在各伺服器生效——這是刻意接受的取捨。曾經試過額外對 `DEV_GUILD_ID` 做一次
guild-scoped 同步換取即時生效，但實測 Discord **不會**把 global 跟 guild-scoped
這兩種註冊視為同一個指令、不會自動去重：開發伺服器的指令選單上，每個指令都會
同時看到兩份一模一樣的紀錄。現在全部只走 global，`DEV_GUILD_ID` 只用來在開機時
順便清掉該伺服器過去累積的 guild-scoped 舊註冊，不再拿它多做一次同步。

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

---

## CI／分支保護

`.github/workflows/ci.yml` 在每個 PR跟推到 `master` 時跑 `ruff check .` ＋
`pytest -q`。測試不需要任何雲端密鑰——`tests/conftest.py` 的 `db` fixture
一律用本機臨時 SQLite 檔，所以這個 workflow 不用設定任何 GitHub secrets。

單人開發沒有另一個人可以核准 PR（GitHub 不允許自己核准自己的 PR），所以
分支保護規則不要求 review，改成要求這個 CI 通過：GitHub 上
Settings → Branches → Add rule（`master`）→ 勾選
**Require status checks to pass before merging**，選 `test` 這個 job。
這樣測試沒過就不能合併，效果等同「有人把關」，但不用維護額外的審核帳號。
