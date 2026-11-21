-- Deliberately small pre-migration Hub database used to prove init() upgrades
-- existing user data without resetting the database.
CREATE TABLE members(
  id INTEGER PRIMARY KEY,
  name TEXT UNIQUE NOT NULL,
  role TEXT NOT NULL,
  token_hash TEXT NOT NULL,
  profile TEXT,
  last_heartbeat REAL,
  created_at REAL NOT NULL
);
CREATE TABLE rooms(
  id INTEGER PRIMARY KEY,
  name TEXT UNIQUE NOT NULL,
  created_at REAL NOT NULL
);
CREATE TABLE messages(
  id INTEGER PRIMARY KEY,
  room_id INTEGER NOT NULL,
  author_id INTEGER NOT NULL,
  kind TEXT NOT NULL,
  body TEXT NOT NULL,
  task_id INTEGER,
  created_at REAL NOT NULL
);
CREATE TABLE tasks(
  id INTEGER PRIMARY KEY,
  room_id INTEGER NOT NULL,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  created_by INTEGER NOT NULL,
  assignee INTEGER,
  status TEXT NOT NULL,
  claim_token TEXT,
  lease_expires REAL,
  attempts INTEGER NOT NULL DEFAULT 0,
  result TEXT,
  review_note TEXT,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE TABLE task_transfers(
  id INTEGER PRIMARY KEY,
  task_id INTEGER NOT NULL,
  from_member TEXT NOT NULL,
  to_member TEXT NOT NULL,
  reason TEXT,
  actor TEXT NOT NULL,
  created_at REAL NOT NULL
);
INSERT INTO members(id,name,role,token_hash,profile,created_at)
VALUES(1,'legacy-member','member','legacy-token-hash','legacy profile',1);
INSERT INTO rooms(id,name,created_at) VALUES(1,'legacy-room',1);
INSERT INTO messages(id,room_id,author_id,kind,body,created_at)
VALUES(1,1,1,'chat','legacy message',1);
INSERT INTO tasks(
  id,room_id,title,body,created_by,assignee,status,attempts,created_at,updated_at
) VALUES(9,1,'legacy task','legacy body',1,NULL,'open',0,1,1);
INSERT INTO task_transfers(
  id,task_id,from_member,to_member,reason,actor,created_at
) VALUES(1,9,'legacy-member','dispatcher','legacy reason','legacy-member',1);
