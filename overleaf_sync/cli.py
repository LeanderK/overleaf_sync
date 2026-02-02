import argparse
import webbrowser
import os
import platform
import sys

from .config import load_config, prompt_first_run, save_config, Config, get_logs_dir
from .sync import run_sync_once, run_sync
from .scheduler import install_macos_launchagent, uninstall_macos_launchagent, install_systemd_user, uninstall_systemd_user
from .olbrowser_login import login_via_qt


def cmd_init(args):
    cfg = load_config()
    if cfg is None:
        cfg = prompt_first_run()
    else:
        print("Config already exists; run with --reset to reconfigure.")
    # Optionally install scheduler
    if args.install:
        install_scheduler(cfg)


def install_scheduler(cfg: Config):
    os_name = platform.system()
    interval = cfg.sync_interval
    if os_name == "Darwin":
        install_macos_launchagent(interval)
    else:
        install_systemd_user(interval)


def cmd_install(args):
    cfg = load_config() or prompt_first_run()
    # Run a manual sync first to validate config and access
    try:
        print("Running a validation sync before installing scheduler...")
        run_sync(cfg)
    except Exception as e:
        print(f"Validation sync failed: {e}")
        print("Not installing scheduler. Fix the issue and retry.")
        return
    install_scheduler(cfg)


def cmd_uninstall(args):
    os_name = platform.system()
    if os_name == "Darwin":
        uninstall_macos_launchagent()
    else:
        uninstall_systemd_user()


def cmd_run_once(args):
    run_sync_once()
def cmd_sync(args):
    cfg = load_config() or prompt_first_run()
    # Apply one-off overrides
    if getattr(args, "count", None):
        cfg.count = args.count
    if getattr(args, "base_dir", None):
        cfg.base_dir = args.base_dir
    if getattr(args, "browser", None):
        cfg.browser = args.browser
    if getattr(args, "profile", None):
        cfg.profile = args.profile
    run_sync(cfg)



def cmd_set_interval(args):
    cfg = load_config() or prompt_first_run()
    val = args.interval
    if val not in ("1h", "12h", "24h"):
        print("Invalid interval; choose 1h, 12h, or 24h")
        sys.exit(2)
    cfg.sync_interval = val
    save_config(cfg)
    print("Updated interval; reinstall scheduler if needed.")


def cmd_set_count(args):
    cfg = load_config() or prompt_first_run()
    cfg.count = args.count
    save_config(cfg)
    print("Updated latest projects count.")


def cmd_set_base_dir(args):
    cfg = load_config() or prompt_first_run()
    cfg.base_dir = args.base_dir
    save_config(cfg)
    print("Updated base directory.")


def cmd_set_cookie(args):
    cfg = load_config() or prompt_first_run()
    value = args.value
    if not value:
        print("Paste cookie string, then press Ctrl-D (EOF):")
        try:
            value = sys.stdin.read()
        except KeyboardInterrupt:
            value = ""
    from .cookies import parse_cookie_string
    try:
        cfg.cookies = parse_cookie_string(value)
        save_config(cfg)
        missing = [k for k in ("overleaf_session2", "GCLB") if k not in cfg.cookies]
        print("Stored cookies in config.")
        if missing:
            print(f"Warning: missing expected cookie(s): {', '.join(missing)}. Make sure you copied the full Cookie header from the Network tab for a request to {cfg.host}.")
    except Exception as e:
        print(f"Failed to parse cookies: {e}")


def cmd_clear_cookie(args):
    cfg = load_config() or prompt_first_run()
    cfg.cookies = None
    save_config(cfg)
    print("Cleared stored cookies from config.")


def _tail(path: str, lines: int = 50) -> list[str]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.readlines()
        return content[-lines:]
    except Exception:
        return []


def cmd_status(args):
    logs_dir = get_logs_dir()
    app_log = os.path.join(logs_dir, "app.log")
    runner_log = os.path.join(logs_dir, "runner.log")
    runner_err = os.path.join(logs_dir, "runner.err.log")
    print("Status (last 50 lines):")
    for label, path in [("App", app_log), ("Runner", runner_log), ("RunnerErr", runner_err)]:
        if os.path.exists(path):
            print(f"--- {label}: {path} ---")
            print("".join(_tail(path)))
        else:
            print(f"--- {label}: {path} (missing) ---")


def cmd_browser_login(args):
    """Guide the user to obtain cookies via the browser (manual copy)."""
    cfg = load_config() or prompt_first_run()
    url = f"https://{cfg.host}/project"
    print("Opening Overleaf in your default browser. If not logged in, please log in.")
    try:
        webbrowser.open(url)
    except Exception:
        print(f"Please open {url} manually.")

    print("\nAfter login, copy your cookie string:")
    print("- Open Developer Tools → Network, select any request to your Overleaf host.")
    print("- Copy the full 'Cookie' header value (it includes HttpOnly cookies).")
    print("- document.cookie is insufficient; it misses HttpOnly cookies like overleaf_session2.")
    print("Paste cookie below and press Ctrl-D (EOF) when done:\n")
    try:
        value = sys.stdin.read()
    except KeyboardInterrupt:
        print("Aborted.")
        return
    if not value.strip():
        print("No cookie provided.")
        return
    from .cookies import parse_cookie_string
    try:
        cfg.cookies = parse_cookie_string(value)
        save_config(cfg)
        print("Stored cookies in config.")
        # Optional quick validation
        try:
            run_sync(cfg)
            print("Cookie validation succeeded (projects synced).")
        except Exception as e:
            print(f"Validation failed (will keep cookies saved): {e}")
    except Exception as e:
        print(f"Failed to parse cookies: {e}")


def cmd_browser_login_qt(args):
    """Open a Qt WebEngine window to login and capture cookies automatically."""
    cfg = load_config() or prompt_first_run()
    try:
        store = login_via_qt()
    except RuntimeError as e:
        print(str(e))
        return
    if not store:
        print("Login did not complete.")
        return
    # Store captured cookies and csrf
    cfg.cookies = store.get("cookie")
    save_config(cfg)
    missing = [k for k in ("overleaf_session2", "GCLB") if not cfg.cookies or k not in cfg.cookies]
    if missing:
        print(f"Warning: missing expected cookie(s): {', '.join(missing)}")
    print("Stored cookies from Qt browser login.")
    # Optional quick validation
    try:
        run_sync(cfg)
        print("Cookie validation succeeded (projects synced).")
    except Exception as e:
        print(f"Validation failed (will keep cookies saved): {e}")


def main():
    parser = argparse.ArgumentParser(prog="overleaf-sync", description="Pull-only Overleaf project sync")
    sub = parser.add_subparsers(dest="cmd")

    p_init = sub.add_parser("init", help="First-run setup and optional scheduler install")
    p_init.add_argument("--install", action="store_true", help="Install background scheduler after setup")
    p_init.set_defaults(func=cmd_init)

    p_install = sub.add_parser("install-scheduler", help="Install background scheduler (LaunchAgent/systemd)")
    p_install.set_defaults(func=cmd_install)

    p_uninstall = sub.add_parser("uninstall-scheduler", help="Uninstall background scheduler")
    p_uninstall.set_defaults(func=cmd_uninstall)

    p_run = sub.add_parser("run-once", help="Run a single pull-only sync now")
    p_run.set_defaults(func=cmd_run_once)

    p_sync = sub.add_parser("sync", help="Manual sync with optional overrides")
    p_sync.add_argument("--count", type=int, help="Override latest projects count for this run")
    p_sync.add_argument("--base-dir", help="Override base directory for this run")
    p_sync.add_argument("--browser", choices=["safari", "firefox"], help="Override browser for this run")
    p_sync.add_argument("--profile", help="Override profile for this run")
    p_sync.set_defaults(func=cmd_sync)

    p_si = sub.add_parser("set-interval", help="Set sync interval (1h|12h|24h)")
    p_si.add_argument("interval", choices=["1h", "12h", "24h"])
    p_si.set_defaults(func=cmd_set_interval)

    p_sc = sub.add_parser("set-count", help="Set latest projects count")
    p_sc.add_argument("count", type=int)
    p_sc.set_defaults(func=cmd_set_count)

    p_sb = sub.add_parser("set-base-dir", help="Set base directory for clones")
    p_sb.add_argument("base_dir")
    p_sb.set_defaults(func=cmd_set_base_dir)

    p_scook = sub.add_parser("set-cookie", help="Store Overleaf cookies in config (paste or pass string)")
    p_scook.add_argument("value", nargs="?", help="Cookie header or 'name=value; name2=value2' string")
    p_scook.set_defaults(func=cmd_set_cookie)

    p_ccook = sub.add_parser("clear-cookie", help="Clear stored cookies from config")
    p_ccook.set_defaults(func=cmd_clear_cookie)

    p_status = sub.add_parser("status", help="Show recent sync status from logs")
    p_status.set_defaults(func=cmd_status)

    p_blogin = sub.add_parser("browser-login", help="Open browser and guide you to copy cookies")
    p_blogin.set_defaults(func=cmd_browser_login)

    p_blogin_qt = sub.add_parser("browser-login-qt", help="Use a Qt browser to login and auto-capture cookies (requires PySide6)")
    p_blogin_qt.set_defaults(func=cmd_browser_login_qt)

    args = parser.parse_args()
    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(1)
    args.func(args)


if __name__ == "__main__":
    main()
