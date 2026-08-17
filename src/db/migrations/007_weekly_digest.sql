-- 007_weekly_digest: 每週活動清單（M9）
--
-- weekly_digest_enabled 預設關閉——不驚動現有伺服器，要的人自己用
-- /settings weekly_digest:true 開。last_weekly_digest_at 是「上次成功
-- 發送的時間點」，任務迴圈靠它判斷這週發過了沒，真相全部在 DB，
-- 不用記憶體排程（理由同 reminders 表，見該表的註解）。
ALTER TABLE guild_settings ADD COLUMN weekly_digest_enabled INTEGER NOT NULL DEFAULT 0;
ALTER TABLE guild_settings ADD COLUMN last_weekly_digest_at INTEGER;
