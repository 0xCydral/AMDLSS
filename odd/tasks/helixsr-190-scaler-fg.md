# HelixSR 1.9.0 (new asset), HelixSR Scaler and DLSS 4 frame generation

Objective: (T1) new installs and "Update HelixSR" work again after HelixSR re-published v1.9.0 and removed the
`HelixSR-1.9.0.2.zip` asset AMDLSS 1.0.0 pinned; the OptiScaler route installs HelixSR Scaler 1.0.3 (the asset that
now carries OptiScaler + HelixSR for games without FSR 3.1). (T2) on the OptiScaler/Scaler route the user can pick
AMD FSR frame generation (today's behaviour) or HelixSR's experimental DLSS 4 frame generation. (T3) docs, version
1.1.0 and release notes.

Why: `HelixSR-1.9.0.2.zip` 404s today (the asset was replaced by `HelixSR-1.9.0.zip`), so every install and update
is broken. The 1.9.0 zip's own installer now says "the setup scripts in HelixSR-1.9.0.zip install only the FSR
drop-in"; its `helixsr_install.py` has no OptiScaler install route at all (`install()` at ~line 1090 always takes
`direct`; an earlier version's opti install `can only be removed`; `Game.can_opti` and the module's `state()` /
`installed_version()` helpers are gone). OptiScaler now travels in the separate, GPL-3.0 HelixSR Scaler package
(`HelixSR-Scaler-1.0.3.zip`, OptiScaler 0.9.4 base).

Evidence (HelixSR v1.9.0 release, 2026-10-15):

- `HelixSR-1.9.0.zip` 3803249 bytes, SHA-256 `795268f60a1262cd7c8cb01a38f553751cc380fa0cbec63c8f466b6807468be4`
  (`SHA256SUMS.txt`), extracts to `HelixSR-1.9.0/`.
- `HelixSR-Scaler-1.0.3.zip` 132504212 bytes, SHA-256
  `974c9d62ea0e088a1e11fe8e7e3196b2d9939d39fb5d709b684c5e7c2c74c351`, extracts to `HelixSR-Scaler-1.0.3/`.
- Scaler layout in a game (its own `setup_linux.sh` / `setup_windows.bat` / `HelixSR-Scaler-package.txt`): every
  file the package list names is copied next to the game's .exe (`OptiScaler.dll` renamed to the first free of
  dxgi/winmm/version/dbghelp/d3d12/wininet/winhttp, else dxgi.dll), plus HelixSR's built `amd_fidelityfx_dx12.dll`
  as `HelixSR\helixsr.dll` and its `helixsr-fg\*.dll` as `HelixSR\helixsr-fg\*.dll`. Replaced game files go to its
  own backup folder; its Linux script keeps a `HelixSR-Scaler.files` manifest and a `HelixSR-Scaler-remove.sh`.
- FG build outputs in the built HelixSR folder: `helixsr-fg\` (nvexec64.dll shipped; `_nvngx.dll` from NVIDIA's
  595.71.05 driver ~404 MB, `nvngx_dlssg.dll` from Streamline 2.8.0, `libxell.dll` from Intel's XeSS SDK) and
  `helixsr_fg_setup.json` (`setup/fg/fg_setup.py`).
- DLSS 4 FG ini keys (Scaler 1.0.3 README + OptiScaler.ini + Config.cpp): `[HelixSR] FrameGeneration=on`,
  `[FrameGen] FGInput=nvngxfg`, `[FrameGen] FGNvngxReplacement=HelixSR`. `FGInput=nvngxfg` forces `FGOutput` to
  NoFG at runtime (dllmain.cpp); the game's own DLSS Frame Generation is the on/off switch. One generated frame.
- The upscaler network is DLSS Model E (CNN); no transformer option exists and none is offered.

Scope: `amdlss/helixsr.py`, new `amdlss/scaler.py`, `amdlss/games.py`, `amdlss/framegen.py`, `amdlss/fsr4.py`,
`amdlss/app.py`, `amdlss/reports.py`, `amdlss/logs.py`, `amdlss/selftest.py`, `README.md`, `amdlss/__init__.py`,
tests. No push, no release, no tag. `.serena/` and `design/` are never staged.

Decisions:

- The OptiScaler route is AMDLSS's own install code in `amdlss/scaler.py`, following Scaler's own placement rules
  and the package's own file list. Scaler ships no non-interactive cross-platform installer (Windows: an
  interactive .bat; Linux: a bash script keeping its own manifest/backup), and AMDLSS must install and remove the
  same way on both systems, so the file list is read from the package (`HelixSR-Scaler-package.txt`) and the
  placement mirrors it in Python. HelixSR's build output is copied, never rebuilt.
- The AMDLSS record is `HelixSR\amdlss-scaler.json` (moved/created with SHA-256s, like `amdlss-fsr4.json`);
  replaced game files are kept under `HelixSR\amdlss-scaler-backup\` and put back by Remove. Scaler's own manifest
  is not written: their remover restores from *their* backup folder, which AMDLSS does not keep.
- AMDLSS 1.0.0 installs (marker `HelixSR\helixsr-install.json`, OptiScaler 0.9.4 via the old bridge) are
  recognised by the new installer itself (`route='optiscaler'`) and removed by its own `uninstall N`
  (`uninstall_optiscaler`, which exists exactly for earlier versions). AMDLSS calls that, never re-implements it.
- The list bridge keeps running HelixSR's own scan; it maps the new `'unsupported'` route (DLSS/XeSS/FSR 2 only) to
  AMDLSS's `'optiscaler'` and computes `can_opti` (both routes) itself, because the new installer dropped those.
- Frame generation: `framegen.FRAMEGEN` (AMD FSR: `Enabled=true`, `FGInput=dlssg`, `FGOutput=fsrfg`,
  `PreserveSwapChain=false`) stays; new `framegen.HELIX_FG` (`[HelixSR] FrameGeneration=on`) and
  `framegen.DLSS4_FG` (`FGInput=nvngxfg`, `FGNvngxReplacement=HelixSR`). `framegen.apply_generator` switches
  exactly one generator on (or both off), restoring the previous values through the existing record machinery.
- DLSS 4 is offered only when the built HelixSR folder holds the FG files (`helixsr_fg_setup.json` and
  `helixsr-fg\` with nvexec64.dll, nvngx_dlssg.dll and _nvngx.dll); otherwise its segment is greyed with the
  reason. Without the FG files the shipped `FrameGeneration=auto` has nothing to run.
- The window uses a three-way segmented control (Off | AMD FSR frame generation | DLSS 4 (HelixSR), experimental)
  in the existing style; reports and "Send logs" name the generator.

## Tasks

- [x] T1 Pin HelixSR 1.9.0 (`HelixSR-1.9.0.zip`, 3803249, `795268f6…`); bridge maps `unsupported` and computes
  `can_opti`; `amdlss/scaler.py` downloads/checks/extracts Scaler 1.0.3 and installs it on the OptiScaler route
  (record + backup, Remove restores); old AMDLSS 1.0.0 installs are removed through HelixSR's own uninstall;
  fakes and tests updated. Also: 1.9.0 no longer builds `amd-fsr`, so AMD's FSR 4 DLL and its license now come
  from the Scaler package when the HelixSR folder has none (`fsr4.amd_dll`); the streamline scan skips the mod's
  own folders (`OptiScaler`, `HelixSR`), else its copied `streamline` DLLs made every Scaler game look like it
  offered DLSS-G.
- [ ] T2 DLSS 4 frame generation choice on the OptiScaler/Scaler route for games with Streamline DLSS-G, gated on
  the built FG files; ini keys through `framegen`; switching/off restores exactly; reports name the generator.
- [ ] T3 README frame generation section + DLSS Model E note; `__init__.py` 1.1.0; release notes for players at
  `/tmp/amdlss-rel/notes110.md`.

## Checks

1. Full suite under Xvfb: `uv run --no-project -q --managed-python --python 3.12 --with pytest --with pillow
   python -m pytest -q`.
2. Real installer: `tests/test_real_helixsr.py` with `AMDLSS_TEST_HELIXSR` (a built 1.9.0 folder),
   `AMDLSS_TEST_SCALER` (the extracted Scaler package) and `AMDLSS_TEST_INSTALLER` (the new zip's
   `setup/helixsr_install.py` laid over a 1.9.0.2-built copy when needed): Scaler route + DLSS 4 ini keys in a
   fake game, old-style opti install removal.
3. `bash packaging/release.sh > /tmp/amdlss-rel/build.log 2>&1`: both selftest exits 0, both binaries report
   1.1.0, both SHA-256s reported.
4. Screenshots of the selected-game panel at 1366x768, 1920x1080, 2560x1440 under Xvfb.

Known environment: `amdlss-wine` needs the Steam library mounted; the Wine build is reported as observed.
Not verifiable here: frame generation never runs (no AMD/Windows PC); the DLSS 4 path is verified at the ini and
file level only.
