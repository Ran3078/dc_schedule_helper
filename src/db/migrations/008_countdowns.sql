-- 008_countdowns: 天數提醒（例如：朋友退伍倒數）
--
-- 跟 events 是分開的表，不是塞進 events 借用它的 starts_at_utc/reminders
-- 機制——events 的提醒是「開始前 N 分鐘」的一次性提醒，這裡要的是「每天
-- 固定時間發一次、直到目標日期」的重複提醒，語意完全不同，硬塞會讓兩邊
-- 的排程邏輯互相污染。
--
-- target_date_utc 存目標日期當天 00:00（建立時使用的時區）的 UTC epoch
-- 毫秒——跟其他時間欄位一樣走「一律 UTC epoch 毫秒」的慣例（001_init.sql
-- 的說明），只是這裡只在意「日期」，時分秒固定是 0。
--
-- send_hour/send_minute 是每筆天數提醒各自的每天發送時間（24 小時制，
-- 該筆建立時使用的 tz），不像 weekly_digest 是全伺服器共用一個時間——
-- 同一個伺服器的不同天數提醒可能想要不同時段發送。
--
-- last_sent_utc 是「上次成功發送的時間點」，跟 weekly_digest 同一種
-- 「真相在 DB、不用記憶體排程」設計：任務迴圈每次醒來都重新判斷今天發過
-- 了沒。status 到了目標日期當天發送完就自動轉成 completed，不會無限期
-- 一直發下去（見這輪確認過的產品決策）。
CREATE TABLE IF NOT EXISTS countdowns (
  id              TEXT PRIMARY KEY,
  guild_id        TEXT NOT NULL,
  channel_id      TEXT NOT NULL,
  creator_id      TEXT NOT NULL,
  title           TEXT NOT NULL,
  content         TEXT,
  target_date_utc INTEGER NOT NULL,
  tz              TEXT NOT NULL,
  send_hour       INTEGER NOT NULL,
  send_minute     INTEGER NOT NULL,
  status          TEXT NOT NULL DEFAULT 'active',  -- active|completed|cancelled
  last_sent_utc   INTEGER,
  created_at      INTEGER NOT NULL,
  updated_at      INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_countdowns_guild ON countdowns(guild_id, status);
CREATE INDEX IF NOT EXISTS idx_countdowns_status ON countdowns(status);
