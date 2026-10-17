# DX12 DLSS add-on: measured performance and HelixSR compatibility

## Objective

Turn the DX12 DLSS add-on (`probe/dx12/dlss/`) into real DLSS for Echoes of Aincrad: image quality at least as good
as the game's TSR, a measured frame-rate gain over native rendering, and a layout that runs alongside HelixSR
(HelixSR Scaler, which is OptiScaler, owns `dxgi.dll` and loads ReShade as `ReShade64.dll`).

## Why

The add-on only swaps TSR for DLSS at the render size the game picks (sg.AntiAliasingQuality=3 gives 1552x873 for
2560x1440, ratio 0.606). Nothing has been measured: no frame times, no comparison with native.

## Facts (2026-10-16)

- Test machine: RTX 4070 (driver 595.104.02), Proton, vkd3d-proton. The title screen renders the 3D scene through
  TSR, so measurements need no player input.
- GameUserSettings.ini: `bUseVSync=False`, `FrameRateLimit=0`, `bUseDynamicResolution=False`.
- UE cvar priority: `[SystemSettings]` in the user Engine.ini outranks scalability groups, so
  `r.ScreenPercentage` there should override the AA level's render size (to be checked).

## Tasks

- [ ] T1 Frame-time stats in the add-on: every report window, log frames, average ms and fps, 1% low, the upscaler
      that ran (DLSS or TSR), render size and ratio; show the same in the DLSS tab. Pure stats in logic.hpp with a
      selftest. Route: delegated (writer trigger: C++ add-on + logic + selftest).
- [ ] T2 Check that `r.ScreenPercentage` in `Saved/Config/WindowsClient/Engine.ini` changes the render size the
      add-on logs. Route: inline (one launch).
- [ ] T3 A/B on the title screen, each about 60 s after a 30 s warm-up: native 100% TSR; 100% DLAA; 66.7% DLSS
      Quality; 58% Balanced; 50% Performance; the game's own 60.6% with TSR vs DLSS. Screenshots for each.
- [ ] T4 Pick the performance lever for players (render scale control from the add-on or AMDLSS) and implement it.
- [ ] T5 HelixSR compatibility: the add-on under HelixSR Scaler's layout (OptiScaler `dxgi.dll` with
      `LoadReshade=true`, `ReShade64.dll`), DLSS calls passed to real DLSS on NVIDIA and to HelixSR Scaler's
      upscaler otherwise. Measured the same way as T3.

## Progress

- 2026-10-16: document created; T1 delegated.
