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
- [x] T4 Live check on the owner's PC, only with the owner's go-ahead: install through AMDLSS, game renders with DLSS,
      remove restores the folder (2026-10-17, NVIDIA layout; see Progress).
- [ ] T5 Frame dump: a TSR dump taken after DLAA ran earlier in the same session is all zeros while the log says
      "0 failed" (launch B, 2026-10-17). A pure-TSR session dumps fine; it also hit a DLSS dump through Scaler (launch E), so it is intermittent. Fix, and make an all-zero readback count as failed.
- [ ] T6 AMD layout on a real Radeon card (the Ryzen iGPU cannot run the game: 4 GPU resets on 2026-10-16).
      First real Radeon report (RX 6600, Windows 11, 1.3.0): `fallback: ngx`. NGX found no NVIDIA core (`_nvngx.dll`
      126, no registry key) and Init returned FeatureNotSupported; Scaler did not answer the load. Fixed in 1.3.1 (T8);
      still waiting for the same player to confirm on the card.
- [x] T8 NGX through HelixSR Scaler's exports when there is no NVIDIA core (1.3.1). The add-on's NGX calls go through
      pointers that start at the SDK and move to the loaded `dxgi.dll`'s NVSDK_NGX_D3D12_* exports when Init fails.
      Reproduced on the 4070 by hiding the prefix's NGXCore FullPath and forcing `[DLSS] Enabled=false, HelixSR=true`:
      Init through Scaler 0x00000001, DLSS available=1, feature created, OptiScaler.log `helixsr.dll in use`, 78-83 fps
      at 1708x961 -> 2560x1440. Also: the card shows **update** when the game's add-on differs from the shipped one
      (`addon_outdated`, test `test_an_add_on_older_than_the_shipped_one_asks_for_update`). Route: inline.
- [ ] T7 Remove prints no log line in the window (remove_game returned no notes in the live run).

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
    `d3d11.dll` = DX11); else a `*_PCD3D_SM6*` pipeline cache = DX12, looked for in `Content/PipelineCaches`, the project's `Saved`, and the
    player's `Saved` (`%LOCALAPPDATA%\\<Project>\\Saved`, or inside the game's Steam Proton prefix on Linux, found with
    `crashes.unreal_saved_dirs`). Anything else shows both routes with the reason.
  - QA fix (2026-10-17): Echoes' exe imports both `d3d11.dll` and `d3d12.dll`, and its SM6 cache is only in the Proton
    prefix (`.../compatdata/2244210/pfx/.../Local/EchoesofAincrad/Saved/EchoesofAincrad_PCD3D_SM6.upipelinecache`), so the
    first version routed it to Luma. Read-only run on the real files now gives `('dx12', 'the game has a Shader Model 6
    pipeline cache, which only DirectX 12 makes')` and `mark_inject` -> `inject12`, no switch.
  - Packaging: `release.sh` builds the add-on first and stops if that fails; `release.sh`, `build-linux.sh` and `build.sh`
    bundle both files as `dx12dlss/`; `check_build.py` compares the bundled add-on with the build byte for byte; the
    selftest checks presence, size and the `MZ` header.
  - Evidence: `/tmp/amdlss-rel/build.log` (`pyinstaller-win exit=0`, `build-linux exit=0`, both `VERSION 1.3.0`, add-on 481280
    bytes identical to the build, both selftests exit 0); `pytest` 556 passed, 9 skipped before the docs commit.
  - Environment: on a fresh worktree `probe/dx12/build.sh` fails inside Docker ("Cross-device link" moving the xwin
    splat), so `probe/dx12/third_party` and `probe/dx12/dlss/third_party` were copied from `dx12-probe/` (both gitignored).
- 2026-10-17 live check (T4), RTX 4070, title screen, Steam launch, no input sent:
  - Scan from source (same code as the binaries): Echoes -> route `inject12`, rhi `dx12` (SM6 cache in the Proton prefix).
    Install placed the NVIDIA layout with Luma's `nvngx_dlss.dll` 310.9.1.0 and `render_scale=quality`; NGX evaluates
    returned `NVSDK_NGX_Result_Success`, so 310.9.1.0 works with the add-on.
  - Performance at 2560x1440 output (5 s windows): native TSR 100% 61.3-66.9 fps (launches B and D); DLSS Quality
    1708x961 87.7-93.3 fps, about 91 median (launch C): +38%. At 1920x1080 output (launch A, side monitor) DLSS Quality
    101-113 vs TSR at the same 66.7% render 107-121: DLSS costs a few percent more than TSR at equal render size.
  - Image quality (`seq_quality.py`, 30 frames each, title-screen camera drift): DLSS Quality vs native TSR 1440p: edge
    flicker 0.85x, all-pixel flicker 0.75x, stair-stepping 1.12x. DLSS Quality vs TSR at the same 66.7% render (1080p):
    edge flicker 0.77x, all-pixel 0.86x, stair-stepping 0.79x. Dumps in `~/Downloads/dx12-spike/dump_seq-20261017/`.
  - Remove restored the folder: dxgi.dll, the add-on, its ini, nvngx_dlss.dll and `HelixSR\` gone; ReShade.ini/.log
    and `dump_seq\` stay (by design). The owner's hand layout was then put back.
- 2026-10-17 AMD (Scaler) layout through HelixSR, on the 4070 (launches E, F): installed with `install_game(cards=[AMD
  card])` (Scaler 1.0.6 as dxgi.dll, ReShade64.dll, LoadReshade=true, `HelixSR\helixsr.dll` from the data folder's
  HelixSR 1.9.0 build, no nvngx_dlss.dll), then `[DLSS] Enabled=false, HelixSR=true` to force what Scaler does on a card
  without DLSS. OptiScaler.log: `NVNGXProxy::HelixSRDlss HelixSR DLSS upscaler: helixsr.dll in use`, 5630 evaluates.
  1708x961 -> 2560x1440 at 87-94 fps (native TSR 61-67). Image vs native TSR: all-pixel flicker 0.80x, edge flicker
  1.17x, stair-stepping 1.30x (HelixSR is rougher on edges than NVIDIA's DLSS, 0.85x / 1.12x). Remove cleared the whole
  Scaler layout. Not a Radeon: HelixSR's speed on an AMD card is still unmeasured (T6).
  - T5 seen again: launch E's first dump was blank (input 0.3% non-zero, output 0); launch F's two dumps were good.
    Intermittent, not tied to DLAA.
- 2026-10-17 render preset (launch G, NVIDIA layout, 1440p, Quality): default 81-90 fps, K 81-85 fps. Vs native TSR:
  default edge flicker 1.00x, all 0.82x, stair 1.16x; K 0.93x, 0.77x, 1.32x. K is steadier but scores higher on the
  stair metric (which also rises with sharper edges) and costs about 4 fps: the default preset stays. Across launches C
  and G the default preset measured edge flicker 0.85x and 1.00x, so title-screen drift moves these numbers by about 0.15.
