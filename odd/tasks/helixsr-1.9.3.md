# HelixSR 1.9.3 (pin) and HelixSR Scaler 1.0.6; AMDLSS 1.3.0 (still unpublished)

Objective: replace the 1.9.1 / 1.0.4 pins with HelixSR 1.9.3 (`HelixSR-1.9.3.zip`) and HelixSR Scaler 1.0.6, because the
v1.9.1 GitHub release was deleted upstream (its asset URLs return 404, so the unpublished AMDLSS 1.3.0 with the 1.9.1
pins could not install anything). Make AMDLSS honest about the one thing the new releases take away: HelixSR's own
frame generation. Keep `VERSION = '1.3.0'` (nothing was published); no push, tag or release here.

Why: HelixSR 1.9.2 (2026-10-16) removed HelixSR's frame-generation engine "while startup freezes and compatibility
issues are investigated"; it also disabled the other Scaler frame-generation routes, which 1.9.3 (2026-10-16T14:22:56Z)
restored. AMDLSS 1.3.0 as it stood built that engine in the setup, copied it into every Scaler game and offered it as
"DLSS 4 frame generation (HelixSR), experimental". With 1.9.3 none of that exists.

Evidence (read or downloaded 2026-10-16):

- `gh release view v1.9.3`: `HelixSR-1.9.3.zip` 136076974 bytes, SHA-256 `7094b7afd006ab56231c880fcd358f115b114ee0734baa19d497a3e42da0a1d1`;
  `HelixSR-Scaler-1.0.6.zip` 133228253 bytes, SHA-256 `1256a5c51ba0db804d60c5ad896a06a17134f8b130a68fabda41621e8dc650af`.
  Both match `SHA256SUMS.txt` (downloaded with `gh release download v1.9.3 -p SHA256SUMS.txt`). Release id 415039511.
  The URLs follow the tag (`.../download/v1.9.3/<asset>`), as the code always built them: there is no release-id pattern
  to change. The HelixSR-Scaler zip is a release asset of the HelixSR tag, so `scaler.ZIP_URL` still follows
  `helixsr.VERSION`.
- Release notes 1.9.3: "corrects the frame-generation restriction in 1.9.2: alternative frame generation is available
  again. Only HelixSR's experimental frame generation is withdrawn. Keeps FSR, XeSS and other supported Scaler
  frame-generation providers selectable, with their usual game/GPU requirements and runtime dependencies. Preserves
  alternative FG input, output, enabled state and provider settings when updating. An older HelixSR provider choice is
  redirected to FFX; HelixSR is excluded from provider fallback and cannot be selected in the menu. Retains the 1.9.0
  INT8 calibration and layer selection, Speed Boost Auto / On / Off, reactive-mask support and newer upscaling fixes
  from 1.9.2. Includes HelixSR Scaler 1.0.6. HelixSR's own FG engine and setup downloads remain absent."
- Release notes 1.9.2: upscaling only; restores the INT8 calibration and default layer selection from 1.9.0 (the
  changes associated with the reported slowdowns and artifacts in 1.9.1); keeps the compatibility fixes, the Speed
  boost setting and reactive-mask support; "Removes HelixSR frame-generation execution from the DLL and its components
  from setup. Settings and leftover files cannot enable it. Updating through setup removes old HelixSR
  frame-generation files and disables the Scaler frame-generation route while preserving other settings." 1.9.2
  shipped Scaler 1.0.5 and disabled the alternative routes too; 1.9.3 undid that part.
- Real build through AMDLSS's own fetch + build, `/tmp/helixsr-193-data` (`python3 -I /tmp/helixsr-193-tools/realbuild.py`
  calling `helixsr.install_helixsr(data, consented=True)`): the zip and the Scaler package downloaded and passed the
  pinned SHA-256s; the setup built the network (120 shaders, 17.0 MB DLL, `helixsr_setup.json`), printed
  "HelixSR frame generation is not included in this release; upscaling is available", and wrote **no**
  `helixsr_fg_setup.json` and **no** `helixsr-fg/` folder. The new top-level file `helixsr-desktop-fg.txt` says "HelixSR
  frame generation is not included in HelixSR 1.9.3. Other Scaler frame-generation providers remain available." The first
  attempt failed inside a Proton `dxc.exe` call ("ShellExecuteEx failed" at shader 115 of 120); the same call, repeated
  with the downloads kept, built cleanly (a wine hiccup, not 1.9.3: the setup reuses what it has).
- Printed game list of the real 1.9.3 setup (verbatim in `tests/test_games.py::PRINTED_193`): `DLSS: HelixSR Scaler
  (select DLSS in the game)` and `FSR 3.1: FSR drop-in (HelixSR replaces the game's FSR DLL: select AMD FSR in the game)`
  - the labels of 1.9.1, unchanged. The installer's `Game.route` set is unchanged too: with the real installer, raw
  routes were `scaler` (3 games) and `direct` (2); AMDLSS's bridge reports `optiscaler` and `direct` for them
  (`tests/test_real_helixsr.py` lists through the real bridge and passes). `unsupported` stays only for a 1.9.0 folder.
- Installer 1.9.3 (`setup/helixsr_install.py`, 87425 bytes): `install_scaler()` now sets `fg_on = False  # This release
  includes upscaling only.`; `put_fg` copies nothing (`fg_source(here)` finds no `helixsr-fg`); the install/uninstall
  command lines (`list`, `install N`, `uninstall N`, `--folder`) are unchanged.
- Scaler 1.0.6 `OptiScaler.ini`: `[HelixSR]` has `Int8=auto`, `WaveSize=auto`, **`FrameGeneration=off`** (was `auto`),
  `QuickFlow=auto`; `[FrameGen]` keeps `Enabled`, `FGInput`, `FGOutput`, `FGNvngxReplacement` (all `auto`; the comment
  says "HelixSR is unavailable in this release"), `PreserveSwapChain`; `[Log]` has `LogToFile`/`LogLevel`; `[Plugins]`
  `LoadReshade=auto`; `[Hotfix]` `CreateD3D12DeviceForLuma=auto` (still the positive name). The Scaler README
  says: "The HelixSR frame-generation route stays disabled even if an older configuration or helixsr-fg folder remains in
  the game ... An old HelixSR provider choice is redirected to FFX."
- `OptiScaler/amd_fidelityfx_upscaler_dx12.dll` in the Scaler 1.0.6 package: version `4.1.1.2740` (read with
  `amdlss.pe.inspect`), the FSR 4.1.1 DLL AMDLSS uses; `OptiScaler.dll` identifies as `HelixSR Scaler v1.0.6 (OptiScaler
  v10.0.0-dev (91df077) (20261016_141656))`.

Decisions:

- Pins: `helixsr.py` 1.9.3 / 136076974 / `7094b7af...`; `scaler.py` 1.0.6 / 133228253 / `1256a5c5...`. Asset names are
  derived from the version. Nothing else changes in the fetch.
- HelixSR's own frame generation is retired, not hidden. `helixsr.fg_folder`, `fg_reason`, `FG_FOLDER`, `FG_DLLS`,
  `FG_SETUP` and the "Update HelixSR to build frame generation" prompts are removed (a finished build is finished, the
  button no longer stays enabled for files that cannot exist); the consent text no longer promises a 400 MB NVIDIA
  download (the 1.9.3 setup downloads NVIDIA's DLSS DLL (56 MB) plus the Python and compiler it always needed); `scaler.install_into` no
  longer copies `helixsr-fg`.
- The DLSS 4 generator is no longer a choice (`framegen.apply_generator(..., DLSS4)` means off, and the window shows two
  buttons). `framegen.DLSS4`, `HELIX_FG`, `DLSS4_FG` and `generator_of` stay, only so a game an earlier AMDLSS set to
  that mode is recognised: the window shows Off with "HelixSR 1.9.2 and later have no frame generation of their own.
  AMD FSR frame generation is still available. Install or Update turns it off.", Install/Update restores the recorded
  values (or writes the off values, or `clear_dlss4` when there is no record) and the after-install text and game log
  say "DLSS 4 frame generation is off: <reason>". Scaler 1.0.6 would redirect that stale provider choice to FFX, so
  leaving it would run something the player did not pick.
- AMDLSS keeps writing frame generation off by default (`framegen.OFF`: `[FrameGen] Enabled=false`, `FGInput=nofg`,
  `[HelixSR] FrameGeneration=off`), exactly as before. 1.0.6 ships `FrameGeneration=off` but `Enabled`/`FGInput` are
  still `auto` in the package ini, so the explicit off values still matter. The AMD FSR frame generation choice
  (`Enabled=true`, `FGInput=dlssg`, `FGOutput=fsrfg`, `PreserveSwapChain=false`) is unchanged: the keys exist in the 1.0.6
  ini and the notes keep FSR frame generation "selectable".
- Routes and labels: no change to the bridge mapping (`unsupported`/`scaler`/`blocked` -> `optiscaler`) or to
  `games._ROW`; verified on the real 1.9.3 output above. A regression test pins the verbatim printed list.
- `VERSION` stays `1.3.0`; the release is not built or published here.
- Not changed, flagged: the player-visible text still says "OptiScaler 0.9.4 with HelixSR built in" (`scaler.plan_text`,
  `fsr4.plan_text`, README); the 1.0.6 DLL reports OptiScaler v10.0.0-dev. It was already stale at 1.0.4 and is outside
  this task.

Tasks:

- [x] Repin `helixsr.py` and `scaler.py`; `test_fetch.py` pinned-values test.
- [x] Real 1.9.3 build through AMDLSS's fetch + build; inspect list labels, routes, ini keys, FG files.
- [x] Remove HelixSR FG machinery (`fg_*`, consent text, build log line, `scaler` copy, "Update to build FG" button state).
- [x] Retire the DLSS 4 choice; keep leftover recognition and switch-off (`framegen`, `app`, `games.after_text`,
      `fsr4.after_text`).
- [x] Tests first where deterministic: `fake_helixsr.py` versions, `test_helixsr.py`, `test_fetch.py`, `test_scaler.py`,
      `test_framegen.py`, `test_app_smoke.py`, `test_games.py` (real 1.9.3 list), `test_fsr4.py`,
      `test_real_helixsr.py` (real ini), `selftest.py`.
- [x] README: 1.9.3 / 1.0.6 where it describes current behaviour; stale 1.0.3; frame generation section.
- [x] Full suite under Xvfb :65, `node --test` in `receiver/`, `--selftest`, real-zip tests.

Checks:

1. `setsid Xvfb :65 -screen 0 1280x900x24 &` then
   `DISPLAY=:65 uv run --no-project -q --managed-python --python 3.12 --with pytest --with pillow python -m pytest -q`.
2. `cd receiver && node --test`; `DISPLAY=:65 python3 AMDLSS.pyw --selftest`.
3. Real 1.9.3 build in `/tmp/helixsr-193-data`, then
   `AMDLSS_TEST_HELIXSR=/tmp/helixsr-193-data/HelixSR-1.9.3 AMDLSS_TEST_SCALER=/tmp/helixsr-193-data/HelixSR-Scaler-1.0.6 pytest tests/test_real_helixsr.py tests/test_pe.py`.

Evidence (observed 2026-10-16):

- `pytest -q` (Xvfb :65): 531 passed, 9 skipped (7 env-gated `test_real_helixsr.py`, 1 `test_pe.py`, 1 Windows-only
  `test_identify.py`).
- Real zips: `tests/test_real_helixsr.py tests/test_pe.py` -> 16 passed.
- `node --test` in `receiver/`: 23 passed. `python AMDLSS.pyw --selftest`: "selftest passed".
- Commits: see `git log feat/helixsr-193`.
