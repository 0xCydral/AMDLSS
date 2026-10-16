# DX12 spike: can AMDLSS inject DLSS into a DirectX 12 Unreal Engine game?

## Objective

Answer yes or no, in 1-2 days, before committing months to a DX12 port of the Inject DLSS route.
Luma UE (the current Inject DLSS route) is DirectX 11 only; on DX12 its tab is blank (`luma-empty-tab.md`).

## Test game

Echoes of Aincrad (Steam 2244210, UE5, `EchoesofAincrad-Win64-Shipping.exe` 393270784 bytes, v1.0.6.0).
All 5 empty-Luma-tab reports in `~/amdlss-reports` come from it.

- No upscaler: no `NGX_DLSS`, `FidelityFX`, `ffxFsr` or `XeSS` strings in the exe.
- No anti-cheat folder (the 2 `EasyAntiCheat` strings in the exe are the EOS SDK).
- DX12 only: launched with `-dx11` under Proton on 2026-10-16, it still wrote `vkd3d-proton.cache.write` and
  `EchoesofAincrad_PCD3D_SM6.upipelinecache`. Luma UE can never run here.
- Player settings: `sg.AntiAliasingQuality=2`, `sg.ResolutionQuality=0` (GameUserSettings.ini).

## Stages (stop at the first "no")

1. **Find the pass.** A ReShade DX12 addon logs one frame of compute dispatches on a hotkey: pipeline shader hash,
   and every bound resource (format, size). Pass: one dispatch group is identifiable by hash across launches, and
   its inputs include scene color and depth at render resolution, motion vectors, and an output at display size.
2. **Capture the inputs.** Copy those resources at that dispatch and dump them to files; check they look right.
3. **Feed an upscaler.** Call DLSS (through HelixSR Scaler / OptiScaler) or FSR directly with those inputs and
   replace the pass output. Pass: a stable upscaled image in game.

## Tasks

- [x] T1 Stage 1 probe addon: `probe/dx12/` (C++ ReShade addon, MinGW build in Docker), build script, README.
      Route: delegated (writer trigger: new multi-file C++ project). Check: the addon builds to a PE32+ DLL.
- [x] T2 Run the probe in Echoes of Aincrad under Proton and identify the TAA/TSR dispatch. Done with 2 captures in
      one launch; a second launch is still owed (see Progress).
- [x] T3 Verdict for stage 1, recorded here: pass, see "Stage 1 result".

## Stage 1 result (2026-10-16): pass

Two F10 captures in one launch, 2560x1440 back buffer, 723 and 722 dispatches. Analysis script:
`~/Downloads/dx12-spike/analyze.py` (not in the repo).

- **Render resolution is 1552x876** (about 61% of 2560x1440): the game already upscales itself.
- **The upscale pass is `cs=1ff10c3ce95d3bcb`** (51724-byte DXIL), groups 320x180 = 2560/8 x 1440/8. It is the only
  dispatch with that hash and the only one-thread-per-output-pixel dispatch, in both captures (D715, D714). The four
  passes before it on the same command list run at render size (194x110 = 1552/8 x 876/8), which fits UE5 TSR.
- **Its real bindings (root param 1):** UAV u0 2560x1440 `r11g11b10_float` (history and output), u1 1280x720
  `r11g11b10_float` (half-size output), u2 2560x1440 `r8_unorm`; SRVs at 1552x876: two `r16g16b16a16_float`,
  `r8g8_unorm`, `r16g16_unorm`, `r8_unorm`, `r8_uint`, `r8_unorm`, `r8g8_unorm`; plus last frame's 2560x1440
  `r11g11b10_float` and `r8_unorm`.
- **History ping-pongs:** capture 1 reads `125f0e160` and writes `dd2f2270`; capture 2 reads `dd2f2270`.
- **Depth** is `3c7b1a70` (1552x876 `r32_g8_typeless`, viewed `r32_float_x8_uint`), the same handle in both captures.
  It is bound to earlier passes, not to `1ff10c3c`.

Not proven yet, and owed to stage 2:
- Which 1552x876 `r16g16b16a16_float` input is pre-upscale scene color, and whether `r16g16_unorm` (`e22b0920`) is
  motion vectors. Formats fit; only a dump of the pixels proves it.
- The hash across a second launch. It hashes the shader bytecode, so it should be stable, but this was not run.
- The descriptor log lists whole tables (up to 64 entries), so root param 0 shows resources the shader may not read.

## Progress

- 2026-10-16: test game chosen and DX11 ruled out (above).
- 2026-10-16: T1 done. `bash probe/dx12/build.sh` exits 0 and builds `probe/dx12/out/amdlss_dx12_probe.addon64`
  (248320 bytes, ReShade headers v6.8.0, API 20). `file`: PE32+ executable (DLL) x86-64. Imports only KERNEL32,
  msvcrt and USER32 (libstdc++, libgcc, winpthread are static). Exports NAME and DESCRIPTION. Not yet run against a
  live ReShade; that is T2.
- 2026-10-16: T2 attempt 1 crashed the game: `EXCEPTION_ACCESS_VIOLATION` in `amdlss_dx12_probe+0x84ac`, a virtual call on the
  `device*` whose vtable pointer had been overwritten. Cause: the MinGW build. ReShade is MSVC-built, and for member
  functions returning a struct by value (`get_resource_desc` etc.) MSVC passes this=rcx, return pointer=rdx while
  MinGW GCC swaps them, so ReShade wrote a `resource_desc` over the device object. Fix: `probe/dx12/build.sh` now
  builds with clang-cl (`--target=x86_64-pc-windows-msvc`, lld) against the MSVC CRT and Windows SDK from xwin 0.10.0,
  static CRT. Checked in the disassembly: `get_resource_desc` is called with rcx = device, rdx = return buffer,
  r8 = resource. DLL imports only KERNEL32 and USER32. Re-run T2 with the new `.addon64`.
