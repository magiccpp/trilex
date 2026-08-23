"""SQLite storage for the sync service."""
import sqlite3
from pathlib import Path

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);

CREATE TABLE IF NOT EXISTS user(
  id         INTEGER PRIMARY KEY,
  email      TEXT UNIQUE NOT NULL,          -- normalised, lowercase
  created_at TEXT NOT NULL,
  last_seen  TEXT,
  rev        INTEGER NOT NULL DEFAULT 0     -- monotonic per-user change counter
);

-- One-time login codes. Only a hash is stored, so a database leak does not
-- hand over live login codes.
CREATE TABLE IF NOT EXISTS login_code(
  id         INTEGER PRIMARY KEY,
  email      TEXT NOT NULL,
  code_hash  TEXT NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  attempts   INTEGER NOT NULL DEFAULT 0,
  used       INTEGER NOT NULL DEFAULT 0,
  ip         TEXT
);
CREATE INDEX IF NOT EXISTS ix_code_email ON login_code(email, used, expires_at);

-- Bearer tokens, also stored only as hashes.
CREATE TABLE IF NOT EXISTS token(
  id         INTEGER PRIMARY KEY,
  user_id    INTEGER NOT NULL REFERENCES user(id) ON DELETE CASCADE,
  token_hash TEXT UNIQUE NOT NULL,
  device     TEXT,
  created_at TEXT NOT NULL,
  last_used  TEXT,
  expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_token_user ON token(user_id);

-- The synced wordbook. `rev` is assigned by the server on every write so a
-- client can ask "what changed since rev N?" without relying on clock skew.
CREATE TABLE IF NOT EXISTS card(
  user_id    INTEGER NOT NULL REFERENCES user(id) ON DELETE CASCADE,
  uid        TEXT NOT NULL,                 -- client-generated UUID
  lang       TEXT NOT NULL,
  word       TEXT NOT NULL,
  note       TEXT NOT NULL DEFAULT '',
  snapshot   TEXT,
  added_at   TEXT,
  due        TEXT,
  stability  REAL NOT NULL DEFAULT 0,
  difficulty REAL NOT NULL DEFAULT 0,
  reps       INTEGER NOT NULL DEFAULT 0,
  lapses     INTEGER NOT NULL DEFAULT 0,
  last_review TEXT,
  deleted    INTEGER NOT NULL DEFAULT 0,    -- tombstone, never hard-deleted
  updated_at TEXT NOT NULL,                 -- client clock, for last-write-wins
  rev        INTEGER NOT NULL,
  PRIMARY KEY(user_id, uid)
);
CREATE INDEX IF NOT EXISTS ix_card_rev ON card(user_id, rev);

CREATE TABLE IF NOT EXISTS rate(
  key    TEXT NOT NULL,
  window TEXT NOT NULL,
  n      INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(key, window)
);
"""


def connect(path: str) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, check_same_thread=False, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=15000")
    return con


def init(path: str) -> sqlite3.Connection:
    con = connect(path)
    con.executescript(SCHEMA)
    con.commit()
    return con
