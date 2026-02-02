import json
import os
import platform
from dataclasses import dataclass, asdict
from typing import Optional, Tuple, Dict

APP_NAME = "overleaf_sync"

@dataclass
class Config:
    base_dir: str
    sync_interval: str = "1h"  # one of: 1h, 12h, 24h
    count: int = 10
    browser: str = "safari"  # safari|firefox
    profile: Optional[str] = None
    host: str = "www.overleaf.com"
    git_helper: bool = True
    cookies: Optional[Dict[str, str]] = None


def _mac_paths() -> Tuple[str, str, str]:
    home = os.path.expanduser("~")
    support = os.path.join(home, "Library", "Application Support", APP_NAME)
    logs = os.path.join(home, "Library", "Logs", APP_NAME)
    caches = os.path.join(home, "Library", "Caches", APP_NAME)
    return support, logs, caches


def _linux_paths() -> Tuple[str, str, str]:
    home = os.path.expanduser("~")
    config_home = os.environ.get("XDG_CONFIG_HOME", os.path.join(home, ".config"))
    state_home = os.environ.get("XDG_STATE_HOME", os.path.join(home, ".local", "state"))
    cache_home = os.environ.get("XDG_CACHE_HOME", os.path.join(home, ".cache"))
    support = os.path.join(config_home, APP_NAME)
    logs = os.path.join(state_home, APP_NAME, "logs")
    caches = os.path.join(cache_home, APP_NAME)
    return support, logs, caches


def get_app_paths() -> Tuple[str, str, str]:
    if platform.system() == "Darwin":
        return _mac_paths()
    return _linux_paths()


def get_config_path() -> str:
    support, _, _ = get_app_paths()
    os.makedirs(support, exist_ok=True)
    return os.path.join(support, "config.json")


def get_logs_dir() -> str:
    _, logs, _ = get_app_paths()
    os.makedirs(logs, exist_ok=True)
    return logs


def get_cache_dir() -> str:
    _, _, caches = get_app_paths()
    os.makedirs(caches, exist_ok=True)
    return caches


def load_config() -> Optional[Config]:
    cfg_path = get_config_path()
    if not os.path.exists(cfg_path):
        return None
    with open(cfg_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return Config(**data)


def save_config(cfg: Config) -> None:
    cfg_path = get_config_path()
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(asdict(cfg), f, indent=2)


def default_base_dir() -> str:
    home = os.path.expanduser("~")
    if platform.system() == "Darwin":
        return os.path.join(home, "Documents", "Overleaf")
    # Linux default
    return os.path.join(home, "Overleaf")


def prompt_first_run() -> Config:
    print("First-time setup for Overleaf Sync")
    # Base directory
    default_dir = default_base_dir()
    base_dir = input(f"Base directory to clone projects into [{default_dir}]: ").strip() or default_dir
    os.makedirs(base_dir, exist_ok=True)

    # Interval
    interval_options = {"1": "1h", "2": "12h", "3": "24h"}
    print("Select sync interval:")
    print("  1) 1 hour (default)")
    print("  2) 12 hours")
    print("  3) 24 hours")
    interval_choice = input("Choice [1/2/3]: ").strip() or "1"
    sync_interval = interval_options.get(interval_choice, "1h")

    # Count
    count_in = input("Number of latest projects to sync [10]: ").strip()
    try:
        count = int(count_in) if count_in else 10
    except ValueError:
        count = 10

    # Browser default
    browser_default = "safari" if platform.system() == "Darwin" else "firefox"
    browser_in = input(f"Browser to read Overleaf cookies from [safari|firefox] (default {browser_default}): ").strip().lower()
    if browser_in not in ("safari", "firefox", ""):
        browser = browser_default
    else:
        browser = browser_in or browser_default

    # Host
    host_in = input("Overleaf host [www.overleaf.com]: ").strip()
    host = host_in or "www.overleaf.com"

    # Git helper
    git_helper_ans = input("Enable OS Git credential helper? [Y/n]: ").strip().lower()
    git_helper = (git_helper_ans != "n")

    # Optional: paste Overleaf cookies (JSON map or Cookie header)
    print("Optional: paste Overleaf cookies to avoid browser access (press Enter to skip).")
    cookie_in = input("Cookies (JSON map or 'name=value; name2=value2'): ").strip()
    cookies: Optional[Dict[str, str]] = None
    if cookie_in:
        from .cookies import parse_cookie_string
        try:
            cookies = parse_cookie_string(cookie_in)
        except Exception:
            try:
                # Try JSON
                import json as _json
                data = _json.loads(cookie_in)
                if isinstance(data, dict):
                    cookies = {str(k): str(v) for k, v in data.items()}
            except Exception:
                cookies = None

    cfg = Config(
        base_dir=base_dir,
        sync_interval=sync_interval,
        count=count,
        browser=browser,
        profile=None,
        host=host,
        git_helper=git_helper,
        cookies=cookies,
    )
    save_config(cfg)
    print(f"Saved config to {get_config_path()}")
    return cfg
