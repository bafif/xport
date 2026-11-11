# How xport is built

[Español](ARCHITECTURE.md) · [Back to the README](../README.en.md)

## The pieces

![How it works: the extension copies what x.com already downloaded and hands it to the local service, which stores it
without duplicates and builds one CSV per account. The 24-hour cap counts every post seen and, if it cannot count it,
the capture stops.](img/flujo.en.svg)

- **The service** (Python, FastAPI): stores what is captured, keeps the cap's ledger, runs the queue, builds the CSVs
  and serves the page. It only listens on `localhost:8000`.
- **The extension** (TypeScript, [WXT](https://wxt.dev)): one codebase for Chrome (MV3) and Firefox (MV2). It takes
  captures from the queue, goes through X's search and hands the service the responses x.com already downloaded. It
  does not interpret them.
- **xport's Chrome** (Linux only): the service opens it inside [cage](https://github.com/cage-kiosk/cage), a screen
  nobody sees, with its own profile and the extension loaded. Without it, the capture is done by the extension in the
  person's browser.
- **The launcher and the installer** (`launcher.py`, `installer.py`): the `xport` command and what puts it on the
  system. The package brings its own Python.

## The layers

Each layer only knows the one below:

```
providers/     where the data comes from, behind TweetProvider (twscrape, official API)
mappers/       X responses → domain model (the only place that interprets them)
domain/        Tweet and TweetLink (pydantic v2)
compliance/    the cap's ledger (SlidingWindowGate) and GatedProvider
storage/       data SQLite (deduplicated, finished stretches), the queue and streaming CSV export
service/       FastAPI: captures, export, cap status, xport's Chrome, the page
cli.py         the command line (Typer)
launcher.py    the xport command: starts the service and opens the page
installer.py   the package's installer
extension/     the extension for Chrome and Firefox (WXT)
```

## Design decisions

**The 24-hour cap**

- It is 900,000 and not a million, to leave a margin for any doubt about what counts as an access.
- The window is rolling: a per-calendar-day counter would let almost two million through between 11 pm and 1 am.
- Accesses are counted, not tweets stored, without deduplication: counting too many is safe and counting too few is
  not.
- Server-side downloads reserve, before each request, an upper bound of what it can bring (three times the page size,
  for the quoted tweets) and wait if it does not fit. What the browser captures has already happened, so it is recorded
  afterwards; that is why the extension stops the capture when the service cannot record it.
- The ledger is a separate SQLite file, so it survives deleting the tweets. Each check and its write run in a
  `BEGIN IMMEDIATE` transaction, so two processes sharing the ledger cannot both get through.
- Every path that reaches X goes through the same ledger, and the cap and the window are constants in the code.

**The capture**

- Capture happens in the browser, not on the server: x.com signs each request with a value that changes often, and the
  browser computes it by itself. The shape of the responses is the same, so the rest of the code serves both paths.
- X does not serve Chrome in `--headless` mode, so xport's Chrome runs with a window, but on an invisible screen. The
  browser is not disguised.
- The queue goes one account at a time, because X throttles search per X account and in parallel it does not pay off.
  It lives in SQLite, so it survives a restart.
- Only `mappers/` interprets X's responses. When x.com changes the shape, it is fixed in one place, and a change the
  mapper does not understand gives an error, not an empty CSV.
- The official API uses full-archive search and not the account's timeline, which only reaches the latest ~3,200
  tweets and would leave an old range out.

**The service's security**

- It only answers requests addressed to `localhost` or `127.0.0.1`: it only listens on the computer, but a page using
  DNS rebinding could reach it under its own domain.
- Any open site can send requests to `localhost` from the browser. So the ones that change something have to come from
  the page itself, from the extension or from a program without a browser, and those carrying another site's `Origin`
  get 403.
- Only FastAPI handles CORS; nginx adds no headers, so they are never duplicated.

**The CSV**

- UTF-8 without BOM, comma, everything quoted, dates in UTC and `<account>_<from>_<to>.csv`. The encoding and the
  delimiter can be changed for Excel.
- It is written row by row, without loading everything in memory. The same range rewrites its file and different
  ranges live side by side.

**The package**

- It brings the portable CPython from python-build-standalone, with the exact dependencies of `uv.lock` installed
  inside: whoever uses it installs nothing but Chrome (and cage, on Linux). It is built on each system because some
  dependencies come compiled (pydantic-core, uvloop).
- The installer is run by that same Python, so its messages come in both languages and pytest tests it.
- The container image is Alpine, multi-stage, with no browser.

## Developing

```sh
uv sync                                   # the environment, from uv.lock
uv run fastapi dev src/tweet_extractor/service/app.py
bash deploy/extension-build.sh            # the extension, with Podman (or: cd extension && npm ci && npm run build)
```

More ways to start the service, and the configuration, are in the [reference](REFERENCE.en.md). The extension has its
own [README](../extension/README.en.md).

## Tests

```sh
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy src
```

They run offline, with made-up X responses. The cap's tests force the two things that matter: that it blocks when
there is no room and that it waits until the oldest access leaves the window. The extension's tests (vitest) run with
`npm test` in `extension/` and inside `bash deploy/extension-build.sh`; among other things they cover when the capture
is cut.

Continuous integration (`.github/workflows/ci.yml`) runs all of that, builds the service image and both versions of
the extension, and builds the installable package on Linux, Windows and macOS, with a smoke test on each.

## Languages

Everything the person sees is in English and Spanish: the service and the CLI in `src/tweet_extractor/locales/`, the
extension in `extension/locales/`. The tests fail if a text is missing from one of the two catalogs. Each document has
its version in the other language next to it (`X.md` and `X.en.md`). The CSV is not translated.
