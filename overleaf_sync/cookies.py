import os
import shutil
import tempfile
from typing import Dict, List

try:
    from rookiepy import safari as rookie_safari
    from rookiepy import firefox as rookie_firefox
except Exception:  # pragma: no cover
    rookie_safari = None
    rookie_firefox = None

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
    """Load Overleaf cookies using Rookie from the selected browser/profile.

    Returns a dict of cookie name -> value suitable for PyOverleaf Api.login_from_cookies.
    """
    if browser == "safari":
        if rookie_safari is None:
            raise RuntimeError("Safari cookie access requires rookiepy; install with 'uv add rookiepy' or 'pip install rookiepy'. On macOS, granting Full Disk Access to your terminal may be required.")
        # Rookie handles Safari paths internally
        cookies = rookie_safari(OVERLEAF_DOMAINS)
        return _to_cookie_dict(cookies)
    elif browser == "firefox":
        if rookie_firefox is None:
            # Fallback to browsercookie for Firefox if rookiepy is unavailable
            if browsercookie is None:
                raise RuntimeError("Firefox cookie access requires rookiepy or browsercookie.")
            cj = browsercookie.firefox()
            jar: Dict[str, str] = {}
            for c in cj:
                domain = getattr(c, "domain", None)
                if domain and not any(d in domain for d in OVERLEAF_DOMAINS):
                    continue
                jar[c.name] = c.value
            return jar
        # Firefox may lock cookies.sqlite; Rookie generally reads via its own logic,
        # but if needed, we can copy the profile dir to a temp path.
        cookies = rookie_firefox(OVERLEAF_DOMAINS)
        return _to_cookie_dict(cookies)
    else:
        raise ValueError("Unsupported browser; choose 'safari' or 'firefox'.")
