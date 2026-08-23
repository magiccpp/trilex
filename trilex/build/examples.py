"""Example sentences for English and Swedish.

Two sources, merged into one table:

  folkets  - 24k hand-written dictionary examples, each already paired with a
             translation. Highest quality; these were captured during the core
             build and only needed extracting.
  tatoeba  - a CC-BY corpus of sentences with human translations, aligned
             en<->sv, indexed onto dictionary headwords.

Sentences are attached to a *lemma*, using the inflection table, so a sentence
containing "vattnet" is filed under "vatten" and one containing "ran" under
"run".
"""
import bz2, html, io, json, re, sys, tarfile, time, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from trilex import db
from trilex.norm import norm

DATA = Path(__file__).resolve().parents[2] / "data"
UA = {"User-Agent": "trilex-dictionary-builder/1.0"}
BASE = "https://downloads.tatoeba.org/exports"
PER_WORD = 10
MAX_WORDS_IN_SENTENCE = 18      # long sentences make poor illustrations
# Tatoeba language code -> ours. English is the hub: it pairs with both.
TATOEBA_LANGS = {"eng": "en", "swe": "sv", "cmn": "zh"}
# Chinese is unsegmented, so headwords are found by scanning n-grams.
ZH_MAX_NGRAM = 4

SCHEMA = """
CREATE TABLE IF NOT EXISTS example(
  lang       TEXT NOT NULL,     -- language the sentence is written in
  norm       TEXT NOT NULL,     -- lemma it illustrates
  text       TEXT NOT NULL,
  trans      TEXT,              -- translation, if the source had one
  trans_lang TEXT,
  src        TEXT NOT NULL,
  score      REAL NOT NULL DEFAULT 0   -- lower sorts first
);
"""
INDEXES = """
CREATE INDEX IF NOT EXISTS ix_example_l ON example(lang, norm, score);
"""

_TOKEN = re.compile(r"[a-zA-ZåäöÅÄÖüïéèáàæøÆØ']+")


def _fetch(name, dest):
    if dest.exists() and dest.stat().st_size > 1000:
        print(f"  have {dest.name}", flush=True)
        return
    url = f"{BASE}/{name}"
    print(f"  downloading {dest.name} ...", flush=True)
    req = urllib.request.Request(url, headers=dict(UA))
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(req, timeout=180) as r, open(tmp, "wb") as fh:
        while chunk := r.read(1 << 20):
            fh.write(chunk)
    tmp.replace(dest)
    print(f"    {dest.stat().st_size/1048576:.1f} MB", flush=True)


# ---------------------------------------------------------------- folkets
def import_folkets(con):
    """Lift examples and idioms out of the entry JSON into the table."""
    con.execute("DELETE FROM example WHERE src LIKE 'folkets%'")
    rows = []
    for r in con.execute("SELECT lang, word, extra FROM entry "
                         "WHERE extra IS NOT NULL AND lang IN ('en','sv')"):
        try:
            extra = json.loads(r["extra"])
        except Exception:
            continue
        other = "sv" if r["lang"] == "en" else "en"
        n = norm(r["word"])
        for kind, weight in (("examples", 0.0), ("idioms", 5.0)):
            for item in extra.get(kind) or []:
                if not isinstance(item, (list, tuple)) or not item:
                    continue
                # Folkets XML double-escapes: the parser yields literal
                # "&quot;" / "&#39;" rather than the characters themselves.
                text = html.unescape(item[0] or "")
                trans = (item[1] or [None])[0] if len(item) > 1 else None
                trans = html.unescape(trans) if trans else None
                if not text:
                    continue
                rows.append((r["lang"], n, text, trans, other if trans else None,
                             f"folkets:{kind[:-1]}", weight + len(text) / 200.0))
    con.executemany("INSERT INTO example(lang,norm,text,trans,trans_lang,src,score)"
                    " VALUES(?,?,?,?,?,?,?)", rows)
    con.commit()
    print(f"  folkets: {len(rows):,} examples", flush=True)


# ---------------------------------------------------------------- tatoeba
def _read_sentences(path, want_lang):
    out = {}
    with bz2.open(path, "rt", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 3 or parts[1] != want_lang:
                continue
            out[int(parts[0])] = parts[2]
    return out


def _lemma_index(con, lang):
    """norm(surface) -> norm(lemma), for every inflected form of the language."""
    idx = {}
    try:
        for r in con.execute(
                "SELECT norm, lemma_norm FROM wform WHERE lang=?", (lang,)):
            idx.setdefault(r["norm"], r["lemma_norm"])
    except Exception:
        pass
    return idx


def _headwords(con, lang):
    return {r["norm"] for r in con.execute(
        "SELECT norm FROM vocab WHERE lang=? AND core=1", (lang,))}


def _zh_terms(text, heads):
    """Headwords occurring in an unsegmented Chinese sentence.

    Chinese has no spaces, so tokenising is not an option; scanning every
    n-gram up to four characters against the headword set is simple, exact for
    our purposes, and fast enough at this corpus size.
    """
    found = set()
    n = len(text)
    for i in range(n):
        for k in range(ZH_MAX_NGRAM, 0, -1):
            if i + k > n:
                continue
            g = text[i:i + k]
            if g in heads:
                found.add(g)
                break
    return found


def import_tatoeba(con):
    files = {code: DATA / f"tatoeba_{code}.tsv.bz2" for code in TATOEBA_LANGS}
    files["links"] = DATA / "tatoeba_links.tar.bz2"
    for code in TATOEBA_LANGS:
        _fetch(f"per_language/{code}/{code}_sentences.tsv.bz2", files[code])
    # Tatoeba no longer publishes per-pair exports or a bare links.csv.bz2;
    # only the global tar is available.
    _fetch("links.tar.bz2", files["links"])

    print("  reading sentences ...", flush=True)
    sent = {}                      # id -> (our lang code, text)
    for code, ours in TATOEBA_LANGS.items():
        got = _read_sentences(files[code], code)
        for sid, text in got.items():
            sent[sid] = (ours, text)
        print(f"    {len(got):,} {ours}", flush=True)

    print("  aligning (streaming ~25M links) ...", flush=True)
    pairs = set()
    with tarfile.open(files["links"], "r:bz2") as tar:
        member = next(m for m in tar if m.name.endswith(".csv"))
        fh = io.TextIOWrapper(tar.extractfile(member), encoding="utf-8",
                              errors="replace")
        for line in fh:
            a, _, b = line.rstrip("\n").partition("\t")
            b = b.split("\t")[0]
            if not (a.isdigit() and b.isdigit()):
                continue
            ia, ib = int(a), int(b)
            sa, sb = sent.get(ia), sent.get(ib)
            if not sa or not sb or sa[0] == sb[0]:
                continue
            # English is the hub; skip sv<->zh, which has no direct source.
            if "en" not in (sa[0], sb[0]):
                continue
            pairs.add((sa, sb) if sa[0] == "en" else (sb, sa))
    print(f"    {len(pairs):,} aligned pairs involving English", flush=True)

    heads = {lg: _headwords(con, lg) for lg in ("en", "sv", "zh")}
    lemmas = {lg: _lemma_index(con, lg) for lg in ("en", "sv")}

    con.execute("DELETE FROM example WHERE src='tatoeba'")
    counts = {"en": {}, "sv": {}, "zh": {}}
    rows = []
    for (la, ta), (lb, tb) in pairs:
        for lang, text, trans, tlang in ((la, ta, tb, lb), (lb, tb, ta, la)):
            if lang == "zh":
                terms = _zh_terms(text, heads["zh"])
                length = len(text)
                if length > 30:
                    continue
                weight = 10.0 + length / 20.0
            else:
                toks = _TOKEN.findall(text)
                if not toks or len(toks) > MAX_WORDS_IN_SENTENCE:
                    continue
                terms = set()
                for tok in toks:
                    n = norm(tok)
                    if n not in heads[lang]:
                        n = lemmas[lang].get(n)
                        if not n or n not in heads[lang]:
                            continue
                    terms.add(n)
                weight = 10.0 + len(toks) / 10.0
            for term in terms:
                c = counts[lang]
                if c.get(term, 0) >= PER_WORD:
                    continue
                c[term] = c.get(term, 0) + 1
                rows.append((lang, norm(term), text, trans, tlang, "tatoeba",
                             weight))
    con.executemany("INSERT INTO example(lang,norm,text,trans,trans_lang,src,score)"
                    " VALUES(?,?,?,?,?,?,?)", rows)
    con.commit()
    print(f"  tatoeba: {len(rows):,} indexed examples "
          + ", ".join(f"{len(counts[l]):,} {l} words" for l in ("en", "sv", "zh")),
          flush=True)


def main():
    con = db.open_dict(DATA / "dict.db")
    con.executescript(SCHEMA)
    t0 = time.time()
    print("[1/2] Folkets dictionary examples")
    import_folkets(con)
    print("[2/2] Tatoeba corpus")
    import_tatoeba(con)
    con.executescript(INDEXES)
    con.commit()
    total = con.execute("SELECT count(*) c FROM example").fetchone()["c"]
    words = con.execute("SELECT count(DISTINCT lang||norm) c FROM example").fetchone()["c"]
    print(f"\n{total:,} example sentences covering {words:,} headwords "
          f"in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
