#!/usr/bin/env bash
# Disassembles a DXIL file with the pinned dxc inside the build container (nothing is installed on the host).
# Run: bash probe/dx12/dlss/disasm.sh <file.dxil> [out.txt]   (out.txt defaults to the input name with .txt)
#
# Use it on tsr_1ff10c3ce95d3bcb.dxil, which the DLSS add-on writes next to itself the first time the game creates
# the TSR shader, to read how UE decodes velocity (u6) and uses the b0 jitter.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=dxc.env
. "$HERE/dxc.env"

[ $# -ge 1 ] && [ -f "$1" ] || { echo "usage: $0 <file.dxil> [out.txt]" >&2; exit 2; }
IN=$(realpath "$1")
OUT=${2:-${IN%.*}.txt}

mkdir -p "$HERE/third_party/dxc"   # downloaded dxc, cached between runs (gitignored)
docker run --rm -e DXC_URL="$DXC_URL" -e DXC_SHA256="$DXC_SHA256" -e DXIL_NAME="$(basename "$IN")" \
    -v "$HERE/third_party/dxc":/opt/dxc -v "$(dirname "$IN")":/in:ro ubuntu:24.04 bash -euo pipefail -c '
    if [ ! -x /opt/dxc/bin/dxc ]; then
        apt-get update -qq >&2
        DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends curl ca-certificates >&2
        curl -fsSL -o /tmp/dxc.tgz "$DXC_URL"
        echo "$DXC_SHA256  /tmp/dxc.tgz" | sha256sum -c - >&2
        tar -xzf /tmp/dxc.tgz -C /opt/dxc
    fi
    LD_LIBRARY_PATH=/opt/dxc/lib /opt/dxc/bin/dxc -dumpbin "/in/$DXIL_NAME"
' > "$OUT"
echo "wrote $OUT ($(wc -l < "$OUT") lines)"
