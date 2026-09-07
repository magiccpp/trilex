"""Turn Wiktionary's translations into bilingual entries.

Folkets lexikon is a general dictionary of ~56k English headwords, so a
technical word - eigenvalue, capacitor, plurality - is simply absent and the
app could only say "not in the dictionary". English Wiktionary carries
Swedish and Mandarin translations for a large share of its million lemmas,
with sense text and topic labels (mathematics, engineering, ...). This step
streams the English dump once more (the etymology pass kept no translations),
stages what it finds, then fills the gaps:

  en entry  for every English lemma the core lacks that has a Swedish or
            Chinese translation (src 'wikt:en'); its senses are those
            translations, so they show as direct translations
  sv entry  for every Swedish translation the core lacks (src 'wikt:sv'),
            glossed with the English lemmas it translates and pivoted on
            them, so Swedish lookups and the zh<->sv pivot both work
  zh entry  likewise for Mandarin (src 'wikt:zh'), with pinyin and
            traditional forms so it stays typeable either way

Existing Folkets / CC-CEDICT entries are never modified. Resumable like the
other streaming steps, via meta 'wikt:en:offset'.

    python -m trilex.build.wikt                 # stream, then apply
    python -m trilex.build.wikt --apply         # apply only (already staged)
    python -m trilex.build.wikt --file x.jsonl  # stage from a local dump
"""
import json, re, sys, time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from trilex import db
from trilex.build.core import Sink
from trilex.build.kaikki import SOURCES, _stream_lines
from trilex.norm import is_cjk, loose, norm

DATA = Path(__file__).resolve().parents[2] / "data"

STAGING = """
CREATE TABLE IF NOT EXISTS wtrans(
  en_word TEXT NOT NULL,
  en_norm TEXT NOT NULL,
  pos     TEXT,
  tlang   TEXT NOT NULL,        -- sv | zh
  tword   TEXT NOT NULL,        -- Swedish word, or simplified Chinese
  trad    TEXT,                 -- traditional Chinese, if it differs
  roman   TEXT,                 -- pinyin with tone marks
  sense   TEXT,                 -- the sense this translation belongs to
  ord     INTEGER NOT NULL,     -- sense order within the lemma
  topics  TEXT                  -- JSON list of Wiktionary topic labels
);
CREATE INDEX IF NOT EXISTS ix_wtrans_en ON wtrans(en_norm);
CREATE INDEX IF NOT EXISTS ix_wtrans_t  ON wtrans(tlang, tword);
"""

# Parts of speech worth a bilingual entry. Proper nouns, symbols, affixes
# and single characters are not: a learner never looks those up.
KEEP_POS = {"noun", "verb", "adj", "adv", "num", "prep", "conj", "pron",
            "intj", "det", "article", "particle", "phrase", "prep_phrase",
            "adv_phrase", "abbrev"}
_WORD = re.compile(r"^[A-Za-z][A-Za-z'\- ]*$")
MAX_WORDS = 4
MAX_SENSES = 8
ZH_CODES = {"cmn", "zh"}


def _clean_zh(word):
    """'電容器 /电容器' -> ('电容器', '電容器'); '数学' -> ('数学', None)."""
    parts = [p.strip() for p in word.split("/") if p.strip()]
    if not parts:
        return None, None
    simp, trad = parts[-1], parts[0]
    if not is_cjk(simp):
        return None, None
    return simp, (trad if trad != simp and is_cjk(trad) else None)


def _translations(rec):
    """Yield (code, word, roman, sense_text, sense_ord) for sv / Mandarin."""
    ords = {}
    for t in rec.get("translations") or ():
        s = (t.get("sense") or "").strip()
        ords.setdefault(s, len(ords))
        yield t, s, ords[s]
    for i, sense in enumerate(rec.get("senses") or ()):
        for t in sense.get("translations") or ():
            yield t, " ".join(sense.get("glosses") or ())[:120], i


def _is_form_stub(rec):
    senses = rec.get("senses") or ()
    return bool(senses) and all(s.get("form_of") or s.get("alt_of") for s in senses)


def stage(con, source=None, resume=True):
    """Stream the English dump and stage every sv / zh translation."""
    con.executescript(STAGING)
    key, done_key = "wikt:en:offset", "wikt:en:done"
    if resume and source is None and con.execute(
            "SELECT 1 FROM meta WHERE k=?", (done_key,)).fetchone():
        print("[wikt] staging already complete, skipping", flush=True)
        return
    start = 0
    if source is None and resume:
        row = con.execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
        start = int(row["v"]) if row else 0
        if start:
            print(f"[wikt] resuming at byte {start:,}", flush=True)
    if source is None:
        lines = _stream_lines(SOURCES["en"], start)
    else:
        con.execute("DELETE FROM wtrans")
        lines = ((ln, 0) for ln in open(source, "rb"))

    cur = con.cursor()
    pending, n, kept, t0 = [], 0, 0, time.time()
    for raw, off in lines:
        n += 1
        try:
            rec = json.loads(raw)
        except Exception:
            continue
        w = rec.get("word") or ""
        if (rec.get("lang_code") != "en" or rec.get("pos") not in KEEP_POS
                or not _WORD.match(w) or len(w.split()) > MAX_WORDS
                or _is_form_stub(rec)):
            continue
        topics = sorted({tp for s in rec.get("senses") or ()
                         for tp in (s.get("topics") or ())})[:8]
        tj = json.dumps(topics) if topics else None
        got = False
        for t, sense, ord_ in _translations(rec):
            code, tw = t.get("code"), (t.get("word") or "").strip()
            if not tw:
                continue
            if code == "sv":
                if len(tw) > 60 or is_cjk(tw):
                    continue
                pending.append((w, norm(w), rec.get("pos"), "sv", tw, None,
                                None, sense, ord_, tj))
            elif code in ZH_CODES:
                simp, trad = _clean_zh(tw)
                if not simp or len(simp) > 20:
                    continue
                pending.append((w, norm(w), rec.get("pos"), "zh", simp, trad,
                                (t.get("roman") or "").strip() or None,
                                sense, ord_, tj))
            else:
                continue
            got = True
        kept += got
        if len(pending) >= 5000:
            cur.executemany("INSERT INTO wtrans VALUES(?,?,?,?,?,?,?,?,?,?)", pending)
            pending.clear()
            if source is None:
                cur.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (key, str(off)))
            con.commit()
            mb = off / 1048576
            print(f"[wikt] {mb:8.0f} MB  {n:>9,} lines  {kept:>8,} lemmas  "
                  f"{mb/max(time.time()-t0,1):.2f} MB/s", flush=True)
    if pending:
        cur.executemany("INSERT INTO wtrans VALUES(?,?,?,?,?,?,?,?,?,?)", pending)
    if source is None:
        cur.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (done_key, "1"))
    con.commit()
    print(f"[wikt] staged {n:,} lines -> {kept:,} lemmas with translations "
          f"in {(time.time()-t0)/60:.1f} min", flush=True)


def _ipa(con, en_norm, word):
    r = con.execute(
        "SELECT ipa FROM wik WHERE lang='en' AND norm=? AND ipa IS NOT NULL "
        "ORDER BY (word=?) DESC, id LIMIT 1", (en_norm, word)).fetchone()
    return r["ipa"] if r else None


def apply(con):
    """Create the entries the core lacks from the staged translations."""
    have = {lg: {r["norm"] for r in con.execute(
                 "SELECT norm FROM entry WHERE lang=?", (lg,))}
            for lg in ("en", "sv", "zh")}
    first_id = con.execute("SELECT coalesce(max(id),0)+1 i FROM entry").fetchone()["i"]
    sink = Sink(con)
    t0 = time.time()

    # Group the staging rows by English lemma, keeping sense order.
    rev = {"sv": defaultdict(list), "zh": defaultdict(list)}   # tword -> [(en, ord)]
    zh_meta = {}
    made = {"en": 0, "sv": 0, "zh": 0}
    topic_hits = defaultdict(int)
    cur_norm, group = None, []

    def flush(group):
        if not group:
            return
        en_norm = group[0]["en_norm"]
        # Most common casing wins ('Matrix' the film loses to 'matrix').
        casings = defaultdict(int)
        for r in group:
            casings[r["en_word"]] += 1
        word = max(casings, key=lambda w: (casings[w], w.islower()))
        pos = group[0]["pos"]
        topics = set()
        for r in group:
            if r["topics"]:
                topics.update(json.loads(r["topics"]))
        senses = {"sv": [], "zh": []}
        for r in sorted(group, key=lambda r: (r["ord"], r["rowid"])):
            lg, tw = r["tlang"], r["tword"]
            if tw not in senses[lg]:
                senses[lg].append(tw)
            rev[lg][tw].append((word, r["ord"]))
            if lg == "zh" and tw not in zh_meta:
                zh_meta[tw] = (r["trad"], r["roman"])
        if en_norm in have["en"]:
            return
        extra = {}
        if topics:
            extra["topics"] = sorted(topics)[:6]
            for tp in topics:
                topic_hits[tp] += 1
        ipa = _ipa(con, en_norm, word)
        if ipa:
            extra["phonetic"] = ipa
        sink.add(lang="en", word=word, pos=pos, extra=extra, src="wikt:en",
                 senses=[("sv", w) for w in senses["sv"][:MAX_SENSES]]
                        + [("zh", w) for w in senses["zh"][:MAX_SENSES]],
                 pivots={}, forms=set())
        have["en"].add(en_norm)
        made["en"] += 1

    for r in con.execute("SELECT rowid, * FROM wtrans ORDER BY en_norm, rowid"):
        if r["en_norm"] != cur_norm:
            flush(group)
            cur_norm, group = r["en_norm"], []
        group.append(r)
    flush(group)
    print(f"  en entries: {made['en']:,}", flush=True)

    # Reverse direction: the translations themselves become headwords.
    for lg in ("sv", "zh"):
        for tw, pairs in rev[lg].items():
            n = norm(tw)
            if not n or n in have[lg]:
                continue
            seen, glosses, pivots = set(), [], {}
            for en, ord_ in sorted(pairs, key=lambda p: p[1]):
                if en in seen:
                    continue
                seen.add(en)
                glosses.append(("en", en))
                pivots[norm(en)] = max(pivots.get(norm(en), 0), 1.0 / (1 + ord_))
            extra, forms = {}, set()
            if lg == "zh":
                trad, roman = zh_meta.get(tw, (None, None))
                if trad:
                    extra["trad"] = trad
                    forms.add((norm(trad), "trad", trad))
                if roman:
                    extra["pinyin"] = roman
                    forms.add((loose(roman), "pinyin", tw))
                    forms.add((norm(roman), "pinyin", tw))
            sink.add(lang=lg, word=tw, pos=None, extra=extra, src=f"wikt:{lg}",
                     senses=glosses[:MAX_SENSES], pivots=pivots, forms=forms)
            have[lg].add(n)
            made[lg] += 1
        print(f"  {lg} entries: {made[lg]:,}", flush=True)
    con.commit()

    # Autocomplete and pivot damping must know about the newcomers. Head forms
    # replace any Wiktionary-only vocab row so they rank as real headwords.
    con.execute("""
      INSERT OR REPLACE INTO vocab(norm,lang,disp,score,core)
      SELECT f.norm, f.lang, f.disp, 1801.0, 1
      FROM form f WHERE f.entry_id >= ? AND f.kind = 'head' AND f.norm <> ''""",
                (first_id,))
    con.execute("""
      INSERT OR IGNORE INTO vocab(norm,lang,disp,score,core)
      SELECT f.norm, f.lang, f.disp, 1001.0, 1
      FROM form f WHERE f.entry_id >= ? AND f.kind <> 'head' AND f.norm <> ''""",
                (first_id,))
    con.execute("DELETE FROM pivot_df")
    con.execute("INSERT INTO pivot_df SELECT en_norm, count(*) FROM pivot GROUP BY en_norm")
    con.execute("INSERT OR REPLACE INTO meta VALUES('wikt:applied', ?)",
                (json.dumps(made),))
    con.commit()
    con.execute("ANALYZE")
    con.commit()
    top = sorted(topic_hits.items(), key=lambda kv: -kv[1])[:12]
    print("  topics: " + ", ".join(f"{k} {v:,}" for k, v in top), flush=True)
    print(f"  applied in {time.time()-t0:.0f}s", flush=True)
    return made


def main(argv):
    path = DATA / "dict.db"
    if "--db" in argv:
        path = Path(argv[argv.index("--db") + 1])
    con = db.open_dict(path, create=False)
    con.execute("PRAGMA synchronous=OFF")
    if "--apply" not in argv:
        src = argv[argv.index("--file") + 1] if "--file" in argv else None
        stage(con, src)
    made = apply(con)
    print(f"done: {made}")


if __name__ == "__main__":
    main(sys.argv[1:])
