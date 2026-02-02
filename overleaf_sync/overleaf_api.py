from typing import List, Dict, Any

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


def list_projects_sorted_by_last_updated(api, cookies: Dict[str, str], limit: int) -> List[Dict[str, Any]]:
    """Login with cookies and list projects, sorted by lastUpdated descending, limited to 'limit'."""
    api.login_from_cookies(cookies)
    projects = api.get_projects()
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
