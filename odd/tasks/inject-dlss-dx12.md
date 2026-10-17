# Inject DLSS (DX12): the DLSS add-on as an AMDLSS route

## Objective

Ship the DX12 DLSS add-on (`probe/dx12/dlss/`) inside AMDLSS as a real install route, so players (the owner's
friends first) install, update and remove it from the AMDLSS window like any other route, and AMDLSS 1.3.0 ships
with it.

## Why

The owner's goal is a DLSS injector inside AMDLSS. Today the add-on is installed by hand on one PC. Luma UE (the
existing Inject DLSS route) is DirectX 11 only, and the 5 empty-Luma-tab reports all come from a DX12 game.

## Facts

- Add-on state: real NVIDIA DLSS 310.9.16 through NGX, both TSR modes (single-pass `1ff10c3ce95d3bcb`, two-pass
  `e32f96d4dc79be5b` + `0b1375003bfb8294`), `render_scale` writes `r.ScreenPercentage` to the user Engine.ini.
  Measured on Echoes of Aincrad, RTX 4070, 2560x1440 title screen: native TSR 63.8 fps, DLSS Quality 82.3 (+29%),
  Balanced 93.5 (+47%), Performance 107.2 (+68%). Details: `odd/tasks/dlss-performance.md`.
- Owner decision: NVIDIA = add-on alone (ReShade as `dxgi.dll`); AMD = add-on + HelixSR Scaler (Scaler as `dxgi.dll`,
  ReShade as `ReShade64.dll`, `[Plugins] LoadReshade=true`), and Scaler sends the add-on's DLSS calls to HelixSR's
  DLL. Verified on the 4070 with Scaler forced to HelixSR (300/300 evaluates); not yet on a real AMD card.
- ReShade 6.8.0.1 with add-on support and NVIDIA's `nvngx_dlss.dll` both come in Luma's release zip, which AMDLSS
  already downloads and pins (`amdlss/luma.py`).
- Base: `feat/helixsr-193` (HelixSR 1.9.3 / Scaler 1.0.6, 1.3.0 unpublished) merged with `spike/dx12-probe`.

## Tasks

- [ ] T1 Route: new module for the DX12 injector (install, update, remove, record and backup like luma.py/scaler.py),
      GPU vendor choice of layout, game detection (DX12 Unreal Engine game with no upscaler of its own), UI entry,
      tests with fakes. Route: delegated (writer trigger: multi-file).
- [ ] T2 Release: release.sh builds the add-on and bundles `amdlss_dx12_dlss.addon64` + its ini into both binaries;
      check_build.py and the selftest prove they are inside.
- [ ] T3 Docs and 1.3.0 notes.
- [ ] T4 Live check on the owner's PC, only with the owner's go-ahead: install through AMDLSS, game renders with DLSS,
      remove restores the folder.

## Progress

- 2026-10-16: branch `feat/inject-dlss-dx12` created from `feat/helixsr-193`, `spike/dx12-probe` merged (20ab89f).
