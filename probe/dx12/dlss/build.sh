#!/usr/bin/env bash
# Builds out/amdlss_dx12_dlss.addon64 and out/nvngx_dlss.dll (stage 3b: the DLSS replacement add-on).
# Same MSVC-ABI clang-cl + xwin toolchain as the probe, inside Docker; nothing is installed on the host.
# Run: bash probe/dx12/dlss/build.sh   (needs docker and git)
#
# Steps: fetch the NGX SDK and the Dear ImGui headers at pinned tags, compile velocity_decode.hlsl to DXIL with a pinned dxc and embed it,
# run selftest.cpp (the pure logic) on the Linux host of the container, then compile and link the add-on.
set -euo pipefail

NGX_TAG=v310.9.16
NGX_COMMIT=9981c6350dc58c0ce21f024fd65e31e851c31689
NGX_DLL_SHA256=d00b5b0ab29c3ca101b70c3a5ecb3a01b7ef33766a3383a28a18623083e6123e
NGX_LIB_SHA256=7b9a5239c1145af5f645ab472aa066f9ea79db36ac819562312aae69d65f3765

IMGUI_TAG=v1.92.5-docking   # the version reshade_overlay.hpp of ReShade 6.8.0 requires (IMGUI_VERSION_NUM 19250)
IMGUI_COMMIT=3912b3d9a9c1b3f17431aebafd86d2f40ee6e59c
IMGUI_H_SHA256=07562842049eaca4e47c3fcdcc9d8eafd442695b9856d6f6639e92e1bb6c737e
IMGUI_CONFIG_SHA256=6e5687893594ebfaf8569280cfea83d025904a8a4e23bec073086f6c5bc8f7fb

HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/dxc.env"
PROBE=$(cd "$HERE/.." && pwd)
NGX="$HERE/third_party/ngx"
IMGUI="$PROBE/third_party/imgui"

# The ReShade headers and the MSVC CRT / Windows SDK splat are fetched by the probe build; run it once if missing.
if [ ! -f "$PROBE/third_party/reshade/include/reshade.hpp" ] || [ ! -d "$PROBE/third_party/xwin/splat/crt" ]; then
    bash "$PROBE/build.sh"
fi

if [ ! -f "$NGX/lib/nvsdk_ngx_s.lib" ] || [ ! -f "$NGX/lib/nvngx_dlss.dll" ]; then
    tmp=$(mktemp -d)
    git clone --quiet --depth 1 --branch "$NGX_TAG" https://github.com/NVIDIA/DLSS.git "$tmp"
    [ "$(git -C "$tmp" rev-parse HEAD)" = "$NGX_COMMIT" ] || { echo "NVIDIA/DLSS $NGX_TAG is not commit $NGX_COMMIT" >&2; exit 1; }
    echo "$NGX_DLL_SHA256  $tmp/lib/Windows_x86_64/rel/nvngx_dlss.dll" | sha256sum -c -
    echo "$NGX_LIB_SHA256  $tmp/lib/Windows_x86_64/x64/nvsdk_ngx_s.lib" | sha256sum -c -
    mkdir -p "$NGX/lib"
    cp -r "$tmp/include" "$NGX/include"
    cp "$tmp/lib/Windows_x86_64/x64/nvsdk_ngx_s.lib" "$tmp/lib/Windows_x86_64/rel/nvngx_dlss.dll" "$NGX/lib/"
    cp "$tmp/LICENSE.txt" "$NGX/LICENSE.txt"
    rm -rf "$tmp"
fi

# Only imgui.h and imconfig.h are needed: the overlay widgets are called through ReShade's own ImGui function table.
if [ ! -f "$IMGUI/imgui.h" ] || [ ! -f "$IMGUI/imconfig.h" ]; then
    tmp=$(mktemp -d)
    git clone --quiet --depth 1 --branch "$IMGUI_TAG" https://github.com/ocornut/imgui.git "$tmp"
    [ "$(git -C "$tmp" rev-parse HEAD)" = "$IMGUI_COMMIT" ] || { echo "ocornut/imgui $IMGUI_TAG is not commit $IMGUI_COMMIT" >&2; exit 1; }
    echo "$IMGUI_H_SHA256  $tmp/imgui.h" | sha256sum -c -
    echo "$IMGUI_CONFIG_SHA256  $tmp/imconfig.h" | sha256sum -c -
    mkdir -p "$IMGUI"
    cp "$tmp/imgui.h" "$tmp/imconfig.h" "$tmp/LICENSE.txt" "$IMGUI/"
    rm -rf "$tmp"
fi

mkdir -p "$HERE/out"
docker run --rm -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" -e DXC_URL="$DXC_URL" -e DXC_SHA256="$DXC_SHA256" \
    -v "$PROBE":/probe -w /probe/dlss ubuntu:24.04 bash -euo pipefail -c '
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends clang lld llvm curl ca-certificates xxd >/dev/null

    mkdir -p /opt/dxc
    curl -fsSL -o /tmp/dxc.tgz "$DXC_URL"
    echo "$DXC_SHA256  /tmp/dxc.tgz" | sha256sum -c -
    tar -xzf /tmp/dxc.tgz -C /opt/dxc
    export LD_LIBRARY_PATH=/opt/dxc/lib
    /opt/dxc/bin/dxc --version
    /opt/dxc/bin/dxc -T cs_6_0 -E main -Fo out/velocity_decode.dxil velocity_decode.hlsl
    echo "velocity_decode.dxil: $(stat -c %s out/velocity_decode.dxil) bytes"
    xxd -i -n g_decode_dxil out/velocity_decode.dxil > out/decode_dxil.h
    /opt/dxc/bin/dxc -T cs_6_0 -E main -Fo out/half_fill.dxil half_fill.hlsl
    echo "half_fill.dxil: $(stat -c %s out/half_fill.dxil) bytes"
    xxd -i -n g_half_dxil out/half_fill.dxil > out/half_dxil.h

    clang++ -std=c++17 -Wall -Wextra selftest.cpp -o /tmp/selftest
    /tmp/selftest

    X=/probe/third_party/xwin/splat
    clang --driver-mode=cl --target=x86_64-pc-windows-msvc -fuse-ld=lld -std:c++17 -D_ALLOW_COMPILER_AND_STL_VERSION_MISMATCH -D_CRT_SECURE_NO_WARNINGS /MT /O2 /W3 /LD \
        /imsvc $X/crt/include /imsvc $X/sdk/include/ucrt /imsvc $X/sdk/include/um /imsvc $X/sdk/include/shared \
        /imsvc /probe/third_party/imgui /imsvc /probe/third_party/reshade/include /imsvc /probe/dlss/third_party/ngx/include /I/probe/dlss/out /I/probe/dlss \
        amdlss_dx12_dlss.cpp /Feout/amdlss_dx12_dlss.addon64 \
        /link /LIBPATH:$X/crt/lib/x86_64 /LIBPATH:$X/sdk/lib/um/x86_64 /LIBPATH:$X/sdk/lib/ucrt/x86_64 \
        /LIBPATH:/probe/dlss/third_party/ngx/lib nvsdk_ngx_s.lib kernel32.lib user32.lib advapi32.lib
    rm -f out/amdlss_dx12_dlss.lib out/amdlss_dx12_dlss.exp
    cp third_party/ngx/lib/nvngx_dlss.dll amdlss_dx12_dlss.ini out/
    chown -R "$HOST_UID:$HOST_GID" out
'
ls -l "$HERE/out/amdlss_dx12_dlss.addon64" "$HERE/out/nvngx_dlss.dll"
