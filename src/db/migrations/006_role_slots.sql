-- 006_role_slots: FF14 團本職位報名（M8）
--
-- 八個固定位置代碼（MT/ST/H1/H2/D1/D2/D3/D4，見 domain/roles.py），每個
-- 位置名額固定 1 人，不需要 capacity 欄位。不做外鍵約束，理由同其餘子表
-- （多伺服器範圍靠 JOIN/WHERE EXISTS 手動界定，見 001_init.sql 開頭慣例）。

CREATE TABLE IF NOT EXISTS event_role_slots (
  id       TEXT PRIMARY KEY,
  event_id TEXT NOT NULL,
  position TEXT NOT NULL,             -- MT|ST|H1|H2|D1|D2|D3|D4
  sort     INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_role_slots_event ON event_role_slots(event_id, sort);

-- 一人一場活動只能選一個位置：PK 用 (event_id, user_id)，換位置是覆寫這一列
-- 而不是新增一列（見 repo.set_role_signup 的 ON CONFLICT 寫法）。
CREATE TABLE IF NOT EXISTS event_role_signups (
  event_id     TEXT NOT NULL,
  role_slot_id TEXT NOT NULL,
  user_id      TEXT NOT NULL,
  job          TEXT NOT NULL,         -- 該位置允許的職業之一，例如「武士」
  waitlisted   INTEGER NOT NULL DEFAULT 0,
  signed_up_at INTEGER NOT NULL,
  PRIMARY KEY (event_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_role_signups_slot ON event_role_signups(role_slot_id, waitlisted, signed_up_at);
