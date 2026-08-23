"""Stream inflected forms (tenses, plurals, declensions) for English and Swedish.

A second pass over the same kaikki dumps, capturing the `forms` field that the
etymology pass ignored. Keyed by lemma rather than by wik row id, so it is
independent of the etymology tables and can be re-run on its own.

Chinese is skipped: it does not inflect.

Two payoffs:
  1. a real conjugation/declension table in the entry view
  2. searching an inflected form finds its lemma - 'ran' -> run,
     'husen' -> hus, 'sprang' -> springa
"""
import json, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from trilex import db
from trilex.build.kaikki import SOURCES, _stream_lines
from trilex.norm import norm

DATA = Path(__file__).resolve().parents[2] / "data"

# Tags that describe the wiktionary table itself, not a real word form.
JUNK_TAGS = {"table-tags", "inflection-template", "error-unrecognized-form",
             "class", "romanization", "no-table-tags", "inflection"}
# Forms worth storing; anything else is noise for a learner's dictionary.
KEEP_TAGS = {
    # English
    "plural", "past", "participle", "present", "singular", "third-person",
    "comparative", "superlative", "gerund",
    # Swedish
    "definite", "indefinite", "nominative", "genitive", "supine", "imperative",
    "infinitive", "positive", "neuter", "common-gender", "passive", "subjunctive",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS wform(
  lang       TEXT NOT NULL,
  lemma      TEXT NOT NULL,
  lemma_norm TEXT NOT NULL,
  pos        TEXT,
  form       TEXT NOT NULL,
  norm       TEXT NOT NULL,
  tags       TEXT NOT NULL
);
"""
INDEXES = """
CREATE INDEX IF NOT EXISTS ix_wform_norm  ON wform(norm);
CREATE INDEX IF NOT EXISTS ix_wform_lemma ON wform(lang, lemma_norm);
"""


def useful(f) -> tuple[str, str] | None:
    form = (f.get("form") or "").strip()
    tags = f.get("tags") or []
    if not form or form in ("-", "—", "?") or len(form) > 80:
        return None
    if any(t in JUNK_TAGS for t in tags):
        return None
    keep = [t for t in tags if t in KEEP_TAGS]
    if not keep:
        return None
    return form, " ".join(sorted(keep))


def build(lang, con, resume=True):
    url = SOURCES[lang]
    key, done_key = f"forms:{lang}:offset", f"forms:{lang}:done"
    if resume and con.execute("SELECT 1 FROM meta WHERE k=?", (done_key,)).fetchone():
        print(f"[{lang}] forms already complete", flush=True)
        return
    row = con.execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
    start = int(row["v"]) if (row and resume) else 0
    if start:
        print(f"[{lang}] resuming forms at byte {start:,}", flush=True)
    else:
        con.execute("DELETE FROM wform WHERE lang=?", (lang,))
        con.commit()

    cur, pending, n, kept = con.cursor(), [], 0, 0
    t0 = time.time()
    for raw, off in _stream_lines(url, start):
        n += 1
        try:
            rec = json.loads(raw)
        except Exception:
            continue
        if rec.get("lang_code") != lang or not rec.get("word") or not rec.get("forms"):
            continue
        lemma = rec["word"]
        ln = norm(lemma)
        seen = set()
        for f in rec["forms"]:
            got = useful(f)
            if not got:
                continue
            form, tags = got
            if (form, tags) in seen or norm(form) == ln and "participle" not in tags:
                continue
            seen.add((form, tags))
            pending.append((lang, lemma, ln, rec.get("pos"), form, norm(form), tags))
            kept += 1
        if len(pending) >= 8000:
            cur.executemany("INSERT INTO wform(lang,lemma,lemma_norm,pos,form,norm,tags)"
                            " VALUES(?,?,?,?,?,?,?)", pending)
            pending.clear()
            cur.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (key, str(off)))
            con.commit()
            mb = off / 1048576
            print(f"[{lang}] {mb:7.0f} MB  {n:>9,} lines  {kept:>8,} forms  "
                  f"{mb/max(time.time()-t0,1):.2f} MB/s", flush=True)
    if pending:
        cur.executemany("INSERT INTO wform(lang,lemma,lemma_norm,pos,form,norm,tags)"
                        " VALUES(?,?,?,?,?,?,?)", pending)
    cur.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (done_key, "1"))
    con.commit()
    print(f"[{lang}] DONE  {kept:,} forms in {(time.time()-t0)/60:.1f} min", flush=True)


if __name__ == "__main__":
    con = db.open_dict(DATA / "dict.db")
    con.executescript(SCHEMA)
    for lg in (sys.argv[1:] or ["sv", "en"]):
        build(lg, con)
    con.executescript(INDEXES)
    con.commit()
    print("forms indexed:",
          con.execute("SELECT count(*) c FROM wform").fetchone()["c"])
