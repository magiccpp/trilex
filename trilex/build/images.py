"""Fetch small illustration images for concrete nouns.

Source is the Wikipedia lead image for the matching article, resized to 128px
WebP - a few kilobytes each, so several thousand fit in a handful of megabytes
and the app stays fully offline.

Licensing is enforced, not assumed: the lead-image FILE lives on Commons (not
on en.wikipedia), so its licence is looked up there and anything that is not
clearly free is discarded. Attribution is stored with every image and shown in
the UI, which is what CC BY-SA requires.
"""
import io, json, re, sys, time, urllib.error, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from trilex import db
from trilex.norm import norm

DATA = Path(__file__).resolve().parents[2] / "data"
# Wikimedia's User-Agent policy requires a contact URL and address. audio.py
# was fixed for this; this builder was missed and paid for it with heavy
# throttling - roughly half of all requests refused.
UA = {"User-Agent": "TrilexDictionaryBuilder/1.0 "
                    "(https://xiaodong.io/thriauga; ken@xiaodong.io) "
                    "python-urllib/3.12"}
THUMB = 256          # 128 was too low: avg 117x105 looked soft on any modern display
BATCH = 50

FREE_HINTS = ("cc0", "public domain", "cc by", "cc-by", "pd-", "pd ",
              "no restrictions", "attribution", "gfdl", "share alike", "sharealike")
NONFREE_HINTS = ("fair use", "non-free", "nonfree", "copyright", "all rights")

SCHEMA = """
CREATE TABLE IF NOT EXISTS image(
  lang    TEXT NOT NULL,
  norm    TEXT NOT NULL,
  word    TEXT NOT NULL,
  data    BLOB NOT NULL,
  w       INTEGER, h INTEGER,
  license TEXT, artist TEXT, file TEXT, page TEXT,
  PRIMARY KEY(lang, norm)
) WITHOUT ROWID;
"""


def _api(host, params, tries=5):
    q = urllib.parse.urlencode({**params, "format": "json", "formatversion": "2"})
    url = f"https://{host}/w/api.php?{q}"
    for a in range(tries):
        try:
            req = urllib.request.Request(url, headers=dict(UA))
            with urllib.request.urlopen(req, timeout=45) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (429, 503):
                time.sleep(min(30, 2 ** a * 2))
                continue
            raise
        except Exception:
            if a == tries - 1:
                raise
            time.sleep(2 ** a)
    return {}


def _strip_html(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s or "")).strip()[:120]


def is_free(license_name: str) -> bool:
    L = (license_name or "").lower()
    if any(b in L for b in NONFREE_HINTS):
        return False
    return any(f in L for f in FREE_HINTS)


def candidates(con, limit):
    """Every single-word English noun, shortest first.

    An earlier version ranked by how many entries pivot through a word, on the
    theory that broadly-translated words are common ones. That backfired: high
    pivot reach measures *polysemy*, so it surfaced 'die', 'run', 'cover' --
    words with many senses and nothing to photograph. Wikipedia's own article
    coverage is a far better concreteness filter than any score computed here,
    so hand it every noun and let the misses fall out.
    """
    rows = con.execute("""
        SELECT DISTINCT e.word
        FROM entry e
        WHERE e.lang='en' AND e.pos='nn'
          AND e.word GLOB '[a-z]*' AND e.word NOT GLOB '* *'
          AND length(e.word) > 2
        ORDER BY length(e.word), e.word LIMIT ?""", (limit,)).fetchall()
    return [r["word"] for r in rows]


def fetch_batch(words):
    """words -> {word: {thumb_url, file, page}} using the article lead image."""
    r = _api("en.wikipedia.org", {
        "action": "query", "prop": "pageimages", "piprop": "thumbnail|name",
        "pithumbsize": str(THUMB * 2), "redirects": "1",
        "titles": "|".join(words)})
    q = r.get("query", {})
    # Follow redirects so 'bicycle' still resolves if the article moved.
    back = {}
    for red in q.get("redirects", []):
        back[red["to"].lower()] = red["from"].lower()
    out = {}
    for p in q.get("pages", []):
        if "missing" in p or not p.get("pageimage") or not p.get("thumbnail"):
            continue
        title = p["title"].lower()
        key = back.get(title, title)
        out[key] = {"thumb": p["thumbnail"]["source"], "file": p["pageimage"],
                    "page": p["title"]}
    return out


def fetch_licenses(files):
    out = {}
    for i in range(0, len(files), BATCH):
        chunk = files[i:i + BATCH]
        r = _api("commons.wikimedia.org", {
            "action": "query", "prop": "imageinfo", "iiprop": "extmetadata",
            "titles": "|".join("File:" + f for f in chunk)})
        for p in r.get("query", {}).get("pages", []):
            if "missing" in p or not p.get("imageinfo"):
                continue
            md = p["imageinfo"][0].get("extmetadata", {})
            # MediaWiki returns titles with spaces, while pageimages gives
            # filenames with underscores. Keying on the raw title meant every
            # lookup missed, every image came back with an empty licence, and
            # was then discarded as non-free - which is what held coverage
            # down to 5.8%. Normalise both sides to underscores.
            key = p["title"][5:].replace(" ", "_")
            out[key] = (
                md.get("LicenseShortName", {}).get("value", ""),
                _strip_html(md.get("Artist", {}).get("value", "")))
        time.sleep(0.4)
    return out


def _ascii_url(u: str) -> str:
    """Percent-encode non-ASCII characters; urllib rejects raw Unicode URLs."""
    parts = urllib.parse.urlsplit(u)
    return urllib.parse.urlunsplit((
        parts.scheme, parts.netloc,
        urllib.parse.quote(parts.path, safe="/%:@!$&'()*+,;=~"),
        urllib.parse.quote(parts.query, safe="=&%:/?@!$'()*+,;~"),
        parts.fragment))


def download(url):
    from PIL import Image
    req = urllib.request.Request(_ascii_url(url), headers=dict(UA))
    for a in range(4):
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                raw = r.read()
            break
        except urllib.error.HTTPError as e:
            if e.code in (429, 503):
                time.sleep(2 ** a * 2); continue
            return None
        except Exception:
            if a == 3:
                return None
            time.sleep(1 + a)
    else:
        return None
    try:
        im = Image.open(io.BytesIO(raw))
        if im.mode in ("RGBA", "LA", "P"):
            bg = Image.new("RGB", im.size, (255, 255, 255))
            im = im.convert("RGBA")
            bg.paste(im, mask=im.split()[-1])
            im = bg
        else:
            im = im.convert("RGB")
        im.thumbnail((THUMB, THUMB))
        buf = io.BytesIO()
        im.save(buf, "WEBP", quality=78, method=6)
        return buf.getvalue(), im.size
    except Exception:
        return None


def main(limit=8000):
    con = db.open_dict(DATA / "dict.db")
    con.executescript(SCHEMA)
    have = {r["norm"] for r in con.execute("SELECT norm FROM image WHERE lang='en'")}
    words = [w for w in candidates(con, limit * 2) if norm(w) not in have][:limit]
    print(f"{len(words)} candidate nouns ({len(have)} already stored)", flush=True)

    kept = skipped_lic = no_img = 0
    t0 = time.time()
    for i in range(0, len(words), BATCH):
        batch = words[i:i + BATCH]
        try:
            found = fetch_batch(batch)
        except Exception as e:
            print(f"  batch failed: {e}", flush=True); continue
        no_img += len(batch) - len(found)
        if not found:
            continue
        lic = fetch_licenses(sorted({v["file"] for v in found.values()}))
        todo = []
        for w, meta in found.items():
            L, artist = lic.get(meta["file"].replace(" ", "_"), ("", ""))
            if not is_free(L):
                skipped_lic += 1
                continue
            todo.append((w, meta, L, artist))
        with ThreadPoolExecutor(max_workers=4) as ex:
            blobs = list(ex.map(lambda t: download(t[1]["thumb"]), todo))
        rows = []
        for (w, meta, L, artist), got in zip(todo, blobs):
            if not got:
                continue
            data, (ww, hh) = got
            rows.append(("en", norm(w), w, data, ww, hh, L, artist,
                         meta["file"], meta["page"]))
        con.executemany("INSERT OR REPLACE INTO image"
                        "(lang,norm,word,data,w,h,license,artist,file,page)"
                        " VALUES(?,?,?,?,?,?,?,?,?,?)", rows)
        con.commit()
        kept += len(rows)
        done = i + len(batch)
        print(f"  {done:>6}/{len(words)}  kept={kept}  non-free={skipped_lic}  "
              f"no-image={no_img}  {done/max(time.time()-t0,1):.1f} w/s", flush=True)
        time.sleep(0.5)

    total, size = con.execute(
        "SELECT count(*), coalesce(sum(length(data)),0) FROM image").fetchone()
    print(f"\n{total:,} images, {size/1048576:.1f} MB "
          f"(avg {size/max(total,1)/1024:.1f} KB)")
    print(f"skipped: {skipped_lic} non-free licence, {no_img} no lead image")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 8000)
