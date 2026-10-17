#!/usr/bin/env bash
# Builds and checks both AMDLSS release assets into /tmp/amdlss-rel/dist. Developer machine only (amdlss-wine, the
# Windows Python under Wine, Xvfb). Run: bash packaging/release.sh > /tmp/amdlss-rel/build.log 2>&1
set -u
R=/tmp/amdlss-rel
cd "$(dirname "$0")/.." || exit 1
SRC="$PWD"                 # the checkout this script lives in (a worktree included), never a fixed path
ZSRC="Z:${SRC//\//\\}"     # the same path the way the Wine Python reads it (Z:\home\...)
mkdir -p "$R"
mountpoint -q /media/cydral/Data || udisksctl mount -b /dev/nvme1n1p2
rm -rf "$R/dist" "$R/work"
echo "== dx12 add-on"   # both binaries bundle it (dx12dlss\ inside), so a release without it is not a release
bash probe/dx12/dlss/build.sh > "$R/dlss-build.log" 2>&1
DLSS_RC=$?
echo "dlss add-on build exit=$DLSS_RC"
if [ "$DLSS_RC" -ne 0 ]; then
    tail -n 20 "$R/dlss-build.log"
    echo "release stopped: the DirectX 12 add-on did not build (log: $R/dlss-build.log)"
    exit 1
fi
echo "== windows exe"
timeout 900 amdlss-wine 'C:\Python312\python.exe' -m PyInstaller --noconfirm --clean --log-level WARN --windowed \
    --onefile --name AMDLSS --icon "$ZSRC/amdlss.ico" \
    --add-data "$ZSRC/amdlss.ico;." \
    --add-data "$ZSRC/probe/dx12/dlss/out/amdlss_dx12_dlss.addon64;dx12dlss" \
    --add-data "$ZSRC/probe/dx12/dlss/amdlss_dx12_dlss.ini;dx12dlss" --hidden-import PIL.ImageTk \
    --hidden-import PIL._tkinter_finder --distpath 'Z:\tmp\amdlss-rel\dist' --workpath 'Z:\tmp\amdlss-rel\work' \
    --specpath 'Z:\tmp\amdlss-rel\work' --paths "$ZSRC" \
    "$ZSRC/AMDLSS.pyw" > "$R/pyi-win.log" 2>&1
echo "pyinstaller-win exit=$?"
echo "== linux binary"
bash packaging/build-linux.sh > "$R/pyi-linux.log" 2>&1
echo "build-linux exit=$?"
cp dist/AMDLSS-linux-x86_64 "$R/dist/"
echo "== checks"
timeout 300 amdlss-wine 'C:\Python312\python.exe' "$ZSRC/packaging/check_build.py" 'Z:\tmp\amdlss-rel\dist\AMDLSS.exe' 2>&1 | grep -v ntsync
uv run --no-project -q --managed-python --python 3.12 --with pyinstaller python packaging/check_build.py "$R/dist/AMDLSS-linux-x86_64"
Xvfb :74 -screen 0 1280x900x24 > /dev/null 2>&1 &
XP=$!
sleep 1
DISPLAY=:74 timeout 300 amdlss-wine 'Z:\tmp\amdlss-rel\dist\AMDLSS.exe' --selftest > "$R/st-exe.log" 2>&1
echo "exe selftest exit=$?"
DISPLAY=:74 timeout 120 "$R/dist/AMDLSS-linux-x86_64" --selftest > "$R/st-linux.log" 2>&1
echo "linux selftest exit=$?"
kill "$XP"
sha256sum "$R"/dist/*
echo "== done"
