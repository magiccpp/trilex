"""Publish the dictionary, media packs and installers as a GitHub release.

GitHub release assets are free and unmetered: up to 2 GiB per file, no total
storage cap, no bandwidth charge. That suits ~415 MB of static downloads far
better than a VPS, and it leaves the server responsible only for the sync API.

Requires the gh CLI, authenticated:  gh auth login

    python -m trilex.build.publish              # publish/refresh v<VERSION>
    python -m trilex.build.publish --tag v1.2.0 # a different tag
"""
import json, shutil, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from trilex.build.dist import VERSION
from trilex.build.pack import OUT as PACK_DIR, sha256

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "data" / "dist"
REPO = "magiccpp/trilex"
BASE = f"https://github.com/{REPO}/releases/latest/download"

ASSETS = [
    ("dict.db.xz",       PACK_DIR),
    ("media-images.db",  PACK_DIR),
    ("media-audio.db",   PACK_DIR),
    (f"thriauga-{VERSION}-linux.tar.gz",       DIST),
    (f"thriauga-{VERSION}-windows.zip",        DIST),
    (f"thriauga-{VERSION}-linux-full.tar.gz",  DIST),
    (f"thriauga-{VERSION}-windows-full.zip",   DIST),
]


def gh(*args, check=True, capture=True):
    exe = shutil.which("gh") or str(Path.home() / ".local/bin/gh")
    r = subprocess.run([exe, *args], capture_output=capture, text=True)
    if check and r.returncode != 0:
        raise SystemExit(f"gh {' '.join(args)} failed:\n{r.stderr or r.stdout}")
    return (r.stdout or "").strip()


def require_auth():
    exe = shutil.which("gh") or str(Path.home() / ".local/bin/gh")
    r = subprocess.run([exe, "auth", "status"], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(
            "Not signed in to GitHub.\n\n"
            "  gh auth login\n\n"
            "Choose GitHub.com, then HTTPS or SSH, and authenticate in the\n"
            "browser. Then run this command again.")
    print("  " + (r.stderr or r.stdout).strip().splitlines()[1].strip())


def build_manifest():
    """Manifest with absolute release URLs, so the client needs no API."""
    manifest = json.loads((PACK_DIR / "manifest.json").read_text())
    manifest["generated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest["source"] = f"https://github.com/{REPO}"
    for p in manifest.get("packs", []):
        p["url"] = f"{BASE}/{p['file']}"
    if manifest.get("dictionary"):
        manifest["dictionary"]["url"] = f"{BASE}/{manifest['dictionary']['file']}"
    out = PACK_DIR / "manifest.json"
    out.write_text(json.dumps(manifest, indent=2))
    return out, manifest


def main():
    tag = "v" + VERSION
    if "--tag" in sys.argv:
        tag = sys.argv[sys.argv.index("--tag") + 1]

    print("[1/4] checking GitHub authentication")
    require_auth()

    print("[2/4] writing manifest with release URLs")
    mf_path, mf = build_manifest()
    for p in mf.get("packs", []):
        print(f"  {p['name']:8} {p['bytes']/1048576:7.1f} MB  {p['url']}")
    if mf.get("dictionary"):
        d = mf["dictionary"]
        print(f"  {'dict':8} {d['bytes']/1048576:7.1f} MB  {d['url']}")

    missing = [n for n, d in ASSETS if not (d / n).exists()]
    if missing:
        raise SystemExit(f"missing artifacts: {', '.join(missing)}")

    print(f"[3/4] creating release {tag}")
    existing = subprocess.run(
        [shutil.which("gh") or str(Path.home() / ".local/bin/gh"),
         "release", "view", tag, "-R", REPO],
        capture_output=True, text=True).returncode == 0
    if existing:
        print(f"  {tag} already exists — refreshing assets")
    else:
        full_mb = sum((DIST / n).stat().st_size for n, d in ASSETS
                      if "-windows-full" in n) / 1048576
        notes = (
            f"Offline English / Svenska / 中文 dictionary.\n\n"
            f"**Everything in one download** (~{full_mb:.0f} MB): "
            f"`thriauga-{VERSION}-windows-full.zip` or "
            f"`thriauga-{VERSION}-linux-full.tar.gz`. Unpack it, run "
            f"`install.bat` (Windows) or `install.sh` (Linux), and the "
            f"dictionary, illustrations and pronunciation audio are all "
            f"installed. Nothing further is fetched from the network.\n\n"
            f"**Small installer** (~70 KB): `thriauga-{VERSION}-windows.zip` "
            f"or `thriauga-{VERSION}-linux.tar.gz`. Same app; the dictionary "
            f"is fetched on first launch, and images and audio are optional "
            f"from **File → Add-ons**.\n\n"
            f"Both need Python 3.10 or newer already installed "
            f"(`winget install Python.Python.3.12` on Windows).\n\n"
            f"| Asset | Size | Contents |\n|---|---|---|\n"
            + "".join(
                f"| `{p['file']}` | {p['bytes']/1048576:.0f} MB | "
                f"{p['title']}, {p['rows']:,} entries |\n"
                for p in mf.get("packs", []))
            + (f"| `{mf['dictionary']['file']}` | "
               f"{mf['dictionary']['bytes']/1048576:.0f} MB | "
               f"Dictionary, downloaded on first run |\n"
               if mf.get("dictionary") else "")
            + "\nData is derived from CC-CEDICT, Folkets lexikon, Wiktionary, "
              "Tatoeba and Wikimedia Commons; see NOTICE for attribution.\n")
        gh("release", "create", tag, "-R", REPO,
           "--title", f"Þríauga {VERSION}", "--notes", notes)

    print("[4/4] uploading assets (large files take a few minutes)")
    files = [str(d / n) for n, d in ASSETS] + [str(mf_path)]
    for f in files:
        size = Path(f).stat().st_size / 1048576
        print(f"  uploading {Path(f).name} ({size:.1f} MB) ...", flush=True)
        gh("release", "upload", tag, f, "-R", REPO, "--clobber")

    print(f"\nPublished: https://github.com/{REPO}/releases/tag/{tag}")
    print(f"Manifest:  {BASE}/manifest.json")


if __name__ == "__main__":
    main()
