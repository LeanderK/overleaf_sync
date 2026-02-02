import platform
import os
from datetime import datetime
from .config import load_config, prompt_first_run, Config, get_logs_dir
from .cookies import load_overleaf_cookies
from .overleaf_api import create_api, list_projects_sorted_by_last_updated
from .projects import folder_name_for, ensure_dir
from .git_ops import clone_if_missing, ensure_remote, detect_default_branch, pull_remote, enable_git_helper


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
        folder = folder_name_for(name, pid, cfg.append_id_suffix)
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
    msg = f"[{datetime.now().isoformat(timespec='seconds')}] Synced {len(projects)} projects into {cfg.base_dir}"
    print(msg)
    try:
        logs_dir = get_logs_dir()
        with open(os.path.join(logs_dir, "app.log"), "a", encoding="utf-8") as lf:
            lf.write(msg + "\n")
    except Exception:
        pass


def run_sync_once():
    cfg = load_config() or prompt_first_run()
    run_sync(cfg)
