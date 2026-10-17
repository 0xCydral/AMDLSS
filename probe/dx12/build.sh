#!/usr/bin/env bash
# Builds out/amdlss_dx12_probe.addon64 (a PE32+ DLL) with clang-cl and the MSVC ABI inside Docker; nothing is
# installed on the host. MinGW is not usable: ReShade is built with MSVC, and the two ABIs swap `this` and the
# hidden return pointer for member functions that return a struct by value (get_resource_desc and friends).
# Run: bash probe/dx12/build.sh   (needs docker and git; the first run downloads the MSVC CRT and Windows SDK)
set -euo pipefail

RESHADE_TAG=v6.8.0   # the runtime under test is ReShade 6.8.0.1; never use headers newer than the runtime
XWIN_VERSION=0.10.0
XWIN_SHA256=d870eb4b2f390878af6da1ccd3cf321d22fcb72720984853b4be732ae597fc88
HERE=$(cd "$(dirname "$0")" && pwd)
HEADERS="$HERE/third_party/reshade"

if [ ! -f "$HEADERS/include/reshade.hpp" ]; then
    tmp=$(mktemp -d)
    git clone --quiet --depth 1 --branch "$RESHADE_TAG" https://github.com/crosire/reshade.git "$tmp"
    mkdir -p "$HEADERS"
    cp -r "$tmp/include" "$HEADERS/include"
    rm -rf "$tmp"
fi

mkdir -p "$HERE/out" "$HERE/third_party/xwin"
docker run --rm -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" -e XWIN_VERSION="$XWIN_VERSION" -e XWIN_SHA256="$XWIN_SHA256" \
    -v "$HERE":/work -w /work ubuntu:24.04 bash -euo pipefail -c '
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends clang lld llvm curl ca-certificates >/dev/null

    # MSVC CRT + Windows SDK, cached in third_party/xwin (gitignored) so only the first build downloads them.
    if [ ! -d third_party/xwin/splat/crt ]; then
        curl -fsSL -o /tmp/xwin.tgz "https://github.com/Jake-Shadle/xwin/releases/download/$XWIN_VERSION/xwin-$XWIN_VERSION-x86_64-unknown-linux-musl.tar.gz"
        echo "$XWIN_SHA256  /tmp/xwin.tgz" | sha256sum -c -
        tar -xzf /tmp/xwin.tgz -C /tmp
        /tmp/xwin-$XWIN_VERSION-x86_64-unknown-linux-musl/xwin --accept-license --cache-dir /tmp/xwin-cache splat --output third_party/xwin/splat
    fi

    X=/work/third_party/xwin/splat
    # Ubuntu 24.04 ships clang 18 and the current MSVC STL asks for 19; the subset used here compiles on 18.
    # /MT: static CRT, so the DLL imports only kernel32/user32 and not vcruntime/ucrt.
    clang --driver-mode=cl --target=x86_64-pc-windows-msvc -fuse-ld=lld -std:c++17 -D_ALLOW_COMPILER_AND_STL_VERSION_MISMATCH -D_CRT_SECURE_NO_WARNINGS /MT /O2 /W3 /LD \
        /imsvc $X/crt/include /imsvc $X/sdk/include/ucrt /imsvc $X/sdk/include/um /imsvc $X/sdk/include/shared \
        /imsvc /work/third_party/reshade/include \
        amdlss_dx12_probe.cpp /Feout/amdlss_dx12_probe.addon64 \
        /link /LIBPATH:$X/crt/lib/x86_64 /LIBPATH:$X/sdk/lib/um/x86_64 /LIBPATH:$X/sdk/lib/ucrt/x86_64 kernel32.lib user32.lib
    rm -f out/amdlss_dx12_probe.lib out/amdlss_dx12_probe.exp
    chown -R "$HOST_UID:$HOST_GID" out third_party/xwin
'
ls -l "$HERE/out/amdlss_dx12_probe.addon64"
