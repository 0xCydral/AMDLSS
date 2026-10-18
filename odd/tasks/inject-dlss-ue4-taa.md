# Inject DLSS: Unreal Engine 4 TemporalAA games

## Objective

Inject DLSS (and HelixSR on AMD) into UE4 games that have only TemporalAA, starting with Project Wingman (UE 4.27).

## Problem

The DX12 add-on only recognizes UE5's TSR pass. On Project Wingman it logged `upscaler=none` (tester report, RX 6700 XT,
2026-10-17). The game also runs DX11 by default, and Engine.ini lives in `Saved\Config\WindowsNoEditor`, which the
add-on does not look in.

## Constraints

- HelixSR and the add-on are DX12 only: the game must run with `-dx12` (UE 4.27 exe carries D3D12RHI).
- UE4 TAA upscales only with `r.TemporalAA.Upsampling=1` (TAAU); otherwise it runs at render size and a spatial pass upscales.
- GeForce keeps NVIDIA's driver DLSS; AMD goes through HelixSR Scaler's NGX exports (1.3.1 path).

## Tasks

- [ ] T1 Confirm Project Wingman runs with `-dx12` and capture the TAAU pass (pipeline, bindings, constants). Route: inline + game run on the owner's PC.
- [ ] T2 Engine.ini: also resolve `Saved\Config\WindowsNoEditor` (done, `engine_ini_path`), and write `r.TemporalAA.Upsampling=1` next to `r.ScreenPercentage` for UE4 (pending: not done in this slice).
- [ ] T3 UE4 TAA adapter in the add-on: identify the TAAU dispatch, map color, depth, velocity, output, jitter; evaluate DLSS instead. Code written and builds with `dlss logic selftest ok` (route: delegated, one writer); not run on the game. Stays open until T1/T5 show `DLSS: active` in Wingman.
- [ ] T4 Launcher: offer Inject DLSS on UE4 DX12-capable games and add `-dx12` (or tell the user to).
- [ ] T5 Measure on the owner's PC: TAA vs DLSS fps at equal and lower render scale, Radeon mode included.

## Acceptance

- Overlay shows `DLSS: active` in Project Wingman, image stable in motion.
- DLSS at a lower render scale gives more fps than the game's TAA at 100%.

## Progress

- 2026-10-17: Wingman is `++UE4+Release-4.27`, 24 D3D12RHI strings in the shipping exe. Mapping TSR detection in the add-on.
- 2026-10-18: `-dx12` works under Proton (vkd3d-proton cache written, add-on loads). Probe mode (compute) committed `00fd3ea`.
  Wingman has 10 compute shaders, none binds an r16g16 velocity texture: TAA is likely a pixel shader. The game renders
  3816x2148 for a 2560x1440 window: its menu "resolution scale" defaults to 150% and overrides Engine.ini's
  r.ScreenPercentage. Probe extended to pixel-shader draws, `f1ef62f`.
- 2026-10-18: TAA found: compute pipeline `5165782d00fcf560` (DXBC SM5, 13576 bytes, 8x8 threads). Bindings: t0 eye
  adaptation 1x1, t1 scene color rgba16f, t2 depth (r32 view of d32s8), t3 velocity r16g16b16a16_unorm (UE 4.27
  4-channel velocity, written as GBuffer RT4 in the base pass), t4 stencil (responsive AA bit 8), t5 history rgba16f,
  u0 output rgba16f; cb0 29 vec4 (TAA params), cb1 145 vec4 (View). Decompile: DXBC -> vkd3d-compiler spirv ->
  spirv-cross GLSL (dxc cannot read DXBC). The game always renders at its menu scale (150% default, 2864x1612 at
  1080p, 3816x2148 at 1440p). Mapping cb0/cb1 offsets (in progress).
- 2026-10-18: T3 code in `probe/dx12/dlss`: hash `5165782d00fcf560` maps to `TsrPass::ue4_taa`; bindings t1 color, t2 depth, t3 velocity,
  u0 output are found from the pipeline layout (any root parameter), cb0/cb1 are found by register b0/b1 in root descriptors or
  tables and read from upload heaps (`prepare_ue4_upscale`, parsers `parse_ue4_taa_constants` and `parse_ue4_view` in `logic.hpp`).
  `camera_mv.hlsl` builds the motion vectors (velocity texture, or camera reprojection from depth for static pixels; ini
  `ue4_mv_unjitter`, default 1). The pass is replaced only when render <= output; at the game's 150% default the game's TAA runs and
  the log says "render larger than output (game resolution scale above 100%)". The frame-sequence dump skips this pass. Not run on
  the game. First run: read the `UE4 TAAU root signature` and `UE4 TAAU constant buffers` log lines to confirm where cb0/cb1 sit
  (the probe log showed no CBV for this shader, so they may be absent from the shadow) and whether cb1 is in an upload heap.
  Unresolved: unjitter sign (test `ue4_mv_unjitter` 1 and 0 on a still camera), pre-exposure only reaches DLSS with auto_exposure=0.
