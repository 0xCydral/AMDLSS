# amdlss_dx12_dlss

Stage 3b of the DX12 spike (`odd/tasks/dx12-spike.md`): a ReShade add-on that replaces the game's TSR upscale with DLSS
Super Resolution. Built separately from the probe in `probe/dx12/`; install one or the other, not both.

TSR comes in two layouts, told apart by the hash of the compute shader's DXIL (`logic::tsr_pass_for_hash`):

| mode | hashes | when |
|---|---|---|
| single pass | `1ff10c3ce95d3bcb`: UpdateHistory writes the 2560x1440 output and the half-size u1 | history at 100% of the output |
| two pass | `e32f96d4dc79be5b`: UpdateHistory (groups 640x360) writes the 5120x2880 history; `0b1375003bfb8294`: resolve (groups 427x240) reads it and writes the 2560x1440 output and the 1280x720 u1 | history at 200% (what the game uses with its current settings) |

The sections below describe the single-pass replacement; "Two-pass TSR" says how the second mode reuses it.

## What it does on that dispatch

1. Finds the pass's textures through the same descriptor-heap shadow the probe uses, at root param 1: output u0, half-size
   output u1, scene color u3, translucency u4, velocity u6. **Depth is not u14**: that slot is an unrelated one UE
   refills every frame (live: empty, 1x1, a 5712x5712 shadow map, an 8192x1024 UAV), and the final TSR pass reads no
   depth. DLSS gets the newest depth buffer bound for rendering whose size equals the render size.
2. Reads TSR's own constants, the root CBV b0 at root param 2. Offsets from the disassembly of UE's shader (`logic.hpp`):
   InputInfo_ViewportMin @32 and ViewportSize @48, HistoryInfo_ViewportMin @144 and ViewportSize @160, InputJitter
   @256 (float2, render pixels, found in T7), bCameraCut @264, ScreenVelocityToInputPixelVelocity @272.
   - **Option A (used first):** the CBV lives in an upload heap and UE wrote it on the CPU before recording, so
     `device::map_buffer_region` reads the first 280 bytes at record time, and `parse_tsr_constants` rejects values
     that cannot be a real TSR frame (empty viewport, jitter outside +-0.5, zero velocity scale).
   - **Option B (fallback):** each good read is matched to Halton(2,3)-0.5 (`jitter_cycle`, default 8) to lock the
     phase; if a later read fails the jitter is predicted from the evaluation count and the last viewport and scale
     are reused. If A never worked, nothing is replaced.
   - **Viewports:** DLSS works on the input viewport (live 1552x873 inside a 1552x876 texture; the rows below the
     viewport are garbage) and the history viewport, never on the texture extents. The feature is created with the
     viewport sizes and recreated when they change; color, depth and motion vectors get the input viewport origin as
     subrect base, the output the history viewport origin, and the decode pass writes only inside the input viewport.
   - **Camera cut:** `bCameraCut != 0` makes that frame an `InReset=1` frame and is counted as `camera-cut` in the
     accounting line.
3. One small compute pass (`velocity_decode.hlsl`, DXIL embedded at build time) does two jobs:
   - Decodes UE's velocity into DLSS motion vectors the way UE's TSR shader does (`mv_encoding=gamma`, the default):
     `e = 4.008016 * enc - 2.003992; V = e * abs(e) * 0.5`, which is the unbias `(enc - 32767/65535) / (0.499 * 0.5)`,
     divided by 2/1.4142, then `sign(e) * e * e`. TSR computes `PrevScreenPos = ScreenPos - V`, so V is current minus
     previous; the pixel velocity is V times ScreenVelocityToInputPixelVelocity, live (+0.5 W, -0.5 H) = (776, -436.5).
     DLSS wants current to previous, so the add-on multiplies by the negated game value (`mv_scale_source=game`,
     default) or by `(mv_scale_x * width, mv_scale_y * height)` from the ini (`mv_scale_source=ini`). `mv_encoding=linear`
     applies the unbias alone. It writes an `r16g16_float` texture.
   - With `composite_translucency=1`, composites the separate translucency u4 over the scene color u3 into an
     `r16g16b16a16_float` texture: `rgb = scene.rgb * trans.a + trans.rgb` (premultiplied, alpha = transmittance).
     DLSS then gets that texture as its color input. If u4 is missing, not `r16g16b16a16_float` or not render size,
     DLSS gets u3 raw and the log says why.
   The scales, encoding and composite flag are root constants, so an ini change needs no shader rebuild.
4. Evaluates DLSS on the game's own command list (`get_native()`), flags `IsHDR | AutoExposure | MVLowRes |
   DepthInverted` (`AutoExposure` follows `auto_exposure`), output written straight into u0, mode chosen from the
   render/output width ratio (nearest of DLAA, MaxQuality, Balanced, MaxPerf, UltraPerformance; DLSS 3.10 rejects
   UltraQuality). `InReset` is set on the first frame and whenever the feature is recreated, which happens when the
   render or output size, `render_preset` or `auto_exposure` changes.
5. Returns true from the `dispatch` event so ReShade skips the original dispatch.
6. If NGX, the feature, a binding or the jitter is missing, or the evaluation fails, it does not skip: the game
   renders normally and the reason is logged once.

NGX and the decode pass change the compute root signature, pipeline, descriptor heaps and root arguments of the
command list. UE caches that state and would not set it again, so everything is set back from the shadow
(descriptor heaps, root signature, pipeline, root tables, root CBVs, root constants) before the list continues.
Resource states are guessed from the descriptor kind: a UAV descriptor means the texture sits in the UAV state and is
moved to `NON_PIXEL_SHADER_RESOURCE` around the pass; an SRV descriptor is assumed to be there already.

Deviation from the brief: the decode pass uses native D3D12 calls (root signature, pipeline, a small descriptor
heap) instead of the ReShade pipeline API, because the heaps and root state have to be restored on the native list
anyway and ReShade's descriptor table allocation would add a second set of heaps to put back.

Known gaps, logged once: u2 (r8 history metadata) is no longer written, so passes that read it see stale data.
Translucency u4 is composited into the color (`composite_translucency`), and u1 (half-size r11g11b10) is filled from u0,
see below (`fill_half_res`).

## Two-pass TSR (history at 200%)

Measured on the title screen (`title-history200.log`, `D 152` and `D 153`, same command list, consecutive dispatches).
Root parameter 1 uses the same register layout as the single-pass shader, so `find_bindings` and the inputs are the same:

| dispatch | u0 | u1 | u2 | u3 / u4 / u6 |
|---|---|---|---|---|
| `e32f96d4dc79be5b` UpdateHistory | UAV, 5120x2880 `r11g11b10_float` (new history) | UAV, 1x1 `r11g11b10_float` (unused) | UAV, 5120x2880 `r8_unorm` (history metadata) | SRV, 1552x876: color `r16g16b16a16_float`, translucency `r16g16b16a16_float`, velocity `r16g16_unorm`; the old history is u11/u12 |
| `0b1375003bfb8294` resolve | UAV, 2560x1440 `r11g11b10_float` (the frame) | UAV, 1280x720 `r11g11b10_float` (bloom / eye adaptation) | SRV, the 5120x2880 history from UpdateHistory (the same resource) | unrelated slots |

What the add-on does:

1. **At UpdateHistory** it runs the single-pass checks (bindings, depth, sizes, TSR constants from b0) and keeps the result as
   a pending snapshot for that command list (`Pending`, `g_pending`). It does not touch the output yet.
2. **At the resolve on the same list** it takes the pending snapshot and finds the resolve's u0 (output) and u1 (half-size).
   It checks that the resolve's u2 is the resource UpdateHistory wrote (`resolve_pairs_with_update`) and that b0's history
   viewport is 1 to 4 times the output (`history_scale_ok`). Then DLSS runs exactly as in single-pass mode, with the DLSS
   output viewport taken from the resolve's output texture (origin 0): `with_output_viewport` replaces the history viewport
   from b0 (HistoryInfo describes the 5120x2880 history, not the output). The resolve is skipped, and `fill_half_res` fills u1
   from u0 as before.
3. **UpdateHistory is skipped only when it is safe** (`logic::TwoPassGate`). Skipping it leaves the game's history stale, so
   if the resolve then cannot be replaced the frame would show an old image. The first frame, and every frame after a failure,
   therefore run the game's UpdateHistory (`twopass-update-kept`) and replace only the resolve; the frame after one fully
   replaced frame skips both (`twopass-update-skipped`). Whatever fails on a skipped frame ends with the game's resolve running
   on the stale history for that one frame (`twopass-stale-history-frame`), and the next frame runs UpdateHistory again.
4. **Failures never leave a frame without an output.** At UpdateHistory a failed check runs the game's UpdateHistory and later
   its resolve (nothing is pending). At the resolve, a missing output, a pairing or size mismatch, a failing DLSS create or
   evaluate, or any other `replace_upscale` failure lets the game's resolve run. A pending UpdateHistory whose command list is
   reset before a resolve arrives is dropped and counted (`fallback-twopass-resolve-missing`). `never_fallback_after_start`
   behaves as in single-pass mode: with no usable depth and DLSS already running, UpdateHistory and the resolve are both skipped
   and the output keeps the previous frame. Every non-DLSS frame makes the next DLSS frame run with `InReset=1`.
5. **b0 layout.** `logic::tsr_layout_for_hash` returns the single-pass offsets for all three hashes. That the 200% UpdateHistory
   shader uses the same offsets is expected (it is another permutation of UE's `FTSRUpdateHistoryCS`, whose common TSR
   parameters come first) but is not verified: its DXIL only exists at run time. All three shaders are written next to the
   add-on as `tsr_<hash>.dxil`; `bash probe/dx12/dlss/disasm.sh tsr_e32f96d4dc79be5b.dxil` shows the real layout. If the
   offsets are wrong the constants fail the plausibility checks and the log says `fallback-jitter` with the values found at the
   expected offsets; the game then renders with TSR.

Log lines of the two-pass path (in addition to the usual `evaluate #n`, `create DLSS feature` and `TSR viewports` lines):

```
TSR bytecode e32f96d4dc79be5b (two-pass UpdateHistory, <n> bytes) written to <dir>tsr_e32f96d4dc79be5b.dxil
TSR bytecode 0b1375003bfb8294 (two-pass resolve, <n> bytes) written to <dir>tsr_0b1375003bfb8294.dxil
two-pass TSR: UpdateHistory e32f96d4dc79be5b kept this frame (first frame or after a failure), only the resolve is replaced; ...
two-pass TSR: history viewport 5120x2880 (resource <h>) -> resolve output 2560x1440, half-size 1280x720; DLSS output is the resolve's output, UpdateHistory is kept
two-pass TSR: a frame was fully replaced by DLSS, UpdateHistory is skipped from the next one
two-pass TSR: UpdateHistory e32f96d4dc79be5b skipped, DLSS replaces the resolve (the game's history is no longer written)
two-pass TSR: DLSS could not replace the resolve (...): the game's resolve runs[ on the stale history of an earlier frame], and UpdateHistory runs again from the next frame
not replacing the upscale pass: <reason> (the game renders normally)
```

## Build

Needs `docker` and `git`; nothing is installed on the host.

```
bash probe/dx12/dlss/build.sh
```

It runs the probe build once if the ReShade headers or the MSVC toolchain (clang-cl + xwin) are missing, then:

- fetches `NVIDIA/DLSS` tag **v310.9.16** (commit `9981c6350dc58c0ce21f024fd65e31e851c31689`) into
  `probe/dx12/dlss/third_party/ngx/` (gitignored): `include/`, the static-CRT library `nvsdk_ngx_s.lib` and
  `nvngx_dlss.dll`; both binaries are checked against pinned sha256 values;
- compiles `velocity_decode.hlsl` to DXIL (`cs_6_0`, 4092 bytes, signed) with DirectXShaderCompiler v1.9.2609
  (`dxc` 1.9.2609.5, pinned tarball with sha256) and embeds it as a byte array;
- fetches `ocornut/imgui` tag **v1.92.5-docking** (commit `3912b3d9a9c1b3f17431aebafd86d2f40ee6e59c`, the version
  ReShade 6.8.0's `reshade_overlay.hpp` requires) into `probe/dx12/third_party/imgui/` (gitignored): only `imgui.h` and
  `imconfig.h`, checked against pinned sha256 values. The widgets run in ReShade's own ImGui through its function table;
  the add-on does not load without an ImGui-enabled ReShade;
- compiles and runs `selftest.cpp` (TSR pass hashes and b0 layouts, the two-pass decisions, Halton, phase matching, preset
  choice, velocity decode, ini parsing and merging, render preset names, the `render_scale` parser and the Engine.ini
  rewrite, frame-time statistics and the perf line) on the
  Linux side of the container;
- compiles and links the add-on with clang-cl `/MT`, as for the probe.

`nvngx_dlss.dll` sha256: `d00b5b0ab29c3ca101b70c3a5ecb3a01b7ef33766a3383a28a18623083e6123e`
(`nvsdk_ngx_s.lib`: `7b9a5239c1145af5f645ab472aa066f9ea79db36ac819562312aae69d65f3765`).
The NGX SDK is NVIDIA's and keeps its own license (`third_party/ngx/LICENSE.txt`); it is not committed.

Output in `probe/dx12/dlss/out/`: `amdlss_dx12_dlss.addon64` (imports only KERNEL32, USER32 and ADVAPI32; NGX
reads the registry with ADVAPI32 and loads `nvngx_dlss.dll` at run time), `nvngx_dlss.dll` and the default
`amdlss_dx12_dlss.ini`.

## Install

Copy `amdlss_dx12_dlss.addon64`, `nvngx_dlss.dll` and `amdlss_dx12_dlss.ini` next to the game's exe, beside an
add-on-enabled ReShade `dxgi.dll` (not the probe `.addon64`). Under Proton set `WINEDLLOVERRIDES="dxgi=n,b" %command%`
and enable DLSS support for the prefix: on NVIDIA that is NVAPI and NGX in Proton (`PROTON_ENABLE_NVAPI=1`; the
driver's Wine NGX files must be available). This was not run here, so check that `NVSDK_NGX_D3D12_Init_with_ProjectID`
succeeds in the log.

## Files written next to the add-on

- `amdlss_dx12_dlss.log`: the ini values, the NGX init result, whether DLSS is available, the feature creation
  parameters, each evaluation for the first 10 frames and then every `log_every_n_frames`, the jitter used and
  whether it came from b0 or from the fallback.
- `ngx/`: NGX's own logs (verbose), and every NGX message is copied into the log with an `NGX ` prefix.

## amdlss_dx12_dlss.ini

| key | default | meaning |
|---|---|---|
| `enabled` | 1 | 0 leaves the game's upscaler alone |
| `render_scale` | game | DLSS mode as a render resolution, written to the game's Engine.ini: `game` (leave the game's scale alone), `dlaa` (100), `quality` (66.7), `balanced` (58), `performance` (50), `ultra_performance` (33.3) or a number from 33 to 100; takes effect the next time the game starts, see "DLSS mode" |
| `render_preset` | default | DLSS render preset hint for every mode: `default`, `J`, `K`, `L` or `M` (the presets NGX 310.9.16 still documents as usable); a change recreates the feature |
| `auto_exposure` | 1 | the `AutoExposure` create flag; 0 supplies no exposure texture and a neutral exposure of 1; a change recreates the feature |
| `jitter_sign_x`, `jitter_sign_y` | 1 | multiplied into the jitter sent to DLSS |
| `mv_scale_source` | game | `game`: TSR's ScreenVelocityToInputPixelVelocity from b0, negated; `ini`: the two keys below |
| `mv_scale_x`, `mv_scale_y` | -0.5, 0.5 | with `mv_scale_source=ini`: velocity to pixels is `(mv_scale_x * viewport width, mv_scale_y * viewport height)` |
| `log_every_n_frames` | 300 | log cadence after the first 10 evaluations |
| `jitter_cycle` | 8 | Halton cycle length for the fallback |
| `composite_translucency` | 1 | composite u4 over u3 before DLSS; 0 feeds u3 raw |
| `mv_encoding` | gamma | `gamma` (what UE's TSR shader does) or `linear` (the unbias alone) |
| `fill_half_res` | 1 | fill u1 (TSR's half-size output, read by bloom and eye adaptation) from u0 after the evaluate |
| `never_fallback_after_start` | 1 | after DLSS started, skip TSR even when no evaluate is possible and keep the previous u0 |
| `perf_report_seconds` | 5 | wall-time length of a frame-time report window, see below; 0 turns the report off |

## DLSS mode (`render_scale`)

DLSS only gains frame rate when the game renders fewer pixels, and the game's own settings leave it at about 61% of the
output (1552x873 for 2560x1440, measured; `sg.ResolutionQuality` in `GameUserSettings.ini` does not change it). The
add-on already picks the DLSS mode from the render/output ratio (`logic::preset_for_ratio`), so what it needs is to change
the render size. `r.ScreenPercentage=50` under `[SystemSettings]` in the user's Engine.ini does that (measured: render
1280x720 -> 2560x1440, ratio 0.500, mode MaxPerf). `render_scale` writes that line:

- **File:** `%LOCALAPPDATA%\<Project>\Saved\Config\WindowsClient\Engine.ini` (`...\Windows\` when only that folder exists;
  under Proton `C:\users\steamuser\AppData\Local\...`). `<Project>` is the folder two levels above the exe's folder
  (`<game>\<Project>\Binaries\Win64\<Project>-Win64-Shipping.exe`), else the exe name before `-Win64`. If neither config
  folder exists the log says so and nothing is written (the game creates it on its first run).
- **Value:** `dlaa` 100, `quality` 66.7, `balanced` 58, `performance` 50, `ultra_performance` 33.3, or a number from 33 to
  100 (one decimal at most is written). `game`, the default, leaves the game's own scale alone.
- **When:** at load, at `DLL_PROCESS_DETACH`, and at once when the overlay changes it. The game reads Engine.ini at start,
  so a change applies from the next start; and it rewrites Engine.ini when it exits and drops the `[SystemSettings]`
  section (measured), so the line has to be written again after every exit. The detach write does that if it runs after
  the game's own write (UE flushes its config in its shutdown path, before `ExitProcess` runs the DLL detaches, which is
  the order this relies on; not observed in a game run); the load write covers the start after the next one in any case.
- **What is written:** a marker comment and the value, in place of any `r.ScreenPercentage` line already in that section:

  ```
  [SystemSettings]
  ; amdlss_dx12_dlss render_scale
  r.ScreenPercentage=50
  ```

  Every other line, each line's ending (LF or CRLF) and a BOM are kept. With `render_scale=game` only a line that has the
  marker above it is removed (and the section header if that leaves it empty), so a player's own `r.ScreenPercentage` is
  never touched.
- **How:** the file goes to `Engine.ini.amdlss.tmp` and is moved over `Engine.ini` (`MoveFileEx` replace). Any error is
  logged and the add-on carries on. One log line per run, for example
  `render_scale=performance (load): wrote r.ScreenPercentage=50 to C:\...\Engine.ini`, `render_scale=performance (detach): ... needs no change`,
  or `render_scale=game (overlay): removed the r.ScreenPercentage line the add-on wrote from ...`.
- A hand edit of `render_scale` in the ini while the game runs is read by the hot reload but is written to Engine.ini only
  at detach (or by the next overlay change).

## Live ini and the game's own TSR shader

- **Hot reload:** the ini is re-read when its modification time changes, checked every 60 upscale dispatches. Changed
  values are logged and DLSS resets its history on the next frame (`InReset=1`). `enabled=0` hands the pass back to
  TSR at once; `enabled=1` resumes DLSS with a reset.
- **TSR bytecode:** the first time the game creates a TSR shader (hash `1ff10c3ce95d3bcb`, `e32f96d4dc79be5b` or
  `0b1375003bfb8294`) its raw DXIL is written to `tsr_<hash>.dxil` next to the add-on, one file per hash.
  `bash probe/dx12/dlss/disasm.sh tsr_1ff10c3ce95d3bcb.dxil`
  disassembles it with the pinned dxc in the build container (`dxc -dumpbin`, output next to the file as `.txt`, or the
  second argument). Read how the shader decodes velocity (linear `* 0.2495 + 0.49999` or a signed square) and how it
  uses the jitter in b0.

## Overlay tab

ReShade's overlay gets a **DLSS** tab (`reshade::register_overlay`, removed again on detach).

- **Status** (read-only): whether DLSS ran on the last upscale dispatch or the game's TSR did (with the fallback reason,
  `Enabled` off, or "previous output kept"), the TSR layout (`single pass`, or `two-pass, UpdateHistory skipped` / `kept`),
  render -> output resolution and ratio, the DLSS mode chosen from that ratio,
  the last NGX evaluate result and the evaluate count, the render preset and auto exposure in use. The mode follows the
  render/output ratio, which `DLSS mode` under Controls sets.
- **Controls:** `Enabled`, `DLSS mode` (Game default, DLAA, Quality, Balanced, Performance, Ultra Performance; a number
  from the ini shows as "Custom"), with the current render size and ratio under it. It saves `render_scale` through the
  ini save path and updates Engine.ini at once; it takes effect the next time the game starts. Then `Render preset` (Default, J, K, L, M: every other enum value of
  `NVSDK_NGX_DLSS_Hint_Render_Preset` in 310.9.16 is removed, deprecated or "reverts to default") and `Auto exposure`.
  A preset or auto exposure change recreates the DLSS feature and resets its history on the next frame. Not offered:
  sharpness (deprecated in DLSS since 2.5.1, no effect), frame generation and Reflex (not possible from this add-on).
- **Advanced:** `jitter_sign_x/y`, `mv_scale_source`, `mv_scale_x/y`, `mv_encoding`, `composite_translucency`,
  `fill_half_res`, `never_fallback_after_start`, `log_every_n_frames`, `jitter_cycle`.
- **How edits apply:** checkboxes and combos on the click, typed numbers when the field is left. The change goes into the
  live config at once (under the mutex the dispatch hook copies it with, so a frame in flight keeps the values it started
  with) and is written to the ini with the same keys, keeping comments and unknown lines; the file is written to
  `amdlss_dx12_dlss.ini.tmp` and moved over the ini, and the hot reload ignores that write. An external edit of the ini is
  shown in the tab as soon as no field is being edited. If the ini cannot be written the tab says so.

## Half-size fill, depth substitution and accounting

- **u1:** TSR also writes u1 (half the output size, `r11g11b10_float`) and the post-process downsample chain reads it
  for bloom and eye adaptation. With TSR skipped nothing wrote it and its memory may be aliased with other passes. After
  a successful evaluate a second small compute pass (`half_fill.hlsl`, 4240 bytes of DXIL) averages 2x2 blocks of u0
  into u1, sizes from the resource descriptions, reads clamped. u0 moves UAV to `NON_PIXEL_SHADER_RESOURCE` and back,
  u1 stays a UAV. It runs on the proxy list, and the game's compute state is restored once after both passes.
  `fill_half_res=0` turns it off. u2 (TSR history metadata) is still not written.
- **Size mismatch:** depth is always the newest depth buffer bound for rendering with the render size (its state is
  assumed to be `NON_PIXEL_SHADER_RESOURCE`); u3 and u6 must also match that size. When there is none, with
  `never_fallback_after_start=1` and DLSS already running, TSR is skipped and u0 keeps the previous DLSS output. Any
  other fallback after DLSS started makes the next DLSS frame run with `InReset=1`.
- **Accounting:** every `log_every_n_frames` upscale dispatches the log gets one line with the counts since the last
  one: `evaluate-ok`, `evaluate-failed`, each `fallback-<reason>`, `size-mismatch-depth-substituted`,
  `size-mismatch-kept-previous-output`, and the latest mismatch with u3, u6 and u14 in full (size, formats, view
  type). The first three mismatches are also logged in full when they happen.
- **Two-pass keys** (counted once per frame, at UpdateHistory unless noted; the "upscale dispatch" count is UpdateHistory too):
  `twopass-update-skipped` / `twopass-update-kept` (what UpdateHistory did), `twopass-replaced` (the resolve was replaced by
  DLSS, counted at the resolve), `twopass-resolve-fallback` (DLSS could not replace it), `twopass-stale-history-frame` (that
  happened with UpdateHistory skipped, so the game's resolve showed an old history), and the new fallback reasons
  `fallback-twopass-resolve-bindings` (no usable u0 at the resolve), `fallback-twopass-pairing` (its u2 is not the history
  UpdateHistory wrote), `fallback-twopass-sizes` (b0's history viewport is not 1 to 4 times the output),
  `fallback-twopass-resolve-missing` (a pending UpdateHistory had no resolve before its command list was reset or a new
  UpdateHistory started). The older keys (`evaluate-ok`, `fallback-bindings`, `fallback-jitter`, ...) apply unchanged to
  UpdateHistory's checks and, for the evaluate, to the resolve.

## Frame timing (DLSS vs TSR)

Each `present` takes a `QueryPerformanceCounter` timestamp. Every `perf_report_seconds` of wall time the log gets one
line, so the same game scene can be compared with `enabled=1` (DLSS) and `enabled=0` (TSR) by a script:

```
perf: t=<s since load, 1 dec> frames=<n> avg_ms=<2 dec> fps=<1 dec> low1_fps=<1 dec> p99_ms=<2 dec> upscaler=<DLSS|TSR|mixed|none> render=<WxH> output=<WxH> ratio=<3 dec>
```

- `frames` is the number of present-to-present deltas in the window; `fps` is 1000 / `avg_ms`; `p99_ms` is the nearest-rank
  99th percentile; `low1_fps` is 1000 / the mean of the slowest 1% of frame times (at least one frame).
- `upscaler`: `DLSS` if every upscale dispatch in the window was a successful DLSS evaluate, `TSR` if every one went to
  the game's TSR (`enabled=0` or any fallback), `mixed` if both happened, `none` if no upscale dispatch ran. A frame that
  kept the previous DLSS output (no usable depth) counts as neither. In two-pass mode the unit is the frame: a frame whose
  resolve DLSS replaced counts as DLSS (UpdateHistory kept or skipped), and any failure at UpdateHistory or at the resolve counts
  as TSR.
- `render`/`output` are the viewport sizes of the last upscale dispatch (`0x0` before the first one).
- The DLSS tab shows fps, average ms, 1% low and the upscaler of the latest window under Status.

## Proxies

ReShade proxies the D3D12 device and command list, and `get_native()` returns the real ones. NGX, the decode pass and
the barriers around them therefore use the proxies: the device from the private data ReShade stores on the real device
(checked against `api device - 8`), the command list as `api list - 8` after a `QueryInterface` check. If either is
not confirmed the game renders normally and the log says why. Our event handlers ignore the events those commands
fire, and the game's compute state is restored on the real list.

## Limits

- The two-pass mode is built, not run. Unverified: that b0 of the 200% UpdateHistory has the single-pass layout, that the
  output viewport of the resolve starts at 0, that the states of the inputs do not change between UpdateHistory and the resolve,
  and that the resolve output keeps its content between frames (needed for `never_fallback_after_start`). u2 of UpdateHistory
  (r8 history metadata, 5120x2880) is not written when UpdateHistory is skipped.
- Not run in game, so the overlay tab has not been seen either (it compiled and links, and the ini merge is selftested).
  Feature handles replaced by a preset or auto exposure change are parked like the ones from a size change.
- The unverified parts are the guessed resource states, whether DLSS accepts an
  `r11g11b10_float` output, and whether the root descriptors that get restored match what UE set.
- `render_scale` is selftested (parser, Engine.ini rewrite, project name) but the file writes, the folder lookup and the
  detach order were not run against the game. It sets the render size for the next start only; the running game keeps its
  size.
- Feature and motion-vector texture replaced on a size change are parked, not released, because frames in flight may
  still use them.
- The velocity decode formula is the one from UE5's `VelocityCommon.ush` as remembered, with the sign and scale
  constants in the ini so they can be flipped without a rebuild.
