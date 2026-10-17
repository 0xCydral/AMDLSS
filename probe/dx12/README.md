# amdlss_dx12_probe

ReShade add-on for stage 1 of the DX12 spike (`odd/tasks/dx12-spike.md`): log one frame of DirectX 12 compute
dispatches so the TAA/TSR pass can be found by shader hash and by the resources it binds.

## What it logs

Press F10 in game. The add-on records the next full frame (present to present), then appends one capture to
`amdlss_dx12_probe.log` in the add-on's own directory and disarms. F10 again takes another capture.

```
=== capture #1 backbuffer=3840x2160 r8g8b8a8_unorm ===
D 41 cl=0x1f2a... cs=9a3f01c2d4b5e607/18432 groups=240x135x1 | [p1 SRV t0 r=1a2b tex2d 1920x1080x1 m1 s1 r16g16b16a16_float use=SRV|UAV view=2d:r16g16b16a16_float] [p2 UAV u0 r=3c4d tex2d 3840x2160x1 ...]
RT cl=0x1f2a... at_dispatch=60 draws=12 rt0=1920x1080:r11g11b10_float ds=1920x1080:d32_float_s8_uint
=== end capture #1 dispatches=312 (indirect=9) draws=1840 dispatch_mesh=0 dispatch_rays=0 ===
```

- `D` line, one per dispatch: index in frame, command list, compute shader (`cs=` FNV-1a 64 of the shader bytecode
  / its size, stable across launches), thread group counts, then every bound resource: parameter index, slot type
  and register (`SRV t3`, `UAV u0`, `CBV b1`, `SMP s0`), resource handle `r=`, dimension, width x height x
  depth-or-layers, mips, samples, format, usage flags, and the view's own type and format. Buffers print size,
  stride, and for CBVs the offset and range.
- `ExecuteIndirect` dispatches are logged as `D` lines with `groups=indirect(...)` because the counts live in a GPU
  buffer. Mesh, ray and draw calls are only counted in the end line.
- `RT` line: render-target and depth bindings, printed once per command list whenever the set changes, with the
  dispatch index and draw count at that moment.
- `cs=?` means the pipeline was not seen being created with bytecode (for example loaded from a pipeline library).

## Descriptor resolution

A D3D12 descriptor table is a GPU handle into a heap and ReShade cannot read a descriptor back. The add-on keeps its
own shadow of every descriptor heap, built from `update_descriptor_tables` (CreateShaderResourceView and friends)
and `copy_descriptor_tables` (CopyDescriptors), the same approach as ReShade's `descriptor_tracking` example. Each
view is snapshotted at `init_resource_view` (resource, view desc, resource desc), so a later slot reuse or a
destroyed resource does not corrupt the log. At a dispatch it takes the tables from `bind_descriptor_tables` and
the root descriptors from `push_descriptors` (compute stage only), walks the ranges of the root signature
(`init_pipeline_layout`) and resolves each range with `device::get_descriptor_heap_offset`.

## Build

Needs `docker` and `git`; nothing is installed on the host.

```
bash probe/dx12/build.sh
```

It fetches the ReShade add-on headers (git tag **v6.8.0**, add-on API version 20, the release the 6.8.0.1 runtime
is built from) into `probe/dx12/third_party/reshade/include` (gitignored), then compiles inside `ubuntu:24.04` with
clang-cl (`--target=x86_64-pc-windows-msvc`, lld-link) against the MSVC CRT and Windows SDK fetched once by `xwin`
0.10.0 into `probe/dx12/third_party/xwin` (gitignored, about 630 MB, first build only), with the static CRT (`/MT`).
Output: `probe/dx12/out/amdlss_dx12_probe.addon64`, a PE32+ x64 DLL importing only KERNEL32 and USER32.

The DLSS replacement add-on (stage 3b) lives in `probe/dx12/dlss/` with its own build script and README.

MinGW cannot be used: ReShade is built with MSVC, and the two ABIs swap `this` and the hidden return pointer for member
functions that return a struct by value (`get_resource_desc`, `get_resource_view_desc`), so a MinGW add-on corrupts the
device object on the first call.

## F11 dump mode (stage 2)

Press F11 in game. The add-on waits for the first dispatch whose compute shader hash is `1ff10c3ce95d3bcb`
(`kUpscaleHash`, found in stage 1), copies its textures, then disarms. F10 logging is unchanged.

- **Before the dispatch** (`pre`): every plain 2D texture in the shader's own table, root param 1 (`kUpscaleRootParam`),
  SRVs and UAVs, plus the most recent depth buffer bound with `bind_render_targets_and_depth_stencil` whose size equals
  the render size. The render size is the most common size among the SRV textures, so it is not hard-coded.
- **After the dispatch** (`post`): the root param 1 UAVs u0-u2 (`kMaxPostUavRegister`), the pass outputs.
- **Where the post copies are recorded:** at the first event on the same command list after the target dispatch:
  `dispatch`, `draw`, `draw_indexed`, `draw_or_dispatch_indirect`, `barrier` or `close_command_list`. ReShade fires
  `barrier` after `ResourceBarrier`, so for a barrier the copy starts from the new state the event reports. This is the
  earliest point after the dispatch and before UE reuses the outputs.
- **Copy:** per texture, a transition to `copy_source`, `copy_texture_to_buffer` into a `memory_heap::readback` buffer
  (mip 0, slice 0), and a transition back. "Before" states are guessed: UAV -> `unordered_access`, SRV and depth ->
  `shader_resource_non_pixel` (`kSrvState`, `kDepthState`; UE reads in compute with NON_PIXEL_SHADER_RESOURCE). The
  guess is exact only if UE left the resource there. vkd3d-proton (Proton) tolerates a wrong state, native D3D12 may not.
- **Write:** once the command list was submitted (`execute_command_list`) and a later present has passed, the add-on
  calls `wait_idle` on that queue, maps the buffers and writes the files. Nothing is written inline in the render path.
- **Depth:** a depth-stencil texture is copied as plane 0 only, so the stencil is dropped and the file is written as
  `r32_float` (`r24_unorm_x8_uint` for 24-bit depth, `r16_unorm` for 16-bit). If a depth copy of a typeless
  `r32_g8_typeless` resource turns out to lose the device on some driver, remove the depth lookup in `dump_begin`.
- **Failures never abort the game:** an unsupported format (block compressed, multisampled, 3D, buffers), a failed
  buffer or a failed map writes a `DUMP skip ...` line to `amdlss_dx12_probe.log` and skips that texture. If the command
  list is reset before submission, or never submitted within 600 presents, the dump is abandoned with a log line.

Files go to `dump/` next to the add-on, named `<capture>_<pre|post>_<slot>_<WxH>_<format>_<resourcehandle>.dds`
(a DDS file with a DX10 header carrying the real DXGI format), plus `<capture>_index.txt` listing every file with its
slots (`t3+u2` when one resource is bound twice), view type, format, size and resource handle.

View them on Linux with the converter (Python 3 standard library only, no numpy needed):

```
python3 -I probe/dx12/dds2png.py dump/001_*.dds        # writes a .png next to each .dds
python3 -I probe/dx12/dds2png.py --selftest
```

Colour formats (`r16g16b16a16_float`, `r11g11b10_float`) get a Reinhard tonemap and sRGB. `r16g16_unorm` maps to R/G
with B=0 as stored (UE encodes velocity centred at 0.5, so 128 is no motion). `r16g16_float`, `r16`, `r8` and `r8g8`
are stretched min..max to the full range. `r32_float` depth is stretched with near = bright; `--z auto` (default) picks
reversed-Z or standard-Z from the median, `--z reversed|standard` forces it.

## Constant-buffer capture (stage 3a, finds the camera jitter)

No hotkey. When a file named `amdlss_dx12_probe.cbcapture` exists next to the add-on (its content does not matter),
the add-on arms itself at the 120th present, so the title scene has settled. In each of the next frames where the
upscale dispatch (`kUpscaleHash`) runs, up to 16 frames in all and once per process, it copies every constant buffer
bound to that dispatch:

- CBV ranges in descriptor tables (`bind_descriptor_tables`) and root CBVs (`push_descriptors`): the buffer range
  (resource, offset, size) is copied with `copy_buffer_region` into a readback buffer at the dispatch. A root CBV has
  no size, so the whole buffer from the offset is taken, capped at 64 KB (`kCbMaxBytes`).
- Root constants: ReShade v6.8.0 reports them through the `push_constants` event, so the add-on keeps the values per
  command list and root parameter and writes them as slot `rc`.
- Copy and barrier: upload-heap buffers cannot be transitioned and are always readable as copy sources, so they get no
  barrier. Default-heap buffers are moved from `constant_buffer` to `copy_source` and back (`kCbDefaultHeapState`, a
  guess). Mapping an upload buffer directly was not used: UE keeps them mapped and the copy sees exactly what the
  dispatch sees.
- Destroyed buffers: a descriptor can outlive its buffer, so `init_resource`/`destroy_resource` keep a set of live
  buffers and a dead one is skipped.
- Write: like F11, after the command list was submitted and a present has passed, then `wait_idle` and map.

Output in `cbcapture/` next to the add-on: `frame_<n>_p<param>_<slot>_<size>.bin` (raw bytes, `n` from 00) and
`index.txt` with one `# frame <n> present <p> render=WxH output=WxH` line per frame (sizes from the bound u3 and u0)
followed by tab-separated rows: frame, present, param, slot, resource, offset, size, heap type, file. Failures write a
`CB skip ...` line to `amdlss_dx12_probe.log` and the capture continues.

Find the jitter on Linux (Python 3 standard library only):

```
python3 -I probe/dx12/find_jitter.py path/to/cbcapture
python3 -I probe/dx12/find_jitter.py --selftest
```

For every 4-byte offset it reads float32 across the frames and ranks pairs (offset, offset+4) that change on most
frames, stay within +-1.0 and correlate with Halton(2,3)-0.5 (Pearson, best over phase 0-15, cycle none/4/8/16 and
both axis orders). It labels the scale as pixels (+-0.5), UV (+-0.5/render width) or NDC (+-1/render width) from the
largest value, and prints the top 10 with their per-frame values.

## Install for a test

1. Use an add-on-enabled ReShade (the 6.8.0.1 build from Luma's release) as `dxgi.dll` next to the game exe.
2. Copy `amdlss_dx12_probe.addon64` into the same folder as the exe.
3. Under Proton set the launch option `WINEDLLOVERRIDES="dxgi=n,b" %command%`.
4. In game, press F10 once the scene is rendering, then read `amdlss_dx12_probe.log` next to the exe.

## Known limits

- Descriptors are resolved when the dispatch is recorded, not when it runs. A heap slot rewritten between recording
  and execution (bindless tricks) shows its state at record time.
- Command lists recorded on several threads interleave in the log; use `cl=` to separate them. UE5 records ahead of
  the present thread, so the captured window holds one frame's worth of recorded work, offset by about a frame.
- Unbounded descriptor ranges print at most the first 64 entries. Root constants are not in the F10 log (the cbcapture mode saves them).
- Descriptor heaps are never freed from the shadow, which is fine for a short test session.
- F10 and F11 ran live (stages 1 and 2). The constant-buffer capture (stage 3a) is built and its analysis self-checked, not yet run in game.
