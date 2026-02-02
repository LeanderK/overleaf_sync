# Overleaf Pull-Only Sync CLI

Overview
- Pull-only tool that periodically clones/pulls your latest Overleaf projects into a local directory.
- Discovers projects via your browser cookies (Rookie) and lists them via PyOverleaf; syncs using Git.
- Runs in the background as a macOS LaunchAgent or Linux systemd user timer.

Requirements
- macOS or Linux with Git installed.
- Python 3.10+.
- Packages: rookiepy, pyoverleaf (installed via requirements.txt).
- Overleaf Git integration enabled on your account to allow cloning/pulling via git.overleaf.com.

Install
```bash
# # Using uv (recommended)
# uv currently not working!
# uv sync

# Or using conda
conda env create -f environment.yml
conda activate overleaf-sync

# Or using pip/venv
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

First Run (setup)
```bash
overleaf-pull init --install
# If the console script isn't found, use:
# uv currently not working!
# uv run python -m overleaf_sync.cli init --install
```
- Prompts for the base directory, interval (1h/12h/24h), count (default 10), browser/profile, and host (default www.overleaf.com).
- Offers a Qt browser login to capture cookies automatically (default Yes if PySide6 is installed). Falls back to optional manual cookie paste.
- Prompts for your Overleaf Git authentication token (required for cloning/pulling and background runs). It will offer to open Overleaf in your browser to fetch it.
- Installs a background job (LaunchAgent on macOS, systemd user timer on Linux).
- Runs a validation sync before installing the scheduler, to confirm access.

Manual Commands
- Run once now:
```bash
overleaf-pull run-once
# Or via uv:
uv run python -m overleaf_sync.cli run-once
```
- Manual sync (with optional overrides):
```bash
overleaf-pull sync --count 5 --base-dir ~/Overleaf --browser firefox
# Or via uv:
uv run python -m overleaf_sync.cli sync --count 5 --base-dir ~/Overleaf --browser firefox
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
- Conda (recommended on macOS/Linux):
```bash
conda activate overleaf-sync
conda install -c conda-forge pyside6
overleaf-pull browser-login-qt
```
- Pip/venv alternative:
```bash
pip install PySide6
python -m overleaf_sync.cli browser-login-qt
```
During setup, if PySide6 is present, the tool will offer the Qt login flow by default.

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
- Status from logs:
```bash
overleaf-pull status
```
- Install or remove background job:
```bash
overleaf-pull install-scheduler
overleaf-pull uninstall-scheduler
# Or via uv:
uv run python -m overleaf_sync.cli install-scheduler
uv run python -m overleaf_sync.cli uninstall-scheduler
```
Installing the scheduler is idempotent: it uninstalls any existing instance first, then reinstalls to ensure only one scheduler is active.
- Adjust interval or latest count:
```bash
overleaf-pull set-interval 12h
overleaf-pull set-count 20
```
- Change base directory:
```bash
overleaf-pull set-base-dir /path/to/Overleaf
```

macOS Logs
- Logs: ~/Library/Logs/overleaf_sync/runner.log

Linux Logs
- `journalctl --user -u overleaf-sync.timer -u overleaf-sync.service`
- And ~/.local/state/overleaf_sync/logs/ if configured.

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

