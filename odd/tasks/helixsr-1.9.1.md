# HelixSR 1.9.1 (pin) and HelixSR Scaler 1.0.4; AMDLSS 1.3.0

Objective: pin HelixSR 1.9.1 (`HelixSR-1.9.1.zip`) and HelixSR Scaler 1.0.4, adapt AMDLSS to everything the 1.9.0 →
1.9.1 diff breaks (route names, native Scaler installs, printed-list labels), keep FG off explicitly written unless
the player picks a generator, and prepare the 1.3.0 release (build + player notes; no push, tag or publish here).

Why: HelixSR 1.9.1 (2026-10-16T02:39:43Z) re-made the release again. The zip is now 137 MB: it carries the whole
`helixsr-scaler/` package (previous pin: 3.8 MB), the setup scripts install HelixSR Scaler themselves, the installer
gained a full Scaler install/uninstall with its own record, and its routes changed (`unsupported` is gone:
`scaler`/`blocked` are new). Scaler 1.0.4 adds the `QuickFlow` frame generation quality key. AMDLSS 1.2.0's pins are
stale and its bridge does not know the new route names.

Evidence (downloaded 2026-10-15, each in a fresh empty dir; `SHA256SUMS.txt` matched):

- `HelixSR-1.9.1.zip` 137494566 bytes, SHA-256 `9f329a8a4359bc21830b1e0b3f8211f52d851760faddc5cbde74e245cf5140ff`
  (SHA256SUMS.txt), extracts to `HelixSR-1.9.1/`.
- `HelixSR-Scaler-1.0.4.zip` 132516449 bytes, SHA-256 `7ad033590b9a138fc524160c4c9ff105f0b20320e4120a43fb7766392e62a86f`.
- File list diff vs 1.9.0: `helixsr-scaler/` added (OptiScaler.dll, OptiScaler.ini, OptiScaler/, Licenses/, README.txt,
  setup_linux.sh, setup_windows.bat, HelixSR-Scaler-package.txt); `amd_fidelityfx_dx12.dll` 3398656 → 4254720;
  `setup/helixsr_install.py` 57475 → 90060; setup scripts grew; `__pycache__/helixsr_install.cpython-314.pyc` gone.
  `helixsr-setup.sh/.ps1/.bat` and `helixsr-uninstall.*` still exist (entry point and flags kept).
- The embedded `helixsr-scaler/OptiScaler.dll` differs from the standalone Scaler zip's by 14 bytes only: PE
  checksum + the build stamp (`v10.0.0-dev (b4b4afb) (20261016_022322)` vs `(20261016_023408)`); same version
  string. The Scaler zip's other files are byte-identical to the embedded copy.
- `helixsr-setup.sh`: `--yes`/`-y` still answers every question yes (ask() returns 0), so AMDLSS's `--yes`/
  `-Yes` still builds FG by default (the new "Set up frame generation?" question is auto-answered yes; `--no-fg`
  skips, `--fg` re-asks; `helixsr_fg_choice.txt` only appears in interactive runs). `setup/helixsr_setup.py`
  flags unchanged (`--status`, `--fg-only`, `--no-fg`, `--yes`); `fg_setup.py` still writes
  `helixsr_fg_setup.json` and fills `helixsr-fg/`, so `helixsr.built`/`fg_reason` still hold.
- Installer 1.9.1: `Game.route` is now `direct` / `scaler` / `blocked` (DLSS/XeSS/FSR 2 + anti-cheat) / `dlss` /
  `optiscaler` / None — `unsupported` is gone. New `Game.ways()`, `direct_installed()`, `current()`,
  `scaler_installed` (`HelixSR-Scaler.files` next to the .exe), `anticheat`. `install(g, here, fg_new=False)`
  installs the right way per `g.route`; `install_scaler()` installs Scaler 1.0.4 from `helixsr-scaler/` with its
  own record (`HelixSR-Scaler.files`, `HelixSR-Scaler-backup`, `HelixSR-Scaler-remove.sh` on Linux) and
  `uninstall_scaler()` puts everything back; `uninstall N` covers `dlss`, `optiscaler`, `scaler` and the drop-in.
  `helixsr_install.py HELIXSR_FOLDER list | install N.. | uninstall N.. | install-all | uninstall-all | remove
  [--folder ..] [--fg on|off]` — AMDLSS's command lines unchanged.
- Scaler 1.0.3 → 1.0.4 ini: exactly one addition, `QuickFlow=auto` in `[HelixSR]` (frame generation quality:
  auto/Best, on/Fast, fastest/Fastest; restart needed). `DisableReactiveMask=auto` (`[InitFlags]`) exists in both;
  the release notes say to untick it in HelixSR Scaler's menu (Insert) to use the game's reactive mask (particle
  and hair ghosting). Both are HelixSR Scaler *menu* settings, not AMDLSS settings.
- Release notes 1.9.1: FG now starts only when a game asks; crash safety net keeps FG off next run; int8
  recalibrated (the #44/#46 flicker/shimmer path); speed boost works; RX 400/500 GVC builds; setup installs the
  FSR drop-in or HelixSR Scaler per game; `manual/` folder; `helixsr-uninstall` handles 1.8.1/1.9.0/Desktop.
- RX 6700 XT liquid-reflection artifacts (`odd/tasks/helixsr-190-artifacts.md`): no HelixSR issue matches the
  symptoms (`snow`, `Wukong`, `reflection`, `artifacts`, `speckles`, `orbs`, `6700` all empty; HelixSR-Scaler has
  issues disabled). #44/#46 are int8 flicker/shimmer and 1.9.1's recalibration answers those (a reporter confirms
  the new version is fixed for them). Nothing in the 1.9.1 notes names tiles/orbs/speckles on reflective liquids;
  the reactive mask note is about particle/hair ghosting. Not addressed by evidence → no AMDLSS change; a retest
  with 1.9.1 (and with the reactive mask) is the next step, the owner's call.

Scope: `amdlss/helixsr.py`, `amdlss/games.py`, `tests/fake_helixsr.py`, `tests/test_helixsr.py`,
`tests/test_games.py`, `amdlss/scaler.py` (pin), `amdlss/__init__.py` (1.3.0), release notes under
`/tmp/amdlss-rel130/`. No push, no tag, no publish. `.serena/` and `design/` are never staged.

Decisions:

- Pin the standalone `HelixSR-Scaler-1.0.4.zip` (the asset SHA256SUMS.txt covers) rather than the embedded
  `helixsr-scaler/` copy in the HelixSR zip: AMDLSS's separate download keeps working for its own Scaler install
  path (which supports FSR 4.1.1, Luma UE and AMDLSS's record/backup), needs no new extraction code, and the
  embedded copy is the same build modulo its build stamp. The Scaler URL already follows `helixsr.VERSION`.
- The bridge keeps mapping the Scaler route to AMDLSS's own name `optiscaler`: 1.9.0's `unsupported` (kept for
  users who have not updated yet) and 1.9.1's `scaler`. `blocked` maps to `optiscaler` too, so AMDLSS keeps its
  1.2.0 behaviour (warn, player decides) rather than silently adopting HelixSR's new hard refusal for
  anti-cheat games; the difference is named in the report for the owner to decide (one-line alternative).
- `state()` reports 1.9.1 native Scaler installs as `optiscaler` when `scaler_installed` is set, so
  Update/Remove reach them: `scaler.install_game` and `fsr4.remove_game` already route a non-AMDLSS
  `optiscaler` state through `helix.remove` (`uninstall N`), which in 1.9.1 removes Scaler natively. Games from a
  not-yet-updated 1.9.0 folder have no `scaler_installed` (getattr default False) and behave as before.
- Printed-list fallback (`games.parse_list_output`): recognize 1.9.1's labels (`FSR 3.1: FSR drop-in (…`,
  `…: HelixSR Scaler (select DLSS in the game)`) next to 1.9.0's, so a bridge failure on a 1.9.1 folder still
  yields the same routes.
- Frame generation: AMDLSS's explicit off values (`framegen.OFF`) stay exactly as they are; 1.9.1's Scaler reads
  `[HelixSR] FrameGeneration`, and Scaler's own auto would run FG whenever the files are there, so AMDLSS must
  keep saying off. No new UI for FG quality (`QuickFlow`) or the reactive mask: both are HelixSR Scaler's own
  Insert-menu settings (release notes), the smallest useful change is not to duplicate them; AMDLSS's record
  machinery leaves unrelated ini keys untouched.
- Update detection needs no code: `package_current` fails for a 1.9.0 extraction (zip_sha256/folder mismatch) and
  `older('1.9.0', '1.9.1')` is true, so "Update HelixSR" shows.

Tasks:

- [ ] Repin `helixsr.py` (1.9.1: 137494566 / `9f329a8a…`) and `scaler.py` (1.0.4: 132516449 / `7ad03359…`).
- [ ] Bridge: route mapping (`unsupported`/`scaler`/`blocked` → `optiscaler`), `scaler_installed` → state
      `optiscaler`; comments.
- [ ] `games.py`: `_ROW` accepts 1.9.1 labels; parse routes for the new Scaler label.
- [ ] Fakes/tests first where deterministic: fake installer models 1.9.1 routes + a native Scaler install;
      bridge mapping tests; label parsing tests; scaler pin tests.
- [ ] Full suite under Xvfb :64, `node --test receiver/`, `--selftest`.
- [ ] 1.3.0 in `amdlss/__init__.py` as its own `chore(release): 1.3.0` commit.
- [ ] Build `bash packaging/release.sh` with output under `/tmp/amdlss-rel130/` (never `/tmp/amdlss-rel`, which
      holds the 1.2.0 assets being published); verify artifacts; player notes `/tmp/amdlss-rel130/notes130.md`.

Checks:

1. Full suite: `setsid Xvfb :64 -screen 0 1280x900x24 &` then
   `DISPLAY=:64 uv run --no-project -q --managed-python --python 3.12 --with pytest --with pillow python -m pytest -q`.
2. `node --test receiver/`; `python AMDLSS.pyw --selftest`.
3. Real 1.9.1 zip build through AMDLSS's own fetch + build (`/tmp/helixsr-191-data`), then
   `tests/test_real_helixsr.py tests/test_pe.py` with `AMDLSS_TEST_HELIXSR`/`AMDLSS_TEST_SCALER`.
4. Build: both selftest exits 0, both binaries report 1.3.0, SHA-256s recorded, notes written.

Evidence (observed):

- Real 1.9.1 build through AMDLSS's own fetch + build into `/tmp/helixsr-191-data` (2026-10-15, 122 s):
  `built True`, `version 1.9.1`, frame generation complete (`helixsr-fg/` with nvexec64.dll, _nvngx.dll,
  nvngx_dlssg.dll, libxell.dll and `helixsr_fg_setup.json`; `helixsr_setup.json` 120 shaders, sm_89), and the
  Scaler package fetched to `HelixSR-Scaler-1.0.4`. The setup's own printed game list carries the 1.9.1 labels;
  `games.parse_list_output` parses that real output (drop-in rows -> `direct`, "DLSS: HelixSR Scaler" rows ->
  `optiscaler`).
- `pytest -q` (Xvfb :64): 531 passed, 9 skipped (the 9: 7 env-gated `test_real_helixsr.py`, 1 `test_pe.py`,
  1 Windows-only `test_identify.py`).
- Real zips (`AMDLSS_TEST_HELIXSR=/tmp/helixsr-191-data/HelixSR-1.9.1`,
  `AMDLSS_TEST_SCALER=/tmp/helixsr-191-data/HelixSR-Scaler-1.0.4`): `tests/test_real_helixsr.py tests/test_pe.py`
  -> 16 passed.
- `node --test receiver/`: 23 passed. `python AMDLSS.pyw --selftest`: "selftest passed".
- Commits: `70dd525` (pins, bridge, labels, tests, README, selftest), `50198cc` (`chore(release): 1.3.0`),
  `63d3500` (`fix(release)`: release.sh builds the checkout it lives in instead of a hard-coded main path, so a
  worktree build no longer packages the main checkout; a run from the main checkout is unchanged).
- Build (`bash packaging/release.sh`, after the owner's 1.2.1 marker; its `/tmp/amdlss-rel` output copied to
  `/tmp/amdlss-rel130/`): `pyinstaller-win exit=0`, `build-linux exit=0`, both `check_build.py` runs print
  `VERSION 1.3.0` + `modules True`, `exe selftest exit=0`, `linux selftest exit=0`.
  - AMDLSS.exe 18979609 bytes, SHA-256 `49da5e70b27fa8c903f3f90774c82d3ae6fc9a9814eda37772d3b09f8a04b74e`
  - AMDLSS-linux-x86_64 24390456 bytes, SHA-256 `2b983578a78ef62f9add768f40b8f14803f6b7c4c8036d328f8bf27eee425865`
