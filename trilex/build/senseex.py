"""Third example-sentence pass: Wiktionary sense-level examples.

Streams the same kaikki dumps again, this time collecting the usage examples
attached to individual senses.

The two languages behave differently, and the ranking reflects that honestly:

  Swedish - ~98% of examples carry an English translation, because a Swedish
            entry on the English Wiktionary is written for English readers.
            These are excellent and rank just after the Folkets examples.
  English - none carry a translation, since an English entry explains itself.
            They still show how a word is used, so they are kept, but ranked
            below every translated example.

Quotations (literary citations) are separated from plain usage examples and
ranked lower: they are often archaic and long.
"""
import json, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from trilex import db
from trilex.build.kaikki import SOURCES, _stream_lines
from trilex.norm import norm

DATA = Path(__file__).resolve().parents[2] / "data"
PER_WORD = 5
MAX_WORDS = 20
MIN_CHARS = 8

# Score bands. Lower sorts first; translated material always wins.
SCORE_TRANSLATED = 6.0
SCORE_QUOTE_TRANSLATED = 8.0
SCORE_PLAIN = 20.0
SCORE_QUOTE_PLAIN = 24.0


def core_vocab(con, lang):
    return {r["norm"] for r in con.execute(
        "SELECT norm FROM vocab WHERE lang=? AND core=1", (lang,))}


def build(lang, con, resume=True):
    key, done_key = f"senseex:{lang}:offset", f"senseex:{lang}:done"
    if resume and con.execute("SELECT 1 FROM meta WHERE k=?", (done_key,)).fetchone():
        print(f"[{lang}] sense examples already complete", flush=True)
        return
    row = con.execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
    start = int(row["v"]) if (row and resume) else 0
    if start:
        print(f"[{lang}] resuming at byte {start:,}", flush=True)
    else:
        con.execute("DELETE FROM example WHERE src LIKE 'wiktionary%' AND lang=?",
                    (lang,))
        con.commit()

    vocab = core_vocab(con, lang)
    print(f"[{lang}] {len(vocab):,} dictionary headwords to match", flush=True)

    # How many we already hold per word from earlier sources, so a word that
    # is already well covered does not crowd out one that has nothing.
    have = {}
    for r in con.execute("SELECT norm, count(*) c FROM example WHERE lang=? "
                         "GROUP BY norm", (lang,)):
        have[r["norm"]] = r["c"]

    cur, pending, n, kept = con.cursor(), [], 0, 0
    t0 = time.time()
    for raw, off in _stream_lines(SOURCES[lang], start):
        n += 1
        try:
            rec = json.loads(raw)
        except Exception:
            continue
        if rec.get("lang_code") != lang or not rec.get("word"):
            continue
        nw = norm(rec["word"])
        if nw not in vocab:
            continue
        budget = PER_WORD - max(0, have.get(nw, 0) - 3)
        if budget <= 0:
            continue
        taken = 0
        for sense in rec.get("senses") or ():
            for ex in sense.get("examples") or ():
                text = (ex.get("text") or "").strip()
                if len(text) < MIN_CHARS or len(text.split()) > MAX_WORDS:
                    continue
                trans = (ex.get("english") or "").strip() or None
                if lang == "en":
                    trans = None          # an English gloss of English is noise
                is_quote = (ex.get("type") or "") == "quote" or bool(ex.get("ref"))
                if trans:
                    score = SCORE_QUOTE_TRANSLATED if is_quote else SCORE_TRANSLATED
                else:
                    score = SCORE_QUOTE_PLAIN if is_quote else SCORE_PLAIN
                score += len(text) / 400.0
                pending.append((lang, nw, text, trans,
                                "en" if trans else None,
                                "wiktionary:quote" if is_quote else "wiktionary",
                                score))
                kept += 1
                taken += 1
                if taken >= budget:
                    break
            if taken >= budget:
                break
        have[nw] = have.get(nw, 0) + taken

        if len(pending) >= 6000:
            cur.executemany("INSERT INTO example"
                            "(lang,norm,text,trans,trans_lang,src,score)"
                            " VALUES(?,?,?,?,?,?,?)", pending)
            pending.clear()
            cur.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (key, str(off)))
            con.commit()
            mb = off / 1048576
            print(f"[{lang}] {mb:7.0f} MB  {n:>9,} lines  {kept:>7,} examples  "
                  f"{mb/max(time.time()-t0,1):.2f} MB/s", flush=True)
    if pending:
        cur.executemany("INSERT INTO example"
                        "(lang,norm,text,trans,trans_lang,src,score)"
                        " VALUES(?,?,?,?,?,?,?)", pending)
    cur.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (done_key, "1"))
    con.commit()
    print(f"[{lang}] DONE {kept:,} examples in {(time.time()-t0)/60:.1f} min",
          flush=True)


if __name__ == "__main__":
    con = db.open_dict(DATA / "dict.db")
    for lg in (sys.argv[1:] or ["sv", "en"]):
        build(lg, con)
    con.executescript(
        "CREATE INDEX IF NOT EXISTS ix_example_l ON example(lang, norm, score);")
    con.commit()
    tot = con.execute("SELECT count(*) c FROM example").fetchone()["c"]
    for lg in ("en", "sv"):
        w = con.execute("SELECT count(DISTINCT norm) c FROM example WHERE lang=?",
                        (lg,)).fetchone()["c"]
        print(f"  {lg}: {w:,} headwords with examples")
    print(f"  {tot:,} example sentences total")
