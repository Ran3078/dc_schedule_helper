-- 005_polls_description: 投票加上說明欄位
--
-- events 表一開始就有 description，polls 表當初漏了（跟 004 補 creator_id
-- 是同一種情況）。用來收 /poll create Modal 裡「投票敘述（選填）」的內容。
--
-- SQLite 的 ALTER TABLE ADD COLUMN 沒有 IF NOT EXISTS 語法；冪等性靠
-- migrate.py 的 _migrations 表保證，理由同 004_polls_creator_id.sql。留空
-- （NULL）不設 NOT NULL——沒有合理的常數預設值可填。
ALTER TABLE polls ADD COLUMN description TEXT;
