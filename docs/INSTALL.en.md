# Installing xport

[Español](INSTALL.md) · [Back to the README](../README.en.md)

## What you need

- **A browser:** Google Chrome, Chromium or Microsoft Edge. Firefox only works with the extension loaded by hand.
- **On Linux, [cage](https://github.com/cage-kiosk/cage):** the invisible screen xport's Chrome runs in. X does not
  serve Chrome in `--headless` mode, so xport opens it on a screen nobody sees. Install it with `sudo apt install cage`
  (Debian, Ubuntu), `sudo pacman -S cage` (Arch), `sudo dnf install cage` (Fedora) or `sudo apk add cage` (Alpine). Without cage xport still works,
  but the capture is done by the extension in your browser, with the tab in view.
- **An X account**, preferably not your own: X may limit accounts that download many tweets.

The installer brings its own Python: you do not need to install Python, uv or Node.

## With the installer

The packages are in the [GitHub releases](https://github.com/bafif/xport/releases/latest), one per system. If yours is
not there, [build it](#building-the-installer).

### Linux

```sh
tar -xzf xport-<version>-linux-x86_64.tar.gz
./xport/install.sh
```

On Alpine (and the other Linux with musl), the package is `xport-<version>-linux-x86_64-musl.tar.gz`; it installs the same way.

It copies xport to `~/.local/lib/xport`, adds it to the applications menu and as the `xport` command in
`~/.local/bin`, tells you if something is missing (with the command that installs it on your distribution) and opens
the page. It does not ask for a password.

### Windows

1. Unzip `xport-<version>-windows-x86_64.zip`.
2. Double-click `xport\install.cmd`. Since the package is not signed, Windows may warn you: «More info» → «Run
   anyway».
3. It goes to `%LOCALAPPDATA%\Programs\xport`, with an «xport» shortcut in the Start menu and on the desktop, and the
   page opens.
4. [Load the extension](#the-extension-in-your-browser) in Chrome or Edge.

### macOS

1. Open `xport-<version>-macos-arm64.tar.gz` (Finder unpacks it) and drag `xport.app` to Applications. The `arm64`
   package is for Apple Silicon Macs; for an Intel Mac, [build it](#building-the-installer) on that Mac.
2. Open xport. Since it is not signed, macOS stops it the first time: on macOS 15 or newer, go to System Settings →
   Privacy & Security and press «Open Anyway»; on earlier versions, right-click `xport.app` → «Open». An `xport.app`
   built on the same Mac skips this.
3. [Load the extension](#the-extension-in-your-browser) in Chrome.

### The first time

xport's page offers «Log in to X»: it opens a Chrome window with xport's profile so you can sign in with your X
account. You only do it once; if X ends the session later, the window shows up again by itself.

## The extension in your browser

On Linux with cage, xport uses its own Chrome and there is nothing to load. On Windows, on macOS and on Linux without
cage, the capture is done by xport's extension in your browser, which you load once:

1. Open `chrome://extensions` (in Edge, `edge://extensions`) and turn on «Developer mode».
2. «Load unpacked» and choose xport's `extension` folder:

   | System | Folder |
   |---|---|
   | Linux | `~/.local/lib/xport/extension` |
   | Windows | `%LOCALAPPDATA%\Programs\xport\extension` |
   | macOS | `/Applications/xport.app/Contents/Resources/xport/extension` |

   On macOS, in the folder picker, press ⌘⇧G and paste the path.

`xport check` (or `xport.cmd check` on Windows) shows the exact folder. While it captures, the X tab the extension
opens has to stay in view: if you cover it, the capture pauses and carries on when you come back.

**In Firefox:** download `xport-<version>-firefox.zip` from the same release, open `about:debugging` → «This
Firefox» → «Load Temporary Add-on…» and choose the zip. Firefox removes it when it closes: you have to load it every
time.

## Opening, closing and checking

The «xport» shortcut starts the service if it is not running and opens the page. From a terminal:

| Command | What it does |
|---|---|
| `xport` | starts the service and opens the page |
| `xport start` | starts the service without opening the page |
| `xport stop` | closes the service |
| `xport status` | tells whether it is running |
| `xport check` | tells whether everything it needs is there, and where your data is |

On Windows the command is `%LOCALAPPDATA%\Programs\xport\xport.cmd`; on macOS,
`/Applications/xport.app/Contents/MacOS/xport`.

## Updating and uninstalling

**Updating:** install the new version on top, just like the first time. Your data is not touched.

**Uninstalling:**

- Linux: `~/.local/lib/xport/uninstall.sh`.
- Windows: `%LOCALAPPDATA%\Programs\xport\uninstall.cmd`.
- macOS: `xport stop` and drag `xport.app` to the Trash.

Your data (the cap's ledger, the tweets, the CSVs and the X session) stays in your data folder, in case you
reinstall: `~/.local/share/xport` on Linux, `%LOCALAPPDATA%\xport` on Windows and
`~/Library/Application Support/xport` on macOS. To remove it, delete that folder.

## Building the installer

The package is built on the same system it is for, because some dependencies come compiled. You need git,
[uv](https://docs.astral.sh/uv/) and Node 22 or newer:

- Linux: with your distribution's package manager, or [fnm](https://github.com/Schniz/fnm) for Node.
- macOS: with [Homebrew](https://brew.sh), `brew install uv node`.
- Windows: `winget install astral-sh.uv`, `winget install OpenJS.NodeJS.LTS` and `winget install Git.Git`.

```sh
git clone https://github.com/bafif/xport
cd xport/extension
npm ci
npm run build:chrome
cd ..
uv run python deploy/bundle/build.py --smoke
```

The commands are the same on Linux, macOS and Windows (in PowerShell). `--smoke` tests the built package: it starts
its service on a free port, checks that it answers and closes it. On Linux, with Podman, the extension can also be
built with `bash deploy/extension-build.sh`, without Node.

The package ends up in `dist/bundle/`:

| System | Package | To install it |
|---|---|---|
| Linux (on Alpine, `…-linux-x86_64-musl.tar.gz`) | `xport-<version>-linux-x86_64.tar.gz` | `tar -xzf dist/bundle/xport-*-linux-*.tar.gz -C dist/bundle && dist/bundle/xport/install.sh` |
| Windows | `xport-<version>-windows-x86_64.zip` | unzip it and double-click `xport\install.cmd` |
| macOS | `xport-<version>-macos-<arch>.tar.gz` | `tar -xzf dist/bundle/xport-*-macos-*.tar.gz -C /Applications && open /Applications/xport.app` |

## From the source, for development

Without the installer, xport runs from the repository:

```sh
uv sync
bash deploy/extension-build.sh        # or: cd extension && npm ci && npm run build
uv run fastapi dev src/tweet_extractor/service/app.py
```

The service is at `http://localhost:8000`, which is also its page. The built extension is in
`extension/dist/chrome-mv3` (Chrome and Edge) and in `extension/dist/firefox-mv2` (Firefox: in `about:debugging`, pick
its `manifest.json`). The other ways to start the service, and the configuration, are in the
[reference](REFERENCE.en.md).

**Having the browser start the service (optional):** with the native messaging host, the browser starts the service
when it opens. Install it once with `bash deploy/native-host/install-linux.sh` (Firefox) or
`bash deploy/native-host/install-linux.sh --chrome` (Chrome or Chromium). With the installer you do not need it: the
shortcut does it. Details and diagnosis in [deploy/native-host/README.en.md](../deploy/native-host/README.en.md).

## If something does not work

- **`xport check`** tells you what is missing: the browser, cage or the extension, with the command to install it.
- **«couldn't reach the service»:** open xport from its shortcut, or with `xport`.
- **Another xport is already running:** the service does not start if there is one on `localhost:8000` or
  `localhost:8080`, so there are never two ledgers of the cap. `xport stop` closes the running one.
- More messages and what they mean, in [How to use it](USAGE.en.md#if-something-does-not-work).
