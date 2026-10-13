#!/usr/bin/env bash
# Builds dist/AMDLSS-linux-x86_64: AMDLSS.pyw frozen with PyInstaller (one file, windowed) from a uv-managed
# Python 3.12, so players need no Python. Run from anywhere:
#     bash packaging/build-linux.sh
# It also prints the SHA-256 of the binary; publish it beside the release. Nothing of HelixSR or NVIDIA goes in it.
set -euo pipefail
cd -- "$(dirname -- "$0")/.."

# Pillow decodes the game covers (Tk cannot read JPEG); the binary needs it and its Tk bridge.
uv run --no-project --managed-python --python 3.12 --with pyinstaller --with pillow python -m PyInstaller \
    --noconfirm --clean --log-level WARN --windowed --onefile \
    --name AMDLSS-linux-x86_64 --add-data "$PWD/amdlss.ico:." \
    --hidden-import PIL.ImageTk --distpath dist --workpath out/pyi-work-linux --specpath out/pyi-work-linux \
    --paths "$PWD" "$PWD/AMDLSS.pyw"
sha256sum dist/AMDLSS-linux-x86_64
