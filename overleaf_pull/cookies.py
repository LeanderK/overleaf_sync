import os
import shutil
import tempfile
from pathlib import Path
from typing import Dict, List

try:
    import browser_cookie3  # type: ignore
except Exception:  # pragma: no cover
    browser_cookie3 = None

OVERLEAF_DOMAINS = ["overleaf.com", ".overleaf.com", "www.overleaf.com"]


def _flatpak_firefox_cookie_file(profile: str | None = None) -> Path | None:
    root = Path.home() / ".var/app/org.mozilla.firefox/.mozilla/firefox"
    cookie_files = list(root.glob("*/cookies.sqlite"))
    if profile:
        selected = root / profile / "cookies.sqlite"
        if selected.is_file():
            return selected
    if len(cookie_files) == 1:
        return cookie_files[0]
    return None


def _to_cookie_dict(cookies: List[dict]) -> Dict[str, str]:
    jar: Dict[str, str] = {}
    for c in cookies:
        name = c.get("name") or c.get("Name")
        value = c.get("value") or c.get("Value")
        domain = c.get("domain") or c.get("Domain")
        if not name or value is None:
            continue
        if domain and not any(d in domain for d in OVERLEAF_DOMAINS):
            continue
        jar[name] = value
    return jar


def parse_cookie_string(s: str) -> Dict[str, str]:
    """Parse a Cookie header or simple `name=value; name2=value2` string into a dict.

    Accepts optional leading 'Cookie:' and trims whitespace.
    """
    s = s.strip()
    if s.lower().startswith("cookie:"):
        s = s.split(":", 1)[1].strip()
    jar: Dict[str, str] = {}
    parts = [p.strip() for p in s.split(";") if p.strip()]
    for part in parts:
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        jar[name.strip()] = value.strip()
    return jar


def load_overleaf_cookies(browser: str, profile: str | None = None) -> Dict[str, str]:
    """Load Overleaf cookies from the selected browser/profile.

    Returns a dict of cookie name -> value suitable for PyOverleaf Api.login_from_cookies.
    Note: Safari cookie loading via code is no longer supported; use Qt login or paste cookies.
    """
    if browser == "safari":
        raise RuntimeError(
            "Safari cookie access is not supported without Qt. Please run 'overleaf-pull browser-login-qt' or paste cookies via 'overleaf-pull set-cookie'."
        )
    elif browser == "firefox":
        if browser_cookie3 is None:
            raise RuntimeError("Firefox cookie access requires the 'browser-cookie3' package.")
        cookie_file = _flatpak_firefox_cookie_file(profile)
        if cookie_file is None:
            cj = browser_cookie3.firefox()
        else:
            cj = browser_cookie3.firefox(cookie_file=str(cookie_file))
        jar: Dict[str, str] = {}
        for c in cj:
            domain = getattr(c, "domain", None)
            if domain and not any(d in domain for d in OVERLEAF_DOMAINS):
                continue
            jar[c.name] = c.value
        return jar
    else:
        raise ValueError("Unsupported browser; choose 'firefox' or use Qt login.")


def save_refreshed_cookies(cfg, cookies: Dict[str, str]) -> None:
    from .config import save_config
    from .notifier import send_notification

    cfg.cookies = cookies
    save_config(cfg)
    send_notification(
        "Overleaf Sync: Firefox cookies refreshed",
        "The saved Overleaf cookie was expired; a working cookie was loaded from Firefox.",
        key="firefox-cookie-refresh",
    )
