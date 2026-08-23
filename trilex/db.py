"""SQLite schema. Two databases, deliberately separate:

  dict.db  - the generated dictionary. Big, rebuildable, disposable.
  user.db  - your wordbook and review history. Small, precious, never regenerated.

Keeping them apart means a dictionary rebuild can never eat your saved words.
"""
import os, sqlite3, sys
from pathlib import Path


def data_dir() -> Path:
    if os.environ.get("TRILEX_DATA"):
        return Path(os.environ["TRILEX_DATA"])
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    return base / "trilex"


def connect(path, *, wal=True) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    # Build steps can overlap (a stream committing while a fetcher writes), so
    # wait for the lock instead of failing immediately.
    con.execute("PRAGMA busy_timeout=30000")
    if wal:
        con.execute("PRAGMA journal_mode=WAL")
    return con


# --- dictionary database ------------------------------------------------
DICT_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);

-- Etymology prose, deduplicated: thousands of entries share identical text
-- (every inflected form of a word repeats its parent's etymology).
CREATE TABLE IF NOT EXISTS etym(
  id  INTEGER PRIMARY KEY,
  sha TEXT UNIQUE,
  txt TEXT NOT NULL
);

-- One row per (language, headword, part-of-speech) from Wiktionary.
CREATE TABLE IF NOT EXISTS wik(
  id      INTEGER PRIMARY KEY,
  lang    TEXT NOT NULL,
  word    TEXT NOT NULL,
  norm    TEXT NOT NULL,
  pos     TEXT,
  ipa     TEXT,
  etym_id INTEGER REFERENCES etym(id),
  gloss   TEXT
);

-- Headwords from the bilingual dictionaries (CC-CEDICT, Folkets).
CREATE TABLE IF NOT EXISTS entry(
  id     INTEGER PRIMARY KEY,
  lang   TEXT NOT NULL,          -- en | sv | zh
  word   TEXT NOT NULL,          -- display headword (zh: simplified)
  norm   TEXT NOT NULL,          -- casefolded/stripped key for lookup
  pos    TEXT,
  extra  TEXT,                   -- JSON: pinyin, traditional, inflections...
  src    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sense(
  id       INTEGER PRIMARY KEY,
  entry_id INTEGER NOT NULL REFERENCES entry(id) ON DELETE CASCADE,
  lang     TEXT NOT NULL,        -- language the gloss is written in
  gloss    TEXT NOT NULL,
  ord      INTEGER DEFAULT 0
);

-- The pivot. English is the hub: zh<->sv is reached through English terms,
-- because no free direct Chinese-Swedish lexicon exists.
CREATE TABLE IF NOT EXISTS pivot(
  entry_id INTEGER NOT NULL REFERENCES entry(id) ON DELETE CASCADE,
  en_norm  TEXT NOT NULL,
  weight   REAL NOT NULL DEFAULT 1.0
);

-- Every searchable surface form -> entry. Includes pinyin, traditional
-- characters and Swedish inflections, so all of them are typeable.
CREATE TABLE IF NOT EXISTS form(
  norm     TEXT NOT NULL,
  lang     TEXT NOT NULL,
  entry_id INTEGER NOT NULL REFERENCES entry(id) ON DELETE CASCADE,
  kind     TEXT NOT NULL,       -- head | pinyin | trad | infl | syn
  disp     TEXT NOT NULL        -- what to show: a synonym form shows itself,
                                -- a pinyin form shows its Chinese characters
);
"""

DICT_INDEXES = """
CREATE INDEX IF NOT EXISTS ix_wik_lw    ON wik(lang, norm);
CREATE INDEX IF NOT EXISTS ix_entry_ln  ON entry(lang, norm);
CREATE INDEX IF NOT EXISTS ix_sense_e   ON sense(entry_id);
CREATE INDEX IF NOT EXISTS ix_pivot_en  ON pivot(en_norm);
CREATE INDEX IF NOT EXISTS ix_pivot_e   ON pivot(entry_id);
CREATE INDEX IF NOT EXISTS ix_form_n    ON form(norm);
CREATE INDEX IF NOT EXISTS ix_form_e    ON form(entry_id);
"""

# --- user database ------------------------------------------------------
USER_SCHEMA = """
CREATE TABLE IF NOT EXISTS card(
  id        INTEGER PRIMARY KEY,
  lang      TEXT NOT NULL,
  word      TEXT NOT NULL,
  note      TEXT DEFAULT '',
  snapshot  TEXT,               -- JSON of the entry as saved, so the card
                                -- still renders if the dictionary is rebuilt
  added_at  TEXT NOT NULL,
  -- SM-2 scheduling state
  due       TEXT NOT NULL,
  interval  REAL NOT NULL DEFAULT 0,
  ease      REAL NOT NULL DEFAULT 2.5,
  reps      INTEGER NOT NULL DEFAULT 0,
  lapses    INTEGER NOT NULL DEFAULT 0,
  -- Forgetting-curve state (FSRS). `interval`/`ease` are kept only so an
  -- older wordbook still opens; scheduling reads stability/difficulty.
  stability   REAL NOT NULL DEFAULT 0,
  difficulty  REAL NOT NULL DEFAULT 0,
  last_review TEXT,
  -- Sync identity. uid is stable across devices; updated_at drives
  -- last-write-wins; deleted is a tombstone so a removal propagates.
  uid        TEXT,
  updated_at TEXT,
  deleted    INTEGER NOT NULL DEFAULT 0,
  -- Explicit "needs pushing" flag. A timestamp high-water mark cannot do this
  -- job: updated_at has second resolution, so an edit made in the same second
  -- as a sync compares equal and is silently never sent.
  dirty      INTEGER NOT NULL DEFAULT 1,
  UNIQUE(lang, word)
);
CREATE TABLE IF NOT EXISTS review(
  id      INTEGER PRIMARY KEY,
  card_id INTEGER NOT NULL REFERENCES card(id) ON DELETE CASCADE,
  ts      TEXT NOT NULL,
  grade   INTEGER NOT NULL,
  interval REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_card_due ON card(due);
CREATE UNIQUE INDEX IF NOT EXISTS ix_card_uid ON card(uid);
CREATE TABLE IF NOT EXISTS sync_state(
  k TEXT PRIMARY KEY, v TEXT
);
CREATE INDEX IF NOT EXISTS ix_rev_card ON review(card_id);
"""


# Optional media packs, downloaded after installation. They live in separate
# files so the base install stays small and a pack can be added or deleted
# without touching the dictionary.
MEDIA_PACKS = {"images": "media-images.db", "audio": "media-audio.db"}


def pack_path(name, path=None):
    base = Path(path).parent if path else data_dir()
    return base / MEDIA_PACKS[name]


def attach_media(con, path=None):
    """ATTACH whichever packs are installed.

    SQLite resolves an unqualified table name across attached databases, so
    queries against `image` and `audio` need no changes whether a pack is
    present or not.
    """
    attached = []
    for name, fname in MEDIA_PACKS.items():
        p = pack_path(name, path)
        if p.exists() and p.stat().st_size > 4096:
            try:
                con.execute(f"ATTACH DATABASE ? AS media_{name}", (str(p),))
                attached.append(name)
            except Exception:
                pass
    return attached


def attach_pack(con, name, path=None) -> bool:
    """ATTACH a single pack that was installed while the app was running."""
    already = {r["name"] for r in con.execute("PRAGMA database_list")}
    alias = f"media_{name}"
    if alias in already:
        return True
    p = pack_path(name, path)
    if not (p.exists() and p.stat().st_size > 4096):
        return False
    try:
        con.execute(f"ATTACH DATABASE ? AS {alias}", (str(p),))
        return True
    except Exception:
        return False


def detach_pack(con, name) -> bool:
    try:
        con.execute(f"DETACH DATABASE media_{name}")
        return True
    except Exception:
        return False


def open_dict(path=None, create=True, media=True):
    path = path or data_dir() / "dict.db"
    con = connect(path)
    if create:
        con.executescript(DICT_SCHEMA)
    if media:
        attach_media(con, path)
    return con


# Columns added after the first release; SQLite has no ADD COLUMN IF NOT
# EXISTS, so widen an existing wordbook by inspecting the table first.
_CARD_MIGRATIONS = [
    ("stability",   "REAL NOT NULL DEFAULT 0"),
    ("difficulty",  "REAL NOT NULL DEFAULT 0"),
    ("last_review", "TEXT"),
    ("uid",         "TEXT"),
    ("updated_at",  "TEXT"),
    ("deleted",     "INTEGER NOT NULL DEFAULT 0"),
    ("dirty",       "INTEGER NOT NULL DEFAULT 1"),
]


def open_user(path=None):
    con = connect(path or data_dir() / "user.db")
    con.executescript(USER_SCHEMA)
    have = {r["name"] for r in con.execute("PRAGMA table_info(card)")}
    for col, decl in _CARD_MIGRATIONS:
        if col not in have:
            con.execute(f"ALTER TABLE card ADD COLUMN {col} {decl}")
    # Backfill sync identity for rows saved before sync existed.
    import uuid as _uuid
    from datetime import datetime as _dt
    for r in con.execute("SELECT id FROM card WHERE uid IS NULL").fetchall():
        con.execute("UPDATE card SET uid=?, updated_at=COALESCE(updated_at,?) "
                    "WHERE id=?",
                    (str(_uuid.uuid4()),
                     _dt.now().astimezone().isoformat(timespec="seconds"), r["id"]))
    con.commit()
    return con
