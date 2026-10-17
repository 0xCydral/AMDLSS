# AMDLSS

AMDLSS is a small program for Windows and Linux, for AMD graphics cards. It installs [HelixSR](https://github.com/lonewolf0622/HelixSR) into your Direct3D 12 games with one click, so a game that offers DLSS (or FSR 3.1) can use HelixSR's upscaler on your card.

HelixSR runs NVIDIA's DLSS network as Direct3D 12 compute shaders. Its own setup is a console program. AMDLSS is a window around that setup: install HelixSR once, see your games, press Install or Remove.

AMDLSS is unofficial. It is not affiliated with HelixSR, NVIDIA or AMD.

## How to use it

1. Download `AMDLSS.exe` from the [Releases](https://github.com/0xCydral/AMDLSS/releases) page and run it. Windows SmartScreen may warn about an unknown publisher, because the exe is not code-signed. You can also run it from source: `python AMDLSS.pyw` (Python 3 with Tk; tested with 3.14).
2. Press **Install HelixSR**. AMDLSS asks you first, because HelixSR's setup downloads NVIDIA's DLSS 310.7.0 DLL under NVIDIA's license. If you agree, AMDLSS:
   - downloads HelixSR 1.9.1 from its own release page and checks the SHA-256,
   - downloads HelixSR Scaler 1.0.4 (about 127 MB), the package with OptiScaler and AMD's FSR 4.1.1 DLL, so FSR 4.1.1 works without an OptiScaler install,
   - runs HelixSR's own `helixsr-setup.ps1 -Yes`,
   - shows the setup's output in the log.

   The setup downloads Python, numpy and the shader compiler once (about 150 MB), builds the network (about a minute) and deletes NVIDIA's DLL. It also builds HelixSR's frame generation (the DLSS 4 option below): that downloads about 400 MB from NVIDIA's download server once and keeps the two small DLLs it needs. The status line then shows the HelixSR version.
3. Your games appear in the list: name, what the game supports, and its state (not installed, FSR drop-in or OptiScaler). HelixSR finds games from Steam, Epic, GOG and Xbox. For any other game press **Add game folder...**.
   The list only offers games AMDLSS can do something with: the game's folder and its .exe folder exist, and the game has an FSR 3.1 DLL or DLSS, XeSS or FSR 2 files (or HelixSR is already installed in it, so Remove stays reachable), or it is an Unreal Engine shipping build AMDLSS can inject a DLSS call into (see *Inject DLSS (Luma UE)* below). The line under the list says how many games are ready and how many are hidden, for example "10 games ready, 26 hidden". Tick **Show all games** to see the hidden ones greyed out with the reason ("not installed" for a Steam leftover, "no DLSS, FSR 3.1 or XeSS found"); they have no Install button. A folder you added by hand that fails the check always stays in the list with its reason; **Forget folder** removes it. HelixSR looks around such a folder (up to four parent folders), which can take a while on a large drive, so AMDLSS does not ask again until you press **Rescan**.
4. Pick a game and press **Install**. AMDLSS shows what will change and asks you to confirm. When it finishes it tells you what to choose in the game. A game that has both an FSR 3.1 DLL and DLSS, XeSS or FSR 2 files works either way, so before you install you pick **Install as: DLSS (OptiScaler)** or **AMD FSR (drop-in)**. DLSS (OptiScaler) is offered first when the game lists DLSS, so the game's DLSS option is usable; otherwise HelixSR's own default is. HelixSR's own setup installs the right route per game (the FSR drop-in, or HelixSR Scaler for a game with DLSS, XeSS or FSR 2). The OptiScaler route in AMDLSS is HelixSR Scaler (the package with OptiScaler 0.9.4 and HelixSR built in), which AMDLSS downloads with **Install HelixSR** (about 127 MB, checked by SHA-256) and places next to the game's .exe itself. Use the FSR drop-in if OptiScaler gives you trouble in that game.
5. **Remove** runs HelixSR's own uninstall for that game and puts the original files back.

## Linux

HelixSR is developed on Linux (Proton, Mesa RADV), and AMDLSS runs there the same way. Download `AMDLSS-linux-x86_64` from the [Releases](https://github.com/0xCydral/AMDLSS/releases) page, make it executable (`chmod +x AMDLSS-linux-x86_64`) and run it; it needs no Python. From source: `python3 AMDLSS.pyw` (Python 3 with Tk; Pillow for the covers).

What differs from Windows:

- **Install HelixSR** runs HelixSR's own `helixsr-setup.sh --yes`. It needs Python 3 with numpy: your system's `python3` when it has them (for example the `python3-numpy` package), otherwise the setup downloads a portable Python into `~/.local/share/HelixSR`. AMDLSS never installs system packages. The shader compiler runs through Proton, so install Proton in Steam (any Proton game does it) or Wine.
- Games are Proton games. HelixSR finds them in your Linux or Flatpak Steam libraries, plus Heroic and Lutris games; anything else goes through **Add game folder...**.
- AMDLSS keeps its files in `~/.local/share/AMDLSS` (or `$XDG_DATA_HOME/AMDLSS`); HelixSR's own downloads go to `~/.local/share/HelixSR`. Delete these folders to remove everything.
- Self-update replaces the `AMDLSS-linux-x86_64` file in place, SHA-256 checked like on Windows.
- To build the Linux binary yourself: `bash packaging/build-linux.sh` (needs `uv`).

## Updating from HelixSR 1.8.1

If you built HelixSR 1.8.1 with an earlier AMDLSS, the window keeps using it and the status line says HelixSR 1.9.1 is available. Press **Update HelixSR** to download, check and build 1.9.1. Games that still run 1.8.1 show the pill **update** on their card: pick one and press **Update**; nothing changes in a game until you do. From 1.9.0 each install writes a commented `helixsr.ini` next to HelixSR's DLL in the game (upscaling and sharpening settings), and the uninstall removes it only if you did not edit it. Collect logs includes it. After updating you can delete the old `%LOCALAPPDATA%\AMDLSS\HelixSR-1.8.1` folder.

## What to choose in the game

HelixSR picks one of two ways for each game. AMDLSS shows the result after the install.

| State in the list | In the game's graphics settings |
|---|---|
| FSR drop-in (the game has an FSR 3.1 DLL) | choose AMD FSR |
| OptiScaler (picked for a game that has an FSR 3.1 DLL, or the game has DLSS, XeSS or FSR 2 only) | choose DLSS. Press Insert in the game to open OptiScaler's menu |
| FSR 4.1.1 drop-in | choose AMD FSR |
| FSR 4.1.1 (OptiScaler) | choose DLSS, then pick FSR 4.1.1 in OptiScaler's menu (see FSR 4.1.1 below) |
| DLSS injected (Luma UE) | press Home, then set Upscaler/Super Resolution to DLSS in Luma's overlay: the game has no upscaler entry of its own (see Inject DLSS below) |

### Frame generation (OptiScaler route only)

Some games, such as Dragon's Dogma 2, offer only NVIDIA's DLSS Frame Generation, which cannot run on AMD. For a game installed through the OptiScaler route, **Frame generation** is off by default and offers two generators; install (or Update) applies the pick, and the log, the report and the after-install text name it:

- **AMD FSR frame generation** is what AMDLSS did before. It sets four values in the `OptiScaler.ini` next to the game's exe, all in `[FrameGen]`: `Enabled=true`, `FGInput=dlssg`, `FGOutput=fsrfg` and `PreserveSwapChain=false`. `Enabled` is OptiScaler's own frame generation switch, off by default and otherwise only reachable from its overlay menu. While it is on, OptiScaler generates frames whenever the game uses DLSS, so do not also pick the game's own FSR frame generation: two frame generators at once cause heavy ghosting and stutter. `PreserveSwapChain=false` stops OptiScaler from keeping its frame generation swap chain when the game recreates its window, which broke frame generation and crashed Assassin's Creed Shadows on exit; OptiScaler sets the same value itself for other games that use DLSS Frame Generation.
- **DLSS 4 frame generation (HelixSR), experimental** runs HelixSR's own network from the frame generation files `Install HelixSR` built (`helixsr-fg` next to HelixSR's DLL). It sets `[HelixSR] FrameGeneration=on` and, in `[FrameGen]`, `FGInput=nvngxfg` and `FGNvngxReplacement=HelixSR`; the game's own DLSS Frame Generation stays the on/off switch. It makes one generated frame, and it needs those files: when HelixSR's setup did not build them the choice is greyed with the reason.

In the game, turn on DLSS Frame Generation either way. The upscaler network HelixSR runs is DLSS "Model E" (a CNN); there is no transformer option and AMDLSS does not offer one.

- The choice only appears in games that offer DLSS frame generation: AMDLSS looks for `sl.interposer.dll` with `sl.dlss_g.dll` (Streamline, up to nine folders down, so it also reaches Unreal's own `Engine\Plugins\Runtime\Nvidia\DLSS\Binaries\ThirdParty\Win64`), or for NVIDIA's `nvngx_dlssg.dll` on its own (a game whose frame generation does not go through Streamline, such as Black Myth: Wukong). The `OptiScaler` and `HelixSR` folders the install itself brings are not searched. Without any of those the whole control is greyed out with "This game has no DLSS Frame Generation for AMDLSS to replace." Install never turns frame generation on there, and Update turns it off if an earlier version had.
- If the DLSS 4 files go missing after an install (an **Install HelixSR** update that could not build them), Install or Update turns frame generation back off, names the reason in the after-install text and in the game's log, and never leaves `OptiScaler.ini` pointing at files that are not there.
- It is untested on real games so far.
- With frame generation off, the off values are written out (`[HelixSR] FrameGeneration=off`, `[FrameGen] Enabled=false` and `FGInput=nofg`): HelixSR Scaler reads its own `auto` as "run HelixSR's frame generation once the `helixsr-fg` files are there", so a game left at `auto` would turn it on by itself. Switching a generator on and off again comes back to exactly the same bytes (AMDLSS keeps the values it replaced in `%LOCALAPPDATA%\AMDLSS\framegen.json`).
- If another OptiScaler copy already sits next to the game's exe (an older install, or one another tool left under a different name), the install moves it into `HelixSR\amdlss-scaler-backup` and records it like every other file it replaces: the game loads whichever proxy it finds first, so a leftover would shadow HelixSR's. Nothing is deleted and **Remove** puts it back. The install only stops, naming the file, when it cannot be moved (the game is running) or does not hash the same afterwards.
- **Remove** takes AMDLSS's Scaler install out and puts the game's files back, or runs HelixSR's own uninstall for an install AMDLSS 1.0.0 made, so nothing stays behind.

HelixSR also has an experimental route that loads its DLSS loader. AMDLSS does not offer it. A game that already uses it (from HelixSR's own setup) shows as "DLSS loader (experimental)" and can only be removed.

## Inject DLSS (Luma UE), experimental

Some Unreal Engine games never call an upscaler at all, so OptiScaler has nothing to intercept and AMDLSS had nothing to install in them. For those, AMDLSS injects a DLSS call: it installs [Luma Framework](https://github.com/Filoppi/Luma-Framework)'s Unreal Engine mod next to the game's .exe, which replaces the engine's TAA with a DLSS (DLAA) call, and installs HelixSR Scaler in the same layout, so the call HelixSR runs is the one Luma makes. The route appears by itself for a game that fits, with the Upscaler row greyed out and the reason under it: such a game has no upscaler to pick.

**What AMDLSS places**, following OptiScaler's own instructions for Luma UE (its wiki page "Luma Unreal Engine Luma UE", tested with OptiScaler 0.9.4, the version inside HelixSR Scaler): Luma's `dxgi.dll` becomes `ReShade64.dll`, OptiScaler takes the name `dxgi.dll` itself, `OptiScaler.ini` is set to load it (`[Plugins] LoadReshade=true`), and Luma's addon, its `Luma` shader folder and the DLSS runtime it calls go next to the game's .exe. Luma UE build 749 is downloaded from Luma's own release page at that moment (about 36 MB, SHA-256 checked; its licence is a custom MIT) together with the HelixSR Scaler package as for any other game. AMDLSS does not redistribute Luma's files.

**In the game**: press Home and set **Upscaler/Super Resolution** to **DLSS** in Luma's overlay; the game's own menu has no upscaler entry. Press Insert for OptiScaler's overlay, which should name DLSS as the input. A Luma tab that opens empty - no settings at all - means the game is running DirectX 12, which Luma UE cannot use: pick DirectX 11 in the game's graphics settings if it offers one, or add `-dx11` to its launch options and start it again. If the frame rate is capped like Vsync, turn Vsync off in OptiScaler's overlay. If the game crashes or shows a non-DirectX 11 device error, set `CreateD3D12DeviceForLuma=false` in its `OptiScaler.ini` (the key HelixSR Scaler 1.0.3 reads; newer OptiScaler builds call it `DontCreateD3D12DeviceForLuma=true`), the workaround OptiScaler's own page documents for Linux and for Windows 10 with FSR 4.

**Where it is offered**: only for a game whose .exe folder is an Unreal shipping folder (`...\Binaries\Win64` or `WinGDK`, holding a 64-bit `*-Win64-Shipping.exe`) and which ships no FSR 3.1, DLSS, XeSS or FSR 2 file of its own. A game with anti-cheat files is refused outright, with its own line in the list. The game has to be able to run in DirectX 11, which Luma UE needs and AMDLSS cannot tell from a game's files: check the game's graphics settings for a DirectX 11 mode, or add `-dx11` to its launch options (an empty Luma tab is the sign it is on DirectX 12); Luma's own wiki lists the games its authors have tried, with what each one needs. AMDLSS claims nothing for a game it cannot see itself in that layout.

**Remove** puts the game's own files back, deletes what AMDLSS placed (a file you changed by hand is kept and reported), restores the `OptiScaler.ini` value it replaced, and takes the HelixSR Scaler install out with it.

This route is experimental: the pairing is the one its authors document and test, not one AMDLSS has tested in a game.

## FSR 4.1.1

Besides HelixSR, a game can use AMD's own FSR 4.1.1. Pick **Upscaler: HelixSR | FSR 4.1.1** in the panel under the game list before you press Install. AMDLSS remembers the pick per game. Every game card has a line "FSR 4.1.1: yes" or "FSR 4.1.1: no, <why>", and the panel spells the reason out, so you can see at a glance which games can use it.

**Which cards.** HelixSR Scaler 1.0.4 carries AMD's official FidelityFX upscaler DLL 4.1.1.2740 (`OptiScaler\amd_fidelityfx_upscaler_dx12.dll`; the earlier HelixSR 1.9.0.2 build had the same file in its amd-fsr folder, and AMDLSS reads either). It runs FSR 4 on RX 9000 cards and an INT8 version of FSR 4 on RX 7000 cards. On any other card AMD's DLL quietly falls back to FSR 3.1.5, so AMDLSS says no there (Radeon 780M and other integrated graphics included). RX 6000 cards are not covered by AMD's DLL. They need a community build of the FSR 4 DLL, which AMDLSS does not download. AMDLSS looks for it by itself: in each game's folder and its `HelixSR\my-fsr` folder, in your Downloads, Desktop and Documents (four folders deep) and in its own data folder, and keeps the highest version it finds. If you only have an archive (a .7z or .zip named like an FSR 4 download), AMDLSS tells you to unpack it; it never unpacks archives. If nothing is found, press **Choose FSR 4 DLL...** and pick the file yourself. It must be a 64-bit DLL that exports `ffxCreateContext` and has a file version starting with 4. On RX 7000 and RX 9000 you can pick your own DLL the same way, to use it instead of AMD's. The panel shows which DLL is used and where it was found.

**Which games.**

- A game with an FSR 3.1 DLL: AMDLSS swaps the game's `amd_fidelityfx_upscaler_dx12.dll` (or `amd_fidelityfx_dx12.dll`, which gets AMD's small loader beside the FSR 4 DLL; AMDLSS downloads that 26 KB file from AMD's FidelityFX SDK once and checks its SHA-256) and keeps the game's own file as `*.original.dll`. A record `amdlss-fsr4.json` beside it lists every file moved and created, and Remove puts back exactly that. A file you replaced by hand is kept and reported. In the game choose AMD FSR.
- A game with DLSS, XeSS or FSR 2: AMDLSS installs HelixSR Scaler (OptiScaler 0.9.4 with HelixSR built in, so fakenvapi and frame generation keep working) and points `OptiScaler.ini` (`[Libraries] FfxDx12Path` and `FfxDx12SRPath`) at two copies of the FSR 4 DLL in `HelixSR\fsr4` next to the game's .exe. In the game choose DLSS, press Insert, then Upscaling > Upscalers > FFX Upscaler and pick FSR 4.1.1; the menu header names the version in use. The FSR 4 watermark in that menu draws the version on screen. AMDLSS leaves `UpscalerIndex` alone, because which entry of the DLL is FSR 4 depends on the card and AMDLSS could not test every card.
- Never in games with anti-cheat (the game is marked "no, anti-cheat"), and not in a folder that covers other games. Vulkan and DirectX 11 games are not covered.

Switching the upscaler of an installed game removes the old one first. **Remove** undoes whichever is installed. HelixSR's drop-in and AMDLSS's FSR 4 drop-in refuse to share a game.

AMD's DLL and loader are redistributed under AMD's license (`FidelityFX_v2_LICENSE.md`, copied beside every AMD DLL AMDLSS installs). FSR is a trademark of Advanced Micro Devices, Inc.

## Sending logs

If a game looks the same with HelixSR as without it, send a log zip instead of describing it. Pick the game, press **Collect logs** (or right-click the game), read the list in the dialog and confirm. AMDLSS writes `AMDLSS-logs-<game>-<date>-<time>.zip` to your Desktop and opens Explorer with it selected. Send that file.

The zip answers four questions: did HelixSR load, did the game call DLSS or FSR at all, did OptiScaler pass the call to HelixSR, did the network run. It holds, for each file that exists:

- the logs: `helixsr.log` and `helixsr_loader.log` (HelixSR's `HelixSR` folder next to the game's .exe, or next to the game's FSR DLL for the drop-in), `OptiScaler.log`, `fakenvapi.log`;
- the settings and install record: `OptiScaler.ini`, `fakenvapi.ini`, `helixsr.ini`, `helixsr-install.json`;
- `files.txt`: name, size, date and file version of every .dll and .exe in those folders, and whether each upscaler DLL is HelixSR's;
- `crashes.txt`: on Windows, what the Windows Application log recorded about this game's crashes in the last 14 days (which DLL faulted, the exception code and offset), plus Application Hang records (Event ID 1002), whether the game was still running when you pressed the button, and the names, sizes and dates of its crash dumps (the dumps themselves never leave your PC). For an Unreal Engine game it also lists the folders in its `Saved\Crashes` (names, sizes and dates, never their files) and adds the last 200 lines of its newest `Saved\Logs` file as `unreal-latest.log`; on Linux, whether a Proton log (`~/steam-<appid>.log`, written with `PROTON_LOG=1` in the game's Steam launch options) was found, and that log sits beside it as `proton.log` when present;
- `history/`: the logs AMDLSS kept right before earlier installs, updates and removes of this game, so the run that crashed survives a switch of route;
- `system.txt`: Windows version, graphics card and driver, CPU, RAM, the AMDLSS and HelixSR versions and `helixsr_setup.json`;
- `amdlss-install.log`: what AMDLSS printed when it installed into that game.

Your Windows user name is replaced by `<user>` in every path and text. Apart from the crash record above (crash dump names are read, dumps are not), nothing else from your user folder is read. Collect logs uploads nothing. A log larger than 16 MB goes in as its last 16 MB. Before an install, update or remove changes a game, AMDLSS copies its current logs into its own data folder (`history/`, at most 10 runs and 32 MB per game); the newest 5 runs ride along in Collect logs and Send logs.

**Send logs** (next to Rescan, or right-click a game) does the same for every game that has an install or a log, in one upload to the AMDLSS log server, and shows a code such as `AMDLSS report K7M2Q9XD`; it is also copied to the clipboard. Paste it in the chat. Each game's files sit in a folder named after the game, `system.txt` and a `games.txt` (name, folder, install mode and HelixSR version of each game) sit at the top. The dialog lists the games and the file count of each before anything leaves your PC. Only text files go up (at most 8 MiB; the largest logs of each game are cut to their last lines to fit) and the server deletes a report after 30 days. The server is in `receiver/`; `tools/amdlss-logs.py fetch <code>` downloads a report.

For a game installed through the OptiScaler route, AMDLSS turns OptiScaler's file logging on after the install (`LogToFile=true` and `LogLevel=2` in the `[Log]` section of `OptiScaler.ini`, the same way as the frame generation values: nothing else in the file changes). **Remove** puts the file back through HelixSR's own uninstall, drops AMDLSS's record and deletes the `OptiScaler.log` that this switch made. Only a game run after the install has an `OptiScaler.log`: start the game, play a minute, close it, then press Collect logs.

## Updates

When AMDLSS opens it asks GitHub in the background whether a newer release exists. If one does, a card appears at the top: **Update** and **Release notes**. **Check for updates** (bottom right) asks again on demand. Pre-releases are only offered while **Beta versions** is ticked; it starts ticked when you run a beta, and a beta is always older than its final release (`0.1.0-beta.4` < `0.1.0`).

Update downloads the release's `AMDLSS.exe` next to the running one and compares its SHA-256 with the digest GitHub lists for that file. With no digest, or a different one, nothing changes. Then AMDLSS renames itself to `AMDLSS.exe.old`, moves the new file into place, starts it with the same arguments and closes; the next start deletes `AMDLSS.exe.old`. The exe must sit in a folder you can write to. Any failure (no network, GitHub's rate limit, a bad hash) shows one line and leaves everything as it was. Your games and settings are not touched.

Run from source, AMDLSS only shows the card with a link to the release page. `AMDLSS.exe --update` does the whole update without a window.

## Game covers

The game cards show the game's cover when AMDLSS can tell which game it is and Steam or GOG has art for it. Nothing is sent until the game is known: AMDLSS first works out what the game is from files already on your disk, and reads only these (nothing is changed or uploaded):

- Steam: the `appmanifest_*.acf` file of the library the game is in (its app id and name).
- GOG: `goggame-<id>.info` in the game's folder or a folder above it (its GOG id and name).
- Epic: the launcher's manifests in `%PROGRAMDATA%\Epic\EpicGamesLauncher\Data\Manifests` whose install folder holds the game (its name).
- Xbox / Game Pass: `MicrosoftGame.config` or `appxmanifest.xml` in the game's folder or above (its name).
- EA app: `__Installer\installerdata.xml` (its name).
- The main exe's version information (its product name, ignored when it only names an engine such as Unreal Engine), then the name HelixSR lists and the folder's name.

What is sent, and where:

- A game with a Steam manifest: only its Steam app id, to `cdn.akamai.steamstatic.com` for the image (and, for newer games, to `store.steampowered.com` for the image's address).
- Any other game: its titles from the list above, one at a time, to `steamcommunity.com` and `store.steampowered.com` (their search), until Steam has a game of that name. AMDLSS takes a result only when its name is the title, or continues the title with whole words (for example "The Witcher 3" and "The Witcher 3: Wild Hunt - Remastered") and the store says it is a game, not DLC or a soundtrack. When there is any doubt the card keeps its colour.
- A GOG game that Steam does not have: its GOG id to `api.gog.com`, and the image from `gog-statics.com`.

Downloads run in the background, so offline the window opens and works as before. Covers are saved in `%LOCALAPPDATA%\AMDLSS\covers`, with a small `names.json` that remembers which game each cover belongs to (a game nothing was found for is looked up again after 7 days). The box **Show game covers (downloads from Steam and GOG)** is on by default; turn it off to stop all requests and show the plain cards. Covers need Pillow, which the exe includes (from source: `pip install pillow`); without it the box is hidden and no cover is fetched. The images belong to their owners. AMDLSS is not affiliated with Valve, Steam or GOG.

## Warnings

- Do not use this in online games or games with anti-cheat. The account can be banned, or the game can refuse to start. AMDLSS looks for anti-cheat files in each game folder and flags the game, but it cannot know every game. Check it yourself.
- Close the game before you install or remove.
- HelixSR is freeware that you did not get from NVIDIA or AMD. Read its [README](https://github.com/lonewolf0622/HelixSR) first.
- The network HelixSR builds is NVIDIA's property. Do not share the files.

## What AMDLSS does and does not do

- It runs HelixSR's own scripts without changing them. Listing games, installing and removing use HelixSR's `helixsr_install.py` with HelixSR's own embedded Python (`install N`, `uninstall N`, `--folder PATH`). A small read-only helper, run in that same Python, reads HelixSR's game scan and returns it as JSON. If the helper fails, AMDLSS parses the installer's printed `list` output instead. `install N` lets HelixSR's installer take its own route for the game (the FSR drop-in, or HelixSR Scaler since 1.9.1); AMDLSS installs the OptiScaler route itself so its record, FSR 4.1.1 and Luma UE behave the same on both systems: it downloads the HelixSR Scaler package (SHA-256 pinned, size checked, zip-slip screened), places its files next to the game's .exe following the package's own `HelixSR-Scaler-package.txt`, and keeps every game file it replaced under `HelixSR\amdlss-scaler-backup` with a record (`HelixSR\amdlss-scaler.json`) so Remove puts them back. Installs HelixSR itself made (AMDLSS 1.0.0's bridge, HelixSR Desktop, HelixSR's own setup) are recognised from HelixSR's own records and removed through HelixSR's own `uninstall N`.
- Before each install or remove, AMDLSS reads the list again and matches the game by folder, so a change in your library cannot send the action to another game.
- For the injected route (a game with no upscaler of its own) AMDLSS does not change the game through HelixSR's installer at all: HelixSR's own scan drops such a game, so it has no list number to act by. AMDLSS downloads Luma UE's official release, and the HelixSR Scaler package, and places both next to the game's .exe itself, recording every file it moved or created (`HelixSR\amdlss-luma.json` and `HelixSR\amdlss-scaler.json`) so Remove restores the game exactly.
- It does not bundle, modify or redistribute HelixSR, Luma or any NVIDIA file. This repository holds only AMDLSS's own code. The AMD files for FSR 4.1.1 come from the HelixSR folder or the HelixSR Scaler package (the DLL) and from AMD's FidelityFX SDK on GitHub (the loader, only when a game needs it).
- Files: HelixSR is extracted to `%LOCALAPPDATA%\AMDLSS\HelixSR-1.9.1`, the HelixSR Scaler package to `%LOCALAPPDATA%\AMDLSS\HelixSR-Scaler-1.0.4`, the injected route's Luma UE to `%LOCALAPPDATA%\AMDLSS\Luma-Unreal-Engine-749`, and the games you added by hand are saved in `%LOCALAPPDATA%\AMDLSS\settings.json`. HelixSR's own downloads go to `%LOCALAPPDATA%\HelixSR`. Delete these folders to remove everything. Remove HelixSR from your games first.

## Credits and licenses

- AMDLSS is free software under the GNU General Public License, version 2 or (at your option) any later version. See `LICENSE`. Parts are copied from the launcher and tools of the [Bloodborne PC port](https://github.com/0xCydral/bloodborne_pc) (GPL-2.0-or-later): the window's palette and button and progress-bar code, and the download, verify and extract code of `tools/fetch_helixsr.py`.
- [HelixSR](https://github.com/lonewolf0622/HelixSR) is closed-source freeware by lonewolf0622 under the [HelixSR Freeware License](https://github.com/lonewolf0622/HelixSR/blob/HEAD/LICENSE). Its setup scripts are Apache-2.0 up to 1.8.1 and under the same Freeware License from 1.9.0, which allows running the setup and redistributing it only unmodified, complete and free. AMDLSS downloads the unmodified official release.
- [OptiScaler](https://github.com/optiscaler/OptiScaler) (GPL-3.0) comes inside the HelixSR Scaler package, which is based on OptiScaler 0.9.4 and is GPL-3.0 as well; AMDLSS downloads that package and places it in games without an FSR 3.1 DLL.
- [Luma Framework](https://github.com/Filoppi/Luma-Framework) is Filippo Tarpini's DX11 modding framework under a custom MIT license ("Any reuse of this code shall include the names of the authors or of the project. Commercial usage is possible but only after asking permission to the authors."). AMDLSS contains none of its code: it downloads Luma's own `Luma-Unreal_Engine.zip` release from GitHub when a game is injected into, checks its pinned SHA-256, and places its files next to the game's .exe (its `dxgi.dll` renamed `ReShade64.dll`, the one rename OptiScaler's own instructions for it require). The `nvngx_dlss.dll` inside that release is NVIDIA's DLSS runtime, which Luma ships; AMDLSS copies it as it is.
- AMD's FidelityFX upscaler DLL 4.1.1 (inside the HelixSR Scaler package, or an older HelixSR folder) and AMD's FidelityFX SDK v2.3.0 loader are AMD's, under AMD's FidelityFX license (`FidelityFX_v2_LICENSE.md`, which AMDLSS copies beside them).
- NVIDIA's DLSS 310.7.0 DLL is downloaded by HelixSR's setup under [NVIDIA's license](https://github.com/NVIDIA/DLSS/blob/v310.7.0/LICENSE.txt).
- Ideas, not code, from similar tools: per-game state lines and a library view (OptiScaler Client, GPL-3.0; DLSS5-Autopilot, MIT), file-name based anti-cheat detection with whole-word matching (DLSS5-Autopilot; the marker list here is our own), a copy-log button and an "Open folder" action (DLSS5-Swapper, MIT).

## Build

AMDLSS needs only Python's standard library and Tk to run (Pillow too, for the game covers). To build `dist/AMDLSS.exe` you need a Windows Python 3 from python.org (the build was tested with 3.14) and, in Git Bash:

```
bash packaging/build.sh
```

The script makes a private virtual environment in `out/pyenv`, installs PyInstaller and Pillow there, and freezes `AMDLSS.pyw` into one windowed exe (about 19 MB with Pillow). It prints the SHA-256 of the exe. `packaging/make_icon.py` redraws `amdlss.ico` (needs Pillow).

## Tests

```
python AMDLSS.pyw --selftest      # logic checks, no window, exits 0
python -m pytest -q               # unit tests
```

The unit tests need no network and no HelixSR. One more test runs HelixSR's real installer and the real HelixSR Scaler package against a fake game folder in `%TEMP%\amdlss-fake`. It is skipped unless `AMDLSS_TEST_HELIXSR` names a built HelixSR folder and `AMDLSS_TEST_SCALER` the unpacked HelixSR Scaler package (`AMDLSS_TEST_ROOT` moves the fake folder elsewhere, `AMDLSS_TEST_INSTALLER` lays a newer `helixsr_install.py` over the copy). It copies the HelixSR folder first, runs the FSR drop-in route, the Scaler route, an earlier install's removal, the frame generation switches and FSR 4.1.1, and deletes everything it made.
