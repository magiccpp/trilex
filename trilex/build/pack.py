"""Split media out of dict.db into downloadable add-on packs.

The base install ships text only. Images and audio are large and optional, so
they are extracted into standalone SQLite files, published to a server, and
downloaded from inside the app when the user wants them.

Each pack is a normal database containing one table, so installing a pack is
just dropping a file into the data directory; the app ATTACHes whatever it
finds at startup.
"""
import hashlib, json, shutil, sqlite3, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from trilex import db

DATA = Path(__file__).resolve().parents[2] / "data"
OUT = DATA / "packs"

PACKS = {
    "images": {
        "table": "image",
        "title": "Illustrations",
        "detail": "Freely-licensed pictures for concrete nouns, with attribution.",
        "schema": """
            CREATE TABLE image(
              lang TEXT NOT NULL, norm TEXT NOT NULL, word TEXT NOT NULL,
              data BLOB NOT NULL, w INTEGER, h INTEGER,
              license TEXT, artist TEXT, file TEXT, page TEXT,
              PRIMARY KEY(lang, norm)) WITHOUT ROWID;""",
    },
    "audio": {
        "table": "audio",
        "title": "Pronunciation audio",
        "detail": "Human recordings from Wikimedia Commons, ~3 KB per word.",
        "schema": """
            CREATE TABLE audio(
              lang TEXT NOT NULL, norm TEXT NOT NULL, word TEXT NOT NULL,
              data BLOB NOT NULL, bytes INTEGER, fname TEXT, accent TEXT,
              PRIMARY KEY(lang, norm)) WITHOUT ROWID;""",
    },
}


def sha256(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()


def build_pack(name, src=None):
    spec = PACKS[name]
    src = src or DATA / "dict.db"
    OUT.mkdir(parents=True, exist_ok=True)
    dest = OUT / f"media-{name}.db"
    dest.unlink(missing_ok=True)

    con = sqlite3.connect(str(dest))
    con.executescript(spec["schema"])
    con.execute("ATTACH DATABASE ? AS src", (str(src),))
    con.execute(f"INSERT INTO {spec['table']} SELECT * FROM src.{spec['table']}")
    con.commit()
    n = con.execute(f"SELECT count(*) FROM {spec['table']}").fetchone()[0]
    con.execute("DETACH DATABASE src")
    con.execute("VACUUM")
    con.close()

    size = dest.stat().st_size
    return {"name": name, "title": spec["title"], "detail": spec["detail"],
            "file": dest.name, "rows": n, "bytes": size,
            "sha256": sha256(dest),
            "built": time.strftime("%Y-%m-%d")}


def strip_from_dict(src=None):
    """Produce the shippable text-only dictionary."""
    src = src or DATA / "dict.db"
    dest = DATA / "dict-core.db"
    dest.unlink(missing_ok=True)
    print(f"  copying {src.stat().st_size/1048576:.0f} MB ...", flush=True)
    shutil.copy2(src, dest)
    con = sqlite3.connect(str(dest))
    con.execute("PRAGMA journal_mode=DELETE")
    for spec in PACKS.values():
        con.execute(f"DROP TABLE IF EXISTS {spec['table']}")
    con.execute("DROP TABLE IF EXISTS audio_ref")   # build-time only
    con.commit()
    print("  vacuuming ...", flush=True)
    con.execute("VACUUM")
    con.close()
    return dest


def describe_dictionary():
    """Metadata for the compressed core dictionary served to new installs."""
    xz = DATA / "dist" / "dict.db.xz"
    plain = DATA / "dict-core.db"
    if not xz.exists():
        return None
    return {"file": xz.name,
            "bytes": xz.stat().st_size,
            "uncompressed": plain.stat().st_size if plain.exists() else 0,
            "sha256": sha256(xz),
            "built": time.strftime("%Y-%m-%d")}


def main():
    manifest = {"version": 1, "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "packs": []}
    d = describe_dictionary()
    if d:
        manifest["dictionary"] = d
        print(f"  dictionary  {d['bytes']/1048576:6.1f} MB compressed "
              f"({d['uncompressed']/1048576:.0f} MB installed)", flush=True)
    for name in PACKS:
        try:
            info = build_pack(name)
        except sqlite3.OperationalError as e:
            print(f"  skip {name}: {e}")
            continue
        manifest["packs"].append(info)
        print(f"  {name:8} {info['rows']:>7,} rows  "
              f"{info['bytes']/1048576:6.1f} MB  {info['sha256'][:16]}...", flush=True)
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"\nmanifest: {OUT / 'manifest.json'}")

    core = strip_from_dict()
    print(f"dict-core.db: {core.stat().st_size/1048576:.0f} MB (text only)")


if __name__ == "__main__":
    main()
