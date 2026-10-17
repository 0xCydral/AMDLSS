#!/usr/bin/env bash
# Builds dist/AMDLSS.exe: AMDLSS.pyw frozen with PyInstaller (one file, windowed), so players need no Python.
# Same approach as packaging/windows/package.sh of the Bloodborne port: a private venv in out/pyenv holds PyInstaller,
# and a Windows Python 3.10+ (python.org; WINPYTHON overrides) makes it. Run from Git Bash or MSYS2:
#     bash packaging/build.sh
# It also prints the SHA-256 of the exe; publish it beside the release. Nothing of HelixSR goes in the exe, and no
# NVIDIA DLL: only AMDLSS's own DirectX 12 DLSS add-on, which probe/dx12/dlss/build.sh builds (needs Docker; run it
# first) and which is bundled as dx12dlss/ next to amdlss.ico.
set -euo pipefail
cd -- "$(dirname -- "$0")/.."
ADDON=probe/dx12/dlss/out/amdlss_dx12_dlss.addon64
[[ -f $ADDON ]] || { echo "missing $ADDON: run 'bash probe/dx12/dlss/build.sh' first" >&2; exit 1; }

python=${WINPYTHON:-}
if [[ -z $python ]]; then
    for candidate in /c/Python3*/python.exe "${LOCALAPPDATA:-/c/Users/$USER/AppData/Local}"/Programs/Python/Python3*/python.exe; do
        [[ -x $candidate ]] && python=$candidate
    done
fi
[[ -n $python ]] || { echo 'Need a Windows Python 3 (python.org) or WINPYTHON=path\to\python.exe' >&2; exit 1; }
export USERPROFILE=${USERPROFILE:-$(cygpath -w "/c/Users/$(id -un)")}
[[ -x out/pyenv/Scripts/python.exe ]] || "$python" -m venv out/pyenv
# Only when missing: pip asks PyPI on every call and hangs for minutes without a connection.
out/pyenv/Scripts/python.exe -c 'import PyInstaller' 2>/dev/null ||
    out/pyenv/Scripts/python.exe -m pip install -q --disable-pip-version-check pyinstaller
# Pillow decodes the game covers (Tk cannot read JPEG); the exe needs it and its Tk bridge.
out/pyenv/Scripts/python.exe -c 'import PIL.ImageTk' 2>/dev/null ||
    out/pyenv/Scripts/python.exe -m pip install -q --disable-pip-version-check pillow
out/pyenv/Scripts/python.exe -m PyInstaller --noconfirm --clean --log-level WARN --windowed --onefile \
    --name AMDLSS --icon "$(cygpath -w "$PWD/amdlss.ico")" --add-data "$(cygpath -w "$PWD/amdlss.ico");." \
    --add-data "$(cygpath -w "$PWD/$ADDON");dx12dlss" \
    --add-data "$(cygpath -w "$PWD/probe/dx12/dlss/amdlss_dx12_dlss.ini");dx12dlss" \
    --hidden-import PIL.ImageTk --hidden-import PIL._tkinter_finder --distpath dist --workpath out/pyi-work --specpath out/pyi-work \
    --paths "$(cygpath -w "$PWD")" "$(cygpath -w "$PWD/AMDLSS.pyw")"
sha256sum dist/AMDLSS.exe
