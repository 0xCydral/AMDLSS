# FSR 4.1.1 as a per-game upscaler

Objective: each game can use AMD's FSR 4.1.1 instead of HelixSR wherever that works, and every game card and the
detail panel say whether the game can use it and, when not, why.

Why: HelixSR 1.9.0 already ships AMD's official FSR 4.1.1 upscaler (`amd-fsr/amd_fidelityfx_upscaler_dx12.dll`,
FileVersion 4.1.1.2740, SHA-256 `d0dcccc7...c9fb46`), which runs FSR 4 on RX 9000 and an INT8 build of FSR 4 on RX 7000,
and FSR 3.1.5 on every other card. AMDLSS only installs HelixSR today.

Scope: new `amdlss/gpu.py`, `amdlss/pe.py`, `amdlss/fsr4.py`; changes in `amdlss/games.py`, `helixsr.py`,
`framegen.py`, `settings.py`, `app.py`, `ui.py`, `logs.py`, `reports.py`, `selftest.py`, `tools/report_html.py`,
`README.md` and tests. Nothing is pushed, tagged or released from this work. No new runtime dependency.

Decisions:

- GPU classes: rdna4 (RX 9000), rdna3 (RX 7000 and Pro W7000), rdna2 (RX 6000), other. Classified by PCI device ID
  (libdrm `amdgpu.ids`, the list Mesa uses), then by the card's name. RDNA3 integrated graphics (780M, 890M) are
  `other`. With several cards the best class wins.
- FSR 4 source: rdna4 and rdna3 use AMD's DLL from the HelixSR folder after its SHA-256 matches the pinned one.
  rdna2 needs a DLL the user picks once; rdna3 and rdna4 may pick their own to override. `other` has no FSR 4.
  A picked DLL must be a 64-bit PE that exports `ffxCreateContext` and whose file version starts with 4.
- Availability is one pure function, in this order: folder refused, anti-cheat found, card cannot run it (or the
  DLL is missing), no FSR 3.1/DLSS/XeSS/FSR 2 input. Routes: drop-in when the game has an FSR 3.1 DLL, OptiScaler when
  it has DLSS, XeSS or FSR 2 inputs.
- Drop-in route is done by AMDLSS, not HelixSR, and recorded in `amdlss-fsr4.json` beside the target. Remove
  restores from that record. HelixSR's drop-in and ours refuse each other.
- OptiScaler route: HelixSR installs OptiScaler as today, then `OptiScaler.ini` gets `[Libraries] FfxDx12Path` and
  `FfxDx12SRPath` through the `framegen` Setting mechanism (tag `#fsr4lib`). `[FSR] UpscalerIndex` is left alone
  (coordinator note, field evidence from an RX 6600: index 0 is not proven to be FSR 4 on every card, and OptiScaler's
  own comment says `auto` picks FSR 4 on RDNA 4 and FSR 3 on the rest). The FSR 4 DLL is the only DLL OptiScaler is
  pointed at, and the after-install text tells the user to pick "FSR 4.1.1" in OptiScaler's menu (Insert, Upscaling,
  FFX Upscaler) and to read the menu header, which names the active version. Unproven without a real card: that the
  DLL's first entry is FSR 4 on RX 7000 and RX 6000 with `auto`.
  Deviation from the plan: the two paths point at two different copies of the DLL in `<exe>\HelixSR\fsr4\`, because
  HelixSR's installer documents (helixsr_install.py, OWN_SR) that one file in both roles hangs the game when the
  upscaler starts.
- A picked DLL is accepted when it is a 64-bit PE exporting `ffxCreateContext` with a file version that starts with 4,
  signed or not. A tester's RX 6600 ran the community build named "FSR 4.1.1b" from HelixSR's my-fsr folder, so
  4.1.x community builds are accepted, not only 4.0.2.
- The choice per game is kept in `settings.json` (`upscalers`, keyed by game folder).

## Tasks

- [x] T1 (4d31f97) GPU class (`amdlss/gpu.py`, `tests/test_gpu.py`)
- [x] T2 (c86fe07) PE reader: exports and file version (`amdlss/pe.py`, `tests/test_pe.py`)
- [x] T3 (3ef442b) FSR 4 source, availability and the choice store (`amdlss/fsr4.py`, `settings.py`, `games.py`)
- [x] T3b (1b7f649) Find the RX 6000 DLL automatically (`fsr4.discover`, `auto_find`): game exe folders, `HelixSR\my-fsr`, the
  folders of the fsr_targets, Downloads, Desktop, Documents (4 levels, capped) and the AMDLSS data folder; only
  community builds count (AMD's pinned SHA-256 is skipped); the highest file version wins; a .7z or .zip named like an
  FSR 4 download gets the hint "Unpack <name> and AMDLSS will find the DLL."; archives are never unpacked. Rescan
  while nothing is found; the window shows where the DLL was found. "Choose FSR 4 DLL..." stays as the fallback.
  Authenticode signer check: not done (the pinned hash already tells AMD's build apart).
- [x] T4 (5853332) Drop-in install and remove with the record and the loader download (`amdlss/fsr4.py`, `helixsr.py`)
- [x] T5 (14497c7) OptiScaler route switch (`amdlss/fsr4.py`, `framegen.py`)
- [x] T6 (cf633fa, 3c62f94) Window: card line, Upscaler control, DLL buttons, install and remove flows (`app.py`, `ui.py`)
- [x] T7 (e5a1c3e) Reports: games.txt, files.txt, system.txt GPU class, report page (`logs.py`, `reports.py`, `tools/report_html.py`)
- [x] T8 (09ca3ea) README, selftest round trip, real HelixSR test, screenshots

## Checks

- Linux: `uv run --no-project --managed-python --python 3.12 --with pytest --with pillow python -m pytest -q`
  (baseline 356 passed, 4 skipped), with an Xvfb display for the Tk tests.
- Windows Python under wine: `amdlss-wine 'C:\Python312\python.exe' -m pytest -q -p no:cacheprovider --color=no`
  (baseline 349 passed, 11 skipped).
- `python AMDLSS.pyw --selftest` exits 0.
- `AMDLSS_TEST_HELIXSR=~/.local/share/AMDLSS/HelixSR-1.9.0` runs `tests/test_real_helixsr.py`.
- Screenshots of the window at 1366x768, 1920x1080, 2560x1440.

## Progress and evidence

Route per task: delegated direct is not available inside this worker, so every task is inline.

- RED was observed before each behaviour for T1 to T5, T7 (an import error or a failing assertion, then GREEN). T6's window
  code was written before its smoke tests (`tests/test_app_fsr4.py`), so its tests were not seen failing first.
- Linux: 457 passed, 7 skipped (the 7 are tests that need AMDLSS_TEST_HELIXSR or a real DLL). Wine: 450 passed, 14 skipped
  (one earlier run failed only on a Windows line-ending slip in this work's own test, fixed in 3dd2665).
- `AMDLSS_TEST_HELIXSR=~/.local/share/AMDLSS/HelixSR-1.9.0`: `tests/test_real_helixsr.py` 5 passed, including the new
  OptiScaler route plus FSR 4 switch and back, and the drop-in with HelixSR switching, on the real installer. AMD's real DLL
  matched the pinned SHA-256 and read as 4.1.1.2740.
- Fixed on the way: `test_optiscaler_route_with_frame_generation_into_a_fake_game` already failed at HEAD (it did not know
  the `Enabled` and `PreserveSwapChain` values frame generation sets since beta.16); `helixsr.py` had a SyntaxWarning in a
  docstring (`Z:\...`).
- Screenshots: /tmp/fsr4-shots/ (rdna3-* and other-*, three sizes, two games each).
- Not verified: anything on a real Windows PC or a real Radeon. Which entry of AMD's DLL OptiScaler runs on RX 7000 and RX 6000
  with `UpscalerIndex=auto`; that OptiScaler accepts two separate copies of the DLL in the two library roles; the loader shim
  with a real game; Authenticode signers.
