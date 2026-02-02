import os
import subprocess
from typing import Optional

REMOTE_NAME = "overleaf"
REMOTE_URL_FMT = "https://git.overleaf.com/{id}"


def _run(cmd: list[str], cwd: Optional[str] = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, check=False, capture_output=True, text=True)


def repo_exists(path: str) -> bool:
    return os.path.isdir(os.path.join(path, ".git"))


def clone_if_missing(base_dir: str, folder: str, project_id: str) -> str:
    path = os.path.join(base_dir, folder)
    if not repo_exists(path):
        url = REMOTE_URL_FMT.format(id=project_id)
        res = _run(["git", "clone", url, path])
        if res.returncode != 0:
            raise RuntimeError(f"git clone failed: {res.stderr.strip()}")
    return path


def ensure_remote(path: str, project_id: str) -> None:
    url = REMOTE_URL_FMT.format(id=project_id)
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
        raise RuntimeError(f"git pull failed: {res.stderr.strip() or res.stdout.strip()}")


def enable_git_helper(os_name: str) -> None:
    if os_name == "Darwin":
        _run(["git", "config", "--global", "credential.helper", "osxkeychain"]) 
    else:
        # Desktop Linux: libsecret; headless fallback to store
        res = _run(["git", "config", "--global", "credential.helper", "libsecret"]) 
        if res.returncode != 0:
            _run(["git", "config", "--global", "credential.helper", "store"]) 
