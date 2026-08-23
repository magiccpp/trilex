"""Fold core.db into dict.db and build the lookup indexes.

Run after both builders finish. Produces:
  vocab     - every typeable surface form, ranked, for autocomplete + fuzzy
  pivot_df  - how many entries each English pivot term reaches, so that
              generic terms ('thing', 'go') are damped in cross-language ranking
"""
import sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from trilex import db

DATA = Path(__file__).resolve().parents[2] / "data"

EXTRA = """
DROP TABLE IF EXISTS vocab;
CREATE TABLE vocab(
  norm  TEXT NOT NULL,
  lang  TEXT NOT NULL,
  disp  TEXT NOT NULL,
  score REAL NOT NULL,
  core  INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(norm, lang)
) WITHOUT ROWID;

DROP TABLE IF EXISTS pivot_df;
CREATE TABLE pivot_df(en_norm TEXT PRIMARY KEY, df INTEGER NOT NULL) WITHOUT ROWID;
"""


def main(vacuum=False):
    con = db.open_dict(DATA / "dict.db")
    con.execute("PRAGMA synchronous=OFF")
    con.execute(f"ATTACH DATABASE '{DATA / 'core.db'}' AS core")
    t0 = time.time()

    # entry/sense/pivot/form are fully derived from core.db, so recreate them
    # from the current schema rather than DELETE-ing rows: a schema change
    # (adding form.disp) otherwise leaves a stale table that INSERT can't fill.
    # wik and etym are NOT touched -- they hold the expensive streamed data.
    con.executescript("""
      DROP TABLE IF EXISTS main.form;
      DROP TABLE IF EXISTS main.pivot;
      DROP TABLE IF EXISTS main.sense;
      DROP TABLE IF EXISTS main.entry;
    """)
    con.executescript(db.DICT_SCHEMA)

    for t, cols in (("entry", "id,lang,word,norm,pos,extra,src"),
                    ("sense", "id,entry_id,lang,gloss,ord"),
                    ("pivot", "entry_id,en_norm,weight"),
                    ("form",  "norm,lang,entry_id,kind,disp")):
        con.execute(f"INSERT INTO main.{t}({cols}) SELECT {cols} FROM core.{t}")
        n = con.execute(f"SELECT count(*) c FROM main.{t}").fetchone()["c"]
        print(f"  merged {t:6s} {n:>9,}", flush=True)
    con.commit()
    con.execute("DETACH DATABASE core")

    con.executescript(EXTRA)
    # Core dictionary forms rank above Wiktionary-only headwords: a word you
    # can actually get a translation for should outrank a bare etymology.
    # One vocab row per (norm, lang). Where several forms collide on the same
    # key, the display text comes from the highest-priority kind -- so 'hus'
    # shows "hus" (a headword), never "boning" (an entry it is a synonym of).
    # MIN over a priority-prefixed string picks that in a single pass.
    con.execute("""
      INSERT INTO vocab(norm,lang,disp,score,core)
      SELECT f.norm, f.lang,
             substr(MIN(CASE f.kind WHEN 'head'   THEN '0'
                                    WHEN 'pinyin' THEN '1'
                                    WHEN 'trad'   THEN '2'
                                    WHEN 'infl'   THEN '3'
                                    ELSE '4' END || f.disp), 2),
             1000.0 + COUNT(*) + (CASE WHEN MAX(f.kind='head') THEN 800 ELSE 0 END),
             1
      FROM form f
      WHERE f.norm <> '' GROUP BY f.norm, f.lang""")
    con.execute("""
      INSERT OR IGNORE INTO vocab(norm,lang,disp,score,core)
      SELECT norm, lang, MIN(word), COUNT(*)*1.0, 0
      FROM wik WHERE norm <> '' GROUP BY norm, lang""")
    con.execute("INSERT INTO pivot_df SELECT en_norm, count(*) FROM pivot GROUP BY en_norm")
    con.commit()

    con.executescript(db.DICT_INDEXES)
    con.executescript("""
      CREATE INDEX IF NOT EXISTS ix_vocab_score ON vocab(lang, score DESC);
      CREATE INDEX IF NOT EXISTS ix_vocab_n     ON vocab(norm);
    """)
    con.execute("ANALYZE")
    con.commit()
    for t in ("entry", "sense", "pivot", "form", "wik", "etym", "vocab"):
        c = con.execute(f"SELECT count(*) c FROM {t}").fetchone()["c"]
        print(f"  {t:8s} {c:>10,}")
    if vacuum:
        print("  vacuuming...", flush=True)
        con.execute("VACUUM")
    print(f"done in {time.time()-t0:.0f}s  "
          f"dict.db = {(DATA/'dict.db').stat().st_size/1048576:.0f} MB")


if __name__ == "__main__":
    main(vacuum="--vacuum" in sys.argv)
