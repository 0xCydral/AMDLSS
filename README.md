# AMDLSS

AMDLSS is a small program for Windows and Linux, for AMD graphics cards. It installs [HelixSR](https://github.com/lonewolf0622/HelixSR) into your Direct3D 12 games with one click, so a game that offers DLSS (or FSR 3.1) can use HelixSR's upscaler on your card.

HelixSR runs NVIDIA's DLSS network as Direct3D 12 compute shaders. Its own setup is a console program. AMDLSS is a window around that setup: install HelixSR once, see your games, press Install or Remove.

AMDLSS is unofficial. It is not affiliated with HelixSR, NVIDIA or AMD.

## How to use it

1. Download `AMDLSS.exe` from the [Releases](https://github.com/0xCydral/AMDLSS/releases) page and run it. Windows SmartScreen may warn about an unknown publisher, because the exe is not code-signed. You can also run it from source: `python AMDLSS.pyw` (Python 3 with Tk; tested with 3.14).
2. Press **Install HelixSR**. AMDLSS asks you first, because HelixSR's setup downloads NVIDIA's DLSS 310.7.0 DLL under NVIDIA's license. If you agree, AMDLSS:
   - downloads HelixSR 1.9.0 from its own release page and checks the SHA-256,
   - runs HelixSR's own `helixsr-setup.ps1 -Yes`,
   - shows the setup's output in the log.

   The setup downloads Python, numpy and the shader compiler once (about 150 MB), builds the network (about a minute) and deletes NVIDIA's DLL. The status line then shows the HelixSR version.
3. Your games appear in the list: name, what the game supports, and its state (not installed, FSR drop-in or OptiScaler). HelixSR finds games from Steam, Epic, GOG and Xbox. For any other game press **Add game folder...**.
   The list only offers games AMDLSS can do something with: the game's folder and its .exe folder exist, and the game has an FSR 3.1 DLL or DLSS, XeSS or FSR 2 files (or HelixSR is already installed in it, so Remove stays reachable). The line under the list says how many games are ready and how many are hidden, for example "10 games ready, 26 hidden". Tick **Show all games** to see the hidden ones greyed out with the reason ("not installed" for a Steam leftover, "no DLSS, FSR 3.1 or XeSS found"); they have no Install button. A folder you added by hand that fails the check always stays in the list with its reason; **Forget folder** removes it. HelixSR looks around such a folder (up to four parent folders), which can take a while on a large drive, so AMDLSS does not ask again until you press **Rescan**.
4. Pick a game and press **Install**. AMDLSS shows what will change and asks you to confirm. When it finishes it tells you what to choose in the game. A game that has both an FSR 3.1 DLL and DLSS, XeSS or FSR 2 files works either way, so before you install you pick **Install as: DLSS (OptiScaler)** or **AMD FSR (drop-in)**. DLSS (OptiScaler) is offered first when the game lists DLSS, so the game's DLSS option is usable; otherwise HelixSR's own default is. This is the same choice as the `o` and `f` answers in HelixSR's own setup. Use the FSR drop-in if OptiScaler gives you trouble in that game.
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

If you built HelixSR 1.8.1 with an earlier AMDLSS, the window keeps using it and the status line says HelixSR 1.9.0 is available. Press **Update HelixSR** to download, check and build 1.9.0. Games that still run 1.8.1 show the pill **update** on their card: pick one and press **Update**; nothing changes in a game until you do. From 1.9.0 each install writes a commented `helixsr.ini` next to HelixSR's DLL in the game (upscaling and sharpening settings), and the uninstall removes it only if you did not edit it. Collect logs includes it. After updating you can delete the old `%LOCALAPPDATA%\AMDLSS\HelixSR-1.8.1` folder.

## What to choose in the game

HelixSR picks one of two ways for each game. AMDLSS shows the result after the install.

| State in the list | In the game's graphics settings |
|---|---|
| FSR drop-in (the game has an FSR 3.1 DLL) | choose AMD FSR |
| OptiScaler (picked for a game that has an FSR 3.1 DLL, or the game has DLSS, XeSS or FSR 2 only) | choose DLSS. Press Insert in the game to open OptiScaler's menu |

### Frame generation (OptiScaler route only)

Some games, such as Dragon's Dogma 2, offer only NVIDIA's DLSS Frame Generation, which cannot run on AMD. For a game installed through the OptiScaler route, the box **Frame generation: use the game's DLSS Frame Generation option (runs AMD FSR frame generation)** is off by default. When you turn it on and press Install (or Update), AMDLSS sets four values in the `OptiScaler.ini` that HelixSR placed next to the game's exe, all in `[FrameGen]`: `Enabled=true`, `FGInput=dlssg`, `FGOutput=fsrfg` and `PreserveSwapChain=false`. `Enabled` is OptiScaler's own frame generation switch, off by default and otherwise only reachable from its overlay menu. While it is on, OptiScaler generates frames whenever the game uses DLSS, so do not also pick the game's own FSR frame generation: two frame generators at once cause heavy ghosting and stutter. `PreserveSwapChain=false` stops OptiScaler from keeping its frame generation swap chain when the game recreates its window, which broke frame generation and crashed Assassin's Creed Shadows on exit; OptiScaler sets the same value itself for other games that use DLSS Frame Generation. Turning frame generation off puts all four back as they were. Every other byte of the file stays as it was. In the game, turn on DLSS Frame Generation; it runs AMD's frame generation. Keep NVIDIA Reflex off.

- It only works in games that use NVIDIA Streamline for DLSS frame generation. That is OptiScaler's limit, not AMDLSS's, so AMDLSS offers the box only where it finds `sl.interposer.dll` and `sl.dlss_g.dll` in the game's folder (up to seven folders down; they often sit in a subfolder such as `NVStreamline\production`). Without them the box is greyed out and says "This game has no DLSS Frame Generation for AMDLSS to replace." Install never turns frame generation on there, and Update turns it off if an earlier version had.
- It is untested on real games so far.
- Update with the box off puts the two values back as they were (AMDLSS keeps them in `%LOCALAPPDATA%\AMDLSS\framegen.json`). **Remove** runs HelixSR's own uninstall, which restores or deletes `OptiScaler.ini`, so nothing stays behind.

HelixSR also has an experimental route that loads its DLSS loader. AMDLSS does not offer it. A game that already uses it (from HelixSR's own setup) shows as "DLSS loader (experimental)" and can only be removed.

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

- It runs HelixSR's own scripts without changing them. Listing games, installing and removing use HelixSR's `helixsr_install.py` with HelixSR's own embedded Python (`install N`, `uninstall N`, `--folder PATH`). A small read-only helper, run in that same Python, reads HelixSR's game scan and returns it as JSON. If the helper fails, AMDLSS parses the installer's printed `list` output instead. `install N` always takes HelixSR's default route, so for the OptiScaler route in a game that also has an FSR 3.1 DLL, a second small helper in the same Python does what the installer's `o` answer does (it sets `route` and `chosen`) and then calls the installer's own `install()`. HelixSR's files are only imported.
- Before each install or remove, AMDLSS reads the list again and matches the game by folder, so a change in your library cannot send the action to another game.
- It does not bundle, modify or redistribute HelixSR or any NVIDIA file. This repository holds only AMDLSS's own code.
- Files: HelixSR is extracted to `%LOCALAPPDATA%\AMDLSS\HelixSR-1.9.0`, and the games you added by hand are saved in `%LOCALAPPDATA%\AMDLSS\settings.json`. HelixSR's own downloads go to `%LOCALAPPDATA%\HelixSR`. Delete these folders to remove everything. Remove HelixSR from your games first.

## Credits and licenses

- AMDLSS is free software under the GNU General Public License, version 2 or (at your option) any later version. See `LICENSE`. Parts are copied from the launcher and tools of the [Bloodborne PC port](https://github.com/0xCydral/bloodborne_pc) (GPL-2.0-or-later): the window's palette and button and progress-bar code, and the download, verify and extract code of `tools/fetch_helixsr.py`.
- [HelixSR](https://github.com/lonewolf0622/HelixSR) is closed-source freeware by lonewolf0622 under the [HelixSR Freeware License](https://github.com/lonewolf0622/HelixSR/blob/HEAD/LICENSE). Its setup scripts are Apache-2.0 up to 1.8.1 and under the same Freeware License from 1.9.0, which allows running the setup and redistributing it only unmodified, complete and free. AMDLSS downloads the unmodified official release.
- [OptiScaler](https://github.com/optiscaler/OptiScaler) (GPL-3.0) comes inside the HelixSR release and is installed by HelixSR's setup into games without an FSR 3.1 DLL.
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

The unit tests need no network and no HelixSR. One more test runs HelixSR's real installer against a fake game folder in `%TEMP%\amdlss-fake`. It is skipped unless `AMDLSS_TEST_HELIXSR` names a built HelixSR folder (`AMDLSS_TEST_ROOT` moves the fake folder elsewhere). It copies that folder first, runs both routes and the frame generation switch, and deletes everything it made.
