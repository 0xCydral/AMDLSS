#!/usr/bin/env bash
# Builds dist/AMDLSS.exe: AMDLSS.pyw frozen with PyInstaller (one file, windowed), so players need no Python.
# Same approach as packaging/windows/package.sh of the Bloodborne port: a private venv in out/pyenv holds PyInstaller,
# and a Windows Python 3.10+ (python.org; WINPYTHON overrides) makes it. Run from Git Bash or MSYS2:
#     bash packaging/build.sh
# It also prints the SHA-256 of the exe; publish it beside the release. Nothing of HelixSR or NVIDIA goes in the exe.
set -euo pipefail
cd -- "$(dirname -- "$0")/.."

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
out/pyenv/Scripts/python.exe -m PyInstaller --noconfirm --clean --log-level WARN --windowed --onefile \
    --name AMDLSS --icon "$(cygpath -w "$PWD/amdlss.ico")" --add-data "$(cygpath -w "$PWD/amdlss.ico");." \
    --distpath dist --workpath out/pyi-work --specpath out/pyi-work \
    --paths "$(cygpath -w "$PWD")" "$(cygpath -w "$PWD/AMDLSS.pyw")"
sha256sum dist/AMDLSS.exe
