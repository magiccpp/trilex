"""Optional media pack downloader.

The base install is text only. Images and pronunciation audio are large, so
they are published as separate SQLite files and fetched on demand. A pack is
installed simply by placing the file in the data directory - the dictionary
ATTACHes whatever it finds at startup.

Downloads resume: a partial file is kept as .part and continued with an HTTP
Range request, which matters for a 150 MB audio pack on a poor connection.
"""
from __future__ import annotations

import hashlib, json, lzma, os, urllib.error, urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import db

# Media is published as GitHub release assets: free, unmetered, and it keeps
# 400 MB of static downloads off the sync server. The /latest/ path always
# resolves to the newest release, so the client needs no version pinning.
DEFAULT_URL = "https://github.com/magiccpp/trilex/releases/latest/download"
# Fallback for a self-hosted deployment serving the pack API instead.
API_URL = "https://xiaodong.io/thriauga"
UA = {"User-Agent": "Thriauga/1.0"}


class PackError(Exception):
    pass


@dataclass
class Pack:
    name: str
    title: str
    detail: str
    file: str
    rows: int
    bytes: int
    sha256: str
    built: str = ""
    url: str = ""          # absolute; set when published to a static host

    @property
    def size_mb(self) -> float:
        return self.bytes / 1048576


class PackClient:
    def __init__(self, base_url=None, data_dir=None):
        self.base_url = (base_url or DEFAULT_URL).rstrip("/")
        self.dir = Path(data_dir) if data_dir else db.data_dir()

    # ------------------------------------------------------------- discovery
    def _manifest_url(self) -> str:
        """Static hosts serve manifest.json directly; the API serves /v1/packs."""
        if "/releases/" in self.base_url or self.base_url.endswith("/download"):
            return f"{self.base_url}/manifest.json"
        return f"{self.base_url}/v1/packs"

    def _asset_url(self, entry: dict, kind: str) -> str:
        """Absolute URL from the manifest, else the API route."""
        if entry.get("url"):
            return entry["url"]
        if "/releases/" in self.base_url or self.base_url.endswith("/download"):
            return f"{self.base_url}/{entry['file']}"
        return (f"{self.base_url}/v1/dictionary" if kind == "dictionary"
                else f"{self.base_url}/v1/packs/{entry['name']}")

    def _manifest(self) -> dict:
        req = urllib.request.Request(self._manifest_url(), headers=dict(UA))
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read())
        except urllib.error.URLError as e:
            raise PackError(f"Cannot reach {self.base_url} ({e.reason})")
        except Exception as e:
            raise PackError(str(e))

    def available(self) -> list[Pack]:
        req = urllib.request.Request(self._manifest_url(), headers=dict(UA))
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                data = json.loads(r.read())
        except urllib.error.URLError as e:
            raise PackError(f"Cannot reach {self.base_url} ({e.reason})")
        except Exception as e:
            raise PackError(str(e))
        out = []
        for p in data.get("packs", []):
            try:
                pack = Pack(**{k: p[k] for k in
                               ("name", "title", "detail", "file", "rows",
                                "bytes", "sha256") if k in p},
                            built=p.get("built", ""))
            except TypeError:
                continue
            pack.url = self._asset_url(p, "pack")
            out.append(pack)
        return out

    def installed_path(self, name) -> Path:
        return self.dir / db.MEDIA_PACKS[name]

    def is_installed(self, name) -> bool:
        p = self.installed_path(name)
        return p.exists() and p.stat().st_size > 4096

    def remove(self, name):
        self.installed_path(name).unlink(missing_ok=True)

    # -------------------------------------------------------------- download
    def download(self, pack: Pack, progress=None, cancel=None) -> Path:
        """Fetch a pack, resuming any partial file, then verify and install.

        `progress(done_bytes, total_bytes)` is called as data arrives;
        `cancel()` returning True aborts and leaves the .part file in place so
        a later attempt continues from there.
        """
        self.dir.mkdir(parents=True, exist_ok=True)
        final = self.installed_path(pack.name)
        part = final.with_suffix(".part")
        have = part.stat().st_size if part.exists() else 0
        if have > pack.bytes:
            part.unlink()
            have = 0

        req = urllib.request.Request(pack.url or self._asset_url(
            {"name": pack.name, "file": pack.file}, "pack"), headers=dict(UA))
        if have:
            req.add_header("Range", f"bytes={have}-")
        mode = "ab" if have else "wb"
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                if have and r.status != 206:
                    # Server ignored the Range; start over rather than append
                    # to a partial file and corrupt it.
                    have, mode = 0, "wb"
                total = pack.bytes
                with open(part, mode) as fh:
                    done = have
                    while True:
                        if cancel and cancel():
                            raise PackError("cancelled")
                        chunk = r.read(1 << 18)
                        if not chunk:
                            break
                        fh.write(chunk)
                        done += len(chunk)
                        if progress:
                            progress(done, total)
        except PackError:
            raise
        except urllib.error.URLError as e:
            raise PackError(f"Download failed ({e.reason})")
        except Exception as e:
            raise PackError(f"Download failed ({e})")

        if part.stat().st_size != pack.bytes:
            raise PackError(f"Size mismatch: expected {pack.bytes:,} bytes, "
                            f"got {part.stat().st_size:,}")
        digest = self._sha256(part)
        if pack.sha256 and digest != pack.sha256:
            part.unlink(missing_ok=True)
            raise PackError("Checksum mismatch — the download was corrupted")
        os.replace(part, final)
        return final

    # ------------------------------------------------------- core dictionary
    def dictionary_info(self):
        d = self._manifest().get("dictionary")
        if d:
            d = dict(d)
            d["url"] = self._asset_url(d, "dictionary")
        return d

    def dictionary_installed(self) -> bool:
        p = self.dir / "dict.db"
        return p.exists() and p.stat().st_size > 10 * 1048576

    def download_dictionary(self, info, progress=None, cancel=None) -> Path:
        """Fetch and decompress the dictionary in one pass.

        The archive is decompressed as it arrives rather than saved first, so
        peak disk use is the finished database alone instead of both.
        """
        self.dir.mkdir(parents=True, exist_ok=True)
        final = self.dir / "dict.db"
        part = self.dir / "dict.db.part"
        part.unlink(missing_ok=True)

        req = urllib.request.Request(
            info.get("url") or self._asset_url(info, "dictionary"),
            headers=dict(UA))
        total = info.get("bytes", 0)
        digest = hashlib.sha256()
        dec = lzma.LZMADecompressor()
        try:
            with urllib.request.urlopen(req, timeout=90) as r, open(part, "wb") as fh:
                done = 0
                while True:
                    if cancel and cancel():
                        raise PackError("cancelled")
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    digest.update(chunk)
                    done += len(chunk)
                    fh.write(dec.decompress(chunk))
                    if progress:
                        progress(done, total)
        except PackError:
            part.unlink(missing_ok=True)
            raise
        except Exception as e:
            part.unlink(missing_ok=True)
            raise PackError(f"Download failed ({e})")

        if info.get("sha256") and digest.hexdigest() != info["sha256"]:
            part.unlink(missing_ok=True)
            raise PackError("Checksum mismatch — the download was corrupted")
        os.replace(part, final)
        return final

    @staticmethod
    def _sha256(path, chunk=1 << 20):
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            while block := fh.read(chunk):
                h.update(block)
        return h.hexdigest()
