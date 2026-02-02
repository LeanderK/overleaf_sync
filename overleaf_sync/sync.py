import platform
import os
from datetime import datetime
from .config import load_config, prompt_first_run, Config, get_logs_dir
from .cookies import load_overleaf_cookies
from .overleaf_api import create_api, list_projects_sorted_by_last_updated
from .projects import folder_name_for, ensure_dir
from .git_ops import (
    clone_if_missing,
    ensure_remote,
    detect_default_branch,
    pull_remote,
    enable_git_helper,
    is_worktree_clean,
    has_unpushed_commits,
)


def run_sync(cfg: Config):
    # Require Git token for all sync operations to ensure non-interactive background runs
    if not cfg.git_token:
        raise RuntimeError("Git token is required. Run 'overleaf-sync set-git-token' and retry.")
    if cfg.git_helper:
        enable_git_helper(platform.system())

    ensure_dir(cfg.base_dir)
    # Prefer cookies from config if present
    if cfg.cookies:
        cookies = cfg.cookies
    else:
        cookies = load_overleaf_cookies(cfg.browser, cfg.profile)
    api = create_api(cfg.host)

    projects = list_projects_sorted_by_last_updated(api, cookies, cfg.count)

    for p in projects:
        pid = p["id"]
        name = p["name"]
        folder = folder_name_for(name, pid)
        repo_dir = os.path.join(cfg.base_dir, folder)
        needs_clone = not os.path.isdir(os.path.join(repo_dir, ".git"))
        if needs_clone and not cfg.git_token:
            raise RuntimeError(
                "Missing Overleaf Git token for cloning. Run 'overleaf-sync set-git-token' and retry."
            )
        repo_path = clone_if_missing(cfg.base_dir, folder, pid, cfg.git_token)
        ensure_remote(repo_path, pid, cfg.git_token)
        branch = detect_default_branch(repo_path)
        pull_remote(repo_path, branch)
    # After successful sync of latest set, automatically prune old projects safely
    expected = {folder_name_for(p.get("name"), p.get("id")) for p in projects}
    pruned = 0
    lingering = 0
    for entry in os.listdir(cfg.base_dir):
        path = os.path.join(cfg.base_dir, entry)
        if os.path.isdir(os.path.join(path, ".git")) and entry not in expected:
            # Remove only if clean and with no unpushed commits
            try:
                branch = detect_default_branch(path)
                clean = is_worktree_clean(path)
                ahead = has_unpushed_commits(path, branch)
                if clean and ahead is False:
                    import shutil
                    shutil.rmtree(path)
                    pruned += 1
                else:
                    lingering += 1
            except Exception:
                lingering += 1
    msg = f"[{datetime.now().isoformat(timespec='seconds')}] Synced {len(projects)} projects into {cfg.base_dir}"
    if pruned or lingering:
        msg += f"; pruned {pruned} old, {lingering} lingering"
    print(msg)
    # Append to app log for status checks
    try:
        logs_dir = get_logs_dir()
        with open(os.path.join(logs_dir, "app.log"), "a", encoding="utf-8") as lf:
            lf.write(msg + "\n")
    except Exception:
        pass


def run_sync_validate_first(cfg: Config):
    if cfg.git_helper:
        enable_git_helper(platform.system())

    ensure_dir(cfg.base_dir)
    if cfg.cookies:
        cookies = cfg.cookies
    else:
        cookies = load_overleaf_cookies(cfg.browser, cfg.profile)
    api = create_api(cfg.host)
    projects = list_projects_sorted_by_last_updated(api, cookies, 1)
    if not projects:
        raise RuntimeError("No projects found for validation.")
    p = projects[0]
    pid = p["id"]
    name = p["name"]
    folder = folder_name_for(name, pid)
    repo_dir = os.path.join(cfg.base_dir, folder)
    needs_clone = not os.path.isdir(os.path.join(repo_dir, ".git"))
    if needs_clone and not cfg.git_token:
        raise RuntimeError("Missing Overleaf Git token for cloning. Run 'overleaf-sync set-git-token'.")
    repo_path = clone_if_missing(cfg.base_dir, folder, pid, cfg.git_token)
    ensure_remote(repo_path, pid, cfg.git_token)
    branch = detect_default_branch(repo_path)
    pull_remote(repo_path, branch)
    print(f"Validation sync OK for '{name}' ({pid}).")


def run_sync_once():
    cfg = load_config() or prompt_first_run()
    run_sync(cfg)
