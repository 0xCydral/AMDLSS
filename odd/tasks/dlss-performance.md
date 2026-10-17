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
- 2026-10-16: T1 done (dcb846e, `perf:` line every `perf_report_seconds`). Live: `upscaler=none` on every window, about
  95 fps on the title screen. Not the overlay commit: the pre-overlay build behaves the same. A one-frame capture
  (probe add-on, armed by the new trigger file `amdlss_dx12_probe.f10`, 4a1a382) shows TSR now runs in two passes with
  the history at 200%: `e32f96d4dc79be5b` UpdateHistory writes 5120x2880 from the 1552x876 inputs, `0b1375003bfb8294`
  resolves it to 2560x1440. The add-on only knew the single-pass `1ff10c3ce95d3bcb`. Captures:
  `~/Downloads/dx12-spike/captures/title-history{100,200}.log`. Two-pass support delegated.
- 2026-10-16: the game rewrites `Saved/Config/WindowsClient/Engine.ini` on exit and drops a hand-added
  `[SystemSettings]` section, so it cannot be the render-scale lever as is (T2 open).
- 2026-10-16: HelixSR v1.9.1 was deleted upstream; v1.9.3 (Scaler 1.0.6) is current and withdraws HelixSR's own
  frame generation. AMDLSS repin delegated on `feat/helixsr-193`. Scaler 1.0.6 unpacked at
  `/tmp/helixsr-scaler-pkg/x/HelixSR-Scaler-1.0.6` (sha256 `1256a5c5...` matches the release digest) for T5.
- Owner decision (2026-10-16): NVIDIA GPUs use the add-on alone (real DLSS, no Scaler). AMD GPUs use the add-on with
  HelixSR Scaler (OptiScaler as `dxgi.dll`, ReShade as `ReShade64.dll` via `LoadReshade=true`), which catches the
  add-on's NGX calls and runs HelixSR's upscaler. T5 functional test on this PC's Radeon iGPU (Raphael) through
  `VKD3D_FILTER_DEVICE_NAME`; frame rates from that run are not meaningful.
- 2026-10-16: `r.ScreenPercentage=50` under `[SystemSettings]` in the user Engine.ini works (render 1280x720 ->
  2560x1440, ratio 0.500, DLSS MaxPerf); `sg.ResolutionQuality` does not change the render size. Same-scale A/B at
  1400x788 -> 1920x1080: TSR about 97 fps, DLSS about 94 fps, so DLSS alone gives no gain at equal render size.
  The 200% UpdateHistory shader `e32f96d4dc79be5b` has the same b0 offsets as `1ff10c3ce95d3bcb` (disassembly).
- 2026-10-16: two-pass support (ad0a5f5) runs live: 300/300 evaluates per report, UpdateHistory skipped.
  Sweep (`bench.sh`, `bench_summary.py`, title screen, 2560x1440 output, medians of 9-13 5 s windows):
  100% TSR 63.8 fps / DLAA 61.4; 67% DLSS Quality 82.3 (+29% over native TSR); 58% Balanced 93.5 (+47%);
  50% Performance 107.2 (+68%). DLSS costs about 3 fps more than TSR at the same render size. T3 done.
- 2026-10-16: `render_scale` (265b106) live: `render_scale=quality` wrote `r.ScreenPercentage=66.7` (with the
  `; amdlss_dx12_dlss render_scale` marker) at load; the game rewrote Engine.ini during startup and a later add-on
  load wrote it back; the next launch rendered 1708x961 -> 2560x1440, ratio 0.667, DLSS MaxQuality. T4 done.
  Known: switching DLSS -> TSR live in two-pass mode shows a burst of bad frames (TSR resumes from a stale history),
  which the owner saw as pixelated flicker during the A/B runs.
- Owner rule (2026-10-16): no simulated keyboard or mouse input on the owner's PC; ask the owner to press keys.
