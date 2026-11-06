# xport: X accounts' tweets to CSV

[Español](README.md)

Downloads the tweets of one or more X accounts between two dates and writes one CSV per account. You use it from a
Chrome or Firefox extension that hands what it captures to a service running on your computer.

**It cannot cross the limit in X's terms.** X's terms fine anyone who accesses more than a million posts in any 24-hour
period, so xport counts every post the browser sees, including quoted tweets and the retweets it later drops, and stops
at 900,000 within any 24-hour window. If the service cannot count what is being captured, the capture does not start,
or it stops.

![How it works: the extension copies what x.com already downloaded and hands it to the local service, which stores it
without repeats and writes one CSV per account. The 24-hour cap counts every post seen and, if it cannot count it, the
capture stops.](docs/img/flujo.en.svg)

## Status

In-browser capture works against x.com: it opens the account's search, scrolls it by itself, carries on when X cuts it
short and picks up where it left off. The CSV has these columns:

| Column | Contents |
|---|---|
| `account` | the account handle, in lowercase |
| `created_at` | date and time of the tweet, ISO 8601 in UTC |
| `content` | the full text, long tweets included |
| `links` | the tweet's external URLs (`expanded_url`), separated by spaces |
| `quoted_tweet_id`, `quoted_tweet_url` | the quoted tweet, if any |

Quote tweets and the account's own threads are in. Retweets and the account's replies inside other people's
conversations are left out; the export says how many replies it left out. If a tweet was edited and you capture it
again, the latest version is kept.

Without a browser there are two ways. With a regular X account it has been blocked since June 2026: x.com changed how
it signs requests and twscrape can no longer do it. With the [official X API](#with-the-official-api), which is paid,
it is implemented but **has not been tried against the real API**: it was built from X's documentation and made-up
responses. The capture cut-offs added on 6 November 2026 have not been tried in a browser yet.

## Everyday use: the popup

Once it is installed (see [Install](#install)), everything happens in the extension's popup:

<img align="right" width="260" src="docs/img/popup.png" alt="The xport popup, in Spanish, with an example account, the
date range, the cap usage and the link to the exported CSV.">

1. **Open the popup:** click the xport icon. At the top it shows how many accesses the cap has counted in the last 24
   hours and how many are left.
2. **Fill it in:** the account without `@`, and «Desde» (from) and «Hasta» (to) as `YYYY-MM-DD`. «Desde» is included;
   «Hasta» is not.
3. **Capture:** «Capturar ▶» opens X's search in a new tab and scrolls it by itself. You can close the popup. If X stops
   returning results before the start date, the extension narrows the range and searches again until the whole range is
   covered. «Detener ⏹» stops it.
4. **Export:** open the popup again and press «Exportar CSV». A link to the file appears, with how many tweets it has.
5. **Carry on another day:** if X throttled the search, come back later and press «Capturar ▶» with the same dates. It
   continues from the oldest tweet you already have, without asking again for what it downloaded.

**It is private.** The extension uses the X session already open in your browser: your cookies never leave it. What it
captures goes to the local service, which only listens on your computer, and stays in the repository's `data/` folder
(or in the Podman volumes, if you started it in containers). It is not sent anywhere else. «Borrar capturado 🗑» deletes
what was captured for one account; the cap's ledger is never deleted. Capture with an X account you can afford to lose:
X may limit accounts that download many tweets.

For now it only captures through X's search, which returns at most about a thousand results per search. If a single
day has more than 800 tweets, the extension keeps what X gave it for that day and moves on to earlier days: the rest of
that day is not included.

If the popup says it will not start because the service does not answer, start the service (see [Usage](#usage)) or
install the automatic start (see [Install](#install)). If it says the cap is full, wait: the popup shows how much is
left.

## Install

You need [uv](https://docs.astral.sh/uv/), which brings the right Python (3.12, pinned in `.python-version`), and
rootless [Podman](https://podman.io) to build the extension without installing Node. With Node 22 you can also build it
without Podman.

1. **Get the code:** on GitHub, «Code» → «Download ZIP», or `git clone` this repository.
2. **Set up the service:** in the repository folder, `uv sync`.
3. **Build the extension:** `bash deploy/extension-build.sh`. It ends up in `extension/dist/`.
4. **Load it in your browser:** see below, for yours.
5. **Automatic start (optional):** with the native messaging host, the browser starts the service when it opens and
   stops it when it closes. Install it once with `bash deploy/native-host/install-linux.sh` (Firefox) or
   `bash deploy/native-host/install-linux.sh --chrome --ext-id=<id>` (Chrome). For a Windows browser with the service in
   WSL, and to diagnose problems, see [deploy/native-host/README.md](deploy/native-host/README.md).

### In Chrome

1. Open `chrome://extensions` and turn on «Developer mode», at the top right.
2. Click «Load unpacked» and choose the `extension/dist/chrome-mv3` folder.
3. Note the 32-letter id shown on the xport card: it is the `<id>` for the automatic start.

### In Firefox

1. Open `about:debugging` and choose «This Firefox».
2. Click «Load Temporary Add-on…» and choose the `extension/dist/firefox-mv2/manifest.json` file.

Firefox removes temporary add-ons when it closes: you have to load it again every time you open it.

## Usage

Start the service in one of these ways. Pick one and always use the same one: each has its own cap ledger, and if you
use both, the cap is no longer a single one.

```sh
uv sync
uv run fastapi dev src/tweet_extractor/service/app.py
cp .env.example .env && podman compose -f deploy/compose.yaml up --build
uv run tweet-extractor --account <account> --since 2025-01-01 --until 2026-01-01
```

- `fastapi dev` starts the service on `http://localhost:8000`, the address the extension uses unless you tell it
  otherwise. It keeps everything in `data/`. It is what the automatic start runs too.
- `podman compose` starts it in Alpine containers, behind nginx, on `http://localhost:8080`, which only listens on your
  computer. In the popup, put that address in the service field. It needs the `.env`, even if it is the empty copy of
  `.env.example`. The data and the cap's ledger live in two separate volumes. After a reboot it comes back by itself
  only if `podman-restart.service` is enabled (`systemctl --user enable podman-restart.service`).
- `tweet-extractor` downloads tweets from the server, without a browser: with the official API (see below) or with the
  cookies of an X account, which has been blocked since June 2026 and fails with an error that says so. `--since` is
  included and `--until` is not (UTC days). Also: `--out-dir`, `--subwindow-days`, `--encoding` (`utf-8-sig` adds the
  BOM Excel expects) and `--delimiter` (`;` for an Excel set to a locale that uses the comma as decimal separator). If
  it stops, running it again skips the stretches it already finished.

### With the official API

If you have an X app with paid API access, set `PROVIDER_BACKEND=official` and the Bearer token in
`X_API_BEARER_TOKEN` in `.env`, and use `tweet-extractor` or `POST /jobs`. It uses full-archive search, which has no
ceiling like the ~3,200 tweets of an account's timeline, and asks for no retweets and no expansions, so you do not pay
for what gets dropped. It makes at most one request per second and, if X says the limit was hit, waits as long as X
says.

Every tweet read is billed: in November 2026, X listed 0.005 dollars per post (5 dollars per thousand) and a cap of
3 million a month. If your plan does not include full-archive search, X answers 403 and xport says so. **It has not
been tried against the real API**: if something fails, the error carries what X answered.

The service answers these requests:

| Request | What it does |
|---|---|
| `GET /healthz` | says whether the service is alive |
| `GET /gate` | cap usage in the last 24 hours, what is left, the cap and the window |
| `POST /ingest` | stores the pages the extension captured and records their accesses |
| `POST /progress` | the oldest stored tweet of an account in a range: where capture resumes |
| `POST /export`, `GET /exports/{file}` | builds and downloads the CSV of a captured range |
| `POST /reset` | deletes what was captured for an account (the cap's ledger is never touched) |
| `POST /jobs`, `GET /jobs`, `GET /jobs/{id}`, `GET /jobs/{id}/csv/{account}` | server-side downloads (blocked) |

Configuration lives in `.env`, which git ignores; [`.env.example`](.env.example) lists every variable with comments.
In-browser capture needs none of them: it uses the browser's session.

| Variable | Purpose | Default |
|---|---|---|
| `PROVIDER_BACKEND` | where the server downloads from: `twscrape` or `official` | `twscrape` |
| `X_AUTH_TOKEN`, `X_CT0` | cookies of a separate X account (twscrape) | empty |
| `X_API_BEARER_TOKEN` | Bearer token for the official API (paid) | empty |
| `AUDIT_DB_PATH` | the cap's ledger, separate from the data | `data/audit/ledger.db` |
| `DATA_DB_PATH`, `ACCOUNTS_DB_PATH` | tweets; twscrape accounts | `data/tweets.db`, `data/accounts.db` |
| `CSV_DIR`, `CAPTURES_DIR` | CSVs of server-side downloads and of captures | `data/csv`, `data/captures` |
| `CORS_ALLOW_ORIGINS` | JSON list of allowed origins (the extension does not need it) | `[]` |
| `ALLOWED_HOSTS` | hosts the service answers to (guards against DNS rebinding) | `["localhost","127.0.0.1"]` |
| `PAGE_SIZE`, `SUBWINDOW_DAYS` | page size and days per search stretch (server) | `20`, `7` |

The cap (900,000) and the window (24 hours) are deliberately not in this list: they are constants in the code and no
variable changes them.

X's search is `from:<account> since:<from> until:<to>`, with days in UTC: `since` is included and `until` is not. Of
the replies, the CSV keeps those that hang from a conversation the same account started. If that first tweet falls
outside the range and was never captured, its replies inside the range are left out too.

## Tests

```sh
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy src
```

They run offline, with made-up X responses. The cap's tests force the two things that matter: that it blocks when
there is no room and that it waits until the oldest access leaves the window. The extension's tests (vitest) run inside
`bash deploy/extension-build.sh`, between the type check and the build; among other things they cover when the crawl
is cut. **Never commit `.env` or the `data/` folder:** they hold session cookies and what was captured.

## How it is built

Continuous integration (`.github/workflows/ci.yml`) runs ruff, mypy and the tests, and builds the service image and
both versions of the extension, with its tests, with Podman. By hand:

```sh
podman build -f deploy/Containerfile -t xport .
bash deploy/extension-build.sh
```

The image comes from `deploy/Containerfile`; the extension ends up in `extension/dist/chrome-mv3` and
`extension/dist/firefox-mv2`. No container mounts the repository: the extension is copied out with `podman cp`. Some
decisions:

- Capture happens in the browser, not on the server. Since x.com changed how it signs requests, in June 2026, fetching
  from outside means rebuilding that signature every few weeks. The browser computes it by itself, and the shape of the
  responses did not change, so the rest of the code works as before.
- The cap is 900,000 and not a million, to leave a margin for any doubt about what counts as an access. The window is
  rolling, because a per-calendar-day counter would let almost two million through between 11 pm and 1 am. Accesses are
  counted, not tweets stored, without deduplication, because counting too many is safe and counting too few is not.
- Server-side downloads reserve, before each request, an upper bound of what it can bring (three times the page size,
  for the quoted tweets) and wait if it does not fit. What the browser captures has already happened, so it is recorded
  afterwards, and that is why the extension cuts the crawl when the service cannot record it.
- The cap's ledger is a separate SQLite file, so it survives deleting the tweets. Each check and its write run in a
  `BEGIN IMMEDIATE` transaction, so two processes sharing the ledger cannot both get through.
- The official API uses full-archive search and not the account's timeline, which only reaches the latest ~3,200
  tweets and would leave an old range out. It asks for no expansions: the quoted tweet's id already comes in the
  response, and X bills expansions from 22 November 2026.
- The service only answers requests addressed to `localhost` or `127.0.0.1`. It only listens on your computer, but a
  web page using DNS rebinding could reach it under its own domain.
- Only `mappers/` interprets X's responses; the extension does not read them. When x.com changes the shape, it is fixed
  in one place, and a change the mapper does not understand gives an error, not an empty CSV.
- The CSV defaults (UTF-8 without BOM, comma, everything quoted, UTC, `<account>_<from>_<to>.csv`) have been settled
  since June 2026. The same range rewrites its file and different ranges live side by side.
- The image is Alpine, multi-stage, with no browser: Chromium on musl is not worth it. If one is ever needed, it goes
  in a separate image.
- Only FastAPI handles CORS; nginx adds no headers, so they are never duplicated.
- The extension is built with [WXT](https://wxt.dev): one codebase for Chrome (MV3) and Firefox (MV2).
- The diagrams come from `docs/img/generar.py` (`python3 docs/img/generar.py`), in Spanish and English, so they are
  fixed in code and not in a drawing program. `docs/img/popup.png` is the real popup with example data.
- The project page (`docs/index.html` and `docs/en/`) is published by GitHub Pages from the `docs/` folder of `main`,
  with no build step.

![The cap: a 48-hour timeline where only the accesses of the last 24 count; their sum is compared with xport's cap of
900,000, below the million in X's terms.](docs/img/tope.en.svg)

The code is layered, and each layer only knows the one below:

```
providers/     where the data comes from, behind TweetProvider (twscrape, official API)
mappers/       X responses → domain model (the only place that interprets them)
domain/        Tweet and TweetLink (pydantic v2)
compliance/    the cap's ledger (SlidingWindowGate) and GatedProvider
storage/       data SQLite (deduplicated, finished stretches) and streaming CSV export
service/       FastAPI: captures, export, cap status, server-side downloads
cli.py         the command line (Typer)
extension/     extension for Chrome and Firefox (WXT)
```

The decisions with their history, and what is left, are in [docs/PLAN.md](docs/PLAN.md) (in Spanish).

## License

[MIT](LICENSE).
