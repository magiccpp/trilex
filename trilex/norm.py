"""Normalisation shared by the builders and the search layer.

Swedish a-ring/a-umlaut/o-umlaut are *letters*, not accented vowels, so `norm`
never folds them away. A separate `loose` form does fold them, and is only ever
used to rescue a typo - never to decide an exact match.
"""
import re, unicodedata

_PUNCT = re.compile(r"[\s　!-/:-@\[-`{-~‐-‧、-〿！-･]+")
_CJK = re.compile(r"[㐀-䶿一-鿿豈-﫿]")

# CC-CEDICT writes pinyin as "shui3"; these map the numbered vowel to a mark.
_TONES = {
    "a": "āáǎàa", "e": "ēéěèe", "i": "īíǐìi", "o": "ōóǒòo",
    "u": "ūúǔùu", "ü": "ǖǘǚǜü", "v": "ǖǘǚǜü",
}


def norm(s: str) -> str:
    """Lookup key: NFC, casefolded, punctuation and spacing removed."""
    s = unicodedata.normalize("NFC", s or "").casefold()
    return _PUNCT.sub(" ", s).strip()


def loose(s: str) -> str:
    """Typo-tolerant key: additionally strips diacritics and inner spaces."""
    s = unicodedata.normalize("NFD", norm(s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.replace(" ", "")


def is_cjk(s: str) -> bool:
    return bool(_CJK.search(s or ""))


def guess_lang(s: str) -> str:
    """Cheap script-based guess. 'en' and 'sv' share the Latin script, so a
    Latin string returns 'latin' and the caller resolves it by lookup."""
    if is_cjk(s):
        return "zh"
    return "latin"


def pinyin_pretty(numbered: str) -> str:
    """'shui3 jiao4' -> 'shuǐ jiào'. Leaves non-pinyin tokens untouched."""
    out = []
    for tok in (numbered or "").split():
        m = re.fullmatch(r"([a-zA-Zü:]+)([1-5])", tok)
        if not m:
            out.append(tok)
            continue
        body, tone = m.group(1).replace("u:", "ü").replace("U:", "Ü"), int(m.group(2))
        if tone == 5:
            out.append(body.lower())
            continue
        low = body.lower()
        # Standard placement: a/e win; in 'ou' the o wins; else the last vowel.
        idx = None
        for pref in ("a", "e", "ou"):
            p = low.find(pref)
            if p >= 0:
                idx = p
                break
        if idx is None:
            vowels = [i for i, c in enumerate(low) if c in "aeiouvü"]
            if not vowels:
                out.append(low)
                continue
            idx = vowels[-1]
        ch = low[idx]
        low = low[:idx] + _TONES[ch][tone - 1] + low[idx + 1:]
        out.append(low)
    return " ".join(out)


def pinyin_plain(numbered: str) -> str:
    """'shui3 jiao4' -> 'shuijiao', for typing pinyin without tones."""
    return re.sub(r"[1-5\s:]", "", (numbered or "").replace("u:", "u")).lower()
