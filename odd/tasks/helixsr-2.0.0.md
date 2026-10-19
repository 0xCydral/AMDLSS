# HelixSR 2.0.0 (pin) and HelixSR Scaler 1.1.0

Objective: replace the 1.9.3 / 1.0.6 pins with HelixSR 2.0.0 (`HelixSR-2.0.0.zip`) and HelixSR Scaler 1.1.0, and follow
the layout change 2.0 brings: the built DLL moves into `HelixSR-fsrfg/`, the game list / install / remove commands of
`setup/helixsr_install.py` are gone, and the setup's `-Yes` / `--yes` now installs into every game. No push, tag or
release here; `VERSION` of AMDLSS is not touched.

Why: HelixSR 2.0.0 (published 2026-10-19, tag `v2.0.0`) is the first release after 1.9.3. It merges HelixSR into one DLL
(`HelixSR-fsrfg/amd_fidelityfx_dx12.dll`, the separate `helixsr.dll` is gone), brings DLSS frame generation back, and
makes HelixSR Scaler 1.1.0 treat HelixSR as its FidelityFX upscaler.

Evidence (read or measured 2026-10-19):

- `gh release view v2.0.0 -R lonewolf0622/HelixSR`: `HelixSR-2.0.0.zip` 136997025 bytes, SHA-256
  `0d60686eddbb0abd680b27d12a088344b212464ebf0a2dc18d10387e1c817f52`; `HelixSR-Scaler-1.1.0.zip` 130590483 bytes, SHA-256
  `827547c368a8468f9d888419f8f11cda687d3df7959725234a4dce4d8b3fc5fd`. Both match the release's `SHA256SUMS.txt`
  (the Scaler zip was downloaded and hashed here). The Scaler zip is a release asset of the HelixSR tag, so
  `scaler.ZIP_URL` still follows `helixsr.VERSION`; its top folder is `HelixSR-Scaler-1.1.0`.
- Members, 1.9.3 vs 2.0.0 (`unzip -Z1`, 84 vs 149 entries): the DLL moved from the root to `HelixSR-fsrfg/`, with
  `HelixSR-fsrfg/helixsr.ini` and `HelixSR-fsrfg/helixsr-fg/nvexec64.dll` (the only frame generation file in the zip);
  `helixsr-desktop-fg.txt` is gone; the bundled `helixsr-scaler/` lost `OptiScaler/streamline/` (12 DLLs) and
  `Licenses/Streamline_LICENSE.txt`, and gained `README-Linux.txt`; `setup/` gained `helixsr_bridge.py`,
  `helixsr_games.py`, `lib/setup_shader_jobs.py`, `lib/k5_xform.py` and the whole `setup/fg/` tree (frame generation's
  build). `setup/helixsr_install.py` shrank from 2024 to 393 lines.
- The built network: `helixsr-setup` builds into `HelixSR-fsrfg/amd_fidelityfx_dx12.dll` (appended, `HXSRNET1` footer) and
  writes `helixsr_setup.json` at the **release root** (`helixsr_setup.py:465`, `rel / 'helixsr_setup.json'`, `FSRFG`
  at :260). `--status` prints `network=0|1 fg=0|1`. The version string `\0HelixSR 2.0.0\0` sits at byte 754354 of the
  DLL, so `helixsr.version_of` and `logs.owner_of` (first 4 MB) still read it.
- DLL exports (read with `amdlss.pe.inspect`, 1.9.3 vs 2.0.0): 22 vs 26, nothing removed. New:
  `HelixSR_FfxFgDlssgVersion`, `HelixSR_FfxFgUiFlag`, `HelixSR_FgPresenterGetApi`, `HelixSR_NgxFgVersion`. AMDLSS reads
  no export of HelixSR's DLL (only the FSR 4 DLL's `ffxCreateContext`), so nothing there depends on it.
- Scaler 1.1.0 package: file list identical to 1.0.6's except the `streamline/` folder and its license (gone).
  `OptiScaler/amd_fidelityfx_upscaler_dx12.dll` is byte-identical to 1.0.6's (4.1.1.2740, SHA-256 `d0dcccc7...`, the
  pin in `fsr4.py`). `OptiScaler.ini`: `[HelixSR] FrameGeneration=off` is **gone** (it has `FGPreset`, `QuickFlow`,
  `Int8`, `WaveSize`); `[FrameGen]` keeps `Enabled`, `FGInput`, `FGOutput`, `FGNvngxReplacement`, `PreserveSwapChain`;
  `[Log]`, `[Plugins] LoadReshade` and `[Hotfix] CreateD3D12DeviceForLuma` are unchanged. Its README says the upscaling
  is "found by itself when HelixSR-fsrfg is next to the game's .exe" (`[Upscalers] Dx12Upscaler=auto` is `ffx`, `[Libraries]
  FfxDx12SRPath` is the DLL in `HelixSR-fsrfg`); an older install's `HelixSR\helixsr.dll` is still read.
- Layout in a game (HelixSR's own bridge, `helixsr_bridge.py` `scaler_install`): the Scaler files, plus
  `HelixSR-fsrfg/amd_fidelityfx_dx12.dll` and, always, `HelixSR-fsrfg/helixsr-fg/*`; `OptiScaler.ini` gets
  `Dx12Upscaler=ffx` where it says auto; frame generation is `[FrameGen] FGInput=nvngxfg` +
  `FGNvngxReplacement=HelixSR`. The record is `HelixSR-Scaler.files`. `helixsr_games.py` is the game scan of 1.9's
  `helixsr_install.py`: `Game` is byte-identical, `games()`/`games_from()` differ in one message; it does **not** skip
  `HelixSR-fsrfg` in its walk (the bridge removes the DLL afterwards).
- **The setup's `-Yes` / `--yes` is no longer safe** (the coordinator's finding, confirmed here). `helixsr-setup.ps1:113`
  and `helixsr-setup.sh:48` (`INST+=(--yes)`) hand it on to `helixsr_install.py --yes`; with it `answer()` (:70) takes
  every default without reading input, so each game found gets its recommended way: HelixSR Scaler, or the FSR drop-in
  for a game with anti-cheat that has FSR 3.1 (`ask_game`; a Scaler pick in an anti-cheat game is refused under
  `ASSUME_YES`, :132, so an anti-cheat game without FSR 3.1 is left alone). 1.9.3's installer stripped `--yes` (`a = [x for x in a if x != '--yes']`). AMDLSS 1.3.x passed
  `-Yes` / `--yes`. Reproduced on a decoy game in a fake `HOME` (a Steam library holding one folder, no real game):
  `helixsr_install.py <release> --yes < /dev/null` printed `DecoyGame (DLSS, FSR 3.1): install? [s/f/n] s` and
  `HelixSR Scaler installed as dxgi.dll`. Without `--yes`, stdin closed (AMDLSS's real build below): `DecoyGame (DLSS, FSR
  3.1): install? [s/f/n]` then `no answer: nothing was changed`, and the decoy's three files kept their SHA-256.
- Other calls into the setup or the installer: `list`, `install N`, `uninstall N` of `helixsr_install.py` do not exist in
  2.0 (it takes `RELEASE_DIR [--folder DIR]... [--yes]` or `RELEASE_DIR remove ...`, interactive). Neither takes
  `--yes` from AMDLSS any more: the 2.0 path uses `helixsr_bridge.py RELEASE install --game DIR --way fsr` and
  `remove --game DIR` (JSON on stdout: `ok`, `error`, `messages`), which has no confirmation flag at all; a 1.9 folder is
  still driven by its installer, whose `--yes` was ignored.
- Setup flags: `-NoFg` / `--no-fg` (skips the frame generation build, whose downloads are NVIDIA's DLSS frame generation
  DLLs 310.3 and 310.9.16 and the NGX core from NVIDIA's Linux driver package), `-Dlss` / `--dlss FILE` (the DLSS DLL
  to build from), `-Rebuild`, `--fg-only` (python only). `helixsr_setup.py` asks only through `input()`, so a run without
  `--yes` and without `--dlss` dies on the closed stdin. Exit code 3 means "network built, frame generation not"
  (cannot happen with `--no-fg`).
- NVIDIA's DLSS DLL the network is built from: `https://raw.githubusercontent.com/NVIDIA/DLSS/v310.7.0/lib/Windows_x86_64/rel/nvngx_dlss.dll`,
  58977904 bytes, SHA-256 `be6e434a94ca32499515eb62ca0e6c274526055d568d0426e4c652dcdfb6ee6e` (downloaded here; the same
  constants as `helixsr_setup.py:28-30`).
- Real build through AMDLSS's own fetch + build (`helixsr.install_helixsr`, fake `HOME` with the real Python/DXC
  folders and Proton symlinked, `/tmp/helixsr-200-data`, 125 s): the zip (130 MB), NVIDIA's DLL (56 MB) and the
  Scaler package (124 MB) downloaded and passed their pinned hashes; the setup built 200 shaders, the network is
  inside `HelixSR-fsrfg/amd_fidelityfx_dx12.dll` (30 MB), `helixsr_setup.json` at the root, `helixsr-fg/` holds only
  the shipped `nvexec64.dll`, no `helixsr_fg_setup.json`; the setup said "upscaling is ready; DLSS frame generation is
  not"; `.download/` was empty afterwards. `tests/test_real_helixsr.py` + `tests/test_pe.py` against that build:
  17 passed (FSR drop-in through the real bridge, Scaler route, FSR 4.1.1 on both routes, an earlier install's removal).
- Layout trap found with the real scan: a game with only `HelixSR-fsrfg/amd_fidelityfx_dx12.dll` (no FSR 3.1 of its own)
  lists as `route direct, fsr31 True, can_opti True, targets [HelixSR-fsrfg/amd_fidelityfx_dx12.dll]` when the walk is not
  told to skip the folder, and as `route optiscaler, fsr31 False` with the skip (A/B on the real 2.0.0).

Decisions:

- Pins: `helixsr.py` 2.0.0 / 136997025 / `0d60686e...`; `scaler.py` 1.1.0 / 130590483 / `827547c3...`.
- Layout: `helixsr.dll_path(folder)` is the one place that knows where the DLL is (`HelixSR-fsrfg/` first, else the root
  of a 1.9 folder); `extracted`, `built`, `version_of`, `locate` use it. An older built folder is still read and listed
  and still offers Update, as with earlier pins (it keeps `install N` / `uninstall N`; `HelixSR.modern` is true when
  `setup/helixsr_bridge.py` exists).
- **No `-Yes` / `--yes` anywhere.** The consent is carried by files: AMDLSS fetches NVIDIA's DLL itself
  (`fetch_dlss`, pinned size and SHA-256, into `.download/`, deleted after the build, a failed one too) and passes it
  with `-Dlss` / `--dlss`; `-NoFg` / `--no-fg` skips the frame generation downloads. The setup's games step then reads the
  closed stdin and changes nothing (shown above). The coordinator's suggested alternative, writing `y\n` to the setup's
  stdin, was not used: the one `y` would go to whichever prompt comes first (the Linux script's package-manager
  question or the DLSS question), and it needs one `y` per NVIDIA download if frame generation is ever added.
  `consent_text` says AMDLSS downloads the DLL and skips the frame generation downloads. Tests:
  `test_no_command_that_reaches_the_setup_carries_yes`, `setup_command` in `selftest.py`, `test_linux.py`.
- Games, list: AMDLSS's read-only bridge imports `helixsr_games` (2.0), else `helixsr_install` (1.9), and adds
  `helixsr-fsrfg` to the module's `WALK_SKIP`. A 2.0 folder has no printed list, so a failed bridge is an error there
  (the printed-list fallback stays for 1.9 folders).
- Games, FSR drop-in: `HelixSR.install` / `remove` run `helixsr_bridge.py ... install --way fsr` / `remove` by the game's
  folder after AMDLSS's own guard (closed game, safe folder, fresh list), and pass the bridge's messages to the log;
  a locked file reads as "Close <game> first" as before. The Scaler route stays AMDLSS's own.
- Scaler placement: `HelixSR-fsrfg/amd_fidelityfx_dx12.dll` (a 1.9 folder: `HelixSR/helixsr.dll`, which Scaler 1.1.0 still
  reads). AMDLSS's record and backup stay in `HelixSR/` (`FOLDER`). `Dx12Upscaler` is **not** written: Scaler's README
  and ini say it finds the DLL by itself and `auto` is `ffx`; HelixSR's own bridge writes `ffx` explicitly. Not tested
  in a game (see the questions). Remove also deletes `HelixSR-fsrfg`'s runtime files and prunes the folder.
  `helixsr-fg` is not copied.
- Frame generation: no product change. The window offers Off and AMD FSR; Install / Update write the off values,
  which switches off a HelixSR frame generation (`FGInput=nvngxfg`) a player picked in Scaler's menu. The setup is built
  with `--no-fg`, the `helixsr-fg` folder is not copied, so HelixSR's frame generation could not run anyway.
  `NO_FG` (the note and the after-install line for a leftover DLSS 4 mode) no longer says "1.9.2 and later have no frame
  generation": it says AMDLSS does not offer HelixSR's own. `framegen.HELIX_FG` (`[HelixSR] FrameGeneration`) is kept for
  an ini that still has the line; a missing line is skipped (tested on the real 1.1.0 ini shape).
- Other layout spots: `games._rescan` and `MOD_FOLDERS` skip `HelixSR-fsrfg`; Collect logs lists the folder (its
  `helixsr.log`); `installed_version` in the bridge reads the DLL in `HelixSR-fsrfg` first.
- No `CHANGELOG.md` exists in this repo (not in history either); the user-visible notes of earlier pins went into the README
  and this kind of file, so the same is done here.

Open questions (product):

1. Offer HelixSR's DLSS frame generation again? It needs the setup without `--no-fg` (three more NVIDIA downloads, in
   the consent text), copying `helixsr-fg/` into games, a window choice that writes `FGInput=nvngxfg` +
   `FGNvngxReplacement=HelixSR` (DirectX 12 games with DLSS frame generation only, `games.scan_streamline` exists), and a
   decision on what Install / Update do to a choice made in Scaler's own menu.
2. Should AMDLSS write `[Upscalers] Dx12Upscaler=ffx` like HelixSR's bridge, once a game run shows what `auto` does with
   AMDLSS's other settings (FSR 4.1.1 route, Luma, the DirectX 12 add-on)?

Tasks:

- [x] Diff 1.9.3 vs 2.0.0 (members, setup entry points and flags, installer with stdin closed, DLL exports, Scaler files).
- [x] Repin `helixsr.py` and `scaler.py`; `test_fetch.py` pinned values.
- [x] Build + consent without `--yes`: `fetch_dlss`, `setup_command`, `build`, `install_helixsr`, consent text, tests.
- [x] Layout: `dll_path`, bridge module fallback, `WALK_SKIP`, 2.0 bridge for drop-in install / remove, Scaler placement and
      cleanup, games / logs / selftest, texts.
- [x] Tests: fake 2.0.0 folder (`fake_helixsr.make`, `age()` for the older-folder cases), `test_helixsr.py`, `test_scaler.py`,
      `test_framegen.py`, `test_linux.py`, `test_fetch.py`, app tests, `test_real_helixsr.py`.
- [x] README.
- [x] Full suite, `--selftest`, real build + real tests.

Checks:

1. `xvfb-run -a uv run --no-project -q --managed-python --python 3.12 --with pytest --with pillow python -m pytest -q`
   (the system `python` has no pytest).
2. `xvfb-run -a python3 AMDLSS.pyw --selftest`.
3. Real build: `HOME=/tmp/helixsr-200-home python3 realbuild.py` (AMDLSS's `helixsr.install_helixsr` into
   `/tmp/helixsr-200-data`), then `AMDLSS_TEST_HELIXSR=/tmp/helixsr-200-data/HelixSR-2.0.0 AMDLSS_TEST_SCALER=/tmp/helixsr-200-data/HelixSR-Scaler-1.1.0
   pytest tests/test_real_helixsr.py tests/test_pe.py`.

Evidence (observed 2026-10-19):

- `pytest -q`: 571 passed, 10 skipped (8 env-gated `test_real_helixsr.py`, 1 `test_pe.py`, 1 Windows-only `test_identify.py`).
- `--selftest`: "selftest passed".
- Real zips: `tests/test_real_helixsr.py tests/test_pe.py` -> 17 passed.
- Commits: see `git log main..feat/helixsr-200`.
