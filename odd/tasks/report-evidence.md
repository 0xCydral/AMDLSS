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

- [x] T1 receiver + client: report names of up to 7 parts (`receiver/validate.js`, `amdlss/reports.py`, tests
  on both sides agree, `MAX_SEGMENTS` compared across the seam)
- [x] T2 reader: session timeline per run in `tools/report_html.py` (Yes/No/Unknown for HelixSR ran, frame
  generation worked, game crashed; started, route and resolution, dispatch and toggle counts, VRAM, errors,
  how it ended), the same lines in `amdlss-logs.py fetch`, a prominent Windows crashes section that names the
  faulting module in plain words, and the Proton log note for Linux reports; every byte of report text stays
  escaped. One crash query is shared across a whole report, not one per game
- [x] T3 history: preserve a game's logs before install, update and remove (`HelixSR._act` calls the injected
  preserve hook, wired in `app.py`), prune to the caps, and carry the newest 5 snapshots in Collect logs and
  Send logs (a game with only history left still reports)
- [x] T4 crashes: `amdlss/crashes.py` collects `crashes.txt` per game (Windows event log behind the platform
  switch, failures never block collection), lists minidumps and WER folders without uploading them, and
  collects the Proton log on Linux; everything redacted
- [x] T5 checks (2026-10-13): Linux 304 passed and 3 skipped (baseline 279 and 3); Windows under wine 297
  passed and 10 skipped (baseline 272 and 10); receiver 23 pass; `--selftest` exit 0 with the new
  `check_history_and_crashes`; the real report TFTKA6A3 regenerated into `/tmp/TFTKA6A3-proof/` reads
  Assassin's Creed Shadows as frame generation working (2757 dispatch lines, 2214 delivered, 14 activates and
  16 deactivates, 12 VRAM warnings with a 7.34 GB peak, clean shutdown 14:16:39, the allocator error named)
  and Monster Hunter Stories 3 as 10512 HelixSR frames at 1920x1080 ending in a destroy line. Live
  `Get-WinEvent` on real Windows stays unexercised (wine has no event log; the query and its parsing are
  covered through the runner seam). The receiver change is committed, not deployed: deploy it before or with
  the client release that first sends history files

Route: delegated direct (multi-file feature).
- [x] T6 shipped 2026-10-13: receiver redeployed (version b06c5440) before the client; released v0.1.0-beta.11 (exe 65e79552, linux ec37799f); updater offers it on both hosts; real report TFTKA6A3 re-read through the new page answers FG Yes / crash No for AC Shadows and 10512 frames / crash No for Monster Hunter
- [x] T7 tester report GZ633MDH fixes (2026-10-13): Event 1001 is read from its replacement strings, not the
  localized message (`crashes._wer_fields`: event name [2], P1..P10 from [5]; APPCRASH P7 exception and P8 offset,
  BEX64 the other way round, AppHang* no module), so a Spanish Windows no longer yields `faulting module: P5:`;
  a 1001 within 10 s of a 1000 for the same exe is shown once; the reader says an old `P5:` line is "crash
  recorded by Windows Error Reporting; this AMDLSS version could not read the faulting module" and a hang
  "stopped responding". Drop-in route is detected from any HelixSR session, then from games.txt (latest run) or
  the install log (kept run), so Monster Hunter's latest run answers frame generation No like its earlier run;
  "Yes" cites the counts line. Linux 313 passed and 3 skipped; wine 306 passed and 10 skipped; `--selftest`
  exit 0. Not released.
