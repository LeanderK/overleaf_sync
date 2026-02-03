import os
import platform
import subprocess
import sys
from .config import get_app_paths, get_config_path

LAUNCHAGENTS_DIR = os.path.expanduser("~/Library/LaunchAgents")
SYSTEMD_USER_DIR = os.path.expanduser("~/.config/systemd/user")

# Renamed project: use 'overleaf-pull' for identifiers
PLIST_LABEL = "com.overleaf.pull"
SERVICE_NAME = "overleaf-pull.service"
TIMER_NAME = "overleaf-pull.timer"


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=False, capture_output=True, text=True)


def _python_exec() -> str:
    # Prefer the currently running interpreter (venv/conda-safe)
    exe = sys.executable
    if exe:
        return exe
    # Fallback to env override or system default
    return os.environ.get("PYTHON", "python3")

def _console_script_path() -> str | None:
    """Return absolute path to the 'overleaf-pull' console script if present.

    Looks next to the current Python executable (e.g., venv/bin on POSIX).
    """
    try:
        bindir = os.path.dirname(_python_exec())
        candidate = os.path.join(bindir, "overleaf-pull")
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
        candidate_exe = os.path.join(bindir, "overleaf-pull.exe")
        if os.path.isfile(candidate_exe) and os.access(candidate_exe, os.X_OK):
            return candidate_exe
    except Exception:
        return None
    return None


def _cli_entry(mode: str = "dynamic") -> list[str]:
    """Return ProgramArguments for the scheduler.

    mode: 'dynamic' runs selective due projects; 'full' runs a full sync.
    """
    script = _console_script_path()
    if script:
        if mode == "full":
            return [script, "sync"]
        return [script, "run-once-dynamic"]
    if mode == "full":
        return [_python_exec(), "-m", "overleaf_pull.cli", "sync"]
    return [_python_exec(), "-m", "overleaf_pull.cli", "run-once-dynamic"]

def _quote_for_systemd(arg: str) -> str:
    """Basic quoting for systemd ExecStart to handle spaces in paths."""
    if not arg:
        return "\"\""
    if any(c.isspace() for c in arg):
        return '"' + arg.replace('"', '\\"') + '"'
    return arg


def install_macos_launchagent(interval: str, mode: str = "dynamic"):
    os.makedirs(LAUNCHAGENTS_DIR, exist_ok=True)
    start_interval = {"30m": 1800, "1h": 3600, "12h": 43200, "24h": 86400}.get(interval, 3600)
    support, logs_dir, _ = get_app_paths()
    os.makedirs(logs_dir, exist_ok=True)
    stdout = os.path.join(logs_dir, "runner.log")
    stderr = os.path.join(logs_dir, "runner.err.log")

    args = _cli_entry(mode)
    program_arguments_xml = "\n".join([f"\t\t<string>{a}</string>" for a in args])

    plist = f"""
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key>
    <string>{PLIST_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
{program_arguments_xml}
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>StartInterval</key>
    <integer>{start_interval}</integer>
    <key>StandardOutPath</key>
    <string>{stdout}</string>
    <key>StandardErrorPath</key>
    <string>{stderr}</string>
  </dict>
</plist>
"""
    plist_path = os.path.join(LAUNCHAGENTS_DIR, f"{PLIST_LABEL}.plist")
    with open(plist_path, "w", encoding="utf-8") as f:
        f.write(plist)
    # Unload any existing agent under the new label, and also attempt to remove the legacy label
    _run(["launchctl", "unload", "-w", plist_path])
    legacy_plist = os.path.join(LAUNCHAGENTS_DIR, "com.overleaf.sync.plist")
    if os.path.exists(legacy_plist):
        _run(["launchctl", "unload", "-w", legacy_plist])
    res = _run(["launchctl", "load", "-w", plist_path])
    if res.returncode == 0:
        print(f"Installed LaunchAgent at {plist_path}")
    else:
        print(f"Failed to load LaunchAgent: {res.stderr}")


def uninstall_macos_launchagent():
    # Remove current label
    plist_path = os.path.join(LAUNCHAGENTS_DIR, f"{PLIST_LABEL}.plist")
    _run(["launchctl", "unload", "-w", plist_path])
    if os.path.exists(plist_path):
        os.remove(plist_path)
        print(f"Removed {plist_path}")
    # Also remove legacy label if present
    legacy_plist = os.path.join(LAUNCHAGENTS_DIR, "com.overleaf.sync.plist")
    if os.path.exists(legacy_plist):
        _run(["launchctl", "unload", "-w", legacy_plist])
        os.remove(legacy_plist)
        print(f"Removed {legacy_plist}")


def install_systemd_user(interval: str, mode: str = "dynamic"):
    os.makedirs(SYSTEMD_USER_DIR, exist_ok=True)
    args = " ".join(_quote_for_systemd(a) for a in _cli_entry(mode))
    service = f"""
[Unit]
Description=Overleaf Sync pull-only job

[Service]
Type=oneshot
ExecStart={args}
"""
    if interval == "30m":
        on_calendar = "*-*-* *:00,30:00"
    elif interval == "1h":
        on_calendar = "hourly"
    elif interval == "12h":
        on_calendar = "*-*-* 00,12:00:00"
    else:
        on_calendar = "daily"

    timer = f"""
[Unit]
Description=Run Overleaf Sync periodically ({interval})

[Timer]
OnCalendar={on_calendar}
Persistent=true
RandomizedDelaySec=300

[Install]
WantedBy=timers.target
"""
    service_path = os.path.join(SYSTEMD_USER_DIR, SERVICE_NAME)
    timer_path = os.path.join(SYSTEMD_USER_DIR, TIMER_NAME)
    with open(service_path, "w", encoding="utf-8") as f:
        f.write(service)
    with open(timer_path, "w", encoding="utf-8") as f:
        f.write(timer)

    _run(["systemctl", "--user", "daemon-reload"])
    # Disable legacy timer if it exists
    _run(["systemctl", "--user", "disable", "--now", "overleaf-sync.timer"])
    # Disable current timer prior to enabling fresh
    _run(["systemctl", "--user", "disable", "--now", TIMER_NAME])
    res = _run(["systemctl", "--user", "enable", "--now", TIMER_NAME])
    if res.returncode == 0:
        print(f"Installed systemd user timer at {timer_path}")
    else:
        print(f"Failed to enable timer: {res.stderr}")


def uninstall_systemd_user():
    # Disable current timer
    _run(["systemctl", "--user", "disable", "--now", TIMER_NAME])
    # Also disable legacy timer if present
    _run(["systemctl", "--user", "disable", "--now", "overleaf-sync.timer"])
    # Remove current unit files
    service_path = os.path.join(SYSTEMD_USER_DIR, SERVICE_NAME)
    timer_path = os.path.join(SYSTEMD_USER_DIR, TIMER_NAME)
    for p in (service_path, timer_path):
        if os.path.exists(p):
            os.remove(p)
            print(f"Removed {p}")
    # Remove legacy unit files
    legacy_service = os.path.join(SYSTEMD_USER_DIR, "overleaf-sync.service")
    legacy_timer = os.path.join(SYSTEMD_USER_DIR, "overleaf-sync.timer")
    for p in (legacy_service, legacy_timer):
        if os.path.exists(p):
            os.remove(p)
            print(f"Removed {p}")
