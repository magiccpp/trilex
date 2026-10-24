"""Produce a mobile-sized database.

The desktop build keeps every Wiktionary headword (~1.5M), most of which have
no translation attached. A phone only needs etymology for words the dictionary
can actually translate, which removes the bulk of the weight.

    python -m trilex.build.trim          -> data/dict-mobile.db
                                            data/dist/dict-mobile.db.xz

The compressed copy is what the iPhone app downloads on first launch. If
data/packs/manifest.json exists (trilex.build.pack has run) it is listed
there as `dictionary_mobile`, so publishing picks it up.
"""
import json, lzma, os, shutil, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from trilex import db
from trilex.build.pack import sha256

DATA = Path(os.environ.get("TRILEX_DATA") or
            Path(__file__).resolve().parents[2] / "data")
XZ = DATA / "dist" / "dict-mobile.db.xz"
MANIFEST = DATA / "packs" / "manifest.json"


def compress(src, dst):
    """xz with the default CRC64 check, which Apple's Compression framework
    decodes natively."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(".part")
    with open(src, "rb") as i, lzma.open(tmp, "wb", preset=6) as o:
        shutil.copyfileobj(i, o, 1 << 20)
    os.replace(tmp, dst)


def list_in_manifest(plain, xz):
    if not MANIFEST.exists():
        return False
    mf = json.loads(MANIFEST.read_text())
    mf["dictionary_mobile"] = {"file": xz.name, "bytes": xz.stat().st_size,
                               "uncompressed": plain.stat().st_size,
                               "sha256": sha256(xz), "built": time.strftime("%Y-%m-%d")}
    MANIFEST.write_text(json.dumps(mf, indent=2))
    return True


def main():
    # Prefer the media-free build: images and audio ship as separate packs,
    # so a phone should not download them twice.
    core = DATA / "dict-core.db"
    src, dst = (core if core.exists() else DATA / "dict.db"), DATA / "dict-mobile.db"
    if not src.exists():
        sys.exit(f"missing {src} - run the desktop build first")
    dst.unlink(missing_ok=True)
    for suf in ("-wal", "-shm"):
        Path(str(dst) + suf).unlink(missing_ok=True)
    print(f"copying {src.stat().st_size/1048576:.0f} MB ...", flush=True)
    shutil.copy2(src, dst)

    con = db.connect(dst, wal=False)
    con.execute("PRAGMA journal_mode=DELETE")
    before = con.execute("SELECT count(*) c FROM wik").fetchone()["c"]

    # Keep a Wiktionary row only when the headword is reachable from the
    # bilingual core - an etymology you can never navigate to is dead weight.
    con.execute("""DELETE FROM wik WHERE id NOT IN (
                     SELECT w.id FROM wik w
                     JOIN vocab v ON v.norm = w.norm AND v.lang = w.lang
                     WHERE v.core = 1)""")
    con.execute("DELETE FROM etym WHERE id NOT IN (SELECT etym_id FROM wik WHERE etym_id IS NOT NULL)")
    # Wiktionary-only vocab entries no longer resolve to anything.
    con.execute("DELETE FROM vocab WHERE core = 0")
    for t in ("image", "audio", "audio_ref"):
        con.execute(f"DROP TABLE IF EXISTS {t}")
    after = con.execute("SELECT count(*) c FROM wik").fetchone()["c"]
    con.commit()
    print(f"wik {before:,} -> {after:,}", flush=True)
    print("vacuuming ...", flush=True)
    con.execute("VACUUM")
    con.execute("PRAGMA optimize")
    con.close()
    print(f"{dst.name}: {dst.stat().st_size/1048576:.0f} MB")
    print("compressing ...", flush=True)
    compress(dst, XZ)
    print(f"{XZ.name}: {XZ.stat().st_size/1048576:.0f} MB")
    if list_in_manifest(dst, XZ):
        print(f"listed in {MANIFEST} as dictionary_mobile")


if __name__ == "__main__":
    main()
