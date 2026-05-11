import asyncio
import time
from typing import Optional

try:
    from desktop_notifier import DesktopNotifier
except Exception:  # pragma: no cover
    DesktopNotifier = None  # type: ignore

_APP_NAME = "overleaf-pull"
_LAST_SENT: dict[str, float] = {}


def _normalize_text(exc: Exception, context: str = "") -> str:
    base = f"{type(exc).__name__}: {exc}"
    if context:
        return f"{context} | {base}".lower()
    return base.lower()


def _throttled(key: str, cooldown_sec: int = 900) -> bool:
    now = time.time()
    last = _LAST_SENT.get(key, 0.0)
    if (now - last) < cooldown_sec:
        return True
    _LAST_SENT[key] = now
    return False


def _contains_any(text: str, needles: list[str]) -> bool:
    return any(n in text for n in needles)


def _is_auth_failure(text: str) -> bool:
    auth_needles = [
        "authentication failed",
        "authorization failed",
        "unauthorized",
        "forbidden",
        "http 401",
        "http 403",
        " 401",
        " 403",
        "invalid token",
        "expired token",
        "access denied",
        "permission denied",
        "overleaf_session2",
        "missing cookie",
        "cookie",
        "login failed",
        "git clone failed",
        "git pull failed",
    ]
    return _contains_any(text, auth_needles)


def _is_api_compat_failure(text: str) -> bool:
    api_needles = [
        "pyoverleaf",
        "object has no attribute",
        "unexpected keyword",
        "unexpected response",
        "attributeerror",
        "keyerror",
        "typeerror",
        "login_from_cookies",
        "get_projects",
    ]
    return _contains_any(text, api_needles)


def send_notification(title: str, message: str, key: Optional[str] = None) -> None:
    """Send a desktop notification if supported; never raise on failure."""
    if DesktopNotifier is None:
        return
    dedupe_key = key or title
    if _throttled(dedupe_key):
        return

    try:
        notifier = DesktopNotifier(app_name=_APP_NAME)
        coro = notifier.send(title=title, message=message)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(coro)
        else:
            loop.create_task(coro)
    except Exception:
        # Notifications should never break sync behavior.
        pass


def notify_sync_failure(exc: Exception, context: str = "") -> None:
    text = _normalize_text(exc, context)
    if _is_auth_failure(text):
        send_notification(
            title="Overleaf Sync: Authentication Failed",
            message="Your Overleaf token/cookies may be expired. Run set-git-token or browser-login-qt.",
            key="auth-failure",
        )
        return

    if _is_api_compat_failure(text):
        send_notification(
            title="Overleaf Sync: API Compatibility Issue",
            message="Overleaf or pyoverleaf API likely changed. Upgrade pyoverleaf and retry sync.",
            key="api-compat-failure",
        )
        return

    send_notification(
        title="Overleaf Sync: Failure",
        message="A sync run failed. Check logs with overleaf-pull status for details.",
        key="generic-sync-failure",
    )
