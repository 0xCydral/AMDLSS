# Send logs

Objective: a tester presses **Send logs** in AMDLSS and gets a short report code to paste in chat; the
developer pulls the report with one command. Nothing a tester sends can carry an executable.

Why: testers send zips by hand through Telegram (2026-10-13, Deivi), and a zip named like an AMDLSS log can
hold anything.

Scope: AMDLSS client (`amdlss/reports.py`, `amdlss/app.py` button), a Cloudflare Worker + R2 receiver
(`receiver/`), a developer fetch CLI (`tools/amdlss-logs.py`). The zip on the Desktop stays.

Constraints: text only, fixed file-name allowlist, 2 MiB per report, validated on the client, the Worker
and the fetch side; fetch needs a secret token; the button label is generic (no developer name).

Scope change (2026-10-13, coordinator): one report carries ALL games that have anything to report (HelixSR or
OptiScaler installed, a helixsr/optiscaler/fakenvapi log, or an AMDLSS session log), not the selected game.
- Send logs is a window action (games bar, next to Rescan) plus the card's right-click item; both send all games.
- Payload: each game's files under a folder named by `logs.slug()` (made valid and unique ignoring case), one
  `system.txt` and a `games.txt` (name, folder, install mode, HelixSR version, frame generation) at the top.
- Limits, same in Worker, client and fetch CLI: names up to 5 parts, 200 files, 8 MiB. Over the budget every game gets
  an equal share (a small game keeps what it does not use) and its largest files are cut to their tails first.
- The confirm dialog lists the games with the file count of each; the fetch summary groups by game.
- Collect logs (the Desktop zip) stays per game and unchanged.

## Tasks

- [x] T1 receiver Worker + tests (delegated, Muse): `node --test receiver/`, 20 pass
- [x] T2 client `ReportClient` + Send logs button + tests (delegated, Muse): 234 pass, 3 skipped (was 204 + 3)
- [x] T3 fetch CLI with quarantine + tests (delegated, Muse): 254 pytest pass, 3 skipped
- [x] T4 deployed on Workers KV (R2 needs a card); live smoke test: good report accepted, `.exe` and `../` refused, no token 401; released v0.1.0-beta.9 (`0f0da78`)
- [x] T5 receiver list: `GET /reports` (Bearer token, newest first) from KV metadata (`created`, app version, game count) stored on put; `node --test receiver/`, 23 pass. Not deployed yet
- [x] T6 reader: `tools/amdlss-logs.py sync` downloads every new report and writes `~/amdlss-reports/index.html` plus one `report.html` per report (`tools/report_html.py` parses, everything escaped); the desktop entry syncs and opens the index, no code entry
- [ ] T7 AMDLSS: refuse Install/Update/Remove while the game runs, and say so in plain words when a file is locked (PermissionError)

Route: delegated direct (3+ non-trivial files).
