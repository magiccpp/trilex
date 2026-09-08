"""Build the Linux and Windows installer archives.

Two flavours per platform:

  thriauga-X.Y.Z-<os>       ~100 KB. Source and an install script only.
                            Dependencies come from PyPI and the dictionary is
                            downloaded on first run, so a dictionary update
                            never needs a new installer.
  thriauga-X.Y.Z-<os>-full  ~415 MB. The same, plus a data/ folder holding the
                            compressed dictionary and both media packs. The
                            installer unpacks them, so one download is the
                            whole thing and the app never touches the network.

The full archives are only produced when the packs exist in data/packs
(built by trilex.build.pack, or downloaded from a previous release).

A native Windows .exe cannot be produced on Linux - PyInstaller must run on
the target platform - so the Windows archive ships a build script that makes
one on a Windows machine instead of pretending otherwise.
"""
import hashlib, io, json, os, shutil, sys, tarfile, time, zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
OUT = DATA / "dist"
PACKS = DATA / "packs"
VERSION = "1.2.1"

# Shipped inside the "full" archives, under data/. Already compressed or
# blob-heavy, so they are stored rather than deflated again.
DATA_FILES = ["dict.db.xz", "media-images.db", "media-audio.db"]

# Globbed, not hand-listed. A hardcoded list silently omitted theme.py when
# it was added, which would have shipped an app that crashed on import.
# Build-time modules stay out of the release.
BUILD_ONLY = {"build"}

CJK_FONT_PATHS = ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
                  "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
                  "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")


def source_files():
    files = ["trilex.py", "requirements.txt", "README.md"]
    for f in sorted((ROOT / "trilex").glob("*.py")):
        files.append(f"trilex/{f.name}")
    return [f for f in files if (ROOT / f).exists()]


INSTALL_SH = r"""#!/usr/bin/env bash
# Thriauga installer for Linux.
set -euo pipefail

APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/trilex"
BIN_DIR="$HOME/.local/bin"
DESKTOP_DIR="$HOME/.local/share/applications"
SRC="$(cd "$(dirname "$0")" && pwd)"

echo "Thriauga installer"
echo

# --- 1. Python ---------------------------------------------------------
PY=""
for c in python3.12 python3.11 python3.10 python3; do
    if command -v "$c" >/dev/null 2>&1; then
        v=$("$c" -c 'import sys;print(sys.version_info>=(3,10))' 2>/dev/null || echo False)
        [ "$v" = "True" ] && { PY="$c"; break; }
    fi
done
if [ -z "$PY" ]; then
    echo "ERROR: Python 3.10 or newer is required but was not found."
    echo "  Debian/Ubuntu:  sudo apt install python3 python3-venv"
    echo "  Fedora:         sudo dnf install python3"
    exit 1
fi
echo "[1/5] Using $($PY -V)"
# Check this before touching the disk, so a missing package leaves nothing
# half-installed behind.
if ! "$PY" -c 'import venv, ensurepip' >/dev/null 2>&1; then
    echo "ERROR: Python's venv/ensurepip modules are missing."
    echo "  Debian/Ubuntu:  sudo apt install python3-venv   (or python3.X-venv)"
    exit 1
fi

# --- 2. Application + virtualenv ---------------------------------------
echo "[2/5] Installing to $APP_DIR"
mkdir -p "$APP_DIR"
cp -r "$SRC/trilex" "$SRC/trilex.py" "$SRC/requirements.txt" "$APP_DIR/"
[ -f "$SRC/README.md" ] && cp "$SRC/README.md" "$APP_DIR/"
[ -f "$SRC/trilex.png" ] && cp "$SRC/trilex.png" "$APP_DIR/"

"$PY" -m venv "$APP_DIR/venv"
echo "[3/5] Installing dependencies (this downloads ~80 MB)"
"$APP_DIR/venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

# --- 3. Bundled dictionary + media (full package only) -----------------
DATA_DIR="${TRILEX_DATA:-${XDG_DATA_HOME:-$HOME/.local/share}/trilex}"
BUNDLED=0
if [ -d "$SRC/data" ]; then
    echo "[4/5] Installing bundled dictionary and media to $DATA_DIR"
    mkdir -p "$DATA_DIR"
    for f in media-images.db media-audio.db; do
        [ -f "$SRC/data/$f" ] && cp "$SRC/data/$f" "$DATA_DIR/"
    done
    if [ -f "$SRC/data/dict.db.xz" ]; then
        echo "      unpacking dict.db (this takes a minute)"
        "$APP_DIR/venv/bin/python" - "$SRC/data/dict.db.xz" "$DATA_DIR/dict.db" <<'PY'
import lzma, os, shutil, sys
src, dst = sys.argv[1], sys.argv[2]
tmp = dst + ".part"
with lzma.open(src, "rb") as i, open(tmp, "wb") as o:
    shutil.copyfileobj(i, o, 1 << 20)
os.replace(tmp, dst)
PY
    fi
    BUNDLED=1
else
    echo "[4/5] No bundled data; the dictionary is downloaded on first launch"
fi

# --- 4. Launcher + menu entry ------------------------------------------
mkdir -p "$BIN_DIR" "$DESKTOP_DIR"
cat > "$BIN_DIR/trilex" <<LAUNCH
#!/usr/bin/env bash
exec "$APP_DIR/venv/bin/python" "$APP_DIR/trilex.py" "\$@"
LAUNCH
chmod +x "$BIN_DIR/trilex"

cat > "$DESKTOP_DIR/trilex.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=Thriauga
GenericName=Trilingual Dictionary
Comment=Offline English / Svenska / Chinese dictionary
Exec=$BIN_DIR/trilex
Icon=$APP_DIR/trilex.png
Terminal=false
Categories=Education;Dictionary;Languages;
Keywords=dictionary;swedish;chinese;english;translate;
DESK
update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true

echo "[5/5] Done."
echo
echo "Start it from your applications menu, or run:  trilex"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo
     echo "NOTE: $BIN_DIR is not on your PATH. Add this to ~/.bashrc:"
     echo "      export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac
echo
if [ "$BUNDLED" = 1 ]; then
    echo "The dictionary, images and audio are installed. Nothing more to download."
else
    echo "On first launch it downloads the dictionary (about 212 MB)."
fi
"""

UNINSTALL_SH = r"""#!/usr/bin/env bash
# Removes the application. Your wordbook is kept unless you pass --all.
set -euo pipefail
APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/trilex"
rm -f "$HOME/.local/bin/trilex" "$HOME/.local/share/applications/trilex.desktop"
if [ "${1:-}" = "--all" ]; then
    rm -rf "$APP_DIR"; echo "Removed everything, including your wordbook."
else
    rm -rf "$APP_DIR/venv" "$APP_DIR/trilex" "$APP_DIR/trilex.py"
    echo "Removed the application."
    echo "Kept your wordbook and dictionary in $APP_DIR"
    echo "Delete them too with: $0 --all"
fi
"""

INSTALL_BAT = r"""@echo off
setlocal enabledelayedexpansion
title Thriauga installer

echo Thriauga installer
echo.

set "APP_DIR=%LOCALAPPDATA%\Thriauga"
set "SRC=%~dp0"

REM --- 1. Python -------------------------------------------------------
set "PY="
py -3 -c "import sys;raise SystemExit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if not defined PY (
    python -c "import sys;raise SystemExit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
    if not errorlevel 1 set "PY=python"
)
if not defined PY (
    echo ERROR: Python 3.10 or newer is required but was not found.
    echo.
    echo Install it with:      winget install Python.Python.3.12
    echo Or download it from:  https://www.python.org/downloads/
    echo.
    echo IMPORTANT: tick "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)
for /f "tokens=*" %%v in ('%PY% -V') do echo [1/5] Using %%v

REM --- 2. Application + virtualenv -------------------------------------
echo [2/5] Installing to %APP_DIR%
if not exist "%APP_DIR%" mkdir "%APP_DIR%"
xcopy /E /I /Y /Q "%SRC%trilex" "%APP_DIR%\trilex" >nul
copy /Y "%SRC%trilex.py" "%APP_DIR%\" >nul
copy /Y "%SRC%requirements.txt" "%APP_DIR%\" >nul
if exist "%SRC%README.md" copy /Y "%SRC%README.md" "%APP_DIR%\" >nul
if exist "%SRC%trilex.ico" copy /Y "%SRC%trilex.ico" "%APP_DIR%\" >nul

%PY% -m venv "%APP_DIR%\venv"
if errorlevel 1 (
    echo ERROR: could not create the virtual environment.
    echo If Thriauga is running, close it and run this installer again.
    pause
    exit /b 1
)

echo [3/5] Installing dependencies ^(this downloads ~80 MB^)
"%APP_DIR%\venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
"%APP_DIR%\venv\Scripts\python.exe" -m pip install --quiet -r "%APP_DIR%\requirements.txt"
if errorlevel 1 ( echo ERROR: dependency installation failed. & pause & exit /b 1 )

REM --- 3. Bundled dictionary + media (full package only) ---------------
set "DATA_DIR=%APPDATA%\trilex"
if defined TRILEX_DATA set "DATA_DIR=%TRILEX_DATA%"
set "BUNDLED=0"
if exist "%SRC%data\" (
    echo [4/5] Installing bundled dictionary and media to %DATA_DIR%
    if not exist "%DATA_DIR%" mkdir "%DATA_DIR%"
    if exist "%SRC%data\media-images.db" copy /Y "%SRC%data\media-images.db" "%DATA_DIR%\" >nul
    if exist "%SRC%data\media-audio.db" copy /Y "%SRC%data\media-audio.db" "%DATA_DIR%\" >nul
    if exist "%SRC%data\dict.db.xz" (
        echo       unpacking dict.db ^(this takes a minute^)
        "%APP_DIR%\venv\Scripts\python.exe" -c "import lzma,os,shutil,sys;s,d=sys.argv[1],sys.argv[2];t=d+'.part';i=lzma.open(s,'rb');o=open(t,'wb');shutil.copyfileobj(i,o,1<<20);i.close();o.close();os.replace(t,d)" "%SRC%data\dict.db.xz" "%DATA_DIR%\dict.db"
        if errorlevel 1 ( echo ERROR: could not unpack the dictionary. & pause & exit /b 1 )
    )
    set "BUNDLED=1"
) else (
    echo [4/5] No bundled data; the dictionary is downloaded on first launch
)

REM --- 4. Launcher + Start Menu shortcut -------------------------------
> "%APP_DIR%\Thriauga.cmd" echo @echo off
>> "%APP_DIR%\Thriauga.cmd" echo start "" "%APP_DIR%\venv\Scripts\pythonw.exe" "%APP_DIR%\trilex.py" %%*

set "SM=%APPDATA%\Microsoft\Windows\Start Menu\Programs"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$s=(New-Object -COM WScript.Shell).CreateShortcut('%SM%\Thriauga.lnk');" ^
  "$s.TargetPath='%APP_DIR%\venv\Scripts\pythonw.exe';" ^
  "$s.Arguments='\"%APP_DIR%\trilex.py\"';" ^
  "$s.WorkingDirectory='%APP_DIR%';" ^
  "$s.Description='Offline English / Svenska / Chinese dictionary';" ^
  "if (Test-Path '%APP_DIR%\trilex.ico') { $s.IconLocation='%APP_DIR%\trilex.ico' };" ^
  "$s.Save()" >nul 2>&1

echo [5/5] Done.
echo.
echo Thriauga has been added to your Start Menu.
if "%BUNDLED%"=="1" (
    echo The dictionary, images and audio are installed. Nothing more to download.
) else (
    echo On first launch it downloads the dictionary ^(about 212 MB^).
)
echo.
pause
"""

BUILD_EXE_BAT = r"""@echo off
REM Optional: build a standalone Thriauga.exe that needs no Python installed.
REM Run this ON WINDOWS after install.bat. It cannot be run from Linux,
REM because PyInstaller must execute on the platform it targets.
setlocal
set "APP_DIR=%LOCALAPPDATA%\Thriauga"
if not exist "%APP_DIR%\venv" ( echo Run install.bat first. & pause & exit /b 1 )
"%APP_DIR%\venv\Scripts\python.exe" -m pip install --quiet pyinstaller
pushd "%APP_DIR%"
"%APP_DIR%\venv\Scripts\pyinstaller.exe" --noconfirm --windowed --name Thriauga ^
    --collect-all PySide6 --hidden-import rapidfuzz ^
    %IF_ICON% trilex.py
popd
echo.
echo Built: %APP_DIR%\dist\Thriauga\Thriauga.exe
pause
"""


def make_icon():
    """Render the app icon to PNG (and ICO) without needing Qt at build time.

    Rendering needs Pillow and a CJK font. When either is missing (a Windows
    build box, say) reuse icons left by an earlier build rather than shipping
    a blank one.
    """
    png, ico = OUT / "trilex.png", OUT / "trilex.ico"
    have_font = any(os.path.exists(p) for p in CJK_FONT_PATHS)
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        have_font = False
    if not have_font:
        return (png if png.exists() else None), (ico if ico.exists() else None)
    size = 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([8, 8, size - 8, size - 8], radius=52, fill="#1a5fb4")
    font = None
    for path in CJK_FONT_PATHS:
        if os.path.exists(path):
            try:
                font = ImageFont.truetype(path, 132)
                break
            except Exception:
                pass
    if font:
        d.text((size / 2, size / 2 - 8), "文", font=font, fill="white", anchor="mm")
    img.save(png)
    img.save(ico, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    return png, ico


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while b := fh.read(1 << 20):
            h.update(b)
    return h.hexdigest()


def build_linux(png, data_files, suffix=""):
    tgz = OUT / f"thriauga-{VERSION}-linux{suffix}.tar.gz"
    tgz.unlink(missing_ok=True)
    base = f"thriauga-{VERSION}"
    # Level 1: the payload of a full archive is xz and media blobs, which gzip
    # cannot shrink; higher levels only cost minutes.
    with tarfile.open(tgz, "w:gz", compresslevel=1 if data_files else 9) as tar:
        for rel in source_files():
            tar.add(ROOT / rel, arcname=f"{base}/{rel}")
        for name, text, mode in (("install.sh", INSTALL_SH, 0o755),
                                 ("uninstall.sh", UNINSTALL_SH, 0o755)):
            data = text.encode()
            info = tarfile.TarInfo(f"{base}/{name}")
            info.size, info.mode, info.mtime = len(data), mode, int(time.time())
            tar.addfile(info, io.BytesIO(data))
        if png:
            tar.add(png, arcname=f"{base}/trilex.png")
        for f in data_files:
            tar.add(f, arcname=f"{base}/data/{f.name}")
    return tgz


def build_windows(ico, data_files, suffix=""):
    zpath = OUT / f"thriauga-{VERSION}-windows{suffix}.zip"
    zpath.unlink(missing_ok=True)
    base = f"thriauga-{VERSION}"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in source_files():
            z.write(ROOT / rel, f"{base}/{rel}")
        z.writestr(f"{base}/install.bat", INSTALL_BAT.replace("\n", "\r\n"))
        z.writestr(f"{base}/build-exe.bat", BUILD_EXE_BAT.replace("\n", "\r\n"))
        if ico:
            z.write(ico, f"{base}/trilex.ico")
        for f in data_files:
            z.write(f, f"{base}/data/{f.name}", compress_type=zipfile.ZIP_STORED)
    return zpath


def build():
    OUT.mkdir(parents=True, exist_ok=True)
    png, ico = make_icon()
    artifacts = [("linux", build_linux(png, [])),
                 ("windows", build_windows(ico, []))]

    data_files = [PACKS / f for f in DATA_FILES]
    missing = [f.name for f in data_files if not f.exists()]
    if missing:
        print(f"  (no full archives: missing {', '.join(missing)} in {PACKS})")
    else:
        artifacts.append(("linux-full", build_linux(png, data_files, "-full")))
        artifacts.append(("windows-full", build_windows(ico, data_files, "-full")))

    index = {"version": VERSION, "built": time.strftime("%Y-%m-%d"), "files": {}}
    for label, path in artifacts:
        size = path.stat().st_size
        index["files"][label] = {"file": path.name, "bytes": size,
                                 "sha256": sha256(path)}
        human = f"{size/1048576:7.1f} MB" if size > 1 << 20 else f"{size/1024:7.1f} KB"
        print(f"  {label:13} {path.name:43} {human}")
    (OUT / "installers.json").write_text(json.dumps(index, indent=2))
    return index


if __name__ == "__main__":
    build()
