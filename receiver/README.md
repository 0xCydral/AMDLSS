# AMDLSS log receiver

A Cloudflare Worker with a Workers KV namespace. The AMDLSS window's **Send logs** button posts a report here and shows the
tester an 8-character code. You read the report with `tools/amdlss-logs.py`.

| Route | Who | What |
|---|---|---|
| `POST /reports` | anyone | Stores a validated report as `reports/<code>.json`, answers `{"code": "..."}` |
| `GET /reports` | holder of `READ_TOKEN` | `{"reports": [{"code", "created"}]}` newest first (`created` is null for a report stored before KV metadata) |
| `GET /reports/<code>` | holder of `READ_TOKEN` | Returns the stored report |

Everything else is 404 or 405.

## What a report may hold

The body is JSON: `{"app": "AMDLSS", "version": str, "game": str, "files": {name: text}}`, at most 8 MiB (the
`Content-Length` and the bytes actually read are both checked), at most 200 files.

- File names: 1 to 7 parts joined by `/` (a kept history file such as
  `Game/history/20261013-141500/exe-folder/HelixSR/helixsr.log` needs 6), each part made of `A-Z a-z 0-9 _ . -`, no `..`, no part starting or ending
  with `.`, no Windows device names (`con`, `nul`, `com1` ...), and the last part ends in `.log`, `.ini`, `.json` or
  `.txt`.
- File text: no NUL and no control character except tab, line feed and carriage return (DEL and the C1 range are
  refused as well, because a terminal obeys them), and valid Unicode.
- `version` and `game`: 1 to 120 printable characters.

The Worker stores the object it rebuilt from these values, never the raw body. The same rules are in
`amdlss/reports.py`, which the app and the fetch tool use. Change one place and you change the other.

## Deploy

Needs Node 20 or newer and a free Cloudflare account (Workers KV needs no card). From this folder:

```bash
npx wrangler login
npx wrangler kv namespace create REPORTS   # put the printed id into wrangler.toml
npx wrangler deploy
npx wrangler secret put READ_TOKEN         # paste a long random string; keep a copy for the fetch tool
```

`deploy` prints the Worker's URL (`https://amdlss-reports.<account>.workers.dev`). Put that URL into `REPORT_URL` in
`amdlss/reports.py` (no trailing slash) and release a build.

Each report is stored with a 30-day `expirationTtl`, so KV deletes it by itself, which is what the app tells the
tester. KV's free plan allows 1,000 writes a day. The put also stores KV metadata (`created` in Unix seconds, the
app version and the game count), which is all the list route reads.

## Rate limit

`wrangler.toml` binds `UPLOAD_LIMITER` (10 uploads a minute per IP and Cloudflare location, from the Workers Rate
Limiting binding). The limit is per location and eventually consistent, so it slows a flood but does not cap it
exactly. The Worker skips the check when the binding is missing.

## Read a report

```bash
export AMDLSS_LOGS_URL=https://amdlss-reports.<account>.workers.dev
export AMDLSS_LOGS_TOKEN=<the READ_TOKEN you set>
python tools/amdlss-logs.py sync        # every new report, plus index.html in ~/amdlss-reports
python tools/amdlss-logs.py fetch <code>
```

## Test

```bash
cd receiver && node --test
```

No dependencies. The tests run the Worker against an in-memory stand-in for KV.
