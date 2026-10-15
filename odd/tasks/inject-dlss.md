# Inject DLSS (experimental): Luma UE gives a game without an upscaler a DLSS call HelixSR Scaler runs

## Objective

AMDLSS today only does something with a game that already calls an upscaler (FSR 3.1 for the drop-in, DLSS/XeSS/FSR 2
for the OptiScaler route): HelixSR Scaler is OptiScaler, and OptiScaler only ever *replaces* a call the game makes.
A game that ships no upscaler at all is hidden ("no DLSS, FSR 3.1 or XeSS found").

This adds a third route, **Inject DLSS (experimental)**, for Unreal Engine games that ship no upscaler: AMDLSS installs
Luma Framework's Unreal Engine mod (Luma UE) next to the game's `.exe`, which replaces the engine's TAA pass with a
DLSS/DLAA call, and installs HelixSR Scaler in the same layout, so the call OptiScaler intercepts is the one Luma
makes. On an AMD card HelixSR then runs that call, exactly as it does for a game with a native DLSS option.

## Evidence (all read, none of it executed)

**Luma UE, the release AMDLSS pins** (`github.com/Filoppi/Luma-Framework`):

- Release `latest-749` ("Release (Build 749)", published 2026-10-14), asset
  `Luma-Unreal_Engine.zip`, 37284374 bytes, SHA-256
  `a357ce98700262064c280c6b9d56852e2876d22a0ccd04f18cc169d32213b2c2`
  (`https://github.com/Filoppi/Luma-Framework/releases/download/latest-749/Luma-Unreal_Engine.zip`; the asset was
  downloaded and hashed again here, and matches the digest the releases API reports).
- The zip holds 55 members: `dxgi.dll` (ReShade 6.8.0.1, x64; verified with the repo's own `amdlss.pe`), 
  `Luma-Unreal Engine.addon` (the Luma mod), `nvngx_dlss.dll` (NVIDIA DLSS 310.9.1.0,
  "NVIDIA DLSS - DVS PRODUCTION"), `Luma/d3dcompiler_47.dll` (Microsoft's HLSL compiler) and 51 `Luma/**/*.hlsl`
  shaders. `scaler.is_optiscaler()` says *no* for every one of them, so the Luma files are never mistaken for a
  leftover OptiScaler copy (`scaler.optiscaler_copies()` leaves them alone).
- Luma's own instructions (wiki Home / "Unreal Engine" section): "Generic mod that replaces default UE4 TAA
  implementation with DLSS (DLAA) or FSR3 ... at the moment it requires DX11 (it might work in UE5 games too)",
  tested UE 4.12-5.4; extract all files into the game's main executable folder; if another mod uses `dxgi.dll`,
  "find a way to proxy them".
- License: **Custom MIT** (`LICENSE.md`): "Any reuse of this code shall include the names of the authors or of the
  project. Commercial usage is possible but only after asking permission to the authors." AMDLSS is free and
  downloads Luma's own release on the player's PC (it never redistributes it, the same model as HelixSR); the
  permission the license asks for concerns commercial use. The owner will contact the author.

**The combination is documented, with the exact layout** (`github.com/optiscaler/OptiScaler/wiki`, page
"Luma Unreal Engine Luma UE", last tested OptiScaler **0.9.4** — the version inside HelixSR Scaler — on an RX 9070 XT,
"Upscaler Inputs: DLSS with Luma Framework"):

- **Method 1 ("Most reliable approach for Luma UE")**: rename Luma UE's `dxgi.dll` to `ReShade64.dll`, install
  OptiScaler as `dxgi.dll`, and set `LoadReshade=true` in `OptiScaler.ini`. Luma's settings file (`[Plugins]`) says
  the same: "Loads Reshade64.dll from game's exe folder. Rename Reshade dll to ReShade64.dll, put next to OptiScaler
  and set to true" — `LoadReshade = auto`, and OptiScaler reads auto as false, so the key must be written.
- Method 2 (Ultimate ASI Loader) and Method 3 (OptiScaler as `winmm.dll`/`version.dll`, Luma's `dxgi.dll` kept) need
  `DontCreateD3D12DeviceForLuma=true` in some cases; Method 1 does not, and is the one the page calls most reliable.
- The page's notes: open Luma/ReShade's overlay with **Home** and set `Upscaler/Super Resolution` to **DLSS**; if
  there is a Vsync-like FPS cap, turn Vsync off in OptiScaler's overlay; Linux and Win 10 + FSR 4 users may need
  `DontCreateD3D12DeviceForLuma=true` if it crashes or shows the non-DX11 device error.
- Luma's own wiki already pairs the two: for *What Remains of Edith Finch*, "you need to install OptiScaler in
  addition to Luma. Magic." (And *Daymare: 1998* "crashes if combined with OptiScaler or UUU" — per-game caveats
  exist, see "Unverified" below.)

## Decision

**Built**, as **Method 1**, with the Luma files placed exactly as Luma's zip ships them and one rename:

| Luma UE zip member | in the game's .exe folder |
|---|---|
| `dxgi.dll` | `ReShade64.dll` (the rename Method 1 requires) |
| `Luma-Unreal Engine.addon` | same |
| `nvngx_dlss.dll` | same (the DLSS runtime Luma calls; OptiScaler spoofs it and serves HelixSR instead) |
| `Luma/**` (52 files) | same relative paths (its shaders and HLSL compiler) |

plus HelixSR Scaler (`OptiScaler.dll` → `dxgi.dll`, its package files, `HelixSR/`), and `[Plugins] LoadReshade=true`
written into the `OptiScaler.ini` the package brings. Luma is installed **first** (so its `dxgi.dll` is renamed out of
the way and OptiScaler's own name for `OptiScaler.dll` is `dxgi.dll`, the order Method 1 describes).

**When the route is offered** (`games.mark_inject`, run after the anti-cheat scan, `app.refresh_games`):

- the game's `.exe` folder is an Unreal shipping folder: `<...>\Binaries\Win64` (or `WinGDK`) holding a
  `*-Win64-Shipping.exe` / `*-WinGDK-Shipping.exe` (the layout `crashes.unreal_project` and OptiScaler's own
  Unreal note both use), **and** that `.exe` is x64 (`amdlss.pe`; the UE zip has no 32-bit build);
- the game ships no upscaler of its own (no FSR 3.1 DLL, no DLSS/XeSS/FSR 2 file — HelixSR's own scan), so nothing
  else in AMDLSS could run in it;
- no anti-cheat files were found (`games.scan_anticheat`). Found ones **refuse** the route with a reason of its own
  (`INJECT_ANTICHEAT`) instead of the old "no upscaler" one: injection is exactly what gets accounts banned.
- A game whose Direct3D version cannot be told apart gets **no claim**: DirectX 11 cannot be detected from the files,
  so the plan and the after-install text say that Luma UE needs the game to run in DirectX 11 and that some games
  need `-dx11`, and the README points at Luma's own tested list. AMDLSS never says a game works.

**Fully reversible**: `HelixSR/amdlss-luma.json` records every file the Luma install created (with its SHA-256) and
every game file it had to move into `HelixSR/amdlss-scaler-backup`, exactly like `amdlss.scaler`'s own record. Remove
puts the moved files back, deletes what AMDLSS created (keeping a file the user changed, and saying so), restores the
`OptiScaler.ini` `[Plugins]` values the file held before (through `amdlss.framegen`'s record), and then removes the
Scaler install. Nothing is ever deleted that AMDLSS did not put there.

## Licence and redistribution

- Luma Framework: Custom MIT (see above). AMDLSS does not bundle or redistribute it: the zip is downloaded from
  Luma's own release page at the moment a game is installed into, SHA-256 checked against the pin.
- The `nvngx_dlss.dll` inside Luma's zip is NVIDIA's DLSS runtime, distributed by Luma, not by AMDLSS. AMDLSS copies
  it out of Luma's download into the game, as Luma's own instructions do.
- HelixSR Scaler (OptiScaler 0.9.4 GPL-3.0 + HelixSR) is downloaded as before; its plan text already names it.

## Implementation

- `amdlss/luma.py` (new): the pins, `fetch()` (resumable download, SHA-256, zip-slip screened, into
  `<data_dir>/Luma-Unreal-Engine-749`), the file layout (the rename table), the record, `install()`/`remove()`,
  `install_game()`/`remove_game()` orchestration, and the window's texts.
- `amdlss/games.py`: `INJECT` route, `INJECT_ANTICHEAT`/"not an Unreal game"/"32-bit" reasons, `inject_layout()`,
  `inject_reason()`, `mark_inject()`, `STATE_TEXT['inject']`, `IN_GAME['inject']`.
- `amdlss/scaler.py`: `install_game(..., name='')` — the inject route forces `dxgi.dll` (Method 1) instead of the
  first free name; a game that already had Scaler under another name is switched over on the way.
- `amdlss/framegen.py`: the `RESHADE` setting (`[Plugins] LoadReshade=true`), with the module's existing
  record/restore machinery, so Remove puts the value the file held back.
- `amdlss/app.py`: the route in `route_of()`, the plan and install path, the after-install text, `Remove`, the
  options row (an inject game has no upscaler to pick: both choices are greyed with the reason), `luma.mark()` in the
  refresh so an installed inject game keeps its state and its Remove.
- `amdlss/selftest.py`: `check_luma()` (pins, layout, round trip on a fake game).
- `tests/fake_luma.py`, `tests/test_luma.py`: the fetch pins, the layout/rename, the record round trip, the
  `LoadReshade` write and restore, the reasons, and the state.

## Checks

- `pytest -q` in the worktree under `Xvfb :62` (observed output in the task report).
- `python3 AMDLSS.pyw` smoke: `amdlss.selftest`'s `check_luma` runs in the same suite.
- The route's texts are checked in tests; no test claims a game works.

## Unverified (a tester must check these)

- **No in-game run.** This machine has no Windows game, no AMD card in a game and no DX11 UE title: the layout,
  the pins and the OptiScaler pairing follow the two projects' own documentation, and nothing here ran the game.
- **A tester's first check, on one UE4 DX11 game** (Luma's list marks DLSS ✅ for e.g. Gylt, Hellblade: Senua's
  Sacrifice, Dead Island 2 — all of which need `-dx11`/`-DX11`): install through AMDLSS, start the game, press
  **Home**, set `Upscaler/Super Resolution` to **DLSS**, and confirm from the on-screen text/`OptiScaler.log` that
  OptiScaler reports `DLSS` as the input and HelixSR as what runs (its own log lines), not "no upscaler".
- **The DirectX 11 requirement is not detectable**: a game whose UE build is DX12-only will not run Luma (the mod
  needs DX11), and some games need the `-dx11` launch argument; AMDLSS cannot tell and says so.
- **Linux/Proton and Win 10 + FSR 4**: the OptiScaler wiki's `DontCreateD3D12DeviceForLuma=true` workaround for a
  crash or the non-DX11 device error is not written by AMDLSS (it is a per-game remedy, not a default).
- **Frame generation** is not offered for this route (a DX11 Luma game is out of OptiScaler's FG inputs' scope here).
- The Luma release AMDLSS pins moves: `latest-749` is a build tag, and a newer build means re-pinning (`ZIP_NAME`,
  `ZIP_SIZE`, `ZIP_SHA256`) exactly as the HelixSR pins are maintained.
