"""Query engine over dict.db. Pure SQLite - no network, no services."""
from __future__ import annotations

import json, math, unicodedata
from dataclasses import dataclass, field
from functools import lru_cache

from .norm import norm, loose, is_cjk

LANGS = ("en", "sv", "zh")
LANG_NAME = {"en": "English", "sv": "Svenska", "zh": "中文"}
LANG_FLAG = {"en": "EN", "sv": "SV", "zh": "ZH"}

try:
    from rapidfuzz import process as _rf_process, fuzz as _rf_fuzz
except ImportError:                                   # pragma: no cover
    _rf_process = None


@dataclass
class Sense:
    lang: str
    gloss: str


@dataclass
class Entry:
    id: int
    lang: str
    word: str
    pos: str | None
    extra: dict
    senses: list[Sense] = field(default_factory=list)

    @property
    def pinyin(self):
        return self.extra.get("pinyin")

    @property
    def traditional(self):
        t = self.extra.get("trad")
        return t if t and t != self.word else None

    @property
    def reading(self):
        """The pronunciation line: pinyin for Chinese, Folkets phonetics else."""
        return self.pinyin or self.extra.get("phonetic")

    def glosses(self, lang=None):
        return [s.gloss for s in self.senses if lang is None or s.lang == lang]


@dataclass
class Related:
    lang: str
    word: str
    entry_id: int
    score: float
    via: list[str]
    reading: str | None = None
    glosses: list[str] = field(default_factory=list)


@dataclass
class Example:
    text: str
    trans: str | None
    trans_lang: str | None
    src: str

    @property
    def hand_written(self) -> bool:
        return self.src.startswith("folkets")


@dataclass
class Inflection:
    form: str
    tags: str
    label: str


@dataclass
class Etymology:
    lang: str
    word: str
    pos: str | None
    ipa: str | None
    text: str | None
    gloss: str | None


@dataclass
class Result:
    query: str
    entries: list[Entry] = field(default_factory=list)
    related: dict[str, list[Related]] = field(default_factory=dict)
    etymologies: list[Etymology] = field(default_factory=list)
    did_you_mean: list[tuple[str, str]] = field(default_factory=list)
    definitions: dict[str, list[str]] = field(default_factory=dict)
    direct: dict[str, list[str]] = field(default_factory=dict)
    inflections: dict[str, list[Inflection]] = field(default_factory=dict)
    # Set when the query was an inflected form and we resolved to its lemma,
    # e.g. searching "ran" lands on "run".
    resolved_from: str | None = None
    # Set when the query is itself a headword AND an inflected form of a
    # different lemma: "ran" is a Swedish noun, but also the past of "run".
    also_form_of: tuple[str, str, str] | None = None
    image: dict | None = None
    audio: dict | None = None
    examples: dict[str, list[Example]] = field(default_factory=list and dict)

    @property
    def found(self):
        return bool(self.entries)


# Ordered so a conjugation reads the way a textbook prints it.
_TAG_ORDER = [
    ("infinitive",),
    ("present",), ("present", "singular", "third-person"),
    ("past",), ("past", "participle"), ("present", "participle"),
    ("supine",), ("imperative",), ("passive",), ("subjunctive",),
    ("plural",),
    ("indefinite", "singular"), ("definite", "singular"),
    ("indefinite", "plural"), ("definite", "plural"),
    ("genitive", "indefinite", "singular"), ("genitive", "definite", "singular"),
    ("genitive", "indefinite", "plural"), ("genitive", "definite", "plural"),
    ("positive",), ("comparative",), ("superlative",),
    ("neuter", "singular"), ("common-gender", "singular"),
]
_TAG_LABEL = {
    "present singular third-person": "he/she/it",
    "past participle": "past participle",
    "present participle": "-ing form",
    "indefinite singular": "singular",
    "definite singular": "the …",
    "indefinite plural": "plural",
    "definite plural": "the … (pl)",
    "genitive indefinite singular": "possessive",
    "supine": "supine",
    "imperative": "imperative",
}


def _label(tags: str) -> str:
    t = " ".join(sorted(tags.split()))
    for k, v in _TAG_LABEL.items():
        if " ".join(sorted(k.split())) == t:
            return v
    return tags.replace("third-person", "3rd").replace("common-gender", "common")


# Folkets uses Swedish grammar abbreviations; Wiktionary uses English names.
# Without this bridge the part-of-speech filter never matched and an adjective
# was shown conjugated like a verb.
_POS_MAP = {"nn": "noun", "vb": "verb", "jj": "adj", "ab": "adv", "pp": "prep",
            "pn": "pron", "nl": "num", "in": "intj", "kn": "conj",
            "article": "article", "rg": "num", "hp": "pron"}

# Archaic orthography (long s) and similar clutter a learner's table.
_ARCHAIC = ("\u017f",)


def _rank(tags: str) -> int:
    ts = frozenset(tags.split())
    for i, combo in enumerate(_TAG_ORDER):
        if frozenset(combo) == ts:
            return i
    return 100 + len(ts)


class Dictionary:
    def __init__(self, con):
        self.con = con
        self._fuzz_cache = None

    # ---------------------------------------------------------------- helpers
    def _load_entries(self, ids, query_norm=None, cjk_query=False):
        if not ids:
            return []
        qm = ",".join("?" * len(ids))
        rows = self.con.execute(
            f"SELECT id,lang,word,pos,extra FROM entry WHERE id IN ({qm})", ids).fetchall()
        by_id = {
            r["id"]: Entry(r["id"], r["lang"], r["word"], r["pos"],
                           json.loads(r["extra"]) if r["extra"] else {})
            for r in rows}
        for s in self.con.execute(
                f"SELECT entry_id,lang,gloss FROM sense WHERE entry_id IN ({qm}) "
                "ORDER BY entry_id, ord", ids):
            e = by_id.get(s["entry_id"])
            if e:
                e.senses.append(Sense(s["lang"], s["gloss"]))
        def rank(e):
            # 1. A real headword match beats a synonym match. Without this,
            #    searching "hus" showed the entry for "byggnad", which merely
            #    lists hus as a synonym -- and then its declension table too.
            is_head = 0 if (query_norm and norm(e.word) == query_norm) else 1
            # 2. Stay in the query's script. "ran" is pinyin for a rare hanzi,
            #    which otherwise outranked the English verb.
            script_ok = 0 if (cjk_query == (e.lang == "zh")) else 1
            # 3. Both sources list the most common sense first, so source
            #    order beats sense count for picking the primary entry.
            return (is_head, script_ok, e.id, e.lang)
        return sorted(by_id.values(), key=rank)

    def _table_exists(self, name) -> bool:
        """Probe rather than read sqlite_master: the table may live in an
        attached media pack, which sqlite_master for `main` does not list."""
        try:
            self.con.execute(f"SELECT 1 FROM {name} LIMIT 1").fetchone()
            return True
        except Exception:
            return False

    def refresh_media(self):
        """Re-probe after a pack is installed at runtime."""
        for attr in ("_has_audio", "_has_images", "_has_ex", "_has_forms"):
            if hasattr(self, attr):
                delattr(self, attr)

    @lru_cache(maxsize=4096)
    def _idf(self, en_norm):
        """Damp pivot terms that reach hundreds of entries. 'water' is a
        precise hinge; 'go' or 'thing' is not."""
        r = self.con.execute("SELECT df FROM pivot_df WHERE en_norm=?", (en_norm,)).fetchone()
        df = r["df"] if r else 1
        return 1.0 / (1.0 + math.log(max(df, 1)))

    # ------------------------------------------------------------- autocomplete
    def suggest(self, prefix, limit=12):
        """Prefix completions across all three languages, best-known first."""
        p = norm(prefix)
        if not p:
            return []
        rows = self.con.execute(
            "SELECT norm,lang,disp,score,core FROM vocab "
            "WHERE norm >= ? AND norm < ? "
            "ORDER BY core DESC, (norm = ?) DESC, score DESC, length(norm) LIMIT ?",
            (p, p + "￿", p, limit * 6)).fetchall()
        out, seen = [], set()
        for r in rows:
            key = (r["disp"], r["lang"])
            if key in seen:
                continue
            seen.add(key)
            out.append((r["disp"], r["lang"]))
            if len(out) >= limit:
                break
        return out

    # -------------------------------------------------------------- fuzzy match
    def _fuzz_pool(self):
        """Candidate headwords bucketed by (script, length).

        The length window does the heavy lifting: it keeps every rapidfuzz pass
        small enough to feel instant, and it stops a six-letter typo from ever
        being "corrected" to the headword 'A'. A similarity cutoff alone does
        not achieve that - short strings score deceptively well.
        """
        if self._fuzz_cache is None:
            pool: dict[tuple[str, int], tuple[list, list]] = {}
            for r in self.con.execute(
                    "SELECT norm, lang, disp FROM vocab WHERE core=1"):
                k = loose(r["norm"])
                if not k:
                    continue
                b = pool.setdefault(
                    ("zh" if is_cjk(r["norm"]) else "la", len(k)), ([], []))
                b[0].append(k)
                b[1].append((r["disp"], r["lang"]))
            self._fuzz_cache = pool
        return self._fuzz_cache

    def did_you_mean(self, q, limit=8, window=2):
        """Nearest real headwords for a query that matched nothing.

        Stays inside the query's own script: a Latin typo should never be
        answered with Chinese characters.
        """
        pool = self._fuzz_pool()
        lq = loose(q)
        if not lq:
            return []
        script = "zh" if is_cjk(q) else "la"
        hits = []
        for L in range(max(1, len(lq) - window), len(lq) + window + 1):
            bucket = pool.get((script, L))
            if not bucket:
                continue
            keys, meta = bucket
            if _rf_process is not None:
                for _, sc, i in _rf_process.extract(
                        lq, keys, scorer=_rf_fuzz.ratio,
                        limit=limit * 2, score_cutoff=60):
                    hits.append((sc, meta[i]))
            else:                                      # stdlib fallback
                import difflib
                for i, k in enumerate(keys):
                    sc = difflib.SequenceMatcher(None, lq, k).ratio() * 100
                    if sc >= 60:
                        hits.append((sc, meta[i]))
        hits.sort(key=lambda h: -h[0])
        out, seen = [], set()
        for _, m in hits:
            if m in seen:
                continue
            seen.add(m)
            out.append(m)
            if len(out) >= limit:
                break
        return out

    # ------------------------------------------------------------- definitions
    def definitions_for(self, lang, word, limit=6):
        """Monolingual definitions from Wiktionary, for the Definition block.

        Folkets and CC-CEDICT give *translations*; this gives what the word
        actually means in its own language.
        """
        out = []
        for r in self.con.execute(
                "SELECT gloss FROM wik WHERE lang=? AND norm=? AND gloss IS NOT NULL "
                "ORDER BY id LIMIT 8", (lang, norm(word))):
            for g in r["gloss"].split(" | "):
                g = g.strip()
                if g and g not in out:
                    out.append(g)
        return out[:limit]

    # --------------------------------------------------------------- examples
    def has_examples(self) -> bool:
        if not hasattr(self, "_has_ex"):
            self._has_ex = bool(self.con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='example'"
            ).fetchone())
        return self._has_ex

    def examples_for(self, lang, word, limit=6):
        """Sentences illustrating a headword, best first.

        `score` puts hand-written dictionary examples ahead of corpus ones, and
        shorter sentences ahead of longer ones.
        """
        if not self.has_examples() or lang not in ("en", "sv", "zh"):
            return []
        # English now has both Swedish- and Chinese-translated examples. Lead
        # with Swedish for a Latin-script headword and Chinese for a CJK one,
        # so the pairing matches what the reader is most likely comparing.
        prefer = "zh" if lang == "zh" else "sv"
        rows = self.con.execute(
            "SELECT text, trans, trans_lang, src FROM example "
            "WHERE lang=? AND norm=? "
            "ORDER BY score + CASE WHEN trans_lang IS NULL THEN 1.0 "
            "                      WHEN trans_lang = ? THEN 0.0 "
            "                      ELSE 1.5 END, length(text) LIMIT ?",
            (lang, norm(word), prefer, limit * 3)).fetchall()
        out, seen = [], set()
        for r in rows:
            if r["text"] in seen:
                continue
            seen.add(r["text"])
            out.append(Example(r["text"], r["trans"], r["trans_lang"], r["src"]))
            if len(out) >= limit:
                break
        return out

    # ------------------------------------------------------------------ audio
    def has_audio(self) -> bool:
        if not hasattr(self, "_has_audio"):
            self._has_audio = self._table_exists("audio")
        return self._has_audio

    def audio_for(self, lang, word):
        """Metadata only; the clip itself is fetched when actually played."""
        if not self.has_audio():
            return None
        r = self.con.execute(
            "SELECT word, accent, fname, bytes FROM audio WHERE lang=? AND norm=?",
            (lang, norm(word))).fetchone()
        return dict(r) if r else None

    def audio_blob(self, lang, word):
        if not self.has_audio():
            return None
        r = self.con.execute("SELECT data FROM audio WHERE lang=? AND norm=?",
                             (lang, norm(word))).fetchone()
        return r["data"] if r else None

    # ----------------------------------------------------------------- images
    def has_images(self) -> bool:
        if not hasattr(self, "_has_images"):
            self._has_images = self._table_exists("image")
        return self._has_images

    def image_for(self, lang, word):
        """Metadata only; the blob is fetched lazily when the view asks."""
        if not self.has_images():
            return None
        r = self.con.execute(
            "SELECT word,w,h,license,artist,page FROM image WHERE lang=? AND norm=?",
            (lang, norm(word))).fetchone()
        return dict(r) if r else None

    def image_blob(self, lang, word):
        r = self.con.execute("SELECT data FROM image WHERE lang=? AND norm=?",
                             (lang, norm(word))).fetchone()
        return r["data"] if r else None

    # ------------------------------------------------------------ inflections
    def has_forms(self) -> bool:
        if not hasattr(self, "_has_forms"):
            self._has_forms = bool(self.con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='wform'"
            ).fetchone())
        return self._has_forms

    def inflections_for(self, lang, word, pos=None, limit=30):
        """Conjugation / declension table for a headword."""
        if not self.has_forms() or lang == "zh":
            return []
        rows = self.con.execute(
            "SELECT form, tags, pos FROM wform WHERE lang=? AND lemma_norm=?",
            (lang, norm(word))).fetchall()
        want = _POS_MAP.get((pos or "").lower())
        if want:
            same = [r for r in rows if (r["pos"] or "").lower() == want]
            if same:
                rows = same
        seen, out = set(), []
        for r in rows:
            if any(ch in r["form"] for ch in _ARCHAIC):
                continue
            key = (r["form"], _label(r["tags"]))
            if key in seen:
                continue
            seen.add(key)
            out.append(Inflection(r["form"], r["tags"], _label(r["tags"])))
        out.sort(key=lambda i: (_rank(i.tags), i.form))
        return out[:limit]

    def lemma_of(self, surface, prefer_lang=None):
        """Map an inflected form back to its lemma: 'ran' -> ('en','run')."""
        if not self.has_forms():
            return None
        rows = self.con.execute(
            "SELECT lang, lemma, tags FROM wform WHERE norm=? LIMIT 30",
            (norm(surface),)).fetchall()
        rows = [r for r in rows
                if not r["lemma"].isupper()          # skip acronyms: HU, US
                and len(r["lemma"]) > 1
                and (prefer_lang is None or r["lang"] == prefer_lang)]
        if not rows:
            return None
        # Prefer a lemma that actually exists in the bilingual core, so we
        # resolve to something we can show translations for.
        for r in rows:
            if self.con.execute("SELECT 1 FROM form WHERE norm=? LIMIT 1",
                                (norm(r["lemma"]),)).fetchone():
                return r["lang"], r["lemma"], r["tags"]
        r = rows[0]
        return r["lang"], r["lemma"], r["tags"]

    # ------------------------------------------------------------------ lookup
    def lookup(self, q, max_related=18):
        res = Result(query=q)
        nq = norm(q)
        if not nq:
            return res
        ids = [r["entry_id"] for r in self.con.execute(
            "SELECT DISTINCT entry_id FROM form WHERE norm=?", (nq,))]
        only_pinyin = bool(ids) and not is_cjk(q) and all(
            r["lang"] == "zh" for r in self.con.execute(
                "SELECT DISTINCT e.lang FROM form f JOIN entry e ON e.id=f.entry_id "
                "WHERE f.norm=?", (nq,)))
        if not ids or only_pinyin:
            # Not a headword - maybe it is an inflected form ("ran", "husen").
            hit = self.lemma_of(q)
            if hit:
                _, lemma, _ = hit
                lemma_ids = [r["entry_id"] for r in self.con.execute(
                    "SELECT DISTINCT entry_id FROM form WHERE norm=?", (norm(lemma),))]
                if lemma_ids:
                    res.resolved_from = q
                    # Keep any pinyin hits too, but the lemma now leads.
                    nq, ids = norm(lemma), lemma_ids + [i for i in ids
                                                        if i not in lemma_ids]
        res.entries = self._load_entries(ids, nq, is_cjk(q))

        # Query-side pivot weights: which English terms does this word mean?
        qp: dict[str, float] = {}
        if ids:
            qm = ",".join("?" * len(ids))
            for r in self.con.execute(
                    f"SELECT en_norm, max(weight) w FROM pivot "
                    f"WHERE entry_id IN ({qm}) GROUP BY en_norm", ids):
                qp[r["en_norm"]] = r["w"]
        # A word that is itself an English pivot term counts as its own hinge.
        if not is_cjk(q) and self.con.execute(
                "SELECT 1 FROM pivot_df WHERE en_norm=?", (nq,)).fetchone():
            qp[nq] = max(qp.get(nq, 0.0), 1.0)

        if qp:
            res.related = self._related(qp, exclude=set(ids), cap=max_related)
        # Direct translations carried on the entry itself always beat
        # pivot-derived ones; collect them per target language.
        for e in res.entries[:4]:
            for sn in e.senses:
                if sn.lang != e.lang:
                    res.direct.setdefault(sn.lang, [])
                    if sn.gloss not in res.direct[sn.lang]:
                        res.direct[sn.lang].append(sn.gloss)
        for e in res.entries[:3]:
            if e.lang not in res.definitions:
                d = self.definitions_for(e.lang, e.word)
                if d:
                    res.definitions[e.lang] = d
        if res.entries and not res.resolved_from and not is_cjk(q):
            hit = self.lemma_of(q, prefer_lang=res.entries[0].lang)
            if hit and norm(hit[1]) != nq:
                res.also_form_of = hit
        if res.entries:
            e = res.entries[0]
            if e.lang != "zh":
                infl = self.inflections_for(e.lang, e.word, e.pos)
                if infl:
                    res.inflections[e.lang] = infl
        if res.entries:
            e0 = res.entries[0]
            res.audio = self.audio_for(e0.lang, e0.word)
            res.image = self.image_for(e0.lang, e0.word)
            if not res.image:
                # Illustrations are keyed on English headwords. For a Swedish
                # or Chinese query, try its direct English translations first
                # (authoritative) before pivot-derived ones.
                for cand in (res.direct.get("en") or [])[:3] + \
                            [r_.word for r_ in (res.related.get("en") or [])[:3]]:
                    res.image = self.image_for("en", cand)
                    if res.image:
                        break
        if res.entries:
            e0 = res.entries[0]
            ex = self.examples_for(e0.lang, e0.word)
            if ex:
                res.examples[e0.lang] = ex
            # Chinese now has its own corpus, but coverage is thinner than
            # English, so top up from the English equivalent when it is sparse.
            if len(ex) < 3:
                for cand in (res.direct.get("en") or [])[:2]:
                    ex_en = self.examples_for("en", cand)
                    if ex_en:
                        res.examples["en"] = ex_en
                        break
        if not res.entries:
            res.did_you_mean = self.did_you_mean(q)
        res.etymologies = self._etymologies(res)
        return res

    def _related(self, qp, exclude, cap):
        terms = sorted(qp, key=lambda t: -qp[t])[:60]
        qm = ",".join("?" * len(terms))
        rows = self.con.execute(
            f"""SELECT p.entry_id, p.en_norm, p.weight, e.lang, e.word, e.extra
                FROM pivot p JOIN entry e ON e.id = p.entry_id
                WHERE p.en_norm IN ({qm})""", terms).fetchall()
        agg: dict[int, Related] = {}
        for r in rows:
            if r["entry_id"] in exclude:
                continue
            s = qp[r["en_norm"]] * r["weight"] * self._idf(r["en_norm"])
            cur = agg.get(r["entry_id"])
            if cur is None:
                extra = json.loads(r["extra"]) if r["extra"] else {}
                cur = agg[r["entry_id"]] = Related(
                    r["lang"], r["word"], r["entry_id"], 0.0, [],
                    reading=extra.get("pinyin") or extra.get("phonetic"))
            if s > cur.score:
                cur.score = s
            if r["en_norm"] not in cur.via:
                cur.via.append(r["en_norm"])
        out = {}
        for lg in LANGS:
            items = sorted((a for a in agg.values() if a.lang == lg),
                           key=lambda a: (-a.score, len(a.word)))
            # Collapse duplicate headwords (Folkets lists homographs separately).
            seen, keep = set(), []
            for it in items:
                if it.word in seen:
                    continue
                seen.add(it.word)
                it.via = sorted(it.via, key=lambda t: -qp[t])[:3]
                keep.append(it)
                if len(keep) >= cap:
                    break
            if keep:
                self._attach_glosses(keep)
                out[lg] = keep
        return out

    def _attach_glosses(self, rels):
        ids = [r.entry_id for r in rels]
        qm = ",".join("?" * len(ids))
        by = {}
        for s in self.con.execute(
                f"SELECT entry_id, gloss FROM sense WHERE entry_id IN ({qm}) "
                "ORDER BY entry_id, ord", ids):
            by.setdefault(s["entry_id"], []).append(s["gloss"])
        for r in rels:
            r.glosses = by.get(r.entry_id, [])[:4]

    # -------------------------------------------------------------- etymology
    def etymology_for(self, lang, word):
        rows = self.con.execute(
            "SELECT w.lang,w.word,w.pos,w.ipa,w.gloss,e.txt "
            "FROM wik w LEFT JOIN etym e ON e.id=w.etym_id "
            "WHERE w.lang=? AND w.norm=? "
            # Prefer the ordinary word: exact spelling first, then common nouns
            # over proper nouns -- otherwise 'water' resolves to the surname.
            "ORDER BY (w.word = ?) DESC, "
            "         (lower(coalesce(w.pos,'')) IN ('name','proper noun')) ASC, "
            "         (e.txt IS NULL) ASC, w.id LIMIT 6",
            (lang, norm(word), word)).fetchall()
        return [Etymology(r["lang"], r["word"], r["pos"], r["ipa"], r["txt"], r["gloss"])
                for r in rows]

    def _etymologies(self, res: Result):
        """Etymology for the searched word plus the best equivalent in each
        of the other two languages - so one search explains all three."""
        want, seen = [], set()
        for e in res.entries:
            if (e.lang, norm(e.word)) not in seen:
                seen.add((e.lang, norm(e.word)))
                want.append((e.lang, e.word))
        for lg in LANGS:
            for r in res.related.get(lg, [])[:1]:
                if (lg, norm(r.word)) not in seen:
                    seen.add((lg, norm(r.word)))
                    want.append((lg, r.word))
        out = []
        for lg, w in want:
            for et in self.etymology_for(lg, w):
                if et.text:
                    out.append(et)
                    break
            else:
                hits = self.etymology_for(lg, w)
                if hits:
                    out.append(hits[0])
        return out
