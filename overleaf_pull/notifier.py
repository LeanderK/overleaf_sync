import asyncio
import sys
import time
from datetime import datetime
from typing import Optional

from .config import get_logs_dir

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


def _now_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _append_app_log(line: str) -> None:
    try:
        logs_dir = get_logs_dir()
        with open(f"{logs_dir}/app.log", "a", encoding="utf-8") as lf:
            lf.write(line + "\n")
    except Exception:
        pass


def _throttled(key: str, cooldown_sec: int = 900) -> bool:
    now = time.time()
    last = _LAST_SENT.get(key, 0.0)
    if (now - last) < cooldown_sec:
        return True
    _LAST_SENT[key] = now
    return False


def _contains_any(text: str, needles: list[str]) -> bool:
    return any(n in text for n in needles)


def _action_title(context: str) -> str:
    lowered_context = context.lower().strip()
    if "pull" in lowered_context:
        return "Overleaf Sync: Pull Failed"
    if "clone" in lowered_context:
        return "Overleaf Sync: Clone Failed"
    if "prune" in lowered_context:
        return "Overleaf Sync: Prune Failed"
    if "list projects" in lowered_context:
        return "Overleaf Sync: List Projects Failed"
    return "Overleaf Sync: Failure"


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
        "overleaf_session2",
        "missing cookie",
        "cookie",
        "login failed",
        "please ensure that you are logged into overleaf in your browser and that your session is valid",
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


def _is_offline_failure(text: str) -> bool:
    offline_needles = [
        "no internet",
        "no internet connectivity",
        "network is unreachable",
        "temporary failure in name resolution",
        "name or service not known",
        "could not resolve host",
        "failed to establish a new connection",
        "connection timed out",
        "timed out",
        "connection refused",
        "connectivity",
        "offline",
    ]
    return _contains_any(text, offline_needles)


def _failure_payload(exc: Exception, context: str = "") -> tuple[str, str, str]:
    text = _normalize_text(exc, context)
    if _is_offline_failure(text):
        return (
            "Overleaf Sync: Offline",
            "no internet connection",
            "offline-failure",
        )
    if _is_api_compat_failure(text):
        return (
            "Overleaf Sync: API Compatibility Issue",
            "Overleaf or pyoverleaf API likely changed",
            "api-compat-failure",
        )

    title = _action_title(context)
    if _is_auth_failure(text):
        return (
            title,
            "authentication/token issue",
            "auth-failure",
        )

    if title == "Overleaf Sync: Pull Failed":
        return (
            title,
            "git pull failed",
            "git-pull-failure",
        )
    if title == "Overleaf Sync: Clone Failed":
        return (
            title,
            "git clone failed",
            "git-clone-failure",
        )
    if title == "Overleaf Sync: Prune Failed":
        return (
            title,
            "could not safely remove an outdated repo",
            "prune-failure",
        )
    if title == "Overleaf Sync: List Projects Failed":
        return (
            title,
            "could not fetch project list",
            "list-projects-failure",
        )

    return (
        title,
        "A sync run failed",
        "generic-sync-failure",
    )


def _short_reason(exc: Exception, context: str = "") -> str:
    text = _normalize_text(exc, context)
    if _is_offline_failure(text):
        return "no internet connection"
    if _is_auth_failure(text):
        return "authentication/token issue"
    if _is_api_compat_failure(text):
        return "API compatibility issue"
    if "pull" in context.lower():
        return "git pull failed"
    if "clone" in context.lower():
        return "git clone failed"
    if "prune" in context.lower():
        return "prune was unsafe"
    return str(exc).strip() or type(exc).__name__


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
    report_sync_failure(exc, context=context, cli=False, desktop=True)


def report_sync_failure(
    exc: Exception,
    context: str = "",
    *,
    cli: bool = True,
    desktop: bool = False,
) -> None:
    title, message, key = _failure_payload(exc, context)
    stamp = _now_stamp()
    reason = _short_reason(exc, context)
    details_text = _normalize_text(exc, context)
    log_line = f"[{stamp}] {title}: {reason}"
    if context:
        log_line += f" | {context}"
    log_line += f" | {details_text}"
    _append_app_log(log_line)
    if cli:
        try:
            details = details_text
        except Exception:
            details = f"{type(exc).__name__}: {exc}"
        print(f"[{stamp}] {title}: {message}", file=sys.stderr)
        print(f"Error Details: {details}", file=sys.stderr)
    if desktop and key != "offline-failure":
        send_notification(title=title, message=f"{stamp}: {message}", key=key)
