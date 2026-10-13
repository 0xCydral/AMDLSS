# Report evidence

Objective: an AMDLSS bug report answers the maintainer's questions on its own. The report page says, per game and
per run, whether HelixSR ran, whether frame generation worked, how the run ended and whether Windows recorded a
crash, with the evidence line for each answer. The logs of a run survive the install, update or remove that
replaces them, and Windows' own crash record is collected with the logs.

Why: on 2026-10-13 a tester (Deivi, report TFTKA6A3) sent everything AMDLSS collects for Assassin's Creed
Shadows (OptiScaler route, frame generation on) and Monster Hunter Stories 3, and the maintainer still had to
ask for more. The evidence was in the logs (OptiScaler loaded 14:14:17, 2757 FSRFG dispatch lines, 12 VRAM
budget warnings with a 7.34 GB peak, a clean shutdown at 14:16:39; Monster Hunter rendered 10512 frames and
closed with a destroy line) but the page showed filtered error lines only. Two kinds of evidence were missing
outright: Monster Hunter's earlier OptiScaler crash run, whose logs the later route switch deleted, and the
Windows Application log record (Event ID 1000) that names the faulting DLL.

Scope: the report reader (`tools/report_html.py`, `tools/amdlss-logs.py`), log collection and history
(`amdlss/logs.py`, new `amdlss/crashes.py`, the preserve hook in `amdlss/helixsr.py` and `amdlss/app.py`), the
Send logs payload (`amdlss/reports.py`) and the receiver name rules (`receiver/validate.js`). No new runtime
dependencies. The receiver is changed and tested here but not deployed from this work; it must be deployed
before or with the client release that first sends history files.

Decisions:

- Timeline: one run is the current logs of a game, plus one run per kept history snapshot. Times are the clock
  times the logs carry (`[14:14:17.315930]`); logs hold no date. Frame generation dispatches are the
  `FSRFG*::Dispatch` lines (2757 in TFTKA6A3, of which 2214 `DispatchCallback` deliveries and 543
  `skipping` lines); frame generation "worked" needs delivered dispatches, not only skipping ones. Activates
  and deactivates are reported as raw line counts (14 and 16 in TFTKA6A3; the log opens with two deactivates
  before the first activate). VRAM warnings are the `Exceeded VRAM budget` lines with the peak of the
  `Total VRAM used` lines.
- How a run ended: "clean" needs a marker (OptiScaler `IsShuttingDown = true`, or the last HelixSR session's
  `destroy after N frames`); a log that stops without one reads "log just stops (likely crash or kill)".
  "Did the game crash" answers Yes only on a matching Windows Event ID 1000/1001 record (dated to the run
  when the report date allows the match), No on a clean marker with no such record, and Unknown when the log
  just stops, because a stop alone cannot tell a crash from a kill.
- History: before an install, update or remove runs, the game's current logs are copied to
  `history/<game-key>/<YYYYMMDD-HHMMSS>/` in the AMDLSS data folder, user name replaced as in reports. Caps:
  2 MiB per file (the tail is kept), at most 10 snapshots and 32 MiB per game, oldest deleted first. Collect
  logs and Send logs carry the newest 5 snapshots per game. The copy never blocks the install.
- Report names grow from 5 to 7 parts so a history file fits
  (`<Game>/history/<stamp>/exe-folder/HelixSR/helixsr.log` is 6): `amdlss/reports.py` and
  `receiver/validate.js` change together, as always.
- Windows crashes: Event ID 1000 and 1001 from the Application log, last 14 days, matched to the game's own
  `.exe` files by name, read with one bounded `Get-WinEvent` FilterHashtable query (500 events, 30 s) through
  an injectable runner. The faulting module, its version, the exception code and the offset come from the
  event replacement strings, which do not depend on the Windows language. Minidumps and WER report folders
  are listed by name, size and time only; their bytes are never collected. On Linux the Proton log
  (`~/steam-<appid>.log`) is collected when present, and the page says how to enable it (`PROTON_LOG=1`).

## Tasks

- [ ] T1 receiver + client: report names of up to 7 parts (`receiver/validate.js`, `amdlss/reports.py`, tests
  on both sides agree)
- [ ] T2 reader: session timeline per run in `tools/report_html.py` (Yes/No/Unknown for HelixSR ran, frame
  generation worked, game crashed; started, route and resolution, dispatch and toggle counts, VRAM, errors,
  how it ended), the same lines in `amdlss-logs.py fetch`, a prominent Windows crashes section that names the
  faulting module in plain words, and the Proton log note for Linux reports; every byte of report text stays
  escaped
- [ ] T3 history: preserve a game's logs before install, update and remove, prune to the caps, and carry the
  newest 5 snapshots in Collect logs and Send logs (a game with only history left still reports)
- [ ] T4 crashes: `amdlss/crashes.py` collects `crashes.txt` per game (Windows event log behind the platform
  switch, failures never block collection), lists minidumps and WER folders without uploading them, and
  collects the Proton log on Linux; everything redacted
- [ ] T5 checks: Linux pytest, Windows pytest under wine, receiver `node --test`, `--selftest`, and the real
  report TFTKA6A3 regenerated into a temp folder showing frame generation working in Assassin's Creed Shadows
  and 10512 clean HelixSR frames in Monster Hunter Stories 3

Route: delegated direct (multi-file feature).
