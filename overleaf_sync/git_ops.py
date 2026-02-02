import os
import subprocess
from typing import Optional

REMOTE_NAME = "overleaf"
REMOTE_URL_FMT = "https://git.overleaf.com/{id}"


def build_remote_url(project_id: str, token: Optional[str] = None) -> str:
    if token:
        return f"https://git:{token}@git.overleaf.com/{project_id}"
    return REMOTE_URL_FMT.format(id=project_id)


def _run(cmd: list[str], cwd: Optional[str] = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, check=False, capture_output=True, text=True)


def repo_exists(path: str) -> bool:
    return os.path.isdir(os.path.join(path, ".git"))


def clone_if_missing(base_dir: str, folder: str, project_id: str, token: Optional[str] = None) -> str:
    path = os.path.join(base_dir, folder)
    if not repo_exists(path):
        url = build_remote_url(project_id, token)
        res = _run(["git", "clone", url, path])
        if res.returncode != 0:
            err = res.stderr.strip() or res.stdout.strip()
            if token and token in err:
                err = err.replace(token, "***")
            raise RuntimeError(f"git clone failed: {err}")
    return path


def ensure_remote(path: str, project_id: str, token: Optional[str] = None) -> None:
    url = build_remote_url(project_id, token)
    res = _run(["git", "remote", "get-url", REMOTE_NAME], cwd=path)
    if res.returncode != 0:
        _run(["git", "remote", "add", REMOTE_NAME, url], cwd=path)
    else:
        # Update if mismatched
        current = res.stdout.strip()
        if current != url:
            _run(["git", "remote", "set-url", REMOTE_NAME, url], cwd=path)


def detect_default_branch(path: str) -> str:
    # Try remote heads
    res = _run(["git", "ls-remote", "--heads", REMOTE_NAME], cwd=path)
    heads = res.stdout.splitlines()
    for line in heads:
        if line.endswith("refs/heads/master"):
            return "master"
    for line in heads:
        if line.endswith("refs/heads/main"):
            return "main"
    # Fallback to local current
    res2 = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=path)
    return res2.stdout.strip() or "master"


def pull_remote(path: str, branch: str) -> None:
    res = _run(["git", "pull", REMOTE_NAME, branch], cwd=path)
    if res.returncode != 0:
        err = res.stderr.strip() or res.stdout.strip()
        # token not available here; URLs are stored in remote
        raise RuntimeError(f"git pull failed: {err}")


def enable_git_helper(os_name: str) -> None:
    if os_name == "Darwin":
        _run(["git", "config", "--global", "credential.helper", "osxkeychain"]) 
    else:
        # Desktop Linux: libsecret; headless fallback to store
        res = _run(["git", "config", "--global", "credential.helper", "libsecret"]) 
        if res.returncode != 0:
            _run(["git", "config", "--global", "credential.helper", "store"]) 
