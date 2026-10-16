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

MinGW cannot be used: ReShade is built with MSVC, and the two ABIs swap `this` and the hidden return pointer for member
functions that return a struct by value (`get_resource_desc`, `get_resource_view_desc`), so a MinGW add-on corrupts the
device object on the first call.

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
- Unbounded descriptor ranges print at most the first 64 entries. Root constants are not logged.
- Descriptor heaps are never freed from the shadow, which is fine for a short test session.
- Untested against a live ReShade: this was built and checked as a PE only.
