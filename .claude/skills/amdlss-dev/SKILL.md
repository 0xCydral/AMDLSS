---
name: amdlss-dev
description: Developer guide for AMDLSS, the one-click HelixSR installer (Tk app for Windows and Linux). Use when working on AMDLSS code, pulling a tester's logs from a "AMDLSS report <code>" message, reading a tester's log zip, diagnosing a game report (drop-in vs OptiScaler, frame generation, crashes), running tests, building the exe and Linux binary, publishing a beta release, or changing the log receiver Worker.
---

# AMDLSS for developers

AMDLSS is a small Tk window that installs HelixSR into games in one click. It does no upscaling itself: it runs
HelixSR's own installer (`amdlss/helixsr.py`) and shows the result. Repo: `~/Projects/AMDLSS`, GitHub
`0xCydral/AMDLSS`, releases are prereleases tagged `v0.1.0-beta.N`.

## Where things are

| Path | What |
|---|---|
| `AMDLSS.pyw` | Entry point. `--selftest` runs the logic checks without a window |
| `amdlss/app.py` | The window: game list, Install/Remove, Collect logs, Send logs |
| `amdlss/helixsr.py` | Downloads, builds and runs HelixSR's installer; the two install routes |
| `amdlss/games.py`, `identify.py` | Finding games and what upscaler each one uses |
| `amdlss/framegen.py` | The OptiScaler frame generation switch (`OptiScaler.ini`) |
| `amdlss/logs.py` | What a game's logs are; the Desktop zip (Collect logs); kept log history |
| `amdlss/crashes.py` | Windows crash records (`crashes.txt`), crash dump listings, the Proton log |
| `amdlss/reports.py` | Send logs: payload rules, trimming, `ReportClient` |
| `amdlss/update.py` | Self-update from GitHub releases |
| `receiver/` | Cloudflare Worker that stores sent reports (Workers KV) |
| `tools/amdlss-logs.py`, `tools/report_html.py` | Pull sent reports onto your disk (`sync`/`fetch`) and render them readable |
| `packaging/release.sh` | Builds and checks both release files |
| `odd/tasks/` | Feature task files |

The two install routes matter in every bug report:

- **Drop-in**: HelixSR replaces the game's FSR DLL (`amd_fidelityfx_dx12.dll` or
  `amd_fidelityfx_upscaler_dx12.dll`; the game's own file is kept as `*.original.dll`). The tester picks AMD FSR in
  the game. Works in AION2 (anti-cheat, at the tester's own risk).
- **OptiScaler**: OptiScaler goes in as `dxgi.dll` with fakenvapi, and the tester picks DLSS. Frame generation
  exists only on this route and is FSR frame generation (OptiScaler's `FGOutput = FSRFG`); there is no DLSS frame
  generation. Never in games with anti-cheat.

Install, Update and Remove first check that no process runs from inside the game's folder (Windows: PowerShell
process paths; Linux: `/proc`, Proton included; `helixsr.running_from`) and refuse in plain words when one does.
Before the change itself runs, the game's current logs are copied to `history/<game>/<stamp>/` in the AMDLSS
data folder (`HelixSR.preserve`, wired in `app.py` to `logs.preserve_history`), so replacing a route cannot
delete the evidence of the run before it.
A failed run whose output names a locked file (`PermissionError`, `WinError 5`, `Errno 13`, access denied in
English or Spanish) reads the same way in the window; the raw output stays in the game's `amdlss-install.log`.

## Getting a tester's logs

Testers press **Send logs**. One report holds every game that has HelixSR or OptiScaler installed or any log, and
they paste `AMDLSS report <CODE>` in the chat.

On the owner's PC, open **AMDLSS Logs** from the app menu (`tools/amdlss-logs-gui.sh`): it syncs (downloads every
report not on disk yet) and opens `~/amdlss-reports/index.html`, newest first with a red/amber/green badge per
game. Each report links to its `report.html`: the system as a table, one card per game with a plain-words status,
the three answers for the latest run (did HelixSR run, did frame generation work, did the game crash: Yes, No or
Unknown, each with its evidence line), a session timeline per run (kept history runs included: start, route and
resolution, frame generation dispatch and activate counts, VRAM warnings and peak, how the run ended), the
Windows crashes with the faulting DLL named in plain words, what AMDLSS did, the real problems (OptiScaler `[E]`
lines, HelixSR errors, traceback ends; .ini files, comments and known noise left out), HelixSR's evidence, and
the raw files collapsed below. `fetch` prints the same timeline in plain text. No code to paste. The read token
is asked once and kept in `~/.config/amdlss-logs/token` (0600); never read that file. `sync` reads the token from
`AMDLSS_LOGS_TOKEN` or that file, and the address from `AMDLSS_LOGS_URL` or the live receiver. From a shell:

```bash
cd ~/Projects/AMDLSS
python -I tools/amdlss-logs.py sync
AMDLSS_LOGS_TOKEN='<READ_TOKEN>' python -I tools/amdlss-logs.py fetch <CODE>   # one report; writes its report.html too
```

- The token is the `READ_TOKEN` secret set on the Worker. It lives with the owner, not in this repo or this file.
  Never print it, commit it or pass it through a chat.
- Files land in `~/amdlss-reports/<CODE>/` (`--out` moves it), mode 0600, never overwritten, never executable.
  `fetch` prints each game's files and its first error/crash/fail lines (same noise filter as the pages);
  `sync` prints how many were new and rebuilds `index.html` over every downloaded report.
- Reports expire after 30 days. A report sent a minute ago can take up to 60 s to be readable (KV propagation).
- The code is 8 Crockford base32 characters (no I, L, O, U); anything else is refused before any request.

Report layout:

```
system.txt                 OS, GPU and driver, CPU, RAM, AMDLSS and HelixSR versions
games.txt                  one line per game: folder, install mode, HelixSR version, frame generation on/off
<Game>/files.txt           every .dll/.exe in the game's folders: size, date, version, HelixSR's or not
<Game>/crashes.txt         Windows Event 1000/1001 for the game's exes (faulting DLL, exception, offset);
                           crash dumps and WER folders by name only; on Linux the Proton log note
<Game>/proton.log          Linux only, when ~/steam-<appid>.log exists (PROTON_LOG=1)
<Game>/amdlss-install.log  what AMDLSS printed when it installed
<Game>/exe-folder/...      helixsr.log, OptiScaler.log/.ini, fakenvapi.log/.ini
<Game>/history/<stamp>/..  the same logs as kept right before an earlier install, update or remove
                           (newest 5 runs sent; 10 runs and 32 MiB per game kept on the tester's PC)
```

A large log is cut to its tail with a note on top; each game gets a fair share of the 8 MiB.

### Old-style zips

Testers on beta.8 or older, or offline, send `AMDLSS-logs-<Game>-<date>.zip` from **Collect logs**. Treat it as
untrusted: unzip into a new empty folder, list it first, and expect only `.log`, `.ini`, `.json`, `.txt`:

```bash
D=$(mktemp -d) && unzip -l "<zip>" && unzip -q "<zip>" -d "$D" && file "$D"/* "$D"/*/* "$D"/*/*/*
```

Anything that is not text is not an AMDLSS log: stop and say so.

## Reading the logs

| Look at | For |
|---|---|
| `helixsr.log` `create:` and `summary:` lines | Render and output size, game flags (HDR, inverted depth), which DLL HelixSR loaded as |
| `helixsr.log` `dispatch N` every 600 frames, then `destroy after N frames` | The upscaler ran and closed cleanly. No `destroy` line: the game died or was killed |
| `helixsr.log` with NUL bytes (`file` says `data`) | Windows cut the file mid-write, usually a crash or a kill |
| `OptiScaler.log` lines with `[E]` | OptiScaler errors. `rg '\[E\]' OptiScaler.log \| cut -c1-200 \| sort \| uniq -c \| sort -rn` |
| `OptiScaler.log` `nvngx_update.exe` errors from Streamline | Noise on AMD, ignore |
| `OptiScaler.log` `FFX FG Callback ... SwapChainVersionDX12` then `allocator->Reset() error: 80004005` | FSR frame generation broke after the game rebuilt its swapchain (settings change, fullscreen toggle, Alt+Tab). Unconfirmed lead, 2026-10-13 |
| `files.txt` with an OptiScaler `dxgi.dll` while `games.txt` says `install: none` | Someone else's OptiScaler install in that folder; `report.html` names it as not AMDLSS's, with its version |
| `files.txt` | Which DLL versions are really there, and whether each upscaler DLL is HelixSR's |
| `fakenvapi.log` `GetLatency: Not enough data` | Normal |
| `OptiScaler.log` `FSRFG_Dx12::DispatchCallback` lines, `::Activate`/`::Deactivate`, `Exceeded VRAM budget`, `MenuCommon::WndProc IsShuttingDown = true` | What the timeline counts: delivered frame generation frames (plain `::Dispatch` lines are skipping or errors), toggles, VRAM pressure, and the clean-shutdown marker. No marker and no HelixSR `destroy`: the log just stops |
| `crashes.txt` | Which DLL Windows blames. A `dxgi.dll` in the game folder is OptiScaler; `kernelbase.dll`/`ntdll.dll` usually carry another DLL's fault. A log that just stops with no Event 1000 stays Unknown: crash and kill look alike |

Before blaming HelixSR for a performance drop, ask whether another game was running: on 2026-10-13 a "75 to 55 FPS"
report turned out to be AC Shadows still open in the background.

## Tests

```bash
uv run --no-project --managed-python --python 3.12 --with pytest --with pillow python -m pytest -q
node --test receiver/
python AMDLSS.pyw --selftest
```

All must pass before a release (304 Python tests and 3 skipped on Linux, 297 and 10 skipped on Windows under
wine, and 23 receiver tests as of the report evidence work). Tests
that need the unconfigured log path set `reports.REPORT_URL = ''` themselves; the shipped value is the live
address.

## Building and releasing

Developer machine only: it needs `amdlss-wine` (Wine with a Windows Python 3.12 at `C:\Python312`), `uv` and Xvfb.

1. Bump `VERSION` in `amdlss/__init__.py`, run the tests, commit `chore(release): 0.1.0-beta.N`.
2. Build both files and their checks (about 5 minutes; run it in the background):
   ```bash
   mkdir -p /tmp/amdlss-rel && bash packaging/release.sh > /tmp/amdlss-rel/build.log 2>&1
   tail -14 /tmp/amdlss-rel/build.log
   ```
   Ship only when both `VERSION` lines show the new version, both `modules True`, and both `selftest exit=0`.
   `modules` checks that PyInstaller bundled `PIL._tkinter_finder`: without it the covers silently stop drawing (beta.8 fix).
3. Write the notes to `/tmp/amdlss-rel/notesN.md`, then:
   ```bash
   git push origin main && git tag -a v0.1.0-beta.N -m "AMDLSS 0.1.0-beta.N" && git push origin v0.1.0-beta.N
   gh release create v0.1.0-beta.N -R 0xCydral/AMDLSS --prerelease --title "AMDLSS 0.1.0-beta.N" \
       --notes-file /tmp/amdlss-rel/notesN.md /tmp/amdlss-rel/dist/AMDLSS.exe /tmp/amdlss-rel/dist/AMDLSS-linux-x86_64
   ```
4. Check GitHub's asset digests match the `sha256sum` lines in the build log, and that the updater offers it:
   `update.fetch_release(True, windows=True/False)` must return the new version. GitHub can lag about 20 s.

Windows Defender sometimes flags a fresh unsigned exe; testers have to allow it.

## The log receiver

`receiver/worker.js` on Cloudflare Workers, storage in Workers KV (R2 needed a card on the account). Rules are in
`receiver/validate.js` and mirrored in `amdlss/reports.py`; a pytest test runs both against the same cases, so change
both together. Limits: 8 MiB body, 200 files, names of up to 7 parts ending in `.log/.ini/.json/.txt` (a kept
history file needs 6), text without
control characters, 10 uploads per minute per IP. Only the re-validated object is stored, never the raw body.
Uploads also store KV metadata (arrival time, app version, game count); `GET /reports` answers the codes and
arrival times, newest first, to the read-token holder only. That list is what `sync` is built on.

```bash
cd receiver
npx wrangler deploy                 # after changing worker.js or validate.js
npx wrangler secret put READ_TOKEN  # rotating the token; the owner types it
npx wrangler tail                   # live request log
```

`receiver/.wrangler/` is the CLI's cache with account details: gitignored, never commit it.
