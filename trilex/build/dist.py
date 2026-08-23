"""Build the Linux and Windows installer archives.

The archives are deliberately tiny (~100 KB): they carry only the application
source and an install script. Dependencies come from PyPI and the dictionary
is downloaded on first run, which keeps the download page honest and means a
dictionary update does not require a new installer.

A native Windows .exe cannot be produced on Linux - PyInstaller must run on
the target platform - so the Windows archive ships a build script that makes
one on a Windows machine instead of pretending otherwise.
"""
import hashlib, io, json, os, shutil, sys, tarfile, time, zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
OUT = DATA / "dist"
VERSION = "1.0.0"

# Globbed, not hand-listed. A hardcoded list silently omitted theme.py when
# it was added, which would have shipped an app that crashed on import.
# Build-time modules stay out of the release.
BUILD_ONLY = {"build"}


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
echo "[1/4] Using $($PY -V)"

# --- 2. Application + virtualenv ---------------------------------------
echo "[2/4] Installing to $APP_DIR"
mkdir -p "$APP_DIR"
cp -r "$SRC/trilex" "$SRC/trilex.py" "$SRC/requirements.txt" "$APP_DIR/"
[ -f "$SRC/README.md" ] && cp "$SRC/README.md" "$APP_DIR/"
[ -f "$SRC/trilex.png" ] && cp "$SRC/trilex.png" "$APP_DIR/"

if ! "$PY" -m venv --help >/dev/null 2>&1; then
    echo "ERROR: the venv module is missing. Install python3-venv and retry."
    exit 1
fi
"$PY" -m venv "$APP_DIR/venv"
echo "[3/4] Installing dependencies (this downloads ~80 MB)"
"$APP_DIR/venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

# --- 3. Launcher + menu entry ------------------------------------------
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

echo "[4/4] Done."
echo
echo "Start it from your applications menu, or run:  trilex"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo
     echo "NOTE: $BIN_DIR is not on your PATH. Add this to ~/.bashrc:"
     echo "      export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac
echo
echo "On first launch it downloads the dictionary (about 212 MB)."
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
for /f "tokens=*" %%v in ('%PY% -V') do echo [1/4] Using %%v

REM --- 2. Application + virtualenv -------------------------------------
echo [2/4] Installing to %APP_DIR%
if not exist "%APP_DIR%" mkdir "%APP_DIR%"
xcopy /E /I /Y /Q "%SRC%trilex" "%APP_DIR%\trilex" >nul
copy /Y "%SRC%trilex.py" "%APP_DIR%\" >nul
copy /Y "%SRC%requirements.txt" "%APP_DIR%\" >nul
if exist "%SRC%README.md" copy /Y "%SRC%README.md" "%APP_DIR%\" >nul
if exist "%SRC%trilex.ico" copy /Y "%SRC%trilex.ico" "%APP_DIR%\" >nul

%PY% -m venv "%APP_DIR%\venv"
if errorlevel 1 ( echo ERROR: could not create the virtual environment. & pause & exit /b 1 )

echo [3/4] Installing dependencies ^(this downloads ~80 MB^)
"%APP_DIR%\venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
"%APP_DIR%\venv\Scripts\python.exe" -m pip install --quiet -r "%APP_DIR%\requirements.txt"
if errorlevel 1 ( echo ERROR: dependency installation failed. & pause & exit /b 1 )

REM --- 3. Launcher + Start Menu shortcut -------------------------------
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

echo [4/4] Done.
echo.
echo Thriauga has been added to your Start Menu.
echo On first launch it downloads the dictionary ^(about 212 MB^).
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
    """Render the app icon to PNG (and ICO) without needing Qt at build time."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None, None
    size = 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([8, 8, size - 8, size - 8], radius=52, fill="#1a5fb4")
    font = None
    for path in ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
                 "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
                 "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"):
        if os.path.exists(path):
            try:
                font = ImageFont.truetype(path, 132)
                break
            except Exception:
                pass
    if font:
        d.text((size / 2, size / 2 - 8), "文", font=font, fill="white", anchor="mm")
    png = OUT / "trilex.png"
    img.save(png)
    ico = OUT / "trilex.ico"
    img.save(ico, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    return png, ico


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while b := fh.read(1 << 20):
            h.update(b)
    return h.hexdigest()


def build():
    OUT.mkdir(parents=True, exist_ok=True)
    png, ico = make_icon()
    artifacts = []

    # ---- Linux tar.gz ----
    tgz = OUT / f"thriauga-{VERSION}-linux.tar.gz"
    tgz.unlink(missing_ok=True)
    with tarfile.open(tgz, "w:gz") as tar:
        base = f"thriauga-{VERSION}"
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
    artifacts.append(("linux", tgz))

    # ---- Windows zip ----
    zpath = OUT / f"thriauga-{VERSION}-windows.zip"
    zpath.unlink(missing_ok=True)
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        base = f"thriauga-{VERSION}"
        for rel in source_files():
            z.write(ROOT / rel, f"{base}/{rel}")
        z.writestr(f"{base}/install.bat", INSTALL_BAT.replace("\n", "\r\n"))
        z.writestr(f"{base}/build-exe.bat", BUILD_EXE_BAT.replace("\n", "\r\n"))
        if ico:
            z.write(ico, f"{base}/trilex.ico")
    artifacts.append(("windows", zpath))

    index = {"version": VERSION, "built": time.strftime("%Y-%m-%d"), "files": {}}
    for label, path in artifacts:
        index["files"][label] = {"file": path.name, "bytes": path.stat().st_size,
                                 "sha256": sha256(path)}
        print(f"  {label:8} {path.name:38} {path.stat().st_size/1024:7.1f} KB")
    (OUT / "installers.json").write_text(json.dumps(index, indent=2))
    return index


if __name__ == "__main__":
    build()
