# AMDLSS

AMDLSS is a small Windows program for AMD graphics cards. It installs [HelixSR](https://github.com/lonewolf0622/HelixSR) into your Direct3D 12 games with one click, so a game that offers DLSS (or FSR 3.1) can use HelixSR's upscaler on your card.

HelixSR runs NVIDIA's DLSS network as Direct3D 12 compute shaders. Its own setup is a console program. AMDLSS is a window around that setup: install HelixSR once, see your games, press Install or Remove.

AMDLSS is unofficial. It is not affiliated with HelixSR, NVIDIA or AMD.

## How to use it

1. Download `AMDLSS.exe` from the [Releases](https://github.com/0xCydral/AMDLSS/releases) page and run it. Windows SmartScreen may warn about an unknown publisher, because the exe is not code-signed. You can also run it from source: `python AMDLSS.pyw` (Python 3 with Tk; tested with 3.14).
2. Press **Install HelixSR**. AMDLSS asks you first, because HelixSR's setup downloads NVIDIA's DLSS 310.7.0 DLL under NVIDIA's license. If you agree, AMDLSS:
   - downloads HelixSR 1.8.1 from its own release page and checks the SHA-256,
   - runs HelixSR's own `helixsr-setup.ps1 -Yes`,
   - shows the setup's output in the log.

   The setup downloads Python, numpy and the shader compiler once (about 150 MB), builds the network (about a minute) and deletes NVIDIA's DLL. The status line then shows the HelixSR version.
3. Your games appear in the list: name, what the game supports, and its state (not installed, FSR drop-in or OptiScaler). HelixSR finds games from Steam, Epic, GOG and Xbox. For any other game press **Add game folder...**.
   The list only offers games AMDLSS can do something with: the game's folder and its .exe folder exist, and the game has an FSR 3.1 DLL or DLSS, XeSS or FSR 2 files (or HelixSR is already installed in it, so Remove stays reachable). The line under the list says how many games are ready and how many are hidden, for example "10 games ready, 26 hidden". Tick **Show all games** to see the hidden ones greyed out with the reason ("not installed" for a Steam leftover, "no DLSS, FSR 3.1 or XeSS found"); they have no Install button. A folder you added by hand that fails the check always stays in the list with its reason; **Forget folder** removes it. HelixSR looks around such a folder (up to four parent folders), which can take a while on a large drive, so AMDLSS does not ask again until you press **Rescan**.
4. Pick a game and press **Install**. AMDLSS shows what will change and asks you to confirm. When it finishes it tells you what to choose in the game. A game that has both an FSR 3.1 DLL and DLSS, XeSS or FSR 2 files works either way, so before you install you pick **Install as: DLSS (OptiScaler)** or **AMD FSR (drop-in)**. DLSS (OptiScaler) is offered first when the game lists DLSS, so the game's DLSS option is usable; otherwise HelixSR's own default is. This is the same choice as the `o` and `f` answers in HelixSR's own setup. Use the FSR drop-in if OptiScaler gives you trouble in that game.
5. **Remove** runs HelixSR's own uninstall for that game and puts the original files back.

## What to choose in the game

HelixSR picks one of two ways for each game. AMDLSS shows the result after the install.

| State in the list | In the game's graphics settings |
|---|---|
| FSR drop-in (the game has an FSR 3.1 DLL) | choose AMD FSR |
| OptiScaler (picked for a game that has an FSR 3.1 DLL, or the game has DLSS, XeSS or FSR 2 only) | choose DLSS. Press Insert in the game to open OptiScaler's menu |

### Frame generation (OptiScaler route only)

Some games, such as Dragon's Dogma 2, offer only NVIDIA's DLSS Frame Generation, which cannot run on AMD. For a game installed through the OptiScaler route, the box **Frame generation: use the game's DLSS Frame Generation option (runs AMD FSR frame generation)** is off by default. When you turn it on and press Install (or Update), AMDLSS sets two values in the `OptiScaler.ini` that HelixSR placed next to the game's exe: `FGInput=dlssg` and `FGOutput=fsrfg` in `[FrameGen]`. Every other byte of the file stays as it was. In the game, turn on DLSS Frame Generation; it runs AMD's frame generation. Keep NVIDIA Reflex off.

- It only works in games that use NVIDIA Streamline for DLSS frame generation. That is OptiScaler's limit, not AMDLSS's.
- It is untested on real games so far.
- Update with the box off puts the two values back as they were (AMDLSS keeps them in `%LOCALAPPDATA%\AMDLSS\framegen.json`). **Remove** runs HelixSR's own uninstall, which restores or deletes `OptiScaler.ini`, so nothing stays behind.

HelixSR also has an experimental route that loads its DLSS loader. AMDLSS does not offer it. A game that already uses it (from HelixSR's own setup) shows as "DLSS loader (experimental)" and can only be removed.

## Warnings

- Do not use this in online games or games with anti-cheat. The account can be banned, or the game can refuse to start. AMDLSS looks for anti-cheat files in each game folder and flags the game, but it cannot know every game. Check it yourself.
- Close the game before you install or remove.
- HelixSR is freeware that you did not get from NVIDIA or AMD. Read its [README](https://github.com/lonewolf0622/HelixSR) first.
- The network HelixSR builds is NVIDIA's property. Do not share the files.

## What AMDLSS does and does not do

- It runs HelixSR's own scripts without changing them. Listing games, installing and removing use HelixSR's `helixsr_install.py` with HelixSR's own embedded Python (`install N`, `uninstall N`, `--folder PATH`). A small read-only helper, run in that same Python, reads HelixSR's game scan and returns it as JSON. If the helper fails, AMDLSS parses the installer's printed `list` output instead. `install N` always takes HelixSR's default route, so for the OptiScaler route in a game that also has an FSR 3.1 DLL, a second small helper in the same Python does what the installer's `o` answer does (it sets `route` and `chosen`) and then calls the installer's own `install()`. HelixSR's files are only imported.
- Before each install or remove, AMDLSS reads the list again and matches the game by folder, so a change in your library cannot send the action to another game.
- It does not bundle, modify or redistribute HelixSR or any NVIDIA file. This repository holds only AMDLSS's own code.
- Files: HelixSR is extracted to `%LOCALAPPDATA%\AMDLSS\HelixSR-1.8.1`, and the games you added by hand are saved in `%LOCALAPPDATA%\AMDLSS\settings.json`. HelixSR's own downloads go to `%LOCALAPPDATA%\HelixSR`. Delete these folders to remove everything. Remove HelixSR from your games first.

## Credits and licenses

- AMDLSS is free software under the GNU General Public License, version 2 or (at your option) any later version. See `LICENSE`. Parts are copied from the launcher and tools of the [Bloodborne PC port](https://github.com/0xCydral/bloodborne_pc) (GPL-2.0-or-later): the window's palette and button and progress-bar code, and the download, verify and extract code of `tools/fetch_helixsr.py`.
- [HelixSR](https://github.com/lonewolf0622/HelixSR) is closed-source freeware by lonewolf0622 under the [HelixSR Freeware License](https://github.com/lonewolf0622/HelixSR/blob/HEAD/LICENSE). Its setup scripts are Apache-2.0. AMDLSS downloads the unmodified official release.
- [OptiScaler](https://github.com/optiscaler/OptiScaler) (GPL-3.0) comes inside the HelixSR release and is installed by HelixSR's setup into games without an FSR 3.1 DLL.
- NVIDIA's DLSS 310.7.0 DLL is downloaded by HelixSR's setup under [NVIDIA's license](https://github.com/NVIDIA/DLSS/blob/v310.7.0/LICENSE.txt).
- Ideas, not code, from similar tools: per-game state lines and a library view (OptiScaler Client, GPL-3.0; DLSS5-Autopilot, MIT), file-name based anti-cheat detection with whole-word matching (DLSS5-Autopilot; the marker list here is our own), a copy-log button and an "Open folder" action (DLSS5-Swapper, MIT).

## Build

AMDLSS needs only Python's standard library and Tk to run. To build `dist/AMDLSS.exe` you need a Windows Python 3 from python.org (the build was tested with 3.14) and, in Git Bash:

```
bash packaging/build.sh
```

The script makes a private virtual environment in `out/pyenv`, installs PyInstaller there, and freezes `AMDLSS.pyw` into one windowed exe (about 12 MB). It prints the SHA-256 of the exe. `packaging/make_icon.py` redraws `amdlss.ico` (needs Pillow).

## Tests

```
python AMDLSS.pyw --selftest      # logic checks, no window, exits 0
python -m pytest -q               # unit tests
```

The unit tests need no network and no HelixSR. One more test runs HelixSR's real installer against a fake game folder in `%TEMP%\amdlss-fake`. It is skipped unless `AMDLSS_TEST_HELIXSR` names a built HelixSR folder (`AMDLSS_TEST_ROOT` moves the fake folder elsewhere). It copies that folder first, runs both routes and the frame generation switch, and deletes everything it made.
