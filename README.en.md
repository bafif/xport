<div align="center">

<img src="docs/img/logo.svg" alt="" width="88">

# xport

**The tweets of any X account, between two dates, in a CSV.**<br>
[Español](README.md) · [Website](https://bafif.github.io/xport/en/) · [Install](#install) · [How to use it](docs/USAGE.en.md)

</div>

Type one or more accounts and a date range. xport goes through X's search for you and leaves you one CSV per account,
with the date, the full text, the links and the quoted tweet. It uses a regular X session: no API key, nothing to pay.

<p align="center"><img src="docs/img/pagina.en.png" width="560" alt="xport's page: the form with the accounts and
the dates, a capture running at 54% of the range, two queued, one done with its CSV to download, and the 24-hour cap's
usage."></p>

## Why xport

- **Several accounts at once.** Add them on xport's page; they go into a queue and come out one by one, each with its
  CSV and a notification when it is done.
- **No windows in the way.** On Linux, xport captures with its own Chrome on an invisible screen: you keep using your
  computer as usual.
- **Long ranges, complete.** X stops each search at about a thousand results and limits how many you can run. xport
  carries on from the last tweet it saw, waits when X asks it to and resumes by itself, even if you close everything.
- **It never crosses X's cap.** X's terms penalize reading more than a million posts in 24 hours. xport counts every
  post it sees and stops at 900,000. That cap is fixed in the code: no setting raises it.
- **Your data stays on your computer.** The service only listens on your machine and sends nothing to any server.
- **Ready for Excel.** UTF-8, a checkbox for an Excel set to Spanish, and the interface in English or Spanish.

## What the CSV has

| Column | What it holds |
|---|---|
| `account` | the account's handle, lowercase |
| `created_at` | the tweet's date and time, ISO 8601 in UTC |
| `content` | the full text, long tweets included |
| `links` | the tweet's external URLs, separated by spaces |
| `quoted_tweet_id`, `quoted_tweet_url` | the quoted tweet, if any |

Quote tweets and the account's own threads are in. Retweets and replies in other people's conversations are left out.

## Install

You need Google Chrome (or Chromium or Microsoft Edge). It is better to capture with an X account other than your own.

### With the installer

Download the one for your system from the [latest release](https://github.com/bafif/xport/releases/latest). It brings
everything xport needs, its own Python included.

| System | File | How to install it |
|---|---|---|
| Linux | `xport-<version>-linux-x86_64.tar.gz` | `tar -xzf xport-*-linux-x86_64.tar.gz && ./xport/install.sh` |
| Windows | `xport-<version>-windows-x86_64.zip` | unzip it and double-click `xport\install.cmd` |
| macOS | `xport-<version>-macos-arm64.tar.gz` | open it and drag `xport.app` to Applications |

On Linux you also need [cage](https://github.com/cage-kiosk/cage), the invisible screen xport's Chrome runs in:
`sudo apt install cage` on Debian and Ubuntu, `sudo pacman -S cage` on Arch. The installer tells you if it is missing.

On Windows and macOS there is one more step: loading xport's extension in your Chrome, once
([how](docs/INSTALL.en.md#the-extension-in-your-browser)). If the release you downloaded has no installer for your
system, it takes a few commands to build one ([how](docs/INSTALL.en.md#building-the-installer)).

### On Linux, from the terminal

With [uv](https://docs.astral.sh/uv/) and Node 22 installed:

```sh
git clone https://github.com/bafif/xport && cd xport
(cd extension && npm ci && npm run build:chrome)
uv run python deploy/bundle/build.py
tar -xzf dist/bundle/xport-*-linux-x86_64.tar.gz -C dist/bundle && dist/bundle/xport/install.sh
```

It ends up in the applications menu and as the `xport` command.

## How to use it

1. **Open xport** from the applications menu (or with `xport` in a terminal). Its page opens in your browser.
2. **The first time**, «Log in to X» opens a window to sign in to X. You only do it once.
3. **Type the accounts and the dates** and press «Capture». Each account sends a notification when its CSV is ready,
   and the link stays on the page.

All the details, with the extension's popup and what each message means, are in
[How to use it](docs/USAGE.en.md).

## Status

- **Linux:** tried end to end against x.com, with the installer and the windowless capture.
- **Windows and macOS:** the installer builds and starts in continuous integration, but it has not been tried on
  those systems yet. There the capture is done by the extension in your Chrome, with the X tab in view.
- **Without a browser:** with X's official API, which is paid, it is implemented but has not been tried against the
  real API. With a regular account it does not work: x.com changed how it signs requests.

## More documentation

- [Install](docs/INSTALL.en.md): each system in detail, building the installer, updating and uninstalling.
- [How to use it](docs/USAGE.en.md): the page, the popup, X's limits and what each message means.
- [Reference](docs/REFERENCE.en.md): the command line, the official API, the service's API and the configuration.
- [How it is built](docs/ARCHITECTURE.en.md): the layers, the design decisions, the tests and how to develop.
- [Plan](docs/PLAN.en.md): the project's rules and what is left.

## License

[MIT](LICENSE).
