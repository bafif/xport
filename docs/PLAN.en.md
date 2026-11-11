# xport: project plan and status

[Español](PLAN.md) · [Back to the README](../README.en.md)

xport downloads the tweets of one or more X accounts between two dates and leaves one CSV per account with what
matters: the account, the date, the text, the links the tweet carries and, if it quotes another tweet, which one. Quote
tweets are in; retweets are not.

This document holds the project's rules, the decisions and their reasons, what is done and tested, and what is left.
Installation and usage are in the [README](../README.en.md) and in the documents under `docs/`; the code's layers and
how to develop, in [How it is built](ARCHITECTURE.en.md).

## What the CSV holds

The columns are in the [README](../README.en.md#what-the-csv-has). What was decided about their content:

- `content` has the full text, long tweets included, and `links` the external URLs (`expanded_url`).
- If a tweet was edited and is captured again, the latest version stays.
- Of the replies, the account's own threads stay, and the ones the account wrote inside other people's conversations
  are left out. The export says how many were left out, so the difference between what was captured and what was
  exported does not look like data loss.
- The file defaults are settled: UTF-8 without BOM, comma as separator, every field quoted (the text has line breaks
  and commas), dates in UTC and the name `<account>_<since>_<until>.csv`, so the same range is rewritten and different
  ranges live side by side. The encoding and the separator can be changed to open it in an Excel set to Spanish
  (`utf-8-sig` and `;`).
- Media is kept only as a URL, never downloaded, and only one level of quotes is kept.

## The context

- Reading X without paying means using the same GraphQL endpoints the website uses, with a logged-in session. The
  official API charges for every post read.
- A user's timeline stops at about 3,200 tweets. The `from:user` search has no such ceiling.
- X's terms set a fine of 15,000 dollars for every million posts accessed in any 24-hour period. That is why the first
  thing is to make crossing that cap impossible, in code.

## Rules that are not negotiable

Several parts of the code refer to these rules by number.

1. **The access cap.** No path in the app may access more than 900,000 posts in 24 hours.
   - The cap is 900,000 and not a million, to leave a margin for any doubt about what counts as an access. There is a
     single one for the whole app, because the terms' limit is per client and not per X account. It is a constant in
     the code: it is not changed from `.env`.
   - The window is rolling: usage at any moment is the sum of everything recorded in the last 86,400 seconds. A
     per-calendar-day counter does not work, because it would allow almost two million between 11 pm and 1 am.
   - Accesses are counted, not stored rows. Every tweet that arrives in a response adds up: the quoting one, the quoted
     one embedded in it and the retweets that are later dropped. The ledger never deduplicates; counting too many is
     safe and counting too few is not.
   - In active scraping, an upper bound of what the page can bring (the page size times three, for the embedded quoted
     tweets) is reserved before the request. If it does not fit, it waits for old entries to leave the window; when the
     response arrives, the reservation is adjusted to the real count.
   - The ledger lives in an audit SQLite separate from the tweets, so it survives deleting the database, and in the
     per-user data folder, the same one for the service, the CLI and the native host. If another xport already
     answers, the service does not start. Each read-decide-write cycle runs under a lock and inside a
     `BEGIN IMMEDIATE` transaction, so not even two processes sharing the ledger (the CLI and the service) can slip in
     between the check and the write.
   - Every access path goes through the same ledger. Server-side providers are wrapped in `GatedProvider`. Browser
     capture records afterwards what it already accessed, including what the server rejects: rejecting does not erase
     an access. Past the cap, the server answers 429 and the extension cuts the crawl.
   - Capture does not see what the ledger cannot count. The extension does not start a capture if the service does not
     answer or the cap is full, and cuts it if the service rejects a batch, reports the cap or stops answering with
     pages left unrecorded.
   - The tests force the two things that matter: that it blocks when there is no room and that it waits until the
     oldest event leaves the window.
2. **Secrets out of the repository.** The cookies (`auth_token`, `ct0`) and any credential go in `.env`, which is
   ignored. The repository only has `.env.example`, without values.
3. **Quotes yes, retweets no.** A tweet with `retweeted_status_result` is dropped (but its access is counted).
4. **GraphQL is interpreted only in `mappers/`.** Neither the providers nor the extension parse tweets; the `queryId`s
   rotate with every x.com deploy, so operations are matched by name.
5. **No browser in the service image.** The image is Alpine, and Chromium on musl is not worth it. If a provider with a
   browser is ever needed, it goes in another image.
6. **CORS in a single place.** FastAPI handles it; nginx adds no CORS headers.

## How capture works

Server-side scraping with twscrape is blocked: x.com signs every request with an `x-client-transaction-id` that
twscrape can no longer compute. So capture happens in the browser: the extension copies the GraphQL that x.com already
downloads in the search and sends it to `POST /ingest`, which reuses `mappers/` and `storage/`. The browser computes
the signature by itself and the shape of the responses is the same, so the rest of the code serves both paths.

**Why the service drives its own Chrome.** With the search in a tab of the everyday browser, capture depends on that
tab staying in view: with the tab hidden, the browser stops painting the page, throttles its timers and x.com stops
loading. A separate unfocused window is not enough, because Chrome on Windows and macOS marks as hidden a window that
another one fully covers, and «keep that window in view» is a technical rule nobody should have to understand. So the
service starts a Chrome (or Chromium, or Edge) with a profile of xport's own, loads the extension into it and hands it
the queue's work; the interface is a local page that opens in the everyday browser.

- **X answers 403 to `HeadlessChrome`**, so that Chrome runs with a window, but inside cage, a virtual screen nobody
  sees. The browser is not disguised. Without cage (today, on Windows and macOS) it is not used, and capture is done by
  the extension in the person's browser, with the pause that warns when the tab is hidden.
- **Branded Chrome ignores `--load-extension`**, so the extension is loaded with `Extensions.loadUnpacked` over
  `--remote-debugging-pipe`, without being saved in the profile, and gets the `xport:runner` mark with
  `Extensions.setStorageItems`. `POST /captures/next` only gives work to whoever sends `x-xport-runner`; if the runner
  is off or gave up, to anyone.
- **When a person is needed, the profile opens with a window:** to log in to X the first time, or when X ends the
  session (the `auth_token` cookie is missing or a tab ended up on the login). The window is a regular Chrome window,
  without the pipe: a small app-mode window does not let a real account finish logging in.
- **None of this clashes with the rules:** it is not Selenium or Playwright, nothing goes into the service image
  (rule 5) and everything seen goes through `/ingest` and the cap (rule 1).

**The limits of X's search.**

- The search goes to the second and in UTC: `from:<account> since_time:<s> until_time:<s>`. `since:` and `until:` take
  days in the searcher's time zone and include the `until:` day: with them, the first hours of the start day would be
  lost and an extra day would be requested.
- Each search settles at about a thousand results. When it settles before `since`, the next one starts at the second
  of the oldest tweet loaded, plus one, so another tweet from the same second is not lost. If a search brings nothing
  older, the range is done.
- X allows 50 searches every 15 minutes per account. On a 429, or with the quota at zero, the capture waits
  (`waiting`, with `resume_at`) until the reset plus 15 seconds, with the tab off the search, and carries on by itself.
  With few searches left in the quota, it spreads the remaining ones.
- Blank loads (the spinner or «Something went wrong») are retried with backoff; they are not the end of the data.

## Where it stands

It works against x.com, end to end, on Linux: the queue and the local page, capture with its own Chrome inside cage,
the wait on the search limit, the search to the second (a day with 2,830 tweets comes out whole), the cut-offs (the
service down in the middle of a capture, the popup without a service and the full cap), the pause with the tab hidden,
the notice of why a capture brought no tweets, and the installer with its first login. Everything is in English and
Spanish.

Not tried by hand: opening a CSV with the Excel checkbox in an Excel set to Spanish, the system notifications, the
installer on Windows and on macOS, and the automatic start with a Windows browser and the service in WSL. The tests run
offline, mypy in strict mode and ruff pass, and the Alpine image, the extension and the package for the three systems
are built in CI.

Server-side scraping with twscrape is implemented and tested offline, but blocked by x.com: it fails closed with a
clear error. The official API is implemented for whoever has a paid key, but it was never tried against the real API.

## Done

What is in the code, where, and how far it is tested. «Real» means against x.com, with a real account.

| What | Where | Tested |
|---|---|---|
| 900,000-access cap: rolling window, counts accesses without deduplicating, reserves before requesting, separate ledger, atomic across processes | `compliance/` | tests (blocking, waiting, `BEGIN IMMEDIATE`); real: it counts every post seen |
| Strict `Tweet` model (UTC, clean ids and URLs, quoted tweet complete or nothing) | `domain/models.py` | tests |
| Mapper: retweets out, quotes in their three places, full text of long tweets, links without a target or self-links out, the quoted tweet's link on x.com | `mappers/twscrape_mapper.py` | tests; real |
| Replies: own threads in, other people's conversations out, applied on export and counted in `excluded_replies` | `storage/`, `POST /export` | tests; real |
| Storage: dedupe by id with the latest version, lowercase handles, checkpoint per sub-window | `storage/sqlite_store.py` | tests |
| Streaming, atomic CSV, `<account>_<since>_<until>.csv` | `storage/csv_exporter.py` | tests; real |
| In-page capture: `fetch` and XHR patch, bridge, relay in batches of 20 pages | `extension/entrypoints/` | real |
| Crawl: scroll, «Retry», reload with backoff, bound at the second of the oldest tweet, resuming from the database | `autoscroll.content.ts`, `lib/autoscroll.ts`, `lib/queue.ts`, `POST /progress` | vitest and tests; real: a day with 2,830 tweets comes out whole |
| Crawl cut-offs: no service, full cap, 429, `over_cap`, rejection, twenty unrecorded pages | `lib/ingest.ts`, `background.ts`, popup | vitest of `decideIngest`; real: the service down, the popup without a service and the full cap |
| Pausing the crawl with the tab hidden, with a notice | `lib/autoscroll.ts` (`decideStep`), `autoscroll.content.ts` | vitest; real in Chrome |
| Waiting for X: on a 429 or with no quota, it waits for the reset off the search and carries on; with few searches, it spreads the remaining ones | `lib/rate.ts`, `capture-main.content.ts`, `background.ts`, the queue's `waiting` state | vitest of `decideRate` and `waitStep`; tests; real: three waits in a row |
| Browser restart in the middle of a capture: resumes from what was stored and crawls a single tab | `background.ts` (`withFrontier`, `claim`), `lib/queue.ts` | vitest; real |
| x.com shape change: `rejected` and `format_changed` in `/ingest`, a sample in the log, crawl cut | `service/ingest.py`, `lib/ingest.ts` | tests with a page of unknown shape; vitest |
| CSV for an Excel set to Spanish from `/export`, the page and the popup | `service/schemas.py` | tests (BOM and `;`); **not opened in Excel** |
| A single cap ledger: per-user data folder, moving `data/`, does not start if another xport runs | `config.py`, `datadir.py`, `service/app.py` | tests |
| Capture queue: one per account, one at a time, persistent; the page, the popup and `tweet-extractor capture` queue | `storage/queue.py`, `service/captures.py`, `lib/queue.ts`, `background.ts` | tests; vitest; real: several accounts in a row, each in its CSV |
| Local page at `/`, with an `Origin` check | `service/page/`, `service/app.py` | tests; real in Chrome, in both languages |
| Export and notification when done | `service/captures.py`, `service/notify.py` | tests with a fake notifier; **notifications not tried by hand** |
| Why there were no tweets: the account does not exist, is suspended, is protected, blocked xport's account, X does not show it, or it exists and posted nothing in those dates | `mappers/account_mapper.py`, `lib/account.ts`, `background.ts`, `service/captures.py` | tests with trimmed real responses; vitest; real: four cases |
| The service's own browser: Chrome with xport's profile inside cage, the extension over the pipe, the queue only for it, the login window, closed when the queue is empty | `service/browser.py`, `service/captures.py`, `lib/api.ts`, `background.ts` | tests with a fake Chrome; real on Linux |
| Daily summary of the cap, apart from the window | `compliance/gate.py` | tests |
| Deleting what was captured for an account without touching the cap | `POST /reset`, popup | tests; real |
| Automatic start through native messaging: Firefox, Chrome, Chrome Beta and Chromium | `deploy/native-host/` | real in Chrome Beta; Windows with WSL untested |
| The service only serves requests to `localhost` or `127.0.0.1` | `service/app.py` | tests |
| Server-side twscrape, with sub-windows and a stop after three empty pages | `providers/twscrape_provider.py`, `providers/_twscrape_gql.py` | offline tests; **blocked by x.com** |
| Paid official API v2, full-archive search | `providers/official_api.py`, `mappers/api_v2_mapper.py` | made-up responses; **never against the real API** |
| CLI and service on the same orchestrator; in-memory jobs | `cli.py`, `orchestrator.py`, `service/` | tests |
| Alpine image with nginx, extension build in Podman, CI | `deploy/`, `.github/workflows/ci.yml` | CI |
| English and Spanish everywhere: service, CLI, local page, extension, website and documents | `locales/`, `i18n.py`, `extension/locales/`, `lib/i18n.ts`, `docs/en/` | tests (complete catalogs, each document with its pair); vitest |
| The `xport` launcher: starts the service if it is not running (or uses the running one), opens the page, closes it and says what is missing | `launcher.py`, `/healthz` | tests; real on Linux |
| The package with its own Python and the installer: copy, shortcut, command, uninstall that keeps the data | `deploy/bundle/build.py`, `installer.py` | tests; real on Linux; Windows and macOS only in CI, **not tried on those systems** |
| First start: the page offers logging in to X before the first capture | `service/browser.py` (`login_now`), `POST /browser/login`, `service/page/` | tests with a fake Chrome; real on Linux |

## The plan's items

The numbers stay because the code cites them. Each item says what it is and, if something is left, what.

### Capture and the cap's ledger

1. **Pausing the crawl with the tab hidden.** Done and tried in Chrome.
2. **Making an x.com shape change noticeable.** Done. If the response envelope ever changes and not the tweet,
   `/ingest` sees zero tweets and cannot tell it from an empty page: that is not covered.
3. **Excel set to Spanish.** Done. Opening an exported CSV with the checkbox in an Excel set to Spanish is left.
4. **A single cap ledger.** Done: one data folder per user for the service, the CLI and the native host, and the
   service does not start if another xport already answers.

### Checks in a browser

5. **The cut-offs in a browser.** All three work: the service down in the middle of a capture (the crawl cuts after 20
   unrecorded pages and the queue resumes when the service is back), the popup without a service and the full cap. An
   edge: the extension sends what it captured every second and a half; if the browser closes in between, what it did
   not send is lost and those accesses do not reach the cap. It is a few pages at most.
6. **The windowless browser and its assumptions.** The search loads and scrolling brings more in a Chrome driven by the
   service; X rejects `HeadlessChrome`, so it runs inside cage. `x-rate-limit-*` comes in `SearchTimeline` and the page
   can read it. `since_time:` and `until_time:` work to the second in «Latest». Edge, Windows and macOS were not tried.

### The queue and the local page

7. **Capture queue.** Done: `POST /captures`, `GET /captures`, `POST /captures/next` and
   `POST /captures/{id}/state`, in `queue.db`, and `tweet-extractor capture`. With the extension of the everyday
   browser, if the person uses the queue's tab for something else, the capture stays running without progress until
   they cancel it.
8. **The local page.** Done (`service/page/`, no build step), with the `Origin` check, inclusive dates with shortcuts,
   per-capture progress and the cap with its daily summary.
9. **Notify when done and export by itself.** Done (`service/notify.py`): on Linux with `notify-send`, where the click
   opens the CSV; on Windows with a PowerShell toast, and on macOS with `osascript`, without a click. Trying them by
   hand is left.
10. **Account that does not exist, is suspended or protected.** Done and tried. A capture that ends without a single
    tweet in the range takes its tab to `x.com/<account>/following`, which requests `UserByScreenName` without loading
    posts; `mappers/account_mapper.py` interprets it. The protected account that xport's account follows and the block
    come from real responses edited by hand: they could not be measured.

### The crawl

11. **Wait for X instead of insisting.** Done and tried. The `fetch` and XHR patch passes on, separately, the status
    and the `x-rate-limit-*` of each `SearchTimeline` response (HTTP, not GraphQL: rule 4 holds) and `decideRate`
    (`lib/rate.ts`) decides. It waits until the reset plus 15 s, and at least a minute after a 429, in case the local
    clock is ahead. With fewer than 10 searches in the quota, it spreads the remaining ones until the reset.
12. **Search to the second.** Done and tried: `since_time:`/`until_time:` in UTC seconds, the bound at the second of
    the oldest tweet plus one, and `/progress` returns the oldest one's time to resume from there.

### xport's browser

13. **The service drives xport's profile.** Done on Linux (see «How capture works»). Every minute it checks the
    session; if a tab lands on an error page twice in a row, it restarts the browser, and on the third failure it hands
    the queue back to the extension of the everyday browser. Left: Windows and macOS, and removing the native host and
    the popup once the installer covers the three systems.

### The installer

14. **Installers for Windows, macOS and Linux:** the service packaged without uv or Python, an «xport» shortcut and the
    first login guided from the page. Done on Linux: `deploy/bundle/build.py`, `launcher.py` and `installer.py`. Left:
    trying the Windows and macOS packages on those systems (CI builds them and smoke-tests them, nothing more);
    publishing the packages in each release; signing the Windows one and `xport.app`, because SmartScreen and
    Gatekeeper warn about unsigned ones; and, on those two systems, the windowless browser of item 13. Until then,
    capture there is done by the extension of the everyday browser.
15. **Publishing the extension unlisted** in the Chrome Web Store (it works for Chrome and Edge) and signing it unlisted
    on AMO (`web-ext sign --channel=unlisted`), so Chrome does not ask for developer mode and Firefox does not remove it
    when it closes.

### What depends on others

16. **Trying the official API against the real API**, with a paid key: that `tweet.fields` still works, that long posts
    bring `note_tweet` and that the rate limit is the documented one.
17. **If twscrape gets unblocked**, check against real data that the search's date filter really bounds, that the
    cursor pages and ends, that the shape of the responses matches the tests' and that the accesses per page stay
    within the reservation. If the real shape surprises, check that walking the envelope does not take a tweet nested
    inside a quote as a top-level one.

### Settled questions

- **Ledger history:** a per-day summary, apart from the window (item 8).
- **Saturated days:** there are none: the search goes to the second and narrows to the oldest tweet loaded (item 12).
- **State of the service's jobs:** `/jobs` is not persisted while no server-side provider works; the queue is
  (item 7).
- **What counts as a «link to a document»:** every external URL. Filtering later in the spreadsheet is easy, and
  filtering earlier loses data.
- **Deleted or protected account:** it is reported and the queue goes on (item 10).
- **How many accounts in parallel:** one, in line (item 7).
- **A Windows browser with the service in WSL:** stays as it is and untried; with the Windows installer it no longer
  makes sense.
