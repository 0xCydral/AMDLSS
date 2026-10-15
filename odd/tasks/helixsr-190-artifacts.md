# RX 6700 XT artifacts: Wukong square tiles, Stellar Blade orbs (HelixSR 1.9.0 + Scaler 1.0.3)

Objective: explain the picture defects the tester sees with HelixSR 1.9.0 / HelixSR Scaler 1.0.3 on an RX 6700 XT
and decide between an AMDLSS change and a HelixSR issue. Reflex is already eliminated by the owner: the same PC and
game (Stellar Blade), original OptiScaler + Nukem's dlssg-to-fsr3, no HelixSR, Reflex forced on as usual - clean
picture.

Evidence (tester's PC, 2026-10-15): Windows 11 10.0.22631, Ryzen 5 5600X, 16 GB, RX 6700 XT (RDNA 2), driver
32.0.21041.5002; AMDLSS 1.1.0; HelixSR 1.9.0 (helixsr.dll 1.9.0.0, SHA-256 63f45e07b428...); HelixSR Scaler 1.0.3;
collected logs and game files under `/tmp/wukong-logs`.

- Wukong (UE5, Steam): upscale 1284x724 to 1080p, 1.02 ms per frame; menu shows Speed boost (int8) on and wave 32;
  the game's DLSS Frame Generation runs through HelixSR ("FG HelixSR 2x"), about 143 FPS. Symptom: large square
  block artifacts (grey/black/blue checkerboard tiles of uniform size) on snow. Speed boost (int8) off: no change.
- Stellar Blade (same PC, HelixSR via Scaler): many large soft coloured orbs (green, pink, blue, white, red), the
  size and look of bloom around single extremely bright pixels, and scattered single-pixel coloured speckles (blue,
  purple, white, red) over dark surfaces.

`/tmp/wukong-logs/exe-folder/OptiScaler.log` (the loaded OptiScaler build):

- line 1: `[W] OptiScaler v0.9.4-final (7534ad0) loaded`
- line 38: `[I] CheckWorkingMode OptiScaler working as d3d12.dll, system dll loaded`
- line 128: `[I] fakenvapi::Init Failed to initialize fakenvapi`
- lines 317-318: `helixsr.dll loaded from override path: ...\Win64\HelixSR\helixsr.dll` and
  `Nvngx_HelixSR::LoadLibraries HelixSR frame generation initialized`

`/tmp/wukong-logs/exe-folder/HelixSR/helixsr.log` (all six lines):

```
[14:19:42.037] ngx: init (ext)
[14:19:42.044] fg: kernels inside this DLL in use for NVIDIA's core
[14:19:42.044] fg: NVIDIA core loaded, executor loaded
[14:19:42.044] fg: 7 imports of d3d12.dll routed (nvapi64.dll -> executor, nvngx_dlssg.dll -> helixsr-fg)
[14:19:42.044] fg: GPU supports frame generation (shader model 6.6, waves 32-64 lanes, 64-bit integers)
[14:19:42.049] ngx: init (ext)
```

`HelixSR/helixsr.ini`: `[Compatibility] WaveSize = 32`, `[Sharpening] Sharpener = off`.
`HelixSR/helixsr_setup.json`: `{"ptx_target": "sm_89", "shaders": 90, "wave64": true, "fp32": true,
"source_sha256": "be6e434a...", "weights_sha256": "762adfde..."}`.

## Findings

1. Reflex is not involved and nothing here is an AMDLSS defect. HelixSR Scaler ships no `fakenvapi.dll` /
   `nvapi64.dll`: `HelixSR-Scaler-package.txt` lists 31 files (OptiScaler.dll, OptiScaler/*, streamline/*) and no
   NVAPI file. fakenvapi is built into the Scaler's OptiScaler: it exports `nvapi_QueryInterface`
   (`OptiScaler/nvapi/fakenvapi.cpp:40`) and `LoadNvApi` (`OptiScaler/hooks/LibraryLoad_Hooks.cpp:764-790`,
   `setUsingAsMainNvapi(true)` on lines 775 and 790) hands the game the OptiScaler dll itself as `nvapi64.dll`
   when the primary GPU is not Nvidia (`UseFakenvapi` on). Governed by `[fakenvapi] UseFakenvapi=auto` (documented default true),
   `ForceReflex=auto` (auto = 0, follow the in-game setting), `LowLatencyInput/Output=auto`, `LatencyFlexMode=auto`,
   `ForceLatencyFlex=auto`; `[DLSSG] UseGamesReflexMarkers=auto` (default true; `Reflex_Hooks.cpp:186` uses the
   game's markers for OptiScaler's own DLSSG while `FGDLSSGUseGamesReflexMarkers` is on); `[Framerate]
   FramerateLimit=auto`. All are `auto` in the shipped ini (lines 316, 1092, 1114, ...). In this run fakenvapi failed
   to initialise (OptiScaler.log:128) while HelixSR's frame generation ran anyway: the game's Reflex plumbing is not
   what breaks the picture, and the owner's Stellar Blade control (Reflex forced on, no HelixSR) is clean.
2. The logs show no bad upscaler input. Neither log mentions motion vectors, depth, exposure, jitter or resource
   states; the OptiScaler log stops at NGX init (373 lines, truncated while the game ran) and helixsr.log has only
   the frame generation lines. The only structural anomaly is the mixed build in finding 3.
3. The Wukong run was a mixed build. The game loaded the older OptiScaler copy sitting in the folder
   (d3d12.dll, 2026-09-12) - not Scaler 1.0.3's dxgi.dll (2026-10-15): `working as d3d12.dll`, and the loaded build
   returns itself for `dxgi.dll` loads. AMDLSS 1.1.0 does not move a foreign OptiScaler copy aside; commit
   `2babcd5` (in the released 1.1.1, not in 1.1.0) fixes exactly that, so a retest with a clean Scaler-only install
   is still owed. It cannot explain the Stellar Blade case (no build log there).
4. Known 1.9.0 quality regressions sit next to this one: HelixSR issues #44 (RX 7800 XT flicker; maintainer:
   "Okay so it's the int 8 path. I'll fix it today!") and #46 (RX 6650 XT shimmering, 1.9.0 vs older). No open issue
   matches square tiles / orbs / speckles (searched checkerboard, artifacts, bloom, fireflies, speckles, snow);
   HelixSR-Scaler has issues disabled. The Scaler's `Issues.md` documents the classic input workarounds
   (`AutoExposure=true` for crushed dark colors, `ColorResourceBarrier=4` for coloured areas) - neither symptom
   matches this report.
5. No ini setting has concrete evidence of avoiding it. HDR was the maintainer's guess in #44 and its reporter
   disproved it (HDR off, int8 on/off, still flickering); Speed boost off was already tried here with no change;
   `AutoExposure` / `ColorResourceBarrier` treat different symptoms; `WaveSize=64` is documented only for RX
   7000/9000 cards ("if the picture is broken"). So AMDLSS must not write any of them as a default.

## Decision

- No AMDLSS change, no 1.1.2, no release, no player notes: nothing points at an AMDLSS default or file. The
  mixed-build state is already the subject of the 1.1.1 fix. Do not change AMDLSS on speculation.
- Draft a HelixSR issue with both games and the log evidence (below).
- For the owner (proposal, not a change): `README.md:58` and `amdlss/games.py:446,448` still say "Keep NVIDIA
  Reflex off". The artifact evidence says Reflex is not the cause (the line may predate this report and could have
  a different, still valid reason), so the wording deserves a revisit outside this task.

## HelixSR issue draft

```markdown
Title: 1.9.0 image corruption on RX 6700 XT: square tiles (Black Myth: Wukong) and coloured orbs/speckles (Stellar Blade)

**PC:** Windows 11 (10.0.22631), Ryzen 5 5600X, 16 GB, AMD RX 6700 XT (RDNA 2), driver 32.0.21041.5002.
HelixSR 1.9.0 + HelixSR Scaler 1.0.3.

## Black Myth: Wukong (UE5, DLSS Frame Generation)

Upscaling 1284x724 to 1080p: about 1.02 ms per frame; the menu shows Speed boost (int8) on and wave 32. DLSS Frame
Generation through HelixSR ("FG HelixSR 2x"), about 143 FPS.

The picture shows **large square block artifacts (grey/black/blue checkerboard tiles of uniform size) on snow**.
Setting Speed boost (int8) off did not help.

`helixsr.log` (all of it):

    [14:19:42.037] ngx: init (ext)
    [14:19:42.044] fg: kernels inside this DLL in use for NVIDIA's core
    [14:19:42.044] fg: NVIDIA core loaded, executor loaded
    [14:19:42.044] fg: 7 imports of d3d12.dll routed (nvapi64.dll -> executor, nvngx_dlssg.dll -> helixsr-fg)
    [14:19:42.044] fg: GPU supports frame generation (shader model 6.6, waves 32-64 lanes, 64-bit integers)
    [14:19:42.049] ngx: init (ext)

OptiScaler.log: "Nvngx_HelixSR::LoadLibraries HelixSR frame generation initialized" (14:19:42.005282);
"fakenvapi::Init Failed to initialize fakenvapi" (14:19:41.312890).

helixsr.ini: `[Compatibility] WaveSize = 32`, `[Sharpening] Sharpener = off`. helixsr_setup.json:
`{"ptx_target": "sm_89", "shaders": 90, "wave64": true, "fp32": true, ...}`.

Note: the game folder had an older OptiScaler copy (d3d12.dll, 2026-09-12) and the game loaded that one; the Scaler
1.0.3 file (dxgi.dll, 2026-10-15) did not load. We will retest with a clean Scaler-only install as well.

## Stellar Blade (same PC, HelixSR Scaler)

Many large soft coloured orbs (green, pink, blue, white, red) - the size and look of bloom around single extremely
bright pixels - and scattered single-pixel coloured speckles (blue, purple, white, red) over dark surfaces.

Control: the same game and PC with the original OptiScaler and Nukem's dlssg-to-fsr3 (no HelixSR), Reflex forced on
as usual: no artifacts.

## Pattern: only where liquids reflect

The artifacts appear only on reflective liquid and wet surfaces, never on the rest of the picture:

- Stellar Blade: only while it rains, and when the camera is underwater.
- Black Myth: Wukong: only on the snow.

## Questions

- These are the surfaces where the game draws reflections. Reflections can carry very bright values, and their
  motion does not match the surface's motion vectors. Does HelixSR clamp or sanitise such colour values (NaN, Inf,
  above the expected range) before the network, and does it handle pixels whose motion vectors do not match what they
  show?

- Is this the same fault as #44 (int8 path) or a second one in 1.9.0? Speed boost off changed nothing here. Would a
  run with `[HelixSR] FrameGeneration=off` (and `WaveSize=64`) help you split upscaler and frame generation? We can
  do that and send `helixsr.log`.
- Anything to check on the upscaler inputs (motion vectors, depth, exposure) or to try in the ini?

Screenshots and full logs available.
```

## Unverified / next steps

- Which component (upscaler or DLSS 4 frame generation) makes the artifacts: not split; a
  `[HelixSR] FrameGeneration=off` run would tell, and the issue offers it.
- Whether a clean Scaler-only install still shows the Wukong tiles: not retested (the run used the stale copy).
- Stellar Blade settings and logs: none captured.
- `fakenvapi::Init Failed to initialize fakenvapi`: cause not in the logs; harmless in this run (frame generation
  and the game both ran).
- The 1284x724 -> 1080p / 1.02 ms / int8 on / wave 32 values come from the tester's menu, not from a log.

## Checks

- No AMDLSS code was changed. Full suite at this commit (Xvfb :61): `509 passed, 9 skipped in 36.26s`
  (the 9: 7 env-gated `test_real_helixsr.py` cases, 1 `test_pe.py`, 1 Windows-only `test_identify.py`).
