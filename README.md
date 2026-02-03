# Overleaf Pull-Only Sync CLI
![PyPI - Version](https://img.shields.io/pypi/v/overleaf-pull)

This project introduces a small background worker that keeps and up-to-date copy your the last active overleaf projects offline (by default the last 10). There's also a handy manual sync command to pull the updates manually. This project is not an automatic two-way merge! It works by pulling via the git-integration, making it save from accidentially pushing work in progress.

**For Users — Quick Start**
Install and setup
```bash
pip install 'overleaf-pull[qt]'
overleaf-pull init --install
```

Manual sync and status
```bash
overleaf-pull run-once   # full sync now
overleaf-pull status     # health + last run + timers
```

Customize (optional)
```bash
# Scheduler cadence and mode (dynamic by default)
overleaf-pull set-interval 30m
overleaf-pull install-scheduler --mode dynamic   # or --mode full

# Other knobs
overleaf-pull set-count 20
overleaf-pull set-base-dir /path/to/Overleaf
overleaf-pull set-git-token   # set once to avoid prompts
```

Overview
- Pull-only tool that periodically clones/pulls your latest Overleaf projects into a local directory.
- Discovers projects via cookies captured during setup (Qt login recommended) or manual paste; Firefox cookies can be read automatically.
- Lists projects via PyOverleaf; syncs using Git.
- Runs in the background as a macOS LaunchAgent or Linux systemd user timer.

Requirements
- macOS or Linux with Git installed.
- Python 3.10+.
- Dependencies are installed automatically via pip. Optional extras: `[qt]` for PySide6 (Qt login).
- Overleaf Git integration enabled on your account to allow cloning/pulling via git.overleaf.com.

Details for Users

Install
```bash
pip install overleaf-pull
# Optional Qt login support:
pip install 'overleaf-pull[qt]'
```

Setup Wizard
```bash
overleaf-pull init --install
```
 - Prompts for the base directory, interval (30m/1h/12h/24h), count (default 10), browser/profile, and host (default www.overleaf.com).
- Offers a Qt browser login to capture cookies automatically (default Yes if PySide6 is installed). Falls back to optional manual cookie paste.
- Prompts for your Overleaf Git authentication token (required for cloning/pulling and background runs). It will offer to open Overleaf in your browser to fetch it.
- Installs a background job (LaunchAgent on macOS, systemd user timer on Linux).
- Runs a validation sync before installing the scheduler, to confirm access.

Manual Sync
Run once now:
```bash
overleaf-pull run-once
```
 - Runs a full sync of the latest projects (not dynamic).
- Manual sync (with optional overrides):
```bash
overleaf-pull sync --count 5 --base-dir ~/Overleaf --browser firefox
```
- Store or clear cookies in config:
- Folder naming preference:
```bash
overleaf-pull set-name-suffix off   # Use display name only
overleaf-pull set-name-suffix on    # Default: append a short ID to avoid collisions
```
This affects the local folder names only; project display names on Overleaf remain unchanged.
```bash
overleaf-pull set-cookie "name=value; other=value2"
overleaf-pull clear-cookie
```
- Browser-assisted cookie capture (like olbrowserlogin):
```bash
overleaf-pull browser-login
# This opens Overleaf in your browser and guides you to copy document.cookie.
```
Required cookies
- At minimum: `overleaf_session2` and `GCLB` must be present in your Cookie header for authenticated requests.
- document.cookie cannot see HttpOnly cookies; copy the full Cookie header from the Network tab for a request to your Overleaf host.

Qt browser login (optional)
- Use a built-in Qt browser to log in and auto-capture cookies.
```bash
pip install 'overleaf-pull[qt]'
overleaf-pull browser-login-qt
```
During setup, if PySide6 is present, the tool offers the Qt login flow by default.

Git authentication token
- Overleaf requires a Git auth token for `git clone`/`git pull`.
- Generate a token in your Overleaf account (see the Git integration/authentication tokens page or the Git instructions shown in your project UI), then set it:
```bash
overleaf-pull set-git-token
# Paste your token when prompted

# Clear it if needed
overleaf-pull clear-git-token
```
- With a token set, the tool will use URLs like `https://git:<TOKEN>@git.overleaf.com/<PROJECT_ID>` automatically.
Status
```bash
overleaf-pull status
```
 - Shows a summary of repo health and the last background run.
 - Displays per-project timers indicating when each is next due.
 - Prints the current scheduler configuration: interval and mode (dynamic/full).
Customize
- Install or remove background job:
```bash
overleaf-pull install-scheduler
overleaf-pull uninstall-scheduler
```
Scheduler modes
- The scheduler supports two modes:
	- dynamic: Runs only projects that are due based on per-project backoff (min 30m, doubles until 24h; resets to 30m on changes).
	- full: Always runs a full sync on each trigger.
- Examples:
```bash
# 30-minute dynamic cadence
overleaf-pull set-interval 30m
overleaf-pull install-scheduler --mode dynamic

# 30-minute full cadence
overleaf-pull set-interval 30m
overleaf-pull install-scheduler --mode full
```
Installing the scheduler is idempotent: it uninstalls any existing instance first, then reinstalls to ensure only one scheduler is active.
**For Developers**

Publish to PyPI (CI)
- This repo includes a GitHub Actions workflow that publishes on tags `v*` using PyPI Trusted Publishers (OIDC).
- Trigger a release:
```bash
git tag v0.1.0
git push origin v0.1.0
```
- The workflow builds sdist/wheel and publishes without storing secrets.
- Adjust interval or latest count:
```bash
overleaf-pull set-interval 30m
overleaf-pull set-count 20
```
- Change base directory:
```bash
overleaf-pull set-base-dir /path/to/Overleaf
```

macOS Logs
- Logs: ~/Library/Logs/overleaf_pull/runner.log

Linux Logs
- `journalctl --user -u overleaf-pull.timer -u overleaf-pull.service`
- And ~/.local/state/overleaf_pull/logs/ if configured.

Notes
- This tool is pull-only; it never pushes to Overleaf.
- Safari cookie access may require permissions; Firefox is often more reliable for unattended use.
- If Safari access fails, paste Overleaf cookies once via `set-cookie` to avoid elevated access.
- Use Git credential helpers for smooth pulls:
```bash
git config --global credential.helper osxkeychain   # macOS
git config --global credential.helper libsecret     # Linux
```

Background runs
- To avoid interactive Git prompts in schedulers, set an Overleaf Git token once:
```bash
overleaf-pull set-git-token
```
- Without a token, new clones will fail with 403; existing repos may also fail if their remotes lack the token. Prompts are disabled in background.

Dynamic scheduling (per-project backoff)
- The background worker runs on a frequent cadence (e.g., every 30 minutes).
- Each project has its own timer with exponential backoff:
	- Minimum interval: 30 minutes. Doubles on no changes, up to 24 hours.
	- Resets to 30 minutes when changes are detected and pulled.
- The `status` command shows the next-due times for the latest projects.

Development
- Create a local environment and install from source:
```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
# Optional Qt support
pip install PySide6
```
- Conda alternative:
```bash
conda env create -f environment.yml
conda activate overleaf-sync
pip install -e .
```

