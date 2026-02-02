import os
import subprocess
from typing import Optional

REMOTE_NAME = "overleaf"
REMOTE_URL_FMT = "https://git.overleaf.com/{id}"


def build_remote_url(project_id: str, token: Optional[str] = None) -> str:
    if token:
        return f"https://git:{token}@git.overleaf.com/{project_id}"
    return REMOTE_URL_FMT.format(id=project_id)


def _git_env() -> dict:
    env = os.environ.copy()
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    return env


def _run(cmd: list[str], cwd: Optional[str] = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, check=False, capture_output=True, text=True, env=_git_env())


def _run_stream(cmd: list[str], cwd: Optional[str] = None, mask_token: Optional[str] = None) -> tuple[int, str]:
    """Run a command and stream combined stdout/stderr live to the console.

    Returns (returncode, combined_output). Masks token occurrences in output if provided.
    """
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=_git_env(),
        bufsize=1,
    )
    combined: list[str] = []
    if proc.stdout is not None:
        for line in proc.stdout:
            s = line.rstrip("\n")
            if mask_token:
                s = s.replace(mask_token, "***")
            print(s)
            combined.append(s)
    rc = proc.wait()
    return rc, "\n".join(combined)


def repo_exists(path: str) -> bool:
    return os.path.isdir(os.path.join(path, ".git"))


def clone_if_missing(base_dir: str, folder: str, project_id: str, token: Optional[str] = None) -> str:
    path = os.path.join(base_dir, folder)
    if not repo_exists(path):
        url = build_remote_url(project_id, token)
        safe_url = url
        if token:
            safe_url = url.replace(token, "***")
        print(f"$ git clone {safe_url} {path}")
        rc, combined = _run_stream(["git", "clone", url, path], mask_token=token)
        if rc != 0:
            raise RuntimeError(f"git clone failed: {combined.splitlines()[-1] if combined else 'unknown error'}")
    return path


def ensure_remote(path: str, project_id: str, token: Optional[str] = None) -> None:
    # If token is provided, ensure remote URL includes it; if not, avoid overriding
    target_url = build_remote_url(project_id, token) if token else None
    res = _run(["git", "remote", "get-url", REMOTE_NAME], cwd=path)
    if res.returncode != 0:
        if target_url:
            safe_url = target_url
            if token:
                safe_url = target_url.replace(token, "***")
            print(f"$ git remote add {REMOTE_NAME} {safe_url}")
            _run(["git", "remote", "add", REMOTE_NAME, target_url], cwd=path)
        return
    current = res.stdout.strip()
    if target_url and current != target_url:
        safe_url = target_url
        if token:
            safe_url = target_url.replace(token, "***")
        print(f"$ git remote set-url {REMOTE_NAME} {safe_url}")
        _run(["git", "remote", "set-url", REMOTE_NAME, target_url], cwd=path)


def detect_default_branch(path: str) -> str:
    # Try remote heads
    print(f"$ git ls-remote --heads {REMOTE_NAME}")
    res = _run(["git", "ls-remote", "--heads", REMOTE_NAME], cwd=path)
    heads = res.stdout.splitlines()
    for line in heads:
        if line.endswith("refs/heads/master"):
            return "master"
    for line in heads:
        if line.endswith("refs/heads/main"):
            return "main"
    # Fallback to local current
    print("$ git rev-parse --abbrev-ref HEAD")
    res2 = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=path)
    return res2.stdout.strip() or "master"


def pull_remote(path: str, branch: str) -> None:
    print(f"$ git pull {REMOTE_NAME} {branch}")
    rc, combined = _run_stream(["git", "pull", REMOTE_NAME, branch], cwd=path)
    if rc != 0:
        raise RuntimeError(f"git pull failed: {combined.splitlines()[-1] if combined else 'unknown error'}")


def get_remote_branch_head(path: str, branch: str) -> Optional[str]:
    """Return the remote branch head commit SHA for the given branch, or None if not found."""
    ref = f"refs/heads/{branch}"
    print(f"$ git ls-remote {REMOTE_NAME} {ref}")
    res = _run(["git", "ls-remote", REMOTE_NAME, ref], cwd=path)
    if res.returncode != 0:
        return None
    line = (res.stdout or "").strip()
    if not line:
        return None
    sha = line.split("\t")[0]
    return sha or None


def get_local_branch_head(path: str, branch: str) -> Optional[str]:
    """Return the local branch head commit SHA, or HEAD if branch missing."""
    res = _run(["git", "rev-parse", f"refs/heads/{branch}"], cwd=path)
    if res.returncode == 0:
        return (res.stdout or "").strip() or None
    # Fallback to HEAD
    res2 = _run(["git", "rev-parse", "HEAD"], cwd=path)
    if res2.returncode == 0:
        return (res2.stdout or "").strip() or None
    return None


def enable_git_helper(os_name: str) -> None:
    if os_name == "Darwin":
        _run(["git", "config", "--global", "credential.helper", "osxkeychain"]) 
    else:
        # Desktop Linux: libsecret; headless fallback to store
        res = _run(["git", "config", "--global", "credential.helper", "libsecret"]) 
        if res.returncode != 0:
            _run(["git", "config", "--global", "credential.helper", "store"]) 
