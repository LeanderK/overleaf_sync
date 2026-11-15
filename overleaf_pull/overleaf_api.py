from typing import List, Dict, Any


def _is_auth_failure(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    if getattr(response, "status_code", None) in (401, 403):
        return True
    text = str(exc).lower()
    return any(marker in text for marker in ("401", "403", "unauthorized", "forbidden"))

# Attempt to import PyOverleaf Api with flexibility (module paths may vary)
try:
    from pyoverleaf.api import Api  # type: ignore
except Exception:  # pragma: no cover
    try:
        from pyoverleaf import Api  # type: ignore
    except Exception:
        Api = None  # type: ignore


def create_api(host: str = "www.overleaf.com"):
    if Api is None:
        raise RuntimeError("pyoverleaf not installed; install with 'pip install pyoverleaf'.")
    return Api(host=host)


def list_projects_sorted_by_last_updated(
    api,
    cookies: Dict[str, str],
    limit: int,
    *,
    fallback_cookies=None,
    on_cookie_refresh=None,
) -> List[Dict[str, Any]]:
    """Login with cookies and list projects, sorted by lastUpdated descending, limited to 'limit'."""
    api.login_from_cookies(cookies)
    try:
        projects = api.get_projects()
    except Exception as exc:
        if fallback_cookies is None or not _is_auth_failure(exc):
            raise
        try:
            cookies = fallback_cookies()
        except Exception as fallback_exc:
            raise RuntimeError(
                "Saved Overleaf cookie was rejected and Firefox cookies could not be loaded."
            ) from fallback_exc
        api.login_from_cookies(cookies)
        projects = api.get_projects()
        if on_cookie_refresh is not None:
            on_cookie_refresh(cookies)
    # Each project likely has attributes: id, name, lastUpdated
    def last_updated(p):
        # p may be a dict or an object; support both
        v = getattr(p, "lastUpdated", None)
        if v is None:
            v = p.get("lastUpdated") if isinstance(p, dict) else None
        return v or 0

    projects_sorted = sorted(projects, key=last_updated, reverse=True)
    result: List[Dict[str, Any]] = []
    for p in projects_sorted[:limit]:
        pid = getattr(p, "id", None) or (p.get("id") if isinstance(p, dict) else None)
        name = getattr(p, "name", None) or (p.get("name") if isinstance(p, dict) else None)
        lu = getattr(p, "lastUpdated", None) or (p.get("lastUpdated") if isinstance(p, dict) else None)
        result.append({"id": pid, "name": name, "lastUpdated": lu})
    return result
