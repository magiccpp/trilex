"""Build the parity fixture for the Swift tests.

Writes a tiny dictionary (every table the app reads, a few dozen rows) as SQL,
then runs the *desktop* search, normalisation and forgetting-curve code over
it and records what they return. The Swift tests load the same SQL and must
produce the same answers, so a change to the Python ranking that is not
carried over to the iPhone port fails a test instead of drifting silently.

    python ios/TrilexKit/Tests/make_fixture.py      (needs rapidfuzz)
"""
import json, sqlite3, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from trilex import db, forgetting as F                      # noqa: E402
from trilex.norm import norm, loose, is_cjk, pinyin_plain   # noqa: E402
from trilex.search import Dictionary                         # noqa: E402
from trilex.build.merge import EXTRA                         # noqa: E402
from trilex.build.forms import SCHEMA as FORMS, INDEXES as FORM_IX    # noqa: E402
from trilex.build.examples import SCHEMA as EXAMPLES          # noqa: E402
from trilex.build.images import SCHEMA as IMAGES              # noqa: E402
from trilex.build.audio import SCHEMA as AUDIO                # noqa: E402

OUT = Path(__file__).resolve().parent / "TrilexKitTests" / "Fixtures"

# id, lang, word, pos, extra, senses [(lang, gloss)]
ENTRIES = [
    (1, "sv", "vatten", "nn", {"phonetic": "vat:en"}, [("en", "water")]),
    (2, "en", "water", "nn", {}, [("sv", "vatten")]),
    (3, "en", "water", "vb", {}, [("sv", "vattna")]),
    (4, "zh", "水", None, {"pinyin": "shuǐ", "trad": "水"}, [("en", "water"), ("en", "river")]),
    (5, "zh", "水饺", None, {"pinyin": "shuǐ jiǎo", "trad": "水餃"}, [("en", "dumpling")]),
    (6, "sv", "hus", "nn", {}, [("en", "house")]),
    (7, "en", "house", "nn", {}, [("sv", "hus")]),
    (8, "sv", "byggnad", "nn", {}, [("en", "building"), ("en", "house")]),
    (9, "en", "run", "vb", {}, [("sv", "springa")]),
    (10, "sv", "springa", "vb", {}, [("en", "run"), ("sv", "röra sig snabbt")]),
    (11, "sv", "ran", "nn", {}, [("en", "robbery")]),
    (12, "zh", "然", None, {"pinyin": "rán"}, [("en", "correct"), ("en", "so")]),
    (13, "zh", "房子", None, {"pinyin": "fáng zi"}, [("en", "house"), ("en", "building")]),
    (14, "en", "beautiful", "jj", {}, [("sv", "vacker"), ("sv", "skön")]),
    (15, "sv", "vacker", "jj", {}, [("en", "beautiful"), ("en", "pretty")]),
    (16, "zh", "美丽", None, {"pinyin": "měi lì", "trad": "美麗"}, [("en", "beautiful")]),
    (17, "sv", "skön", "jj", {}, [("en", "beautiful"), ("en", "comfortable")]),
    (18, "en", "Water", "pm", {}, [("sv", "Water (efternamn)")]),
]
PINYIN = {4: "shui3", 5: "shui3 jiao3", 12: "ran2", 13: "fang2 zi5", 16: "mei3 li4"}
SYNONYMS = [("hus", "sv", 8)]
PIVOT = [
    (1, "water", 1.0), (2, "water", 1.0), (3, "water", 0.7), (4, "water", 1.0), (4, "river", 0.5),
    (5, "dumpling", 1.0), (6, "house", 1.0), (7, "house", 1.0), (8, "building", 1.0),
    (8, "house", 0.6), (9, "run", 1.0), (10, "run", 1.0), (11, "robbery", 1.0),
    (12, "correct", 0.8), (12, "so", 0.4), (13, "house", 1.0), (13, "building", 0.5),
    (14, "beautiful", 1.0), (15, "beautiful", 1.0), (15, "pretty", 0.6), (16, "beautiful", 1.0),
    (17, "beautiful", 0.8), (17, "comfortable", 1.0),
]
ETYM = {
    1: "From Middle English water, from Old English wæter, from Proto-Germanic *watōr.",
    2: "A surname.",
    3: "From Old Swedish vatn, from Old Norse vatn.",
    4: "Pictogram of flowing water.",
    5: "From Ancient Greek θέρμη (thérmē, “heat”) + dynamics.",
    6: "From Old English hūs.",
    7: "From Old English rinnan.",
}
# lang, word, pos, ipa, etym_id, gloss
WIK = [
    ("en", "water", "noun", "/ˈwɔːtə/", 1, "A clear liquid | The liquid in rivers and lakes"),
    ("en", "Water", "name", None, 2, "A surname"),
    ("en", "water", "verb", "/ˈwɔːtə/", 1, "To pour water on"),
    ("sv", "vatten", "noun", "/ˈvatːɛn/", 3, "en genomskinlig vätska"),
    ("zh", "水", "character", None, 4, "water"),
    ("en", "thermodynamics", "noun", "/ˌθɜːməʊdaɪˈnæmɪks/", 5, "The science of heat and work"),
    ("en", "house", "noun", "/haʊs/", 6, "A building for living in"),
    ("en", "run", "verb", "/ɹʌn/", 7, "To move swiftly on foot"),
    ("en", "ran", "verb", "/ɹæn/", None, "simple past of run"),
    ("sv", "hus", "noun", "/hʉːs/", None, "byggnad att bo i"),
]
# lang, lemma, pos, form, tags
WFORM = [
    ("en", "run", "verb", "ran", "past"),
    ("en", "run", "verb", "running", "present participle"),
    ("en", "run", "verb", "runs", "present singular third-person"),
    ("en", "run", "verb", "run", "past participle"),
    ("en", "run", "noun", "runs", "plural"),
    ("sv", "hus", "noun", "huset", "definite singular"),
    ("sv", "hus", "noun", "husen", "definite plural"),
    ("sv", "hus", "noun", "hus", "indefinite plural"),
    ("sv", "hus", "noun", "husets", "genitive definite singular"),
    ("sv", "springa", "verb", "sprang", "past"),
    ("sv", "springa", "verb", "sprungit", "supine"),
    ("sv", "springa", "verb", "ſpringa", "infinitive"),
    ("sv", "springa", "verb", "springer", "present"),
    ("en", "water", "noun", "waters", "plural"),
    ("en", "water", "verb", "watered", "past"),
    ("en", "HU", "noun", "hu", "plural"),
    ("en", "beautiful", "adj", "more beautiful", "comparative"),
]
# lang, norm, text, trans, trans_lang, src, score
EXAMPLE_ROWS = [
    ("en", "water", "I drink water every day.", "Jag dricker vatten varje dag.", "sv", "tatoeba", 0.5),
    ("en", "water", "Water is wet.", None, None, "wikt-sense", 0.3),
    ("en", "water", "Could I have some water?", "我可以喝点水吗？", "zh", "tatoeba", 0.5),
    ("en", "water", "I drink water every day.", "Jag dricker vatten varje dag.", "sv", "tatoeba", 0.6),
    ("zh", "水", "我要喝水。", "I want to drink water.", "en", "tatoeba", 0.5),
    ("sv", "hus", "Huset är stort.", "The house is big.", "en", "folkets", 0.0),
    ("sv", "hus", "Husen är röda och husets tak är svart.", "The houses are red.", "en", "tatoeba", 0.2),
    ("en", "run", "He ran home.", "Han sprang hem.", "sv", "tatoeba", 0.5),
    ("en", "run", "Running is fun; I run daily.", None, None, "wikt-quote", 2.0),
]
VOCAB_EXTRA = [("thermodynamics", "en", "thermodynamics", 0.1, 0)]


def build(con):
    con.executescript(db.DICT_SCHEMA + db.DICT_INDEXES + EXTRA + FORMS + FORM_IX + EXAMPLES
                      + IMAGES + AUDIO)
    for eid, lang, word, pos, extra, senses in ENTRIES:
        con.execute("INSERT INTO entry VALUES(?,?,?,?,?,?,?)",
                    (eid, lang, word, norm(word), pos,
                     json.dumps(extra, ensure_ascii=False) if extra else None,
                     "cedict" if lang == "zh" else "folkets"))
        for i, (sl, g) in enumerate(senses):
            con.execute("INSERT INTO sense(entry_id,lang,gloss,ord) VALUES(?,?,?,?)", (eid, sl, g, i))
        con.execute("INSERT INTO form VALUES(?,?,?,?,?)", (norm(word), lang, eid, "head", word))
        if eid in PINYIN:
            con.execute("INSERT INTO form VALUES(?,?,?,?,?)",
                        (pinyin_plain(PINYIN[eid]), "zh", eid, "pinyin", word))
        if extra.get("trad") and extra["trad"] != word:
            con.execute("INSERT INTO form VALUES(?,?,?,?,?)", (norm(extra["trad"]), "zh", eid, "trad", word))
    for n, lang, eid in SYNONYMS:
        con.execute("INSERT INTO form VALUES(?,?,?,?,?)", (n, lang, eid, "syn", n))
    for eid, en, w in PIVOT:
        con.execute("INSERT INTO pivot VALUES(?,?,?)", (eid, en, w))
    con.execute("INSERT INTO pivot_df SELECT en_norm, count(*) FROM pivot GROUP BY en_norm")
    # One vocab row per (norm, lang), scored so shorter, earlier words lead.
    for r in con.execute("SELECT f.norm, f.lang, f.disp, f.entry_id FROM form f ORDER BY f.entry_id").fetchall():
        con.execute("INSERT OR IGNORE INTO vocab VALUES(?,?,?,?,1)",
                    (r[0], r[1], r[2], round(10.0 / (1 + r[3]), 4)))
    con.executemany("INSERT OR IGNORE INTO vocab VALUES(?,?,?,?,?)", VOCAB_EXTRA)
    for i, t in ETYM.items():
        con.execute("INSERT INTO etym VALUES(?,?,?)", (i, f"sha{i}", t))
    for lang, word, pos, ipa, etym, gloss in WIK:
        con.execute("INSERT INTO wik(lang,word,norm,pos,ipa,etym_id,gloss) VALUES(?,?,?,?,?,?,?)",
                    (lang, word, norm(word), pos, ipa, etym, gloss))
    for lang, lemma, pos, form, tags in WFORM:
        con.execute("INSERT INTO wform VALUES(?,?,?,?,?,?,?)",
                    (lang, lemma, norm(lemma), pos, form, norm(form), tags))
    con.executemany("INSERT INTO example VALUES(?,?,?,?,?,?,?)", EXAMPLE_ROWS)
    con.execute("INSERT INTO image VALUES(?,?,?,?,?,?,?,?,?,?)",
                ("en", "water", "water", b"\x89PNG\r\n\x1a\nfixture", 80, 60, "CC BY-SA 4.0",
                 "A. Photographer", "Water.jpg", "https://commons.wikimedia.org/wiki/File:Water.jpg"))
    con.execute("INSERT INTO image VALUES(?,?,?,?,?,?,?,?,?,?)",
                ("en", "house", "house", b"\x89PNGhouse", 64, 64, "CC0", None, "House.jpg", None))
    con.execute("INSERT INTO audio VALUES(?,?,?,?,?,?,?)",
                ("en", "water", "water", b"ID3fixture-mp3", 14, "En-us-water.ogg", "US"))
    con.execute("INSERT INTO audio VALUES(?,?,?,?,?,?,?)",
                ("sv", "vatten", "vatten", b"ID3vatten", 9, "Sv-vatten.ogg", None))
    con.commit()


def result_json(r):
    return {
        "query": r.query,
        "entries": [{"id": e.id, "lang": e.lang, "word": e.word, "pos": e.pos,
                     "reading": e.reading, "traditional": e.traditional,
                     "senses": [[s.lang, s.gloss] for s in e.senses]} for e in r.entries],
        "related": {lg: [{"word": x.word, "entry_id": x.entry_id, "score": x.score, "via": x.via,
                          "reading": x.reading, "glosses": x.glosses} for x in rs]
                    for lg, rs in r.related.items()},
        "etymologies": [[e.lang, e.word, e.pos, e.ipa, e.text, e.gloss] for e in r.etymologies],
        "did_you_mean": [list(x) for x in r.did_you_mean],
        "definitions": r.definitions,
        "direct": r.direct,
        "inflections": {lg: [[i.form, i.tags, i.label] for i in v] for lg, v in r.inflections.items()},
        "resolved_from": r.resolved_from,
        "also_form_of": list(r.also_form_of) if r.also_form_of else None,
        "image": r.image,
        "audio": r.audio,
        "examples": [[lg, [[x.text, x.trans, x.trans_lang, x.src] for x in v]]
                     for lg, v in r.examples.items()],
        "wiktionary_only": r.wiktionary_only,
    }


QUERIES = ["water", "Water", "  WATER!! ", "vatten", "水", "shui", "shuijiao", "水餃", "hus",
           "husen", "huset", "ran", "run", "running", "springa", "sprang", "byggnad", "beautiful",
           "美丽", "skön", "watter", "beutiful", "vaten", "huss", "thermodynamics", "xyzzyq",
           "", "!!", "hu", "house", "房子", "fangzi", "vacker"]
PREFIXES = ["wa", "va", "sh", "h", "水", "be", "SPR", "x", ""]
NORMS = ["Water", "  Hello,  World! ", "Ångström", "ÅÄÖ åäö", "straße", "ΣΊΣΥΦΟΣ", "ﬁne",
         "水饺", "shuǐ jiǎo", "café", "naïve—test", "「水」", "é", "Ｆｕｌｌ　ｗｉｄｔｈ",
         "tab\tand\nnewline", "İstanbul", "ǅemal", "...", "a-b_c"]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    build(con)
    (OUT / "dict.sql").write_text("\n".join(con.iterdump()) + "\n", encoding="utf-8")

    d = Dictionary(con)
    expected = {
        "lookups": [result_json(d.lookup(q)) for q in QUERIES],
        "suggest": [[p, [list(x) for x in d.suggest(p)]] for p in PREFIXES],
        "norm": [[s, norm(s), loose(s), is_cjk(s)] for s in NORMS],
    }

    # A card's life: grades on given elapsed days, from new.
    mem, steps = F.Memory(), []
    for g, days in [(F.GOOD, 0), (F.GOOD, 3), (F.AGAIN, 10), (F.HARD, 2), (F.EASY, 20),
                    (F.GOOD, 0), (F.AGAIN, 400)]:
        mem = mem.review(g, days)
        steps.append({"grade": g, "elapsed": days, "stability": mem.stability,
                      "difficulty": mem.difficulty, "reps": mem.reps, "lapses": mem.lapses,
                      "next": mem.next_interval(), "next_85": mem.next_interval(0.85),
                      "preview": {str(k): v for k, v in F.preview(mem, 5).items()}})
    expected["forgetting"] = steps
    expected["retrievability"] = [[t, s, F.retrievability(t, s)]
                                  for t, s in [(0, 1), (1, 1), (10, 3.7), (100, 30), (5, 0)]]
    expected["fmt_interval"] = [[x, F.fmt_interval(x)] for x in (0.5, 1, 12.4, 45, 400, 29.6)]

    (OUT / "expected.json").write_text(
        json.dumps(expected, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {OUT / 'dict.sql'} and {OUT / 'expected.json'}")


if __name__ == "__main__":
    main()
