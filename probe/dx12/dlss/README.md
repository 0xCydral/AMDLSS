# amdlss_dx12_dlss

Stage 3b of the DX12 spike (`odd/tasks/dx12-spike.md`): a ReShade add-on that replaces the game's TSR upscale dispatch
(compute shader hash `1ff10c3ce95d3bcb`) with DLSS Super Resolution. Built separately from the probe in
`probe/dx12/`; install one or the other, not both.

## What it does on that dispatch

1. Finds the pass's textures through the same descriptor-heap shadow the probe uses, at root param 1: output u0, scene
   color u3, velocity u6, depth u14. Translucency u4 is looked up but not used (known gap).
2. Reads the camera jitter, a float2 in render pixels at byte 256 of the root CBV b0 at root param 2 (found in T7).
   - **Option A (used first):** the CBV lives in an upload heap and UE wrote it on the CPU before recording, so
     `device::map_buffer_region` reads the 8 bytes at record time. The value must lie within +-0.5.
   - **Option B (fallback):** each good read is matched to Halton(2,3)-0.5 (`jitter_cycle`, default 8) to lock the
     phase; if a later read fails the jitter is predicted from the evaluation count. If A never worked, nothing is
     replaced.
3. Decodes UE's velocity into DLSS motion vectors with a small compute pass (`velocity_decode.hlsl`, DXIL embedded
   at build time): `v = (enc - 32767/65535) / (0.499 * 0.5)`, the inverse of UE5's `EncodeVelocityToTexture`
   (`VelocityCommon.ush`), then times `(mv_scale_x * width, mv_scale_y * height)` = `(-0.5 w, +0.5 h)` so the result is
   "previous minus current" in render pixels, y down. It writes an `r16g16_float` texture of render size.
4. Evaluates DLSS on the game's own command list (`get_native()`), flags `IsHDR | AutoExposure | MVLowRes |
   DepthInverted`, output written straight into u0, preset chosen from the render/output width ratio (nearest of
   DLAA, UltraQuality, MaxQuality, Balanced, MaxPerf, UltraPerformance). `InReset` is set on the first frame and
   whenever the feature is recreated, which happens when the render or output size changes.
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

Known gaps, logged once: u4 translucency is dropped and u2 (r8 history metadata) is no longer written, so passes that
read them see stale data. u1 (half-size r11g11b10) is filled from u0, see below (`fill_half_res`).

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
- compiles and runs `selftest.cpp` (Halton, phase matching, preset choice, velocity decode, ini parsing) on the
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
| `jitter_sign_x`, `jitter_sign_y` | 1 | multiplied into the jitter sent to DLSS |
| `mv_scale_x`, `mv_scale_y` | -0.5, 0.5 | NDC delta to pixels is `(mv_scale_x * width, mv_scale_y * height)` |
| `log_every_n_frames` | 300 | log cadence after the first 10 evaluations |
| `jitter_cycle` | 8 | Halton cycle length for the fallback |
| `fill_half_res` | 1 | fill u1 (TSR's half-size output, read by bloom and eye adaptation) from u0 after the evaluate |
| `never_fallback_after_start` | 1 | after DLSS started, skip TSR even when no evaluate is possible and keep the previous u0 |

## Half-size fill, depth substitution and accounting

- **u1:** TSR also writes u1 (half the output size, `r11g11b10_float`) and the post-process downsample chain reads it
  for bloom and eye adaptation. With TSR skipped nothing wrote it and its memory may be aliased with other passes. After
  a successful evaluate a second small compute pass (`half_fill.hlsl`, 4240 bytes of DXIL) averages 2x2 blocks of u0
  into u1, sizes from the resource descriptions, reads clamped. u0 moves UAV to `NON_PIXEL_SHADER_RESOURCE` and back,
  u1 stays a UAV. It runs on the proxy list, and the game's compute state is restored once after both passes.
  `fill_half_res=0` turns it off. u2 (TSR history metadata) is still not written.
- **Size mismatch:** when u14 is not the size of u3 and u6, the newest depth buffer bound for rendering with the render
  size is used instead (its state is assumed to be `NON_PIXEL_SHADER_RESOURCE`). When there is none, with
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

- Not run in game. The unverified parts are the guessed resource states, whether DLSS accepts an
  `r11g11b10_float` output, and whether the root descriptors that get restored match what UE set.
- Feature and motion-vector texture replaced on a size change are parked, not released, because frames in flight may
  still use them.
- The velocity decode formula is the one from UE5's `VelocityCommon.ush` as remembered, with the sign and scale
  constants in the ini so they can be flipped without a rebuild.
