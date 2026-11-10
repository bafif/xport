# xport: plan and project status

[Español](PLAN.md)

I built xport to download the tweets of one or more X accounts between two dates and end up with
one CSV per account holding only what I care about: the account, the date, the text, the links the
tweet carries and, if it quotes another tweet, which one. Quote tweets are in; retweets are not.

This document gathers in one place what used to be spread across the initial plan, one
specification per component and a status note. It covers what I decided and why, in what order I
built it, where it stands and what's left. The README has usage and configuration; the reasoning
goes here.

## What goes in the CSV

| Column | What it holds |
|---|---|
| `account` | the account handle, lowercase |
| `created_at` | date and time of the tweet, ISO 8601 in UTC |
| `content` | the full text (for long tweets, the whole text and not the truncated one) |
| `links` | the tweet's external URLs (`expanded_url`), separated by spaces |
| `quoted_tweet_id`, `quoted_tweet_url` | the quoted tweet, if any |

If a tweet was edited and I capture it again, the latest version is kept.

Of the replies, I keep the account's own threads and leave out the replies the account wrote
inside other people's conversations. The export reports how many were left out, so the difference
between what was captured and what was exported doesn't look like data loss.

I settled the file defaults on June 13 and I don't intend to reopen them: UTF-8 without BOM,
comma as separator, every field quoted (the text has line breaks and commas), dates in UTC and the
name `<account>_<since>_<until>.csv`, so the same range gets overwritten and different ranges
coexist. Encoding and separator can be changed by parameter to open the file in an Excel set up in
Spanish (`utf-8-sig` and `;`). Media is stored only as a URL, not downloaded, and I keep a single
level of quotes.

## Where I started

Before writing any code I looked at what could be used in 2026 to read X without paying:

- snscrape half works and is unmaintained, and Nitter died in 2024 when X removed guest accounts.
  What's still alive (twscrape, twikit, Scweet) uses the cookies of a logged-in account against the
  same GraphQL endpoints the web uses.
- The official API has no useful free plan: since February 2026 it's pay-per-read, and the fixed
  plans with full historical search are closed to new accounts or cost thousands of dollars a
  month.
- A user's timeline cuts off at around 3,200 tweets. The search `from:usuario since:… until:…`
  doesn't have that ceiling if the range is split into smaller windows.
- X's terms forbid scraping and set a fine of 15,000 dollars per million posts accessed in any
  24-hour period.

That gave the plan: start with twscrape behind an interface of my own, leave room ready for the
official API, and before anything else build something that makes it impossible, in code, to go
over the terms' cap.

## Non-negotiable rules

Several parts of the code refer to these rules by number.

1. **The access cap.** No path in the app can access more than 900,000 posts in 24 hours.
   - The cap is 900,000 and not a million to leave a margin for any doubt about what counts as an
     access. There's a single one for the whole app, because the terms' limit is per client and not
     per X account. It's a constant in the code: it can't be changed from `.env`.
   - The window is sliding: usage at any given moment is the sum of everything recorded in the last
     86,400 seconds. A per-calendar-day counter doesn't work, because it would allow almost two
     million between 11 p.m. and 1 a.m.
   - Accesses are counted, not stored rows. Every tweet that arrives in a response counts: the one
     that quotes, the embedded quoted one and the retweets I discard later. The register never
     deduplicates; overcounting is safe and undercounting isn't.
   - In active scraping, an upper bound of what the page can bring (the page size times three, for
     the embedded quoted tweets) is reserved before requesting. If it doesn't fit, it waits for old
     entries to leave the window; when the response arrives, the reservation is adjusted to the real
     count.
   - The register lives in an audit SQLite separate from the tweets one, so it survives the database
     being deleted, and in the per-user data folder, the same one for the service, the CLI and the
     native host. If another xport is already responding, the service doesn't start. Every
     read-decide-write cycle runs under a lock and inside a `BEGIN IMMEDIATE` transaction, so two
     processes with the same register (the CLI and the service) can't slip in between the check and
     the write either.
   - Every access path goes through the same register. Server-side providers are wrapped in
     `GatedProvider`. The browser capture records afterwards what it already accessed, including
     what the server rejects: rejecting doesn't erase an access. Past the cap, the server responds
     429 and the extension stops the crawl.
   - The capture doesn't see what the register can't count. The extension doesn't start a capture if
     the service doesn't respond or the cap is full, and it stops it if the service rejects a batch,
     signals the cap or stops responding with unrecorded pages.
   - The tests enforce the two things that matter: that it blocks when there's no room and that it
     waits until the oldest event leaves the window.
2. **Secrets out of the repo.** The cookies (`auth_token`, `ct0`) and any credential go in `.env`,
   which is ignored. The repo only has `.env.example` with no values.
3. **Quotes yes, retweets no.** A tweet with `retweeted_status_result` is discarded (but its access
   is counted).
4. **GraphQL is interpreted only in `mappers/`.** Neither the providers nor the extension parse
   tweets; the `queryId`s rotate with each x.com deploy, so the operation is looked up by name.
5. **No browser in the service image.** The image is Alpine and Chromium on musl isn't worth it.
   If a browser-based provider is ever needed, it goes in another image.
6. **CORS in one place.** FastAPI handles it; nginx doesn't add CORS headers.

## How it's put together

It's layered, and each layer only knows the one below:

```
providers/     de dónde salen los datos, detrás de TweetProvider (twscrape, API oficial)
mappers/       GraphQL crudo → modelo de dominio
domain/        Tweet y TweetLink (pydantic v2)
compliance/    el registro del tope (SlidingWindowGate) y GatedProvider
storage/       SQLite de datos (deduplicado, checkpoints), la cola de capturas y export CSV en streaming
datadir.py     la mudanza de los datos viejos (`data/`) a la carpeta por usuario
service/       FastAPI: jobs, ingesta de la captura, la cola, export, estado del tope, notificaciones
               y la página local (`service/page/`)
cli.py         la línea de comandos (Typer)
extension/     extensión para Chrome y Firefox (WXT, un solo código)
```

`providers/factory.py` is the only place where scraping vs. the official API is chosen (the
`PROVIDER_BACKEND` variable), and it builds each provider with its mapper. The orchestrator
(`orchestrator.run_job`) walks the sub-windows of each account and is shared by the CLI and the
service.

## How I built it

**June 4: the cap first.** I set up the project with uv and the first thing I wrote was the cap
register and the provider contract, before a single line of scraping. Compared to the initial sketch
I changed three things: the reservation is identified by an autoincrement id and not by the time
(two reservations in the same second overwrote each other), the reservation actually happens before
the request, and the clock and the wait can be injected to test the window without waiting 24 hours.
The wait, besides, runs outside the lock.

**June 8: hardening and going public.** I fixed how the register connection is closed, got the repo
ready to go public (README, MIT license) and built the domain model. `Tweet` has only the CSV columns
plus `id`, which is the database key. It validates shape strictly (date with time zone and converted
to UTC, ids and URLs with no empties or control characters, quoted id and URL together or neither,
extra fields forbidden), but leaves the text free: it can be empty and is stored as is, line breaks
included. I reviewed it thoroughly twice and those reviews produced finer validations, like
rejecting dates that overflow when converted to UTC.

**June 9: the twscrape provider.** I integrated it at the page level and not the tweet level,
because the cap needs to reserve before each request. All the dependency on twscrape's internals
stayed in a single file (`providers/_twscrape_gql.py`), and the boundary is clear: the provider walks
the response envelope (entries, cursors, access count) and hands over the raw tweets; the mapper is
the one that interprets them. The range is split into fixed sub-windows aligned to UTC days, and
the network is injected so every test runs offline.

**June 13: the four phases in code.** That day I wrapped up what I had planned in four stages:

- The mapper: discards retweets and deleted tweets by returning `None`, but raises an error if the
  GraphQL shape isn't the expected one, so an x.com rotation shows up as an error and not as an
  empty CSV. It unwraps `TweetWithVisibilityResults`, looks for the quoted tweet in its three
  possible places, keeps the full text of long tweets and filters out links with no destination or
  that point to the tweet itself.
- The reply policy by conversation root: replies whose conversation started in a tweet by the same
  account are kept. It's applied at export time and over the whole collection, not per page. It has
  a known limitation: if the root falls outside the requested range, its replies inside the range
  are lost.
- Storage: SQLite with `INSERT OR IGNORE` by id (windows can overlap) and a checkpoint per
  sub-window, so a retry doesn't request again what already finished (every repeated request spends
  cap). The CSV is written streaming and atomically: if it fails halfway, the previous file stays.
- The CLI and the orchestrator. Then the factory and an official API provider that fails closed: it
  documents the request to `/2/users/:id/tweets`, but I didn't implement the mapping because I have
  nothing to verify it against real responses.
- The FastAPI service on top of the same orchestrator, with the cap register and the database as
  single instances, nginx in front and a multi-stage Alpine image with the register on a separate
  volume.
- The extension, with WXT, in its first form: a popup that asks the local service for jobs.

**June 14: twscrape stops working and I change course.** I tried with real cookies for the first
time and the provider failed closed, as it was supposed to. The problem wasn't xport's: x.com had
migrated its web client to Vite and twscrape could no longer compute the `x-client-transaction-id`
header, without which X rejects requests. There was no new release fixing it. I weighed four ways
out: wait for a fix, patch the computation myself, move to the paid official API, or capture from
the browser the GraphQL that x.com already downloads. Patching meant rebuilding the algorithm on top
of the new bundle and redoing it every few weeks. Browser capture is immune to that: the browser
computes the header on its own, the JSON shape didn't change and the mappers are reused as they are.
I chose capture and implemented it that same day:

- The extension patches `fetch` and `XMLHttpRequest` in the page, clones the `SearchTimeline`
  responses without touching the ones x.com uses, and passes them to the service via `POST /ingest`,
  along with the operation name. It interprets nothing.
- The service extracts, maps, stores deduplicated and records the accesses with `record`: since the
  access already happened in the browser, there's nothing to reserve. Counting every capture, even
  if one repeats a page the browser pulled from its cache, is still the safe choice.
- Export became on demand (`POST /export`) and filters by date range.

**June 15 to 17: using it for real.** With real use against x.com came the problems the tests
couldn't show, and that's where almost everything the extension does today came from:

- The extension opens the search on its own and scrolls it to the bottom. If it loads blank it
  retries with increasing waits, if X shows "Retry" it clicks it, and if there's a spinner it waits.
- Live search stops at around a thousand results. When scrolling stalls before reaching the start
  date, the extension narrows the range down to the day of the oldest loaded tweet and searches
  again, until the whole period is covered. The day is read from what the page already shows,
  without interpreting GraphQL.
- X also throttles the search endpoint for hours. To avoid starting from scratch, `POST /progress`
  says how far the stored data goes and the capture continues from there. Since it comes from the
  service's database and not from the browser, it survives even if the extension's state is
  cleared.
- If the oldest day already has a huge number of tweets (800 or more), keeping it included would
  leave the capture stuck there, so it's skipped and its tail is lost. The threshold is high on
  purpose: loading nothing is a connection problem and gets retried, not skipped.
- On the 16th I added a native messaging host so the browser brings the service up and down on its
  own, without having to leave it running by hand. It supervises the same service and uses the same
  cap register.

**June 19: finishing touches.** `POST /reset` deletes an account's captured data without touching
the cap register, the extension has icons and the Windows installer went pure ASCII, with a mode
that keeps Chrome's service worker alive.

**October 31: catching up.** nginx accepted at most 1 MB and returned 413 on large `/ingest` batches,
which the extension discarded without recording them in the cap; now it accepts large batches. I
added building the extension in a container (no Node needed on the machine), the English README and
continuous integration with ruff, mypy, the tests and both builds.

**November 1.** This document replaced the initial plan, the per-component specifications and the
status note.

**November 6: plan audit.** I checked every decision against the code. The fundamental ones hold:
the cap with a sliding window that counts accesses, browser capture, the mappers as the only place
that interprets GraphQL, and the CSV defaults. What didn't hold was how the cap was enforced in
browser capture, and I fixed it along with four other problems:

- In browser capture the cap stopped nothing. Past the cap, `/ingest` responded 429 without recording
  the batch, and the extension discarded it and kept scrolling: real accesses the register never
  counted. Worse, if the service didn't respond, the popup started the capture anyway, and whatever
  the buffer collected (up to a thousand pages) was lost without being counted. Now the server also
  records what it rejects, and the extension doesn't start without the service or without room in
  the cap, and stops the crawl on a 429, an `over_cap`, any other rejection, or twenty pages pending
  without the service. Also, batches go twenty pages at a time: a single one with everything
  accumulated could exceed nginx's 32 MB and get lost with a 413.
- The register lock was an `asyncio` one, that is, single-process: the CLI and the service on the
  same register could read the same usage and both get through. Now the cycle runs in a
  `BEGIN IMMEDIATE` transaction, with a test that fails without it.
- X handles are case-insensitive, but storage wasn't: capturing "NASA" one day and "nasa" another
  split the data into two accounts, and resuming and export saw only one part. Now they're stored
  lowercase, and when the database is opened whatever was already there is lowercased.
- The compose didn't set `CAPTURES_DIR`, so the capture export wrote inside `/app`, which is owned
  by root, with the service running as another user: it failed, and whatever was written would have
  been lost with the container. Now it goes to the data volume.
- Server-side pagination only stopped if the cursor repeated. At the end of a search X can return
  empty pages with a new cursor, each one spending cap; now it stops after three empty pages in a
  row, like twscrape, which stops at the first.

The README switched to Spanish, with an English mirror (`README.en.md`).

After going over the report I decided four more things, and they were done the same day:

- **Edited tweets:** the latest captured version is kept, not the first.
- **Official API:** twscrape stays dormant, but the paid official API is now implemented for whoever
  has a key. It uses full-archive search (the user timeline doesn't go past the ~3,200 most recent
  posts), without retweets and without expansions, which X charges for since November 22, 2026.
  It's built from X's documentation and tested with fabricated responses: I couldn't test it against
  the real API because I don't have a paid key. X's docs currently contradict themselves on names
  (`tweet.fields` in the guides, `post.fields` in the generated pages); I use the usual ones.
- **Local service:** it only serves requests addressed to `localhost` or `127.0.0.1`, against DNS
  rebinding.
- **Extension tests:** vitest, with the pure functions in `extension/lib/`, among them the one that
  decides when to stop the crawl. They run inside the extension build, so in CI too.

**November 6, afternoon: GitHub.** The repo was already public. I went through the entire history
looking for cookies, tokens and keys, and there weren't any. The only personal thing was my home path
in the Windows installer, which is now asked for as a parameter. I added images to the README (two
diagrams generated by `docs/img/generar.py` and a screenshot of the real popup with sample data) and
a project page at `docs/index.html`, which GitHub Pages publishes from the `docs/` folder on `main`.

**November 6: the look.** I redrew the logo as a vector (`docs/img/logo.svg`, generated by
`generar.py`) and the extension icons come from it. The page, the popup and the diagrams moved to X's
black ("Lights out"), with their own touches taken from the logo: the blue corner-arrow on what goes
out, the logo in the header, monospaced type for data and a CSV grid in the background. The page adds
a CSV example and a share card (`docs/img/social.png`).

**November 6: Chrome.** Autostart didn't work in Chrome Beta or Chromium: the installer only wrote to
the stable Chrome folder. Now it writes to the folder of each installed variant. The Chrome extension
has a fixed id, derived from a public key in the manifest, so it no longer has to be copied by hand.
And the host recognizes when Chrome launches it and keeps the service alive even if the service worker
goes to sleep; before, that depended on a variable only the Windows installer set.

**November 6: the first capture in Chrome Beta.** It ran without errors: the host started the service,
every batch went in and the cap counted every post seen. A problem showed up in the data: the link to
the quoted tweet was the permalink exactly as X gave it, almost always on twitter.com and sometimes a
t.co, which doesn't lead to the tweet. Now it's always on x.com, and the stored ones fix themselves
when the service starts.

**November 6, night: second review.** I went over every decision again thinking about how comfortable
it is to use and whether it can be used without having the browser in front. The fundamental decisions
still stand: the cap, browser capture, the mappers as the only place that interprets GraphQL and the
CSV defaults. What I found went into "Pending", with priority and what it takes to close it. Two
things can lose data silently: the capture relies on the tab being visible, and `/ingest` silently
discards tweets the mapper doesn't understand.

**November 7: decisions.** With that review in view I decided three things. Capturing without the
browser in front goes with an invisible Chrome driven by the service, not with an unfocused window.
The four bugs the review found all get fixed. And all the proposed improvements go in. In every
decision, the next step called the shots: a comfortable installer for Windows, macOS and Linux, and
using it shouldn't require knowing anything technical. The why is in "Capturing without the browser
in front", and the work order, in "Pending".

**November 7: Phase 1, the four bugs.** They were fixed in the code and with tests; what needs a real
browser or a real Excel is still open in "Pending":

- The capture pauses when the tab is hidden. The decision for each scroll moved to a pure function
  (`decideStep`): with the tab hidden, or if it got hidden mid-step, a still scroll doesn't count, so
  it's never mistaken for the thousand-result wall. The crawl waits for `visibilitychange`, with no
  timers (Chrome throttles them in a hidden tab), and the popup shows "The capture of @account is
  paused: its tab was hidden" while it lasts.
- An x.com shape change gets noticed. `/ingest` counts the tweets the mapper doesn't understand
  (`rejected`); if they're more than half the batch, it flags `format_changed`, logs a truncated sample
  of the tweet and the extension stops the crawl with "x.com changed the data format: xport needs an
  update". What is understood gets stored and everything is counted in the cap.
- `/export` accepts `encoding` and `delimiter`, validated as in `/jobs` (a codec that exists, a single
  character), and the popup has the "For Excel in Spanish" checkbox, which is remembered.
- A single cap register. The data and the register moved to the fixed per-user folder (`XPORT_DATA_DIR`
  changes it, always absolute), and `.env` is read from the repo even if the CLI runs from another
  folder. Relative paths hang off that folder, and the ones in the old `.env.example` (`data/…`) point
  to the same thing as the defaults. The first time, whatever is in the repo's `data/` and in the
  current folder's is moved, only if it has a register: it's moved, not copied, and if the new folder
  already has a register, the old one's accesses are added to it and the old one is deleted. Tweet
  databases aren't merged: if there's already one, the old one stays where it was and a warning is
  shown. The service, before opening anything, looks for another xport on 8000 and 8080 (`/healthz`
  now says `service: xport`) and, if it finds one, doesn't start and explains why. Uvicorn runs the
  lifespan before opening its port, so whatever responds is always another process. The compose stays
  for CI and deployment.

**November 10: Phase 3, the queue and the local page.** All with tests; none of it has gone through a
browser yet:

- The queue lives in `queue.db`, in the data folder, and both the service and the CLI write to it, so
  whatever reads and decides goes in `BEGIN IMMEDIATE`. One capture per account; the same account and
  range already queued isn't repeated. A finished or stopped one doesn't change anymore, so a late
  notice from the extension doesn't revive it. `POST /captures/next` returns the one in progress or
  takes the next one (that's why it's POST and not GET), and with the cap full it doesn't start any
  (rule 1).
- The extension checks the queue with `alarms` every half minute, on startup and when the popup
  enqueues. The decision is a pure function (`decideQueue`): with no crawl it starts whichever the
  service says; with a crawl it continues as long as it's the current one, and if it was cancelled it
  stops. Where it starts from comes from `/progress`, as the popup did (`startPlan`). The content
  script reports the pause and the end, and waits for the response before deleting the crawl: that
  way the next round doesn't restart the one that just finished. It reuses the previous capture's tab
  if it's still on a capture search and, if it was closed, reopens it.
- The popup's "Capture ▶" now enqueues: there's a single path, and every capture exports and notifies
  when it finishes.
- When it finishes, the service exports the CSV with the capture's format and sends the notification;
  a stop notifies with its reason, unless the person did it. With not a single tweet in the range
  there's no CSV, and it says why that might be.
- The page is served at `/`, with no build. Requests that change something with an `Origin` that's
  neither the page's nor an extension's get 403; without `Origin` (the CLI, curl) they go through.
  nginx passes `Host` with the port, so the comparison works behind the compose.
- The cap gained a per-day summary (`access_daily`, in the audit database) that's written in the same
  transaction as each access, corrected with each `reconcile`, never pruned and never read by the cap.
  An old register builds it with whatever it has in the window, and moving an old `data/` also adds
  its history.
- The CLI has two commands: `extract` (the old one, now with a name) and `capture`.

**November 10, later: Phase 2, tested against x.com without a window.** With Chrome Beta 158 on Linux,
a dedicated profile in the data folder of the account running the test, and a separate X account. The
extension was loaded via `--remote-debugging-pipe` and the network was watched through the same
channel, without touching the page. What came out is in Phase 2, below. The most serious thing was a
crawl bug: X treats the `until:` day as INCLUDED, so narrowing to `until:<oldest day>` didn't advance
when that day was the oldest, and the crawl reloaded the same search endlessly. I saw it happen: it
burned X's 50 searches for the window and kept going with a 429 every four seconds. `nextUntil` now
always lowers the bound (if it doesn't go down, it moves to the previous day), and `startPlan`'s
resume asks up to the oldest day, not up to the next one. The same month that used to loop finished
in full in 11 searches.

**November 10, night: Phase 4, the crawl.** In the code and with tests: vitest for `decideRate`,
`nextBound`, `startPlan` and `waitStep`, and pytest for the `waiting` state, the new column in an old
queue and `/progress` with the time. What changes is in items 11 and 12, below. Also:

- June's "ceiling of about a thousand results per search" matches exactly 50 searches of 20 tweets,
  X's quota for 15 minutes: it may never have been a search ceiling but the rate limit. The crawl
  doesn't need to know: on a 429 it waits, and if a search settles before `since` without a 429, it
  narrows to the second and continues.
- The queue gains the `resume_at` column; a Phase 3 `queue.db` gets it when opened. The queue's
  `IN (…)` clauses are built from the length of the state tuples, which now have one more.
- The test against x.com was left half done. To find a day with more than a thousand tweets I probed
  the first page of several accounts, one probe per account: @AmazonHelp was doing about 170 tweets
  per hour at 14 UTC on Monday the 9th, @RailwaySeva and @JioCare about 70 by day and 10 by night
  (exactly a thousand per day), @ANI about 600 per day, and bots that reply to mentions don't show up
  in search. What's probed goes in through `/ingest` and moves the resume front (the oldest stored),
  so before capturing you have to delete that account's data. The @AmazonHelp capture of the 9th
  never got to scroll: Chrome ran the service worker from the previous test, stored in the profile's
  cache (the search went out with `since:`/`until:`), and bumping the manifest version wasn't enough:
  the profile's `Service Worker` folder had to be moved aside. It's a matter of loading the extension
  through the pipe into a profile that already had it; "Reload" in `chrome://extensions` registers the
  service worker again. In between, the machine ran out of memory and the test got cut once. With the
  new background, a day of @nasa worked up to the narrowing: the search went out with
  `since_time:`/`until_time:` (x.com doesn't rewrite it, not even with both bounds at midnight), it
  resumed at the oldest stored tweet plus one second and lowered the bound to the second of the oldest
  loaded one. At that point X sent the separate account to `x.com/account/access`, with a Cloudflare
  challenge, and the test was left waiting for a person to verify it (detecting that is item 13).
- A capture in progress when the extension is updated leaves its crawl in the old format, and the
  browser restores the tab with the old search: there the new crawl doesn't start and the capture got
  stuck (it happened in the test). The background asks the capture's tab whether it stayed on an old
  search for the same account (`isStaleSearch`) and, if so, takes it to the current one. If the person
  uses that tab for something else on x.com, it leaves it alone.

**November 10, night, afterwards: Phase 4 against x.com.** Bautista verified the separate account in
a window and the test continued. Since the machine was short on memory and a background process gets
cut on its own, the browser ran in ten-minute stretches: each stretch closed and reopened it, and the
capture continued from what was left. November 9 of @AmazonHelp came out in full in 53 minutes: 2,830
tweets, all 24 hours, the longest gap 8 minutes at 23:29. The CSV has 17: the rest are replies to other
people's conversations. Along the way:

- The first search brought 880 tweets (44 pages) without stopping; what cut it was the quota. Another
  sign that June's "thousand-result ceiling" was the rate limit.
- With fewer than 10 searches left in the quota, the pace dropped on its own to one page every 80
  seconds. On reaching zero, the capture went to "waiting for X" with the reset time, the tab moved to
  the local page and, once the time passed, went back to the search. It happened three times.
- A day of @nasa finished on its own: the search up to the second of the oldest tweet brought nothing
  more and the capture closed with its CSV in 49 seconds.
- The restarts showed two bugs, already fixed. On reopening the search it asked again from the top,
  because the progress made scrolling within a search isn't kept in the crawl state: now the
  background lowers the bound to the oldest stored tweet (`resumeBound`) when reopening the tab or
  when a wait ends. And the browser restores the capture's tab while the background opens another,
  so two tabs crawled the same thing: now each tab claims the capture before starting (`claim`) and
  only one crawls.
- The cap counted 4,145 accesses over the whole test, including the probes and what was requested
  again.

**November 10, night: Phase 5, the service's own browser.** I wanted to capture all my tweets and the
queue wouldn't start: the extension in my Chrome still had the November 6 crawl, from before the queue,
and `decideQueue` let it continue forever even though it had no tab. Now a pre-queue crawl gives way to
the first capture that shows up. And since the idea was not having to touch anything, I built item 13:
the service brings up its own Chrome with an xport profile, loads the extension into it through the
debugging pipe and leaves a marker in its storage; `POST /captures/next` only hands work to whoever
shows that marker, so the extension in my everyday browser doesn't open tabs. When testing it against
x.com, X answered 403 to everything: since that afternoon it rejects any request presenting itself as
`HeadlessChrome` (with `curl` and that user agent, 403; with Chrome's, 200). I didn't disguise it: I
moved to the plan B that was already decided, the same Chrome with a window inside cage with no real
display, and there X sees it as a regular Chrome and the page is "visible". The separate account's
cookie in `.env` no longer worked (the search bounces to the login, which now lives at
`/i/jf/onboarding`): the service notices, clears the profile's cookies and opens a small window to log
in once. With my account, that small window in app mode and with the pipe wouldn't get past "Continue";
it became a regular Chrome window, without the pipe, and the service learns about the session from the
profile's cookie database. Once logged in, the capture took in one page and stalled: Chrome was still
running the extension's service worker from before Phase 4, saved in the profile under the same
version. Now the service clears that cache every time it starts its Chrome.

## Where it stands today

It works, and I've used it against real x.com: in June and on November 6 in Chrome Beta, already with
that early morning's stops in the code. That time it worked end to end: the host brought up the
service, the batches went in and the cap counted every post seen. On November 10, without a window and
with the queue, I tested the stops (the service down mid-capture, the popup without the service and the
cap full) and they worked. Phase 1 (November 7) still has no manual test: the pause with the tab hidden,
the stop on format change and the CSV opened in an Excel in Spanish have tests, but not a manual test.
Same for Phase 3 (November 10): the queue, the local page and the notifications have tests and a smoke
test against the service, but haven't gone through a browser. Phase 4 (November 10), waiting for X on
the rate limit and the per-second search, was tested against x.com: a day of @AmazonHelp with 2,830
tweets came out in full, hitting X's search limit three times. Since the night of November 10, on
Linux, the service captures with its own Chrome inside cage, with no window in sight (Phase 5). The
tests run offline, mypy in strict mode and ruff pass, and the Alpine image and the extension build in
CI.

Server-side scraping with twscrape is implemented and tested offline, but it's still blocked by the
x.com change: it fails closed with a clear error. The official API is implemented for whoever has a
paid key, but it was never tested against the real API.

## Done

What's in the code, where, and how far it's tested. "Real" means against x.com, with a real account.

| What | Where | Tested |
|---|---|---|
| 900,000-access cap: sliding window, counts accesses without deduplicating, reserves before requesting, separate and atomic register across processes | `compliance/` | tests (blocking, waiting, `BEGIN IMMEDIATE`); real: counted every post seen (Nov 6) |
| Strict `Tweet` model (UTC, clean ids and URLs, quoted complete or nothing) | `domain/models.py` | tests |
| Mapper: retweets out, quotes in their three places, full text of long tweets, links with no destination or self-links out, quoted link on x.com | `mappers/twscrape_mapper.py` | tests; real (Nov 6) |
| Replies: own threads yes, other people's conversations no, applied at export and counted in `excluded_replies` | `storage/`, `POST /export` | tests; real |
| Storage: dedupe by id keeping the latest version, lowercase handles, checkpoint per sub-window | `storage/sqlite_store.py` | tests |
| Streaming, atomic CSV, `<account>_<since>_<until>.csv` | `storage/csv_exporter.py` | tests; real |
| In-page capture: `fetch` and XHR patch, bridge, relay in batches of 20 pages | `extension/entrypoints/` | real (June and Nov 6) |
| Adaptive crawl: scroll, "Retry", reload with backoff, range narrowing to the second of the oldest tweet, resume from the database | `autoscroll.content.ts`, `POST /progress` | real; pure functions with vitest |
| Crawl stops: no service, cap full, 429, `over_cap`, rejection, twenty unrecorded pages | `lib/ingest.ts`, `background.ts`, popup | vitest for `decideIngest`; real without a window (Nov 10): the service down, the popup without the service and the cap full |
| Per-second search in UTC (`since_time:`/`until_time:`): the bound drops to the second of the oldest tweet and the crawl ends; no saturated days and no lost first hours | `lib/autoscroll.ts` (`nextBound`), `lib/queue.ts` (`startPlan`), `POST /progress` | vitest and tests; real (Nov 10): a day of 2,830 tweets came out in full, all 24 hours |
| Crawl pause with the tab hidden, with a notice in the popup | `lib/autoscroll.ts` (`decideStep`), `autoscroll.content.ts` | vitest for `decideStep`; **no manual test** |
| Waiting for X: on a 429 or with no quota, the capture waits for the reset outside the search and continues on its own; with few searches left, it spreads them out | `lib/rate.ts`, `capture-main.content.ts`, `autoscroll.content.ts`, `background.ts`, queue `waiting` state | vitest for `decideRate` and `waitStep`; queue tests; real (Nov 10): three waits for the reset, each on its own and with the tab on the local page |
| Browser restart mid-capture: resumes from what's stored, not from the top, and crawls a single tab | `background.ts` (`withFrontier`, `claim`), `lib/queue.ts` (`resumeBound`, `claimAllowed`) | vitest; real (Nov 10): each stretch of the test restarted the browser |
| x.com shape change: `rejected` and `format_changed` in `/ingest`, sample in the log, crawl stop | `service/ingest.py`, `lib/ingest.ts` | tests with a page of unknown shape; vitest |
| CSV for Excel in Spanish from `/export` and the popup | `service/schemas.py`, popup | tests (BOM and `;`); **not opened in Excel** |
| A single cap register: per-user data folder, the repo's `.env`, moving `data/`, doesn't start if another xport is running | `config.py`, `datadir.py`, `service/app.py` | tests (the CLI from two folders, the move, merging two registers, the second service) |
| Capture queue: one per account, one at a time, persistent; the extension takes it with `alarms`; the popup and `tweet-extractor capture` enqueue | `storage/queue.py`, `service/captures.py`, `lib/queue.ts`, `background.ts` | tests (two accounts in two CSVs, states, restart); vitest; **not exercised in a browser** |
| Local page at `/`, with `Origin` check | `service/page/`, `service/app.py` | tests and smoke test; **not opened in a browser** |
| Export and notification on finish; notice if the range has no tweets | `service/captures.py`, `service/notify.py` | tests with a fake notifier; **notifications not tested by hand** |
| The service's own browser: Chrome with an xport profile inside cage, the extension loaded through the debugging pipe, the queue only for it, a small window to log in to X when needed, closed when the queue is empty | `service/browser.py`, `service/captures.py`, `lib/api.ts`, `background.ts` | tests with a fake Chrome; real (Nov 10): loads the extension, the marker, the virtual display, the bounce to login and the window |
| A pre-queue crawl no longer blocks it | `lib/queue.ts` (`decideQueue`) | vitest; real (Nov 10): it was what blocked my capture |
| Daily cap summary, separate from the window | `compliance/gate.py` | tests (reserve and reconcile, pruning, old register, move) |
| Delete an account's captured data without touching the cap | `POST /reset`, popup | tests; real |
| Autostart via native messaging: Firefox, Chrome, Chrome Beta and Chromium, fixed id in Chrome | `deploy/native-host/` | real in Chrome Beta (Nov 6); Windows with WSL untested |
| The service only serves requests to `localhost` or `127.0.0.1` | `service/app.py` | tests |
| Server-side twscrape, with sub-windows and stop after three empty pages | `providers/twscrape_provider.py`, `providers/_twscrape_gql.py` | offline tests; **blocked by x.com** since Jun 14 |
| Paid official API v2, full-archive search | `providers/official_api.py`, `mappers/api_v2_mapper.py` | fabricated responses; **never against the real API** |
| CLI and service on the same orchestrator; in-memory jobs | `cli.py`, `orchestrator.py`, `service/` | tests |
| Alpine image with nginx, extension build in Podman, CI | `deploy/`, `.github/workflows/ci.yml` | CI |
| README in Spanish and English, GitHub Pages page, generated logo and icons | `README.md`, `README.en.md`, `docs/` | by eye |

## Capturing without the browser in front

Today the capture needs the search tab visible. The crawl (`autoscroll.content.ts`) scrolls with
`window.scrollTo`, waits 1.2 s with `setTimeout` and checks whether the page grew; the one that asks for
more tweets is x.com itself when it sees the scroll. With the tab hidden (another tab in front or the
window minimized) the browser stops rendering the page, and the scroll events and observers a page uses
to load more depend on that rendering. Besides, Chrome drops a hidden tab's timers to one per second
and, after five minutes, to one per minute. The expected outcome is that x.com stops loading.

If that happens, the crawl doesn't stay still: it gets it wrong. Four scrolls without growth (about
5 s) look to it like the thousand-result wall, so it narrows the range and reloads. Each reload brings a
single page, and on a day with more than one page `nextUntil` can't advance while including it, so it
excludes it and the rest of that day is lost without notice. Since "Capture" opens the search in a new
tab of the current window, just going back to the previous tab is enough for this to happen.

### November 7 decision: an invisible Chrome driven by the service

There were two paths: open the capture in its own unfocused window, or a separate browser that runs
without a window. The window is the easiest to program, but it doesn't do what I'm after. Chrome on
Windows and macOS marks as hidden a window that another one fully covers, so with any maximized program
on top the capture stalls; with the fix from item 1 it pauses and notifies, but it doesn't advance. For
someone who knows nothing about this, "keep that window visible" is a technical rule they have to
understand and remember. The invisible browser doesn't depend on what the person does with their
windows and is the same on all three systems. What calls the shots is the next step: a comfortable
installer for Windows, macOS and Linux, and using it shouldn't require knowing anything technical.

How it ends up:

- **The service drives the browser, not the other way around.** Today the everyday browser brings up
  the service via native messaging. It flips: the service starts and stops a Chrome (or Edge, which
  ships with every Windows) with its own xport profile, with no window while capturing. Capturing no
  longer needs the native host, nor the per-browser manifests the installer would have to write.
- **The extension lives only in that profile.** It captures and crawls as today, with `/ingest`, the
  cap and the mappers unchanged at their core, but it takes work from the service's queue instead of
  the popup.
- **The interface moves to a local page** served by the same service at `http://localhost:8000/` and
  opened in the usual browser: one or more accounts, the dates, the queue with its progress, the cap
  and the CSVs to download. The popup stays while the page isn't there and then goes away. Since any
  site can send requests to `localhost`, the ones that change something require the `Origin` to be the
  page's own, on top of the `Host` check that's already there.
- **When a person is needed, the profile opens with a window.** The first time, to log in to X (a
  separate account is a good idea), and every time X asks for verification or closes the session: the
  service detects it, stops the invisible browser, opens that same profile in a small window and the
  page explains what to do. Then it hides again.
- **The data goes to a fixed per-user folder:** `~/.local/share/xport` on Linux, `%LOCALAPPDATA%\xport`
  on Windows and `~/Library/Application Support/xport` on macOS, with a variable to change it. Today
  paths are relative to the folder it's started from (`data/…`), which also splits the cap register
  (item 4).
- **It doesn't clash with the rules.** It's not Selenium or Playwright and there's no remote control of
  the browser: it's the person's Chrome with the extension, and nothing goes into the service image
  (rule 5). Everything seen goes through `/ingest` and the cap (rule 1).

Before building on top of it, it has to be tested (item 6), and if it fails, what to do is already
decided:

- That X works normally with Chrome in `--headless`: that the search loads, that scrolling brings more
  and that the captures go in. Headless mode presents itself as `HeadlessChrome` and X could treat it
  differently. I'm not disguising it as another browser: if X rejects it, on Linux the same Chrome runs
  with a window inside a virtual display (Xvfb), and on Windows and macOS what's left is the own
  unfocused window, with the pause that notifies. The architecture (the service driving the profile,
  the queue and the local page) is the same in all three cases; only how the browser is launched
  changes.
- That an extension installed in the profile loads in `--headless`, in Chrome and in Edge.

What this leaves solved for the installer, which is the next step and isn't part of this
implementation:

- Branded Chrome doesn't accept `--load-extension` since version 137, and loading an unpacked extension
  requires developer mode, which is no good for someone without technical knowledge. The extension gets
  published unlisted on the Chrome Web Store, which works for Chrome and Edge, and on AMO for Firefox.
  The installer leaves it installed in the xport profile or opens its page for a single click. If the
  Web Store rejects it, the alternative is for the installer to download Chrome for Testing, which does
  accept `--load-extension`.
- The service is packaged for each system without requiring uv or Python, with an "xport" shortcut that
  starts it and opens the page.
- Chrome or Edge is required; on Windows, Edge is always there. Firefox as the capture browser is left
  for later: it can be done, with the signed extension and its own profile, but it doesn't go first.

## Pending

Everything below has been decided since November 7: the four bugs from the review get fixed and all the
improvements go in. It's organized in phases, in this order, and each phase fits in one or two
sessions. Each item says why, how, when it's done and how much it takes (small: an afternoon; medium: a
couple of days).

### Phase 1: the four bugs (done on November 7)

It's in the code and with tests (see "How I built it"). The numbers continue, because the rest of the
plan cites them. What's missing is what needs a real browser or a real Excel:

1. **Pause the crawl with the tab hidden.** Missing the manual test: switch tabs mid-capture, see the
   notice in the popup, come back and check in `/progress` that the range stayed whole.
2. **Make an x.com shape change noticeable.** Done; the test uses a page of unknown shape. If someday
   the response envelope changes and not the tweet, `/ingest` sees zero tweets and can't tell it apart
   from an empty page: that's not covered.
3. **Excel in Spanish.** Missing: open a CSV exported with the checkbox in an Excel set up in Spanish.
4. **A single cap register.** Done. An xport from before this change doesn't say `service` in
   `/healthz`, so an old service still running isn't detected: after updating you have to bring it
   down (with Chrome, the native host keeps it alive until restart).

### Phase 2: what's left to test (done on November 10)

Tested on Linux with Chrome Beta 158, without a window, a separate X account and the Phase 3 service
(see "How I built it"). The numbers continue, because the rest of the plan cites them.

5. **The stops in a browser.** All three work:
   - With the service down mid-capture, the crawl went on for 18 more searches and stopped on its own
     after collecting 20 unrecorded pages. When the service came back, the queue resumed the capture
     from what was stored.
   - The popup without the service doesn't start and explains why.
   - With the register preloaded to 10 accesses from the cap, the first page crossed it (it ended at
     900,016, as expected for what already happened in the browser), the crawl stopped with the cap
     notice and the queue didn't start any other.

   An edge case: whatever the extension has in memory unsent is lost if the browser closes in that
   second and a half, and those accesses don't reach the cap. It's a few pages at most; I didn't fix it.
6. **The invisible browser and the two assumptions.**
   - **It works without a window.** The search loads, scrolling brings more and the captures go in:
     September 2025 of @nasa, 54 tweets, in 11 searches. With a window inside a virtual display (`cage`
     with no real display, Linux's plan B) it also works. Headless is chosen. Edge wasn't installed;
     Windows and macOS remain untested.
   - **The extension doesn't install with `--load-extension`:** Chrome 158 ignores it, as the November
     7 decision said. `Extensions.loadUnpacked` via `--remote-debugging-pipe` (with
     `--enable-unsafe-extension-debugging`) loads it for that run, without saving it in the profile.
     It's an alternative to publishing on the Web Store for the browser the service drives, but it uses
     the debugging channel: it's left to decide in item 13.
   - **`x-rate-limit-*` arrives** on `SearchTimeline`, which is same-origin (x.com), so the page can
     read it: **50 searches every 15 minutes** per X account, and when exhausted, 429 with `remaining`
     at 0 (item 11).
   - **`since_time:` and `until_time:` work** in "Latest", to the second and in UTC (item 12).
   - **`since:` and `until:` aren't what xport assumed.** X takes them as days in the searcher's time
     zone (UTC−3 in the test) and includes the `until:` day. Measured with @Reuters: `since:2026-11-06`
     brings nothing before 03:00 UTC on the 6th, and `until:2026-11-07` goes up to 02:59 UTC on the 8th.
     Three consequences: the first (UTC) hours of the start day are never requested and are lost
     without notice; an extra day is requested at the top, which the export discards but spends
     accesses; and the crawl could loop, already fixed (see "How I built it"). The first is fixed by
     moving the search to `since_time:`/`until_time:` (item 12).
   - **The range stays whole:** the @nasa month came out the same both times it was captured (54
     tweets) and reached the first tweet in the range. I didn't compare it tweet by tweet against a
     capture with a window.

### Phase 3: the queue and the local page (done on November 10)

It's in the code and with tests (see "How I built it"). The numbers continue, because the rest of the
plan cites them. What's missing is running it through a browser and what each item says:

7. **Capture queue.** Done: `POST /captures`, `GET /captures`, `POST /captures/next` and
   `POST /captures/{id}/state`, in `queue.db`, and `tweet-extractor capture`; the popup's "Capture ▶"
   also enqueues. Missing the manual test: two accounts queued from the page that come out in two CSVs
   without touching anything in between. If the queue's tab is closed, the background reopens it; if
   the person uses it for something else, the capture stays in progress without advancing until they
   cancel it (the invisible browser solves this, item 13). The "waiting for X" state arrived with
   item 11.
8. **The local page.** Done (`service/page/`, no build), with the `Origin` check, inclusive dates with
   shortcuts, per-capture progress and the cap with its daily summary. Missing: open it in a real
   browser. The popup's "Captured" counter stays as it was, until the popup goes away.
9. **Notify when it finishes and export on its own.** Done in the service (`service/notify.py`): on
   Linux with `notify-send`, where the click opens the CSV; on Windows with a PowerShell toast, and on
   macOS with `osascript`, without click. None was tested by hand, and the Windows and macOS ones never
   even ran.
10. **Nonexistent, suspended or protected account.** The first part is there: a whole range without
    tweets ends with "No tweets found: does the account exist, is it public, and did it post in those
    dates?". The second part is missing: capture `UserByScreenName` (the search doesn't request it; the
    profile has to be opened) and map it in `mappers/` to tell the three cases apart. The tests need
    real x.com responses, so it goes together with the item 6 test. Medium.

### Phase 4: the crawl (done on November 10)

It's in the code, with tests, and tested against x.com (see "How I built it"). The numbers continue,
because the rest of the plan cites them.

11. **Wait for X instead of insisting.** Done: the `fetch` and XHR patch passes the status and the
    `x-rate-limit-*` of each `SearchTimeline` response separately (HTTP, not GraphQL: rule 4 holds), and
    `decideRate` (`lib/rate.ts`) decides. On a 429, or with the quota at zero, the crawl saves how far it
    got and until when it waits: the reset plus 15 s, and at least one minute after the 429, in case the
    local clock is ahead. The background moves the tab from the search to the local page, so x.com stops
    retrying, and takes it back to the search when the time passes (it checks on the queue round, every
    half minute). The capture shows "waiting for X until 18:40" (`waiting`, with `resume_at`) in the
    queue, the page and the popup, and isn't notified: it continues on its own. With fewer than 10
    searches left in the quota, the crawl spreads the remaining ones until the reset, with a wait of up
    to two minutes per response. Tested: the capture of a day of @AmazonHelp exhausted the quota three
    times, waited for the reset each time and continued on its own. With the pace spread out, X hardly
    gets to answer 429: the crawl sees the quota at zero first, and waits anyway.
12. **Split saturated days by hour.** Done, and to the second: the search becomes `from:<cuenta>
    since_time:<s> until_time:<s>`, in UTC seconds, and when it settles before `since` the bound drops
    to the second of the oldest loaded tweet plus one: it re-includes it, so no other tweet from the same
    second is lost, whether `until_time:` is exclusive or not. If a search brings nothing older than that
    tweet, the range is done (with a "Retry" visible, on the other hand, it reloads). `/progress` returns
    the time of the oldest one and no longer `oldest_count`, and the resume starts there. The 800
    threshold and the day skipping are gone, and with them the first (UTC) hours of the start day that
    used to be lost. Tested: November 9 of @AmazonHelp came out in full, 2,830 tweets with all 24 hours
    covered, and the capture finished on its own when the search up to 00:00:32 brought nothing older.

### Phase 5: the invisible browser (done on November 10, on Linux)

13. **The service drives the xport profile.** Done like this: the service looks for Chrome, Chromium or
    Edge and cage; with captures in the queue, it starts the browser inside cage (X rejects `--headless`
    with a 403), loads the extension with `Extensions.loadUnpacked` through the pipe and leaves the
    `xport:runner` marker with `Extensions.setStorageItems` from its service worker. Every minute it
    checks the session: if the cookie is missing or a tab ended up on X's login, it clears the cookies
    and opens the profile in a regular Chrome window, without the pipe or any special flag, until the
    session cookie shows up in the profile's cookie database (a small window in app mode and with the
    pipe wouldn't let a real account finish logging in); if a tab stays on an
    error page twice in a row, it restarts it, and on the third failure it hands the queue back to the
    extension in the usual browser. Without cage (Windows, macOS, or Linux without it installed) it's not
    used, and capture continues as before. Missing: Windows and macOS (plan B's own unfocused window),
    and removing the native host and the popup once the installer is there. What the plan said: How: it
    starts and stops Chrome or Edge with `--user-data-dir=<datos>/browser`, without a window while there
    are captures in the queue (item 6 chose headless; the extension is loaded through the debugging pipe
    or installed from the Web Store, to be decided). It opens it in a small, unfocused window
    (`windows.create({ focused: false })`) to log in, when X asks for verification or closes the session
    (it notices because the search redirects to login or because no pages come in), and as plan B where
    windowless mode doesn't work. The native host and the popup stay during the transition and are then
    removed. Done when: a capture requested from the page finishes with no window in sight, and if X
    closes the session the window to log in appears. When it's there, the repo's `CLAUDE.md` and the
    README switch to describing this architecture. Medium to large.

### Phase 6: the installer (after this implementation)

14. **Installers for Windows, macOS and Linux**, with what the decision above leaves solved: the service
    packaged without uv or Python, an "xport" shortcut and the xport profile set up on first launch.
15. **Publish the extension unlisted** on the Chrome Web Store (works for Chrome and Edge) and sign it
    unlisted on AMO (`web-ext sign --channel=unlisted`), so Chrome doesn't ask for developer mode and
    Firefox doesn't remove it on close. I'll get the accounts for both stores myself.

### What depends on others

16. **Test the official API against the real API**, with a paid key (I don't have one): that
    `tweet.fields` still works, that long posts bring `note_tweet` and that the rate limit is the
    documented one.
17. **If twscrape gets unblocked**, check against real data that the search date filter really bounds
    results, that the cursor paginates and ends, that the response shape matches the one in the tests
    and that the accesses per page don't exceed the reservation. If the real shape is surprising, check
    that walking the envelope doesn't take a tweet nested inside a quote as a top-level one. Every now
    and then, see whether a release came out that computes `x-client-transaction-id` again.

### Questions that were open, now decided

- **Register history:** a per-day summary, separate from the window (item 8).
- **Saturated days:** there are none anymore: the search goes to the second and narrows to the oldest
  loaded tweet (item 12).
- **State of the service's jobs:** `/jobs` isn't persisted while no server-side provider works; the
  queue is (item 7).
- **What counts as a "link to a document":** every external URL. Filtering later in the spreadsheet is
  easy, and filtering beforehand loses data.
- **Deleted or protected account:** it's reported and the queue continues (item 10).
- **How many accounts in parallel:** one, in line (item 7).
- **A Windows browser with the service in WSL:** stays as it is and untested. With a Windows installer
  it stops making sense.

## How to pick it up on another machine

```bash
git clone https://github.com/bafif/xport.git
cd xport
uv sync                  # creates .venv from uv.lock
cp .env.example .env     # only for twscrape or the official API
uv run pytest -q
```

Captured data doesn't travel with the repo: it's in the data folder (`~/.local/share/xport` on Linux).
These don't travel through git and get regenerated: `.env` (with the cookies of a separate account),
`.venv` (`uv sync`) and `extension/node_modules`, `extension/dist` and `extension/.wxt` (the build with
Podman or `npm ci && npm run build` inside `extension/`).