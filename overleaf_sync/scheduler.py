import os
import platform
import subprocess
from .config import get_app_paths, get_config_path

LAUNCHAGENTS_DIR = os.path.expanduser("~/Library/LaunchAgents")
SYSTEMD_USER_DIR = os.path.expanduser("~/.config/systemd/user")

PLIST_LABEL = "com.overleaf.sync"
SERVICE_NAME = "overleaf-sync.service"
TIMER_NAME = "overleaf-sync.timer"


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=False, capture_output=True, text=True)


def _python_exec() -> str:
    return os.environ.get("PYTHON", "python3")


def _cli_entry() -> list[str]:
    # Use module invocation to avoid packaging complexities
    return [_python_exec(), "-m", "overleaf_sync.cli", "run-once"]


def install_macos_launchagent(interval: str):
    os.makedirs(LAUNCHAGENTS_DIR, exist_ok=True)
    start_interval = {"1h": 3600, "12h": 43200, "24h": 86400}.get(interval, 3600)
    support, logs_dir, _ = get_app_paths()
    os.makedirs(logs_dir, exist_ok=True)
    stdout = os.path.join(logs_dir, "runner.log")
    stderr = os.path.join(logs_dir, "runner.err.log")

    args = _cli_entry()
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
    _run(["launchctl", "unload", "-w", plist_path])
    res = _run(["launchctl", "load", "-w", plist_path])
    if res.returncode == 0:
        print(f"Installed LaunchAgent at {plist_path}")
    else:
        print(f"Failed to load LaunchAgent: {res.stderr}")


def uninstall_macos_launchagent():
    plist_path = os.path.join(LAUNCHAGENTS_DIR, f"{PLIST_LABEL}.plist")
    _run(["launchctl", "unload", "-w", plist_path])
    if os.path.exists(plist_path):
        os.remove(plist_path)
        print(f"Removed {plist_path}")


def install_systemd_user(interval: str):
    os.makedirs(SYSTEMD_USER_DIR, exist_ok=True)
    args = " ".join(_cli_entry())
    service = f"""
[Unit]
Description=Overleaf Sync pull-only job

[Service]
Type=oneshot
ExecStart={args}
"""
    if interval == "1h":
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
    _run(["systemctl", "--user", "disable", "--now", TIMER_NAME])
    res = _run(["systemctl", "--user", "enable", "--now", TIMER_NAME])
    if res.returncode == 0:
        print(f"Installed systemd user timer at {timer_path}")
    else:
        print(f"Failed to enable timer: {res.stderr}")


def uninstall_systemd_user():
    _run(["systemctl", "--user", "disable", "--now", TIMER_NAME])
    service_path = os.path.join(SYSTEMD_USER_DIR, SERVICE_NAME)
    timer_path = os.path.join(SYSTEMD_USER_DIR, TIMER_NAME)
    for p in (service_path, timer_path):
        if os.path.exists(p):
            os.remove(p)
            print(f"Removed {p}")
