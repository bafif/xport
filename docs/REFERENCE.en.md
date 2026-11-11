# Reference

[Español](REFERENCE.md) · [Back to the README](../README.en.md)

The command line, X's official API, the service's API and the configuration. To install and use xport, see
[Install](INSTALL.en.md) and [How to use it](USAGE.en.md).

## Starting the service

| Way | Address | What for |
|---|---|---|
| `xport` (installed) | `http://localhost:8000` | everyday use: starts the service and opens the page |
| `uv run fastapi dev src/tweet_extractor/service/app.py` | `http://localhost:8000` | development, from the repository |
| the native messaging host | `http://localhost:8000` | having the browser start it ([details](../deploy/native-host/README.en.md)) |
| `podman compose -f deploy/compose.yaml up --build` | `http://localhost:8080` | continuous integration and deployment, in Alpine containers behind nginx |

All but the compose share the data folder and the cap's ledger, wherever they are started from. The compose has its
own, in two separate Podman volumes, and needs a `.env` (`cp .env.example .env` is enough); to use it from the popup,
put `http://localhost:8080` in the service field. If an xport already answers on `localhost:8000` or
`localhost:8080`, the service does not start and says so: two services would keep two ledgers of the cap.

## The command line

```sh
uv run tweet-extractor capture --account nasa,esa --since 2025-01-01 --until 2026-01-01
uv run tweet-extractor extract --account <account> --since 2025-01-01 --until 2026-01-01
```

- **`capture`** queues captures for the extension, one per account. `--account` repeats or takes several separated by
  commas; `--excel` builds the CSV for an Excel set to Spanish. Progress and the CSVs are on the page.
- **`extract`** downloads the tweets from the server, without a browser: with the [official API](#with-the-official-api)
  or with the cookies of an X account, which does not work today (x.com changed how it signs requests) and fails with
  an error that says so. `--since` is included and `--until` is not (UTC days). Options: `--out-dir` (by default,
  `csv/` in the data folder), `--subwindow-days`, `--encoding` (`utf-8-sig` adds the BOM Excel expects) and
  `--delimiter` (`;` for an Excel that uses the comma as decimal separator). If it stops, running it again skips the
  stretches it already finished.
- **`--lang en`** or **`--lang es`** changes the language of the messages.

## With the official API

With an X app that has paid API access, set `PROVIDER_BACKEND=official` and the Bearer token in
`X_API_BEARER_TOKEN` in `.env`, and use `tweet-extractor extract` or `POST /jobs`.

- It uses full-archive search, which has no ceiling like the ~3,200 tweets of an account's timeline.
- It asks for no retweets and no expansions, so you do not pay for what gets dropped: the quoted tweet's id already
  comes in the response.
- It makes at most one request per second and, if X says the limit was hit, waits as long as X says.
- If your plan does not include full-archive search, X answers 403 and xport says so.

Every post read is billed (X listed 0.005 dollars per post and a cap of 3 million a month). **It has not been tried
against the real API:** if something fails, the error carries what X answered.

## The service's API

| Request | What it does |
|---|---|
| `GET /` | xport's page |
| `GET /i18n/{lang}` | the page's texts in `es` or `en` |
| `GET /healthz` | says whether the service is alive, that it is xport and its process id |
| `GET /gate` | cap usage in the last 24 hours, what is left, the cap and the window |
| `GET /gate/days` | what was seen per day over the last 30 days with activity (does not count towards the cap) |
| `POST /captures`, `GET /captures` | queues one capture per account; lists the latest, with their progress |
| `POST /captures/next` | the capture the extension has to do; 204 if there is none or the cap is full |
| `POST /captures/{id}/state` | what the extension reports (running, paused, done, stopped) or a cancellation |
| `GET /captures/{id}/csv` | the CSV of a finished capture |
| `GET /browser`, `POST /browser/login` | the state of xport's Chrome and its X session; opens the window to log in |
| `POST /ingest` | stores the pages the extension captured and records their accesses in the cap |
| `POST /progress` | the oldest stored tweet of an account in a range: where capture resumes |
| `POST /export`, `GET /exports/{file}` | builds and downloads the CSV of a captured range (optional `encoding` and `delimiter`) |
| `POST /reset` | deletes what was captured for an account (the cap's ledger is never touched) |
| `POST /jobs`, `GET /jobs`, `GET /jobs/{id}`, `GET /jobs/{id}/csv/{account}` | server-side downloads, without a browser |

The service only answers requests addressed to `localhost` or `127.0.0.1`. The ones that change something (queueing,
cancelling, deleting) have to come from the page itself, from the extension or from a program without a browser, like
the CLI: those carrying another site's `Origin` get 403.

## Configuration

It goes in a `.env`: the repository's when xport runs from the source, or the data folder's when it is installed.
[`.env.example`](../.env.example) lists the variables with comments. In-browser capture needs none of them. Relative
paths hang from the data folder.

| Variable | Purpose | Default |
|---|---|---|
| `XPORT_DATA_DIR` | the data folder (absolute path) | your system's, see [How to use it](USAGE.en.md#privacy) |
| `XPORT_BROWSER` | have the service capture with its own Chrome | `true` |
| `XPORT_BROWSER_BIN`, `XPORT_BROWSER_CAGE` | the browser and cage it uses | found by itself: in `PATH` and, the browser, in the macOS and Windows install folders |
| `XPORT_EXTENSION_DIR` | the built extension loaded into that Chrome | the package's, or `extension/dist/chrome-mv3` |
| `BROWSER_PROFILE_DIR` | that Chrome's profile, with the X session | `browser` |
| `XPORT_NOTIFY` | system notification when a capture finishes or stops | `true` |
| `XPORT_LANG` | language of the service and its notifications: `es` or `en` | the system's |
| `AUDIT_DB_PATH` | the cap's ledger, apart from the data | `audit/ledger.db` |
| `DATA_DB_PATH`, `QUEUE_DB_PATH` | the tweets; the capture queue | `tweets.db`, `queue.db` |
| `CSV_DIR`, `CAPTURES_DIR` | CSVs of server-side downloads and of captures | `csv`, `captures` |
| `PROVIDER_BACKEND` | where the server downloads from: `twscrape` or `official` | `twscrape` |
| `X_API_BEARER_TOKEN` | Bearer token for the official API (paid) | empty |
| `X_AUTH_TOKEN`, `X_CT0`, `ACCOUNTS_DB_PATH` | cookies and accounts for twscrape | empty, `accounts.db` |
| `PAGE_SIZE`, `SUBWINDOW_DAYS` | page size and days per search stretch (server) | `20`, `7` |
| `ALLOWED_HOSTS` | hosts the service answers to (against DNS rebinding) | `["localhost","127.0.0.1"]` |
| `CORS_ALLOW_ORIGINS` | JSON list of allowed origins (the extension does not need it) | `[]` |

The cap (900,000) and the window (24 hours) are deliberately not in this list: they are constants in the code and no
variable changes them. **Never commit `.env`:** it may hold session cookies.

## X's search

xport searches `from:<account> since_time:<from> until_time:<to>`, in UTC seconds: the start day is included in full
and the end day is not (`since:` and `until:` would take days in the searcher's time zone). Of the replies, the CSV
keeps those that hang from a conversation the same account started. If that first tweet falls outside the range and
was never captured, its replies inside the range are left out too.
