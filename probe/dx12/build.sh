#!/usr/bin/env bash
# Builds out/amdlss_dx12_probe.addon64 (a PE32+ DLL) with MinGW-w64 inside Docker; nothing is installed on the host.
# Run: bash probe/dx12/build.sh   (needs docker and git; fetches the ReShade headers once)
set -euo pipefail

RESHADE_TAG=v6.8.0   # the runtime under test is ReShade 6.8.0.1; never use headers newer than the runtime
HERE=$(cd "$(dirname "$0")" && pwd)
HEADERS="$HERE/third_party/reshade"

if [ ! -f "$HEADERS/include/reshade.hpp" ]; then
    tmp=$(mktemp -d)
    git clone --quiet --depth 1 --branch "$RESHADE_TAG" https://github.com/crosire/reshade.git "$tmp"
    mkdir -p "$HEADERS"
    cp -r "$tmp/include" "$HEADERS/include"
    rm -rf "$tmp"
fi

mkdir -p "$HERE/out"
docker run --rm -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" -v "$HERE":/work -w /work ubuntu:24.04 bash -euo pipefail -c '
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends g++-mingw-w64-x86-64 >/dev/null
    # reshade.hpp includes <Windows.h>; MinGW ships windows.h and Linux paths are case sensitive.
    mkdir -p /tmp/shim && echo "#include <windows.h>" > /tmp/shim/Windows.h
    # posix thread model for std::mutex; -static folds libwinpthread, libstdc++ and libgcc into the DLL.
    x86_64-w64-mingw32-g++-posix -std=c++17 -O2 -s -Wall -Wextra -shared -static -static-libgcc -static-libstdc++ \
        -I/tmp/shim -isystem third_party/reshade/include amdlss_dx12_probe.cpp -o out/amdlss_dx12_probe.addon64
    chown -R "$HOST_UID:$HOST_GID" out
'
ls -l "$HERE/out/amdlss_dx12_probe.addon64"
