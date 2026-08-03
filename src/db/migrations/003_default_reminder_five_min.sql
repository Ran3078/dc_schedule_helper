-- 003_default_reminder_five_min: 預設提醒改成只在活動前 5 分鐘提醒一次
--
-- 原本預設是 1 天 / 1 小時 / 10 分鐘前三次提醒，改成只提前 5 分鐘提醒一次。
-- 這裡只回填「還停在舊預設值」的伺服器 —— 若有人已經自行用 /settings
-- 調整過（未來 M7 才會有這個指令，但先寫成安全的寫法），這裡不會覆蓋掉
-- 使用者自訂的值。
UPDATE guild_settings SET default_reminders = '5' WHERE default_reminders = '1440,60,10';
