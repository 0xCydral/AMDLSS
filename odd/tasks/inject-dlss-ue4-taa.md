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
- [ ] T2 Engine.ini: also resolve `Saved\Config\WindowsNoEditor`, and write `r.TemporalAA.Upsampling=1` next to `r.ScreenPercentage` for UE4.
- [ ] T3 UE4 TAA adapter in the add-on: identify the TAAU dispatch, map color, depth, velocity, output, jitter; evaluate DLSS instead.
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
