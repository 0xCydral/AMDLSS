# Send logs

Objective: a tester presses **Send logs** in AMDLSS and gets a short report code to paste in chat; the
developer pulls the report with one command. Nothing a tester sends can carry an executable.

Why: testers send zips by hand through Telegram (2026-10-13, Deivi), and a zip named like an AMDLSS log can
hold anything.

Scope: AMDLSS client (`amdlss/reports.py`, `amdlss/app.py` button), a Cloudflare Worker + R2 receiver
(`receiver/`), a developer fetch CLI (`tools/amdlss-logs.py`). The zip on the Desktop stays.

Constraints: text only, fixed file-name allowlist, 2 MiB per report, validated on the client, the Worker
and the fetch side; fetch needs a secret token; the button label is generic (no developer name).

## Tasks

- [x] T1 receiver Worker + tests (delegated, Muse): `node --test receiver/`, 20 pass
- [ ] T2 client `ReportClient` + Send logs button + tests (delegated, Muse)
- [ ] T3 fetch CLI with quarantine + tests (delegated, Muse)
- [ ] T4 deploy: user runs `wrangler login`, then deploy, set `REPORT_URL`, release beta.9

Route: delegated direct (3+ non-trivial files).
