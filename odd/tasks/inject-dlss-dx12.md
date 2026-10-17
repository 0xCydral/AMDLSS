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

- [x] T1 Route (fe52d35 module, routing and tests; 12ff446 window): new module for the DX12 injector (install, update, remove, record and backup like luma.py/scaler.py),
      GPU vendor choice of layout, game detection (DX12 Unreal Engine game with no upscaler of its own), UI entry,
      tests with fakes. Route: delegated (writer trigger: multi-file).
- [x] T2 Release (9ccd053): release.sh builds the add-on and bundles `amdlss_dx12_dlss.addon64` + its ini into both binaries;
      check_build.py and the selftest prove they are inside.
- [x] T3 Docs and 1.3.0 notes (README section; notes draft at `/tmp/amdlss-rel/notes130.md`; this file).
- [ ] T4 Live check on the owner's PC, only with the owner's go-ahead: install through AMDLSS, game renders with DLSS,
      remove restores the folder.

## Progress

- 2026-10-16: branch `feat/inject-dlss-dx12` created from `feat/helixsr-193`, `spike/dx12-probe` merged (20ab89f).
- 2026-10-17: built on `feat/inject-dlss-dx12`.
  - Route `amdlss/dx12inject.py` mirrors `luma.py`: record `HelixSR\amdlss-dx12dlss.json`, every file placed, moved or
    created with SHA-256s, Remove restores byte for byte (a pre-existing `dxgi.dll` is backed up and comes back). Layout by
    card (`gpu.detect`, any vendor `10de` = NVIDIA, else Scaler): NVIDIA = `dxgi.dll` (ReShade), `amdlss_dx12_dlss.addon64`,
    `amdlss_dx12_dlss.ini`, `nvngx_dlss.dll`; other = `dxgi.dll` (Scaler, `scaler.install_game(name='dxgi.dll',
    placed=True)`), `ReShade64.dll`, add-on, ini, `HelixSR\helixsr.dll`, `[Plugins] LoadReshade=true`. The ini is the
    tracked one with `render_scale=quality`; Update keeps the player's value. ReShade and `nvngx_dlss.dll` come from
    Luma's pinned zip (`luma.fetch`).
  - Open fact: the `nvngx_dlss.dll` in Luma's zip (sha256 `3975567b...`) is DLSS **310.9.1.0**; the add-on is built against
    and was measured with **310.9.16.0** (`out/nvngx_dlss.dll`, sha256 `d00b5b0a...`). The NVIDIA layout therefore ships a
    runtime that has not been measured. The alternative is to bundle `out/nvngx_dlss.dll` (59 MB) instead.
  - Open fact: the AMD layout sends no `nvngx_dlss.dll` (owner decision); the 2026-10-16 HelixSR run had NVIDIA's DLL
    beside it. Not run on a Radeon.
  - Routing (`games.unreal_rhi`, used by `mark_inject`): `DefaultGraphicsRHI` in a loose `DefaultEngine.ini`; else the
    graphics DLLs the shipping `.exe` imports (`pe.imports`, import and delay-load tables: only `d3d12.dll` = DX12, only
    `d3d11.dll` = DX11); else a `*_PCD3D_SM6*` pipeline cache under `Content/PipelineCaches` or `Saved` = DX12. Anything
    else shows both routes with the reason. Not validated against the real Echoes of Aincrad `.exe` (the brief forbids
    touching that folder): `python3 -c "from amdlss import pe; print(pe.imports(r'<exe>'))"` is a read-only check.
  - Packaging: `release.sh` builds the add-on first and stops if that fails; `release.sh`, `build-linux.sh` and `build.sh`
    bundle both files as `dx12dlss/`; `check_build.py` compares the bundled add-on with the build byte for byte; the
    selftest checks presence, size and the `MZ` header.
  - Evidence: `/tmp/amdlss-rel/build.log` (`pyinstaller-win exit=0`, `build-linux exit=0`, both `VERSION 1.3.0`, add-on 481280
    bytes identical to the build, both selftests exit 0); `pytest` 556 passed, 9 skipped before the docs commit.
  - Environment: on a fresh worktree `probe/dx12/build.sh` fails inside Docker ("Cross-device link" moving the xwin
    splat), so `probe/dx12/third_party` and `probe/dx12/dlss/third_party` were copied from `dx12-probe/` (both gitignored).
