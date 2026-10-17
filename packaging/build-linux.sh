#!/usr/bin/env bash
# Builds dist/AMDLSS-linux-x86_64: AMDLSS.pyw frozen with PyInstaller (one file, windowed) from a uv-managed
# Python 3.12, so players need no Python. Run from anywhere:
#     bash packaging/build-linux.sh
# It also prints the SHA-256 of the binary; publish it beside the release. Nothing of HelixSR goes in it, and no NVIDIA
# DLL: the one NVIDIA-related file is AMDLSS's own DirectX 12 DLSS add-on, which probe/dx12/dlss/build.sh builds (the
# release script runs it first; run it yourself before this one) and which is bundled as dx12dlss/ next to amdlss.ico.
set -euo pipefail
cd -- "$(dirname -- "$0")/.."
ADDON=probe/dx12/dlss/out/amdlss_dx12_dlss.addon64
[ -f "$ADDON" ] || { echo "missing $ADDON: run 'bash probe/dx12/dlss/build.sh' first" >&2; exit 1; }

# Pillow decodes the game covers (Tk cannot read JPEG); the binary needs it and its Tk bridge.
uv run --no-project --managed-python --python 3.12 --with pyinstaller --with pillow python -m PyInstaller \
    --noconfirm --clean --log-level WARN --windowed --onefile \
    --name AMDLSS-linux-x86_64 --add-data "$PWD/amdlss.ico:." \
    --add-data "$PWD/$ADDON:dx12dlss" --add-data "$PWD/probe/dx12/dlss/amdlss_dx12_dlss.ini:dx12dlss" \
    --hidden-import PIL.ImageTk --hidden-import PIL._tkinter_finder --distpath dist --workpath out/pyi-work-linux --specpath out/pyi-work-linux \
    --paths "$PWD" "$PWD/AMDLSS.pyw"
sha256sum dist/AMDLSS-linux-x86_64
