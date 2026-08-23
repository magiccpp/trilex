"""Build the bilingual core from CC-CEDICT (zh<->en) and Folkets (sv<->en).

English is the pivot language. There is no free Chinese-Swedish lexicon, so
zh<->sv is always reached by matching English terms on both sides. That is a
real limitation and the UI labels such results as indirect.

Writes to core.db so it never contends with the long-running kaikki job for
the dict.db write lock; merge.py folds the two together afterwards.
"""
import gzip, json, re, sys, xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from trilex import db
from trilex.norm import norm, pinyin_plain, pinyin_pretty

DATA = Path(__file__).resolve().parents[2] / "data"

# CC-CEDICT glosses that are cross-references or metadata, not translations.
_SKIP = re.compile(
    r"^(CL:|see |see also |variant of |old variant of |archaic variant of "
    r"|abbr\. for |used in |erhua variant of |also written )", re.I)
_PAREN = re.compile(r"\([^)]*\)|\[[^\]]*\]|\{[^}]*\}")
_ARTICLE = re.compile(r"^(a|an|the) ", re.I)


def pivot_terms(gloss: str):
    """Reduce a gloss to the short English terms worth pivoting on.

    'to irrigate; to water' -> {'to irrigate','irrigate','to water','water'}
    Long descriptive glosses stay as senses but make poor pivots, so they are
    dropped here - otherwise 'the emergency number for law enforcement...'
    would link Chinese to Swedish through the word 'the'.
    """
    if not gloss or _SKIP.match(gloss.strip()):
        return set()
    g = _PAREN.sub(" ", gloss)
    out = set()
    for part in re.split(r"[;/]", g):
        p = re.sub(r"\s+", " ", part).strip(" .,!?")
        if not p or len(p.split()) > 4 or len(p) < 2:
            continue
        out.add(norm(p))
        if p.lower().startswith("to "):
            out.add(norm(p[3:]))
        stripped = _ARTICLE.sub("", p)
        if stripped != p:
            out.add(norm(stripped))
    return {t for t in out if t and len(t) > 1}


class Sink:
    """Batched inserts into entry/sense/pivot/form."""

    def __init__(self, con):
        self.con, self.cur = con, con.cursor()
        self.n = 0

    def add(self, *, lang, word, pos, extra, src, senses, pivots, forms):
        self.cur.execute(
            "INSERT INTO entry(lang,word,norm,pos,extra,src) VALUES(?,?,?,?,?,?)",
            (lang, word, norm(word), pos,
             json.dumps(extra, ensure_ascii=False) if extra else None, src))
        eid = self.cur.lastrowid
        if senses:
            self.cur.executemany(
                "INSERT INTO sense(entry_id,lang,gloss,ord) VALUES(?,?,?,?)",
                [(eid, sl, sg, i) for i, (sl, sg) in enumerate(senses)])
        if pivots:
            self.cur.executemany(
                "INSERT INTO pivot(entry_id,en_norm,weight) VALUES(?,?,?)",
                [(eid, t, w) for t, w in pivots.items()])
        allf = {(norm(word), "head", word)} | set(forms)
        self.cur.executemany(
            "INSERT INTO form(norm,lang,entry_id,kind,disp) VALUES(?,?,?,?,?)",
            [(f, lang, eid, k, d) for f, k, d in allf if f])
        self.n += 1
        if self.n % 20000 == 0:
            self.con.commit()
            print(f"  ...{self.n:,} entries", flush=True)
        return eid


CEDICT_LINE = re.compile(r"^(\S+)\s+(\S+)\s+\[([^\]]*)\]\s+/(.*)/\s*$")


def build_cedict(sink):
    path = DATA / "cedict.txt.gz"
    n = 0
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("#"):
                continue
            m = CEDICT_LINE.match(line.rstrip("\n"))
            if not m:
                continue
            trad, simp, py, body = m.groups()
            glosses = [g for g in body.split("/") if g.strip()]
            if not glosses:
                continue
            pivots = {}
            for i, g in enumerate(glosses):
                # Earlier glosses are the primary sense; weight them higher.
                w = 1.0 / (1 + i)
                for t in pivot_terms(g):
                    pivots[t] = max(pivots.get(t, 0), w)
            forms = {(norm(trad), "trad", trad),
                     (pinyin_plain(py), "pinyin", simp),
                     (norm(pinyin_pretty(py)), "pinyin", simp)}
            sink.add(lang="zh", word=simp, pos=None,
                     extra={"trad": trad, "pinyin": pinyin_pretty(py),
                            "pinyin_num": py},
                     src="cedict",
                     senses=[("en", g) for g in glosses],
                     pivots=pivots, forms=forms)
            n += 1
    print(f"CC-CEDICT: {n:,} entries", flush=True)


def _texts(w, tag, attr="value"):
    return [e.get(attr) for e in w.findall(tag) if e.get(attr)]


def build_folkets(sink, fname, headlang, glosslang):
    path = DATA / fname
    n = 0
    for _, w in ET.iterparse(str(path), events=("end",)):
        if w.tag != "word":
            continue
        head = w.get("value")
        if not head:
            w.clear()
            continue
        trans = _texts(w, "translation")
        defs = [(d.get("value"), _texts(d, "translation"))
                for d in w.findall("definition") if d.get("value")]
        examples = [(e.get("value"), _texts(e, "translation"))
                    for e in w.findall("example") if e.get("value")]
        idioms = [(e.get("value"), _texts(e, "translation"))
                  for e in w.findall("idiom") if e.get("value")]
        syns = _texts(w, "synonym")
        infl = [i.get("value") for p in w.findall("paradigm")
                for i in p.findall("inflection") if i.get("value")]
        ph = w.find("phonetic")

        senses = [(glosslang, t) for t in trans]
        # Folkets definitions are written in the headword's own language.
        senses += [(headlang, d) for d, _ in defs]

        if headlang == "en":
            pivots = {norm(head): 1.0}
            pivots.update({norm(s): 0.5 for s in syns if norm(s)})
        else:
            pivots = {}
            for i, t in enumerate(trans):
                for term in pivot_terms(t) or {norm(t)}:
                    pivots[term] = max(pivots.get(term, 0), 1.0 / (1 + i))

        forms = {(norm(i), "infl", i) for i in infl}
        forms |= {(norm(s), "syn", s) for s in syns}
        extra = {"class": w.get("class"), "inflections": infl,
                 "synonyms": syns, "phonetic": ph.get("value") if ph is not None else None,
                 "examples": examples, "idioms": idioms,
                 "definitions": defs,
                 "comment": w.get("comment")}
        sink.add(lang=headlang, word=head, pos=w.get("class"),
                 extra={k: v for k, v in extra.items() if v},
                 src=f"folkets:{headlang}", senses=senses,
                 pivots=pivots, forms=forms)
        n += 1
        w.clear()
    print(f"Folkets {fname}: {n:,} entries", flush=True)


if __name__ == "__main__":
    p = DATA / "core.db"
    if p.exists():
        p.unlink()
    con = db.open_dict(p)
    sink = Sink(con)
    build_cedict(sink)
    build_folkets(sink, "folkets_en_sv.xml", "en", "sv")
    build_folkets(sink, "folkets_sv_en.xml", "sv", "en")
    con.commit()
    con.executescript(db.DICT_INDEXES)
    con.commit()
    for t in ("entry", "sense", "pivot", "form"):
        print(f"  {t:6s} {con.execute(f'SELECT count(*) c FROM {t}').fetchone()['c']:>9,}")
