# SPDX-License-Identifier: GPL-2.0-or-later
"""A stand-in for the unpacked Luma UE zip, so tests run AMDLSS's injected route with no network and no 37 MB
download. make() writes <root>/Luma-Unreal-Engine-749, exactly where luma.locate() looks, with the files the real
zip holds (its ReShade dxgi.dll, its addon, the NVIDIA DLSS runtime it calls, its HLSL compiler and its shaders);
the shader tree is cut to two files, so a test can still see that the whole tree is copied and where."""
from pathlib import Path

from amdlss import luma

TOP = luma.TOP
FILES = (luma.SCALER_NAME, luma.ADDON, luma.DLSS, f'{luma.SHADERS}/d3dcompiler_47.dll',
         f'{luma.SHADERS}/Unreal Engine/Luma_MotionVec_UE4_Decode.hlsl',
         f'{luma.SHADERS}/Global/Luma_Copy_PS.hlsl')


def make(root, files=FILES):
    """The unpacked package under `root` (root/Luma-Unreal-Engine-749). Returns its folder."""
    folder = Path(root) / TOP
    for rel in files:
        path = folder / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f'STAND-IN {rel}'.encode())
    return folder
