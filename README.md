# xport

Export the tweets of one or more X (Twitter) accounts between two dates to **one CSV per
account**, with a hard, code-enforced cap on how many posts the tool can access in any rolling
24 hours.

| Column | Meaning |
|---|---|
| `account` | account handle |
| `created_at` | tweet date and time, ISO 8601 in UTC |
| `content` | tweet text |
| `links` | external URLs embedded in the tweet (`expanded_url`) |
| `quoted_tweet_id` / `quoted_tweet_url` | link from a quote tweet to the tweet it quotes |

Quote tweets are **included**; retweets are **excluded**. Replies are kept when they belong to the
account's own threads, and replies inside other people's conversations are left out (the export
reports how many were excluded).

## Compliance Gate

X's terms penalize accessing more than 1,000,000 posts in any 24-hour window. xport makes that
impossible to cross by construction:

- **`hard_cap = 900_000`**, global for the whole app, below X's limit to leave a margin. It is a
  code constant: it cannot be raised from `.env`.
- **Rolling 24-hour window**, never a calendar day: `usage(now) = Σ count WHERE ts > now − 86400`.
- **Accesses are counted, not rows saved**: the quoted tweet embedded in a response and the
  retweets that get discarded count too. The ledger never deduplicates (over-counting is safe).
- **Fail closed**: active scraping reserves an upper bound *before* each request and waits if it
  does not fit; captures that already happened in the browser are recorded *after* the fact, and
  further captures are rejected with 429 once over the cap.
- **Durable and atomic**: the ledger lives in its own SQLite audit database, separate from the
  tweet data, and every read-decide-write cycle runs under a lock.

Every data path goes through the same gate, and the tests force both the block and the wait.

## How it works

```
 browser extension ──► local service (FastAPI) ──► mappers ──► SQLite ──► CSV per account
 (captures X's own          │  POST /ingest
  GraphQL responses)        └──► Compliance Gate ledger (separate SQLite)
```

The extension opens X's search for the account and date range, scrolls it by itself and captures
the GraphQL responses that x.com already loads in the page; the browser computes X's request
signatures natively, so nothing has to be reverse-engineered. The local service maps them to the
domain model, stores them deduplicated and records the accesses in the gate. **Exportar CSV**
then downloads the file.

- **Adaptive crawl**: X's live search stops at around 1,000 results per query, so when the scroll
  stalls before reaching the start date the extension narrows the range to the oldest day loaded
  and searches again, until the whole range is covered.
- **Picks up where it left off**: X also throttles the search endpoint for hours. On the next
  capture, the service reports how far the stored data already goes and the crawl continues from
  there instead of starting over.
- **Optional auto-start**: with the native messaging host (`deploy/native-host/`), opening the
  extension starts the local service and closing the browser stops it.

Other backends sit behind the same `TweetProvider` interface and the same gate:

- **twscrape** (server-side scraping, used by the CLI and `POST /jobs`): implemented and tested
  offline, but **blocked upstream** since x.com moved its web client to Vite in mid-2026 and broke
  the request-signature generator twscrape depends on. It fails closed with a clear error.
- **Official X API v2**: a stub that fails closed until there is an API key to verify it with.

## Status

Beta. The four planned phases (CLI, swappable backends, local service with nginx, browser
extension) are in the code; the tests run offline, and `mypy --strict` and ruff pass. I have used
the in-browser capture against live x.com, and that is where the adaptive crawl and the resume
came from. Still pending: a documented end-to-end check of the whole flow, the native messaging
bridge from Windows browsers to WSL, and the official API backend. The plan, the reasoning behind
each decision and the current status are in [docs/PLAN.md](docs/PLAN.md) (Spanish).

## Requirements

- [uv](https://docs.astral.sh/uv/) and Python 3.12 (pinned in `.python-version`).
- Docker, to run the service with nginx or to build the extension without Node on the host.
- Chrome or Firefox, logged in to X, ideally with an account you can afford to lose: X may limit
  accounts that scrape.

## Setup

```bash
git clone https://github.com/bafif/xport.git
cd xport
uv sync                     # creates .venv from uv.lock
cp .env.example .env        # only needed for the twscrape/official backends (see below)
```

## Run the local service

Directly (the extension's default address is `http://localhost:8000`):

```bash
uv run fastapi dev src/tweet_extractor/service/app.py
```

Or in Docker (Alpine, multi-stage) behind nginx on `http://localhost:8080`; the gate ledger and the
data live in two separate volumes:

```bash
docker compose -f deploy/docker-compose.yml up --build
```

With the compose stack, set the service address in the extension's popup to
`http://localhost:8080`.

## Build and load the extension

```bash
docker build -f deploy/extension.Dockerfile -o extension/dist extension
```

This type-checks and builds both targets inside a pinned Node image. Without Docker,
`cd extension && npm ci && npm run build` with Node 22 does the same.

- **Chrome**: `chrome://extensions` → Developer mode → *Load unpacked* → `extension/dist/chrome-mv3`.
- **Firefox**: `about:debugging` → *This Firefox* → *Load Temporary Add-on* →
  `extension/dist/firefox-mv2/manifest.json`.

Then open the popup (its UI is in Spanish), enter the account and the date range, press
**Capturar ▶** and, when it is done, **Exportar CSV**. **Detener ⏹** stops a capture. To start the
service from the browser automatically, see [deploy/native-host/README.md](deploy/native-host/README.md).

## CLI (twscrape backend)

```bash
uv run tweet-extractor --account someone --account other --since 2025-01-01 --until 2026-01-01
```

`--since` is inclusive and `--until` exclusive (UTC days). Other options: `--out-dir`,
`--subwindow-days`, `--encoding` (`utf-8-sig` adds a BOM for Excel) and `--delimiter` (`;` for
spreadsheets that use a comma as decimal separator). A re-run skips the sub-windows already
completed. It needs the session cookies in `.env` and is currently blocked upstream (see above).

## HTTP API

| Endpoint | What it does |
|---|---|
| `GET /healthz` | liveness |
| `GET /gate` | gate usage, remaining budget, cap and window |
| `POST /ingest` | store GraphQL pages captured by the extension and record their accesses |
| `POST /progress` | oldest stored tweet for an account and range (the resume point) |
| `POST /export`, `GET /exports/{file}` | build and download the CSV of a captured range |
| `POST /reset` | delete what was captured for an account (the gate ledger is never touched) |
| `POST /jobs`, `GET /jobs`, `GET /jobs/{id}`, `GET /jobs/{id}/csv/{account}` | server-side extraction jobs (twscrape backend) |

## Configuration (`.env`)

Secrets live only in `.env`, which is git-ignored. Every variable is listed with comments in
[`.env.example`](.env.example):

| Variable | Purpose | Default |
|---|---|---|
| `PROVIDER_BACKEND` | `twscrape` or `official` | `twscrape` |
| `X_AUTH_TOKEN`, `X_CT0` | session cookies of a separate X account (twscrape) | empty |
| `X_API_BEARER_TOKEN` | official API v2 token (stub) | empty |
| `AUDIT_DB_PATH` | gate ledger, separate from the data | `data/audit/ledger.db` |
| `DATA_DB_PATH`, `ACCOUNTS_DB_PATH` | tweets, twscrape accounts | `data/tweets.db`, `data/accounts.db` |
| `CSV_DIR`, `CAPTURES_DIR` | CSV output of jobs and of captures | `data/csv`, `data/captures` |
| `CORS_ALLOW_ORIGINS` | JSON list of allowed origins (the extension does not need it) | `[]` |
| `PAGE_SIZE`, `SUBWINDOW_DAYS` | backend page size, search sub-window in days | `20`, `7` |

The in-browser capture needs none of these secrets: it uses the session already open in the
browser.

## Development

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy src
uv run pytest
```

Layers, from the bottom up; each one only knows the one below:

```
providers/     swappable data source behind TweetProvider (twscrape, official API stub)
mappers/       raw GraphQL → domain model (the only place that parses GraphQL)
domain/        Tweet and TweetLink (pydantic v2)
compliance/    SlidingWindowGate + GatedProvider
storage/       SQLite store (dedupe, checkpoints) + streaming CSV exporter
service/       FastAPI: jobs, ingest, export, gate status
cli.py         Typer CLI
extension/     WXT extension for Chrome MV3 and Firefox
```

Design notes and the reasoning behind each decision (Spanish): [docs/PLAN.md](docs/PLAN.md).

## License

[MIT](LICENSE) © 2026 Bautista D. Fiori
