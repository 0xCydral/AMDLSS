# amdlss_dx12_dlss

Stage 3b of the DX12 spike (`odd/tasks/dx12-spike.md`): a ReShade add-on that replaces the game's TSR upscale dispatch
(compute shader hash `1ff10c3ce95d3bcb`) with DLSS Super Resolution. Built separately from the probe in
`probe/dx12/`; install one or the other, not both.

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
- compiles and runs `selftest.cpp` (Halton, phase matching, preset choice, velocity decode, ini parsing and merging,
  render preset names) on the Linux side of the container;
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

## Live ini and the game's own TSR shader

- **Hot reload:** the ini is re-read when its modification time changes, checked every 60 upscale dispatches. Changed
  values are logged and DLSS resets its history on the next frame (`InReset=1`). `enabled=0` hands the pass back to
  TSR at once; `enabled=1` resumes DLSS with a reset.
- **TSR bytecode:** the first time the game creates the TSR shader (hash `1ff10c3ce95d3bcb`) its raw DXIL is written
  to `tsr_1ff10c3ce95d3bcb.dxil` next to the add-on. `bash probe/dx12/dlss/disasm.sh tsr_1ff10c3ce95d3bcb.dxil`
  disassembles it with the pinned dxc in the build container (`dxc -dumpbin`, output next to the file as `.txt`, or the
  second argument). Read how the shader decodes velocity (linear `* 0.2495 + 0.49999` or a signed square) and how it
  uses the jitter in b0.

## Overlay tab

ReShade's overlay gets a **DLSS** tab (`reshade::register_overlay`, removed again on detach).

- **Status** (read-only): whether DLSS ran on the last upscale dispatch or the game's TSR did (with the fallback reason,
  `Enabled` off, or "previous output kept"), render -> output resolution and ratio, the DLSS mode chosen from that ratio,
  the last NGX evaluate result and the evaluate count, the render preset and auto exposure in use. The mode follows the
  game's Anti-Aliasing Level and resolution; the add-on cannot change it.
- **Controls:** `Enabled`, `Render preset` (Default, J, K, L, M: every other enum value of
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

## Proxies

ReShade proxies the D3D12 device and command list, and `get_native()` returns the real ones. NGX, the decode pass and
the barriers around them therefore use the proxies: the device from the private data ReShade stores on the real device
(checked against `api device - 8`), the command list as `api list - 8` after a `QueryInterface` check. If either is
not confirmed the game renders normally and the log says why. Our event handlers ignore the events those commands
fire, and the game's compute state is restored on the real list.

## Limits

- Not run in game, so the overlay tab has not been seen either (it compiled and links, and the ini merge is selftested).
  Feature handles replaced by a preset or auto exposure change are parked like the ones from a size change.
- The unverified parts are the guessed resource states, whether DLSS accepts an
  `r11g11b10_float` output, and whether the root descriptors that get restored match what UE set.
- Feature and motion-vector texture replaced on a size change are parked, not released, because frames in flight may
  still use them.
- The velocity decode formula is the one from UE5's `VelocityCommon.ush` as remembered, with the sign and scale
  constants in the ini so they can be flipped without a rebuild.
