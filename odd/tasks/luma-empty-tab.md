# The Luma tab is empty: the game is not running DirectX 11

## Objective

An AMD/Windows tester ran AMDLSS 1.2.1's **Inject DLSS** on an Unreal Engine game (name not yet known) and sent a
screenshot: ReShade's overlay opens (Home), the Luma addon's tab is there, and its whole content area is blank - no
settings at all. The questions: every condition under which that tab renders nothing; which of AMDLSS's layout
choices could cause it; which logs would prove the cause on the tester's PC, and whether Collect logs gathers them.

## Evidence (all read, none of it executed)

**Luma UE, the release AMDLSS pins** (`github.com/Filoppi/Luma-Framework`):
- Tag `latest-749` = commit `ede92efffb864fe3cda2f640906ed0b9699bd54e` (2026-10-14); the source tree used below is
  that commit. The zip AMDLSS downloads is `Luma-Unreal_Engine.zip`, SHA-256
  `a357ce98700262064c280c6b9d56852e2876d22a0ccd04f18cc169d32213b2c2` (the pin in `amdlss/luma.py`).
- The zip's `dxgi.dll` is ReShade **6.8.0.1** (64-bit, "UNOFFICIAL") with the add-on loader; its strings name
  `ReShade.ini`, `ReShade64.dll`, `Add-on failed to initialize or is disabled. Skipped.` and
  `Registered add-on "%s" v%hu.%hu.%hu.%hu using ReShade API version %u.` (the lines that land in ReShade's log).
- The zip's `Luma-Unreal Engine.addon` (9.4 MB PE, x64) contains the release UI strings ("Enable Luma HDR",
  "Super Resolution Auto Exposure", "Reload Shaders", "Shader Defines", "Advanced Settings") and **no** "The
  application tried to create a non DirectX 11 device" and no `SkipAPICompatibilityCheck`: the DirectX 11 warning
  dialog is compiled out of the Unreal Engine build.

**The overlay gate, in the source that build comes from**:
- `Source/Games/Unreal Engine/main.cpp:5` defines `CHECK_GRAPHICS_API_COMPATIBILITY 1`.
- `Source/Core/core.hpp:163-171`: with that macro, `SKIP_UNSUPPORTED_DEVICE_API(api)` expands to
  `if (!IsSupportedGraphicsAPI(api)) return;`.
- `Source/Core/core.hpp:2655-2658`: `IsSupportedGraphicsAPI` is `api == d3d11` - nothing else.
- `Source/Core/core.hpp:11026-11031`: the overlay callback `OnRegisterMainOverlay` opens with exactly
  `SKIP_UNSUPPORTED_DEVICE_API(runtime->get_device()->get_api())` and then dereferences the device data. It is the
  function's **only** early return (checked across the whole function, lines 11026-16360).
- `Source/Core/core.hpp:11263`: the tab bar - and with it the Settings tab (`core.hpp:14175`), which draws the game's
  own `DrawImGuiSettings` (`Source/Games/Unreal Engine/main.cpp:1241`, called at `core.hpp:14884`) - is what any
  supported device gets.
- `Source/Core/core.hpp:2662-2683`: the non-DirectX 11 MessageBox exists only when
  `!CHECK_GRAPHICS_API_COMPATIBILITY`, i.e. never in the Unreal Engine build. On DirectX 12 the addon therefore
  says nothing at all: the tab is blank.
- The tab is registered as `reshade::register_overlay(NAME, ...)` (`core.hpp:16678`) with `NAME` =
  `Globals::MOD_NAME` = "Luma" (`core.hpp:215`, `Source/Core/includes/globals.h:25`): the tab existing proves the
  addon loaded and registered, and a blank tab proves the callback returned at the gate.
- Shaders do not gate the draw: `GetShadersRootPath()` (`core.hpp:1051-1085`) resolves the `Luma/` folder next to
  the module (the `.addon`, i.e. next to the game's .exe), per-game shaders under `Luma/<Game>`
  (`core.hpp:16292-16300`); missing or failing shaders show as the "Reload Shaders" button and its error tooltip,
  not as a blank tab.

**AMDLSS's layout against the pairing the authors document** (`github.com/optiscaler/OptiScaler/wiki`, page
"Luma Unreal Engine Luma UE", last tested 0.9.4 - the base of the HelixSR Scaler package AMDLSS installs):
- Method 1: rename Luma's `dxgi.dll` to `ReShade64.dll`, OptiScaler as `dxgi.dll`, `[Plugins] LoadReshade=true`.
  AMDLSS does exactly that (`amdlss/luma.py:52-53`, `amdlss/framegen.py:49`); the addon, its `Luma` shaders and
  `nvngx_dlss.dll` go next to the .exe. The addon loaded (the tab is there), so no addon-path or shader-path
  mismatch exists to fix.
- The pairing's own D3D12 quirk is by design and not the cause: with the Luma addon present, OptiScaler v0.9.4
  enables its `CreateD3D12DeviceForLuma` quirk when `LoadReshade=true` (`OptiScaler/dllmain.cpp:1153-1156` and
  `1629-1637`) and creates a private D3D12 device **before** loading ReShade
  (`OptiScaler/hooks/Dxgi_Hooks.cpp:83-152`, "Applying Luma DX12 workaround - creating D3D12 device early"), so
  ReShade never attaches to that device - its runtime stays the game's own API.
- HelixSR Scaler 1.0.3 (the pinned package AMDLSS installs, SHA-256
  `974c9d62ea0e088a1e11fe8e7e3196b2d9939d39fb5d709b684c5e7c2c74c351`) reads `CreateD3D12DeviceForLuma` (`auto` in
  its `OptiScaler.ini`; the DLL carries that string and not `DontCreateD3D12DeviceForLuma`), so AMDLSS's advice to
  set `DontCreateD3D12DeviceForLuma=true` named a key this build does not read.
- Luma's own wiki, "Unreal Engine": "at the moment it requires DX11 (it might work in UE5 games too)"; its testers'
  game list marks games that need a `-DX11 launch argument` and games that "Doesn't boot in DX11".

**Logs**:
- ReShade's log is `ReShade.log` next to the .exe: ReShade opens its log at `config.path()` with the extension
  replaced (crosire/reshade `source/dll_main.cpp:159-173`); the shipped `dxgi.dll` names `ReShade.ini` as its
  config, so the log sits beside the ReShade64.dll/config it loaded.
- Luma has no log file of its own (its binary carries none); its messages go to ReShade's log through
  `reshade::log::message` (`core.hpp:1110` and throughout). OptiScaler's log and ini were already collected;
  ReShade's were not.

## Conclusion

The empty tab is the DirectX 12 case. Luma UE is a DirectX 11 mod (every engine hook and the NGX integration are
11-only), the shipped build prints nothing for any other device API (the only message about it is compiled out),
and ReShade's runtime follows the game, so a DirectX 12 Unreal game leaves exactly the observed blank tab.
AMDLSS's layout matches the documented Method 1, so nothing in the install is at fault. Confidence: high, from the
gate being the callback's only early return and from the shipped binary's strings.

## What the tester must check or send

- Whether the game runs DirectX 11: its graphics settings (many Unreal games offer a DirectX 11/12 choice), else
  add `-dx11` to its launch options (Steam: right-click the game, Properties, Launch Options). With DirectX 11 the
  Luma tab has settings.
- Press **Collect logs** and send the zip; it now carries `ReShade.log` and `ReShade.ini` beside the .exe too. The
  line to look for is the add-on registration (`Registered add-on "Luma-Unreal Engine.addon" ...`) and the log's
  own lines about the game's API; an empty-tab run with DirectX 11 disproves this diagnosis.

## What changed in AMDLSS

- `amdlss/luma.py`: `after_text` explains the empty tab and the `-dx11` path, and names
  `CreateD3D12DeviceForLuma=false` (the key the installed package reads); `plan_text` says how to get the game onto
  DirectX 11 (graphics settings or `-dx11`). `SETTINGS = 'Luma.ini'` was removed: Luma keeps its settings through
  ReShade's config API (ReShade.ini), and nothing referenced the constant.
- `amdlss/logs.py`: `PATTERNS` collects `reshade*.log` and `reshade*.ini`.
- `README.md`: the same corrections in the "Inject DLSS (Luma UE)" section.
