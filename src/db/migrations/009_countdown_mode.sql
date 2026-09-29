-- 009_countdown_mode: 天數提醒支援「正數」模式 + 自訂前後綴文字模板
--
-- mode 預設 'countdown'，既有資料行行為不變。target_date_utc 的意義依
-- mode 而定：countdown 是未來目標日期（不變），countup 是過去的錨點日期
-- （新語意，重複利用同一欄位，不新增欄位）。
--
-- suffix 是接在天數後面的文字（例如「天」），NULL 代表不接——
-- render_countdown_text() 用 `f"{title}{count}{suffix or ''}"` 組出
-- 完整訊息，不需要任何佔位符語法。
ALTER TABLE countdowns ADD COLUMN mode TEXT NOT NULL DEFAULT 'countdown';
ALTER TABLE countdowns ADD COLUMN suffix TEXT;
