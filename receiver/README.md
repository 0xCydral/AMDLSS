# AMDLSS log receiver

A Cloudflare Worker with an R2 bucket. The AMDLSS window's **Send logs** button posts a report here and shows the
tester an 8-character code. You read the report with `tools/amdlss-logs.py`.

| Route | Who | What |
|---|---|---|
| `POST /reports` | anyone | Stores a validated report as `reports/<code>.json`, answers `{"code": "..."}` |
| `GET /reports/<code>` | holder of `READ_TOKEN` | Returns the stored report |

Everything else is 404 or 405.

## What a report may hold

The body is JSON: `{"app": "AMDLSS", "version": str, "game": str, "files": {name: text}}`, at most 2 MiB (the
`Content-Length` and the bytes actually read are both checked), at most 24 files.

- File names: 1 to 3 parts joined by `/`, each part made of `A-Z a-z 0-9 _ . -`, no `..`, no part starting or ending
  with `.`, no Windows device names (`con`, `nul`, `com1` ...), and the last part ends in `.log`, `.ini`, `.json` or
  `.txt`.
- File text: no NUL and no control character except tab, line feed and carriage return (DEL and the C1 range are
  refused as well, because a terminal obeys them), and valid Unicode.
- `version` and `game`: 1 to 120 printable characters.

The Worker stores the object it rebuilt from these values, never the raw body. The same rules are in
`amdlss/reports.py`, which the app and the fetch tool use. Change one place and you change the other.

## Deploy

Needs Node 20 or newer and a Cloudflare account with R2 enabled. From this folder:

```bash
npx wrangler login
npx wrangler r2 bucket create amdlss-reports
npx wrangler secret put READ_TOKEN        # paste a long random string; keep a copy for the fetch tool
npx wrangler deploy
```

`deploy` prints the Worker's URL (`https://amdlss-reports.<account>.workers.dev`). Put that URL into `REPORT_URL` in
`amdlss/reports.py` (no trailing slash) and release a build.

Delete reports after 30 days, which is what the app tells the tester:

```bash
npx wrangler r2 bucket lifecycle add amdlss-reports expire-30-days reports/ --expire-days 30 --force
npx wrangler r2 bucket lifecycle list amdlss-reports      # check the rule is there
```

## Rate limit

`wrangler.toml` binds `UPLOAD_LIMITER` (10 uploads a minute per IP and Cloudflare location, from the Workers Rate
Limiting binding). The limit is per location and eventually consistent, so it slows a flood but does not cap it
exactly. The Worker skips the check when the binding is missing.

## Read a report

```bash
export AMDLSS_LOGS_URL=https://amdlss-reports.<account>.workers.dev
export AMDLSS_LOGS_TOKEN=<the READ_TOKEN you set>
python tools/amdlss-logs.py fetch <code>
```

## Test

```bash
node --test receiver/
```

No dependencies. The tests run the Worker against an in-memory stand-in for R2.
