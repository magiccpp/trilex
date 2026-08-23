"""Download the two small bilingual sources. Run once; then work offline."""
import os, sys, urllib.request
from pathlib import Path

DATA = Path(os.environ.get("TRILEX_DATA") or
            Path(__file__).resolve().parents[2] / "data")

FILES = {
    "cedict.txt.gz":
        "https://www.mdbg.net/chinese/export/cedict/cedict_1_0_ts_utf-8_mdbg.txt.gz",
    "folkets_en_sv.xml":
        "https://folkets-lexikon.csc.kth.se/folkets/folkets_en_sv_public.xml",
    "folkets_sv_en.xml":
        "https://folkets-lexikon.csc.kth.se/folkets/folkets_sv_en_public.xml",
}
UA = {"User-Agent": "trilex-offline-dictionary-builder/1.0"}


def fetch(force=False):
    DATA.mkdir(parents=True, exist_ok=True)
    for name, url in FILES.items():
        dest = DATA / name
        if dest.exists() and dest.stat().st_size > 1000 and not force:
            print(f"  have {name} ({dest.stat().st_size/1048576:.1f} MB)")
            continue
        print(f"  downloading {name} ...", flush=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        req = urllib.request.Request(url, headers=dict(UA))
        with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as fh:
            while chunk := r.read(1 << 20):
                fh.write(chunk)
        tmp.replace(dest)
        print(f"    {dest.stat().st_size/1048576:.1f} MB", flush=True)


if __name__ == "__main__":
    fetch(force="--force" in sys.argv)
