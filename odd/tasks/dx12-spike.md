# DX12 spike: can AMDLSS inject DLSS into a DirectX 12 Unreal Engine game?

## Objective

Answer yes or no, in 1-2 days, before committing months to a DX12 port of the Inject DLSS route.
Luma UE (the current Inject DLSS route) is DirectX 11 only; on DX12 its tab is blank (`luma-empty-tab.md`).

## Test game

Echoes of Aincrad (Steam 2244210, UE5, `EchoesofAincrad-Win64-Shipping.exe` 393270784 bytes, v1.0.6.0).
All 5 empty-Luma-tab reports in `~/amdlss-reports` come from it.

- No upscaler: no `NGX_DLSS`, `FidelityFX`, `ffxFsr` or `XeSS` strings in the exe.
- No anti-cheat folder (the 2 `EasyAntiCheat` strings in the exe are the EOS SDK).
- DX12 only: launched with `-dx11` under Proton on 2026-10-16, it still wrote `vkd3d-proton.cache.write` and
  `EchoesofAincrad_PCD3D_SM6.upipelinecache`. Luma UE can never run here.
- Player settings: `sg.AntiAliasingQuality=2`, `sg.ResolutionQuality=0` (GameUserSettings.ini).

## Stages (stop at the first "no")

1. **Find the pass.** A ReShade DX12 addon logs one frame of compute dispatches on a hotkey: pipeline shader hash,
   and every bound resource (format, size). Pass: one dispatch group is identifiable by hash across launches, and
   its inputs include scene color and depth at render resolution, motion vectors, and an output at display size.
2. **Capture the inputs.** Copy those resources at that dispatch and dump them to files; check they look right.
3. **Feed an upscaler.** Call DLSS (through HelixSR Scaler / OptiScaler) or FSR directly with those inputs and
   replace the pass output. Pass: a stable upscaled image in game.

## Tasks

- [x] T1 Stage 1 probe addon: `probe/dx12/` (C++ ReShade addon, MinGW build in Docker), build script, README.
      Route: delegated (writer trigger: new multi-file C++ project). Check: the addon builds to a PE32+ DLL.
- [x] T2 Run the probe in Echoes of Aincrad under Proton and identify the TAA/TSR dispatch. Done with 2 captures in
      one launch; a second launch is still owed (see Progress).
- [x] T3 Verdict for stage 1, recorded here: pass, see "Stage 1 result".
- [x] T4 Stage 2 dump tool: F11 in `probe/dx12` copies the upscale pass textures to `dump/*.dds` and `probe/dx12/dds2png.py`
      converts them. Check: `build.sh` exits 0, imports only KERNEL32 and USER32, `dds2png.py --selftest` passes.
- [x] T5 Run the F11 dump in game, convert it, and identify scene color and motion vectors from the pixels.
- [x] T6 Re-run the F11 dump with the depth fix and confirm the depth buffer image (fresh launch, also confirms the
      shader hash survives a restart). Done on the title screen, dump in `~/Downloads/dx12-spike/dump2/`:
      - Same hash, same slot layout, at a different size: 1400x788 to 1920x1080 (stage 3 must read sizes per frame).
      - Depth is u14 (`r32_float` view), the same resource the depth lookup found, so the pass binds its own depth.
      - Reversed-Z: near geometry is bright, sky is 0, and the shapes match the color in u3.
      - The PNG showed only the even rows, squashed into the top half. Cause: the readback pitch came from the 8-byte
        `r32_g8_typeless` resource format while the copy holds the 4-byte depth plane. Fixed (pitch = copied texel
        size x row length), rebuilt and installed. A clean depth image is not yet re-dumped. Not blocking: depth is
        proven present and valid.

- [x] T7 Stage 3a: find the camera jitter. Auto-capture (no key) the constant buffers of the upscale dispatch for 16
      consecutive frames, then find the float2 that cycles within +-0.5 render pixels (Halton-like). Route: delegated
      (same writer, probe change). The parent ran it by launching the game; no owner input. Result:
      - 16 consecutive frames (presents 120-135) on the title screen, render 1552x876, output 2560x1440, no `CB skip`.
      - **Jitter is root param 2 (b0), byte offset 256, float2 in render pixels**: exact Halton(2,3) - 0.5, score
        1.000, e.g. frame 0 = (+0.375, +0.055556). The same pair is at p3 (b1) offset 2392.
      - UV-scale copies (x sign flipped) at p3 b1 offsets 1280 and 624; 624 lags by one frame (previous jitter).
      - Capture copied to `~/Downloads/dx12-spike/cb1/` (not in repo).
- [ ] T8 Stage 3b: replace the pass with DLSS (RTX 4070 on this machine) fed by u3+u4, u14 depth, decoded u6 and the
      T7 jitter; write u0. Pass: a stable upscaled image. Route and design decided after T7.
      Built, not yet run: `probe/dx12/dlss/` (`bash probe/dx12/dlss/build.sh` -> `amdlss_dx12_dlss.addon64` + `nvngx_dlss.dll`).

## Stage 2 result (2026-10-16): pass for color, motion vectors and output; depth pending

F11 dump #1 in a fresh launch: the hash `1ff10c3ce95d3bcb` matched again, the game did not crash or hang, and 16 of
19 bindings were written (u13-u15 are not plain 2D textures). PNGs in `~/Downloads/dx12-spike/dump1/` (not in repo).

- **Scene color is u3** (1552x876 `r16g16b16a16_float`): the full lit scene at render resolution, pre-upscale.
- **u4 (1552x876 `r16g16b16a16_float`) is a separate translucency layer**: black except candle particles and fog. An
  upscaler fed only u3 would drop these, so stage 3 must composite u4 over u3 first or feed it as a reactive mask.
- **Motion vectors are u6** (1552x876 `r16g16_unorm`): smooth camera-motion gradient with the running character as a
  distinct silhouette, values centred at 0.5. UE encoding, so stage 3 needs a decode to pixel offsets.
- **Output is u0** (2560x1440 `r11g11b10_float`): the clean upscaled frame, same scene as u3.
- **Depth was not dumped**: `DUMP no depth buffer of render size 0x0 seen`. Cause: UE declares root param 1 as UAV
  ranges even for read-only slots, and the render-size vote counted only SRVs, so it counted nothing. Fixed by counting
  every binding (1552x876 wins 8 to 4); rebuilt, not yet re-run (T6).

## Stage 1 result (2026-10-16): pass

Two F10 captures in one launch, 2560x1440 back buffer, 723 and 722 dispatches. Analysis script:
`~/Downloads/dx12-spike/analyze.py` (not in the repo).

- **Render resolution is 1552x876** (about 61% of 2560x1440): the game already upscales itself.
- **The upscale pass is `cs=1ff10c3ce95d3bcb`** (51724-byte DXIL), groups 320x180 = 2560/8 x 1440/8. It is the only
  dispatch with that hash and the only one-thread-per-output-pixel dispatch, in both captures (D715, D714). The four
  passes before it on the same command list run at render size (194x110 = 1552/8 x 876/8), which fits UE5 TSR.
- **Its real bindings (root param 1):** UAV u0 2560x1440 `r11g11b10_float` (history and output), u1 1280x720
  `r11g11b10_float` (half-size output), u2 2560x1440 `r8_unorm`; SRVs at 1552x876: two `r16g16b16a16_float`,
  `r8g8_unorm`, `r16g16_unorm`, `r8_unorm`, `r8_uint`, `r8_unorm`, `r8g8_unorm`; plus last frame's 2560x1440
  `r11g11b10_float` and `r8_unorm`.
- **History ping-pongs:** capture 1 reads `125f0e160` and writes `dd2f2270`; capture 2 reads `dd2f2270`.
- **Depth** is `3c7b1a70` (1552x876 `r32_g8_typeless`, viewed `r32_float_x8_uint`), the same handle in both captures.
  It is bound to earlier passes, not to `1ff10c3c`.

Not proven yet, and owed to stage 2:
- Which 1552x876 `r16g16b16a16_float` input is pre-upscale scene color, and whether `r16g16_unorm` (`e22b0920`) is
  motion vectors. Formats fit; only a dump of the pixels proves it.
- The hash across a second launch. It hashes the shader bytecode, so it should be stable, but this was not run.
- The descriptor log lists whole tables (up to 64 entries), so root param 0 shows resources the shader may not read.

## Progress

- 2026-10-16: test game chosen and DX11 ruled out (above).
- 2026-10-16: T1 done. `bash probe/dx12/build.sh` exits 0 and builds `probe/dx12/out/amdlss_dx12_probe.addon64`
  (248320 bytes, ReShade headers v6.8.0, API 20). `file`: PE32+ executable (DLL) x86-64. Imports only KERNEL32,
  msvcrt and USER32 (libstdc++, libgcc, winpthread are static). Exports NAME and DESCRIPTION. Not yet run against a
  live ReShade; that is T2.
- 2026-10-16: T2 attempt 1 crashed the game: `EXCEPTION_ACCESS_VIOLATION` in `amdlss_dx12_probe+0x84ac`, a virtual call on the
  `device*` whose vtable pointer had been overwritten. Cause: the MinGW build. ReShade is MSVC-built, and for member
  functions returning a struct by value (`get_resource_desc` etc.) MSVC passes this=rcx, return pointer=rdx while
  MinGW GCC swaps them, so ReShade wrote a `resource_desc` over the device object. Fix: `probe/dx12/build.sh` now
  builds with clang-cl (`--target=x86_64-pc-windows-msvc`, lld) against the MSVC CRT and Windows SDK from xwin 0.10.0,
  static CRT. Checked in the disassembly: `get_resource_desc` is called with rcx = device, rdx = return buffer,
  r8 = resource. DLL imports only KERNEL32 and USER32. Re-run T2 with the new `.addon64`.
- 2026-10-16: T4 done. F11 arms a dump of the first dispatch with hash `1ff10c3ce95d3bcb`: root param 1 textures
  and the render-size depth before it, u0-u2 after it, recorded on the same command list and written after the
  list is submitted and a present has passed. `bash probe/dx12/build.sh` exit 0, 238592 bytes, PE32+ DLL, imports
  only KERNEL32 and USER32. `python3 -I probe/dx12/dds2png.py --selftest` prints `dds2png selftest ok`. Not run in
  game yet (T5). Unverified guesses to check on the first run: the "before" resource states (NON_PIXEL_SHADER_RESOURCE
  for SRVs and depth) and how ReShade lays out the depth footprint of `r32_g8_typeless`.
- 2026-10-16: T7 tool built, not yet run. Create `amdlss_dx12_probe.cbcapture` next to the add-on and launch: after the
  120th present the add-on copies every constant buffer and the root constants of the upscale dispatch for 16 frames
  into `cbcapture/` (`frame_<n>_p<param>_<slot>_<size>.bin` + `index.txt`). Then
  `python3 -I probe/dx12/find_jitter.py cbcapture` ranks the float pairs that look like Halton(2,3) jitter.
  `bash probe/dx12/build.sh` exit 0, 264704 bytes, PE32+ DLL, imports only KERNEL32 and USER32;
  `find_jitter.py --selftest` passes (planted pair at offset 64 ranks first, pixel and NDC scale).
- 2026-10-16: T8 built, not yet run. `probe/dx12/dlss/` is a separate add-on that skips the `1ff10c3ce95d3bcb` dispatch and
  evaluates DLSS (NGX SDK `NVIDIA/DLSS` v310.9.16) on the game's list: color u3, depth u14 (inverted), velocity u6
  decoded to render-pixel motion vectors by an embedded DXIL pass, jitter read from b0 offset 256 (option A, upload
  heap map at record time; option B Halton fallback), output u0. NGX and the decode pass change compute root state,
  so heaps, root signature, pipeline and root arguments are restored from the shadow. Gaps: u4 translucency dropped,
  u1 and u2 not written. `bash probe/dx12/dlss/build.sh` exit 0, add-on imports only KERNEL32, ADVAPI32 and USER32,
  decode shader 4092 bytes of signed DXIL (dxc 1.9.2609.5), logic selftest passes. To check on the first run: NGX init
  and `SuperSampling.Available` in `amdlss_dx12_dlss.log`, whether DLSS accepts an `r11g11b10_float` output, whether
  the guessed resource states and restored root descriptors hold, and the sign of jitter and velocity (ini).
- 2026-10-16: T8 first live run (Proton Experimental 11.0, RTX 4070, driver 595.99): NGX init succeeded (`available=1`,
  snippet 310.9.16 loaded, MV texture created), then the game crashed during DLSS feature creation: NGX logged
  `NvAPI_D3D12_GetCudaIndependentDescriptorObject failed - nvapi status -5` (invalid argument) twice, followed by an
  access violation in `nvapi64` reached through ReShade's dxgi. Cause: `get_native()` returns the real D3D12 device and
  command list, but ReShade proxies both and its device proxy converts descriptor handles, so NGX was given objects
  the proxy path does not know. Fix: NGX, the decode pass and every barrier now use the proxies. The proxy device is
  read from the private data ReShade stores on the real device (GUID `2523AFF4-978B-4939-BA16-8EE876A4CB2A`) and
  cross-checked against `api device - 8`; the proxy command list is `api list - 8`, used only when that device rule
  held and a `QueryInterface(ID3D12GraphicsCommandList)` on the candidate returns the candidate itself, otherwise the
  game renders normally. Our handlers ignore the events the proxy list fires for NGX's and the decode pass's commands,
  and the game's compute state is put back on the real list afterwards. The log now names each unusable binding (slot,
  view or resource handle, size, reason) and the sizes in a mismatch, the first three times per reason. Rebuilt, not yet
  re-run. The earlier "textures not all found" and "differ in size" lines are expected to be explained by this logging.
- 2026-10-16: T8 live result with the proxies (cec9aaa): DLSS runs, 600+ evaluates succeed, no crash, jitter read from b0
  every frame, first screenshots clean. Then single bad frames appear between good ones (the owner sees a blink): exposure
  jumps toward white and large saturated blocks of cyan, green and blue. Not a steady-state failure in the log. Two
  suspects, both addressed in one change. (1) u1, TSR's half-size output that bloom and eye adaptation read, is never
  written and its memory is aliased: now filled from u0 after the evaluate (`fill_half_res=1`, 2x2 average, own
  compute pass on the proxy list). (2) Frames whose u14 is not render size fell back to TSR, which then ran on history
  that DLSS frames wrote and produced the garbage frame: now the newest depth buffer of render size is substituted,
  and if none exists with DLSS already running the TSR dispatch is skipped anyway and u0 keeps the previous output
  (`never_fallback_after_start=1`); other fallbacks after the start make the next DLSS frame run with `InReset=1`. The log
  line was also cut off at u14 (1 KB buffer); it is 4 KB now and carries u14 size, formats and view type. New per-report
  accounting (every `log_every_n_frames` dispatches): evaluates, each fallback reason, substitutions, kept-previous
  skips and the latest mismatch, so a reason that keeps firing is visible. Built (half_fill 4240 bytes of DXIL), not yet
  re-run: A/B with `fill_half_res` and `never_fallback_after_start`.
