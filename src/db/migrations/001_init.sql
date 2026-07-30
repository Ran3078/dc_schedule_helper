-- 001_init: 初始 schema
--
-- 慣例：
--   * 所有時間欄位一律 INTEGER，存 UTC epoch **毫秒**。顯示時交給 Discord
--     時間戳 <t:epoch:F> 自動換算成每位使用者的本地時區。
--   * 布林值用 INTEGER 0/1（SQLite 無原生 boolean）。
--   * id 用 nanoid(10) 字串，短到能塞進 Discord custom_id 的 100 字元上限。

CREATE TABLE IF NOT EXISTS guild_settings (
  guild_id            TEXT PRIMARY KEY,
  default_tz          TEXT    NOT NULL DEFAULT 'Asia/Taipei',
  announce_channel_id TEXT,
  default_reminders   TEXT    NOT NULL DEFAULT '1440,60,10',  -- 提前幾分鐘，逗號分隔
  allow_everyone_ping INTEGER NOT NULL DEFAULT 0,
  organizer_role_id   TEXT,                                   -- NULL = 所有人皆可開活動
  locale              TEXT    NOT NULL DEFAULT 'zh-TW',
  sync_native_events  INTEGER NOT NULL DEFAULT 1,             -- 同步 Discord 原生活動分頁
  created_at          INTEGER NOT NULL,
  updated_at          INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS user_prefs (
  user_id    TEXT PRIMARY KEY,
  tz         TEXT,                                  -- 覆寫個人時區，只影響輸入解析
  dm_optout  INTEGER NOT NULL DEFAULT 0,
  updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
  id               TEXT PRIMARY KEY,
  guild_id         TEXT NOT NULL,
  channel_id       TEXT NOT NULL,
  message_id       TEXT,                            -- 公告訊息，供 RSVP 後即時更新
  thread_id        TEXT,
  creator_id       TEXT NOT NULL,
  title            TEXT NOT NULL,
  starts_at_utc    INTEGER NOT NULL,                -- 必填
  ends_at_utc      INTEGER,
  tz               TEXT NOT NULL,                   -- 建立時使用的時區，供日後編輯還原
  location         TEXT,                            -- 選填
  description      TEXT,                            -- 選填
  capacity         INTEGER,                         -- NULL = 不限；候補邏輯留待 Phase 2
  status           TEXT NOT NULL DEFAULT 'scheduled',  -- scheduled|cancelled|completed
  discord_event_id TEXT,                            -- 原生 Scheduled Event 連動
  rrule            TEXT,                            -- 重複規則，Phase 3
  parent_event_id  TEXT,                            -- 重複活動的母體
  created_at       INTEGER NOT NULL,
  updated_at       INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_guild_start ON events(guild_id, starts_at_utc);
CREATE INDEX IF NOT EXISTS idx_events_status ON events(status, starts_at_utc);
CREATE INDEX IF NOT EXISTS idx_events_message ON events(message_id);

-- 被安排的參加對象（人 / 角色 / everyone）
CREATE TABLE IF NOT EXISTS event_invitees (
  event_id    TEXT NOT NULL,
  target_type TEXT NOT NULL,                        -- user|role|everyone
  target_id   TEXT NOT NULL,                        -- everyone 時填 guild_id
  required    INTEGER NOT NULL DEFAULT 0,           -- 1 = 必到
  PRIMARY KEY (event_id, target_type, target_id)
);

CREATE TABLE IF NOT EXISTS rsvps (
  event_id     TEXT NOT NULL,
  user_id      TEXT NOT NULL,
  status       TEXT NOT NULL,                       -- yes|no|maybe|waitlist
  note         TEXT,
  responded_at INTEGER NOT NULL,
  PRIMARY KEY (event_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_rsvps_event_status ON rsvps(event_id, status);

CREATE TABLE IF NOT EXISTS polls (
  id           TEXT PRIMARY KEY,
  guild_id     TEXT NOT NULL,
  event_id     TEXT,                                -- NULL = 獨立投票
  channel_id   TEXT NOT NULL,
  message_id   TEXT,
  question     TEXT NOT NULL,
  kind         TEXT NOT NULL DEFAULT 'generic',     -- generic|time_slot
  multi        INTEGER NOT NULL DEFAULT 0,          -- 0 = 單選, 1 = 複選
  max_choices  INTEGER,                             -- 複選上限，NULL = 不限
  anonymous    INTEGER NOT NULL DEFAULT 0,
  allow_change INTEGER NOT NULL DEFAULT 1,
  closes_at    INTEGER,
  status       TEXT NOT NULL DEFAULT 'open',        -- open|closed
  created_at   INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_polls_message ON polls(message_id);
CREATE INDEX IF NOT EXISTS idx_polls_event ON polls(event_id);

CREATE TABLE IF NOT EXISTS poll_options (
  id      TEXT PRIMARY KEY,
  poll_id TEXT NOT NULL,
  label   TEXT NOT NULL,
  emoji   TEXT,
  sort    INTEGER NOT NULL,
  meta    TEXT                                      -- time_slot 時放候選時段 epoch
);

CREATE INDEX IF NOT EXISTS idx_poll_options_poll ON poll_options(poll_id, sort);

CREATE TABLE IF NOT EXISTS poll_votes (
  poll_id   TEXT NOT NULL,
  option_id TEXT NOT NULL,
  user_id   TEXT NOT NULL,
  voted_at  INTEGER NOT NULL,
  PRIMARY KEY (poll_id, option_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_poll_votes_poll_user ON poll_votes(poll_id, user_id);

-- 提醒佇列：排程的唯一真相來源。
-- 刻意不用記憶體排程器 —— Render 免費方案會重啟進程，記憶體排程一重啟就全失。
CREATE TABLE IF NOT EXISTS reminders (
  id          TEXT PRIMARY KEY,
  event_id    TEXT NOT NULL,
  fire_at_utc INTEGER NOT NULL,
  offset_min  INTEGER NOT NULL,
  audience    TEXT NOT NULL DEFAULT 'invitees',     -- invitees|yes|maybe|no_response|channel
  channel_dm  TEXT NOT NULL DEFAULT 'channel',      -- channel|dm|both
  sent_at     INTEGER,
  state       TEXT NOT NULL DEFAULT 'pending'       -- pending|sent|skipped|failed
);

-- 排程 tick 每 30 秒就靠這個索引撈待發提醒
CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(state, fire_at_utc);
CREATE INDEX IF NOT EXISTS idx_reminders_event ON reminders(event_id);
