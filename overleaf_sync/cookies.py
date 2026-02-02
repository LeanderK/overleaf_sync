import os
import shutil
import tempfile
from typing import Dict, List

try:
    import browsercookie  # type: ignore
except Exception:  # pragma: no cover
    browsercookie = None

OVERLEAF_DOMAINS = ["overleaf.com", ".overleaf.com", "www.overleaf.com"]


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
        if browsercookie is None:
            raise RuntimeError("Firefox cookie access requires the 'browsercookie' package.")
        cj = browsercookie.firefox()
        jar: Dict[str, str] = {}
        for c in cj:
            domain = getattr(c, "domain", None)
            if domain and not any(d in domain for d in OVERLEAF_DOMAINS):
                continue
            jar[c.name] = c.value
        return jar
    else:
        raise ValueError("Unsupported browser; choose 'firefox' or use Qt login.")
