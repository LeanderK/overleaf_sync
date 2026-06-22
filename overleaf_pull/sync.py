import platform
import os
import socket
import re
from datetime import datetime
from .config import load_config, prompt_first_run, Config, get_logs_dir, load_schedule_state, save_schedule_state
from .cookies import load_overleaf_cookies
from .overleaf_api import create_api, list_projects_sorted_by_last_updated
from .projects import folder_name_for, ensure_dir
from .notifier import report_sync_failure
from .git_ops import (
    clone_if_missing,
    ensure_remote,
    detect_default_branch,
    pull_remote,
    enable_git_helper,
    is_worktree_clean,
    has_unpushed_commits,
    get_remote_branch_head,
    get_local_branch_head,
    build_remote_url,
)


def is_plugged_in() -> bool:
    """
    Detect if the system is plugged into power (not running on battery).
    Works on macOS and Linux.
    
    Returns:
        bool: True if plugged in, False if on battery. Returns True if no battery detected (desktop).
    """
    try:
        import psutil
        battery = psutil.sensors_battery()
        if battery is None:
            return True  # No battery detected (desktop)
        return battery.power_plugged
    except Exception:
        return True  # Assume plugged in if detection fails


def run_sync(cfg: Config):
    # Require Git token for all sync operations to ensure non-interactive background runs
    if not cfg.git_token:
        raise RuntimeError("Git token is required. Run 'overleaf-pull set-git-token' and retry.")
    if cfg.git_helper:
        enable_git_helper(platform.system())

    ensure_dir(cfg.base_dir)
    _check_and_update_stale_tokens(cfg)
    # Prefer cookies from config if present
    if cfg.cookies:
        cookies = cfg.cookies
    else:
        cookies = load_overleaf_cookies(cfg.browser, cfg.profile)
    api = create_api(cfg.host)

    try:
        projects = list_projects_sorted_by_last_updated(api, cookies, cfg.count)
    except Exception as e:
        report_sync_failure(e, context="list projects", cli=True, desktop=True)
        raise

    # Fast-fail on no internet for manual sync. Log only; do not adjust timers.
    if not _has_internet(cfg):
        _log_manual_offline()
        raise RuntimeError("No internet connectivity to Overleaf/git; aborted full sync.")
    # Load schedule state to adjust timers based on manual sync outcome
    state = load_schedule_state()
    proj_state = state.setdefault("projects", {})
    import time as _time
    now = int(_time.time())
    MIN_SEC = 1800
    MAX_SEC = 86400

    # Compute candidates: union of latest-N from API and any projects that are due now
    api_map = {str(p.get("id")): p for p in projects if p.get("id") is not None}
    api_ids = set(api_map.keys())
    due_ids = {pid for pid, ent in proj_state.items() if int(ent.get("next_due_ts", 0) or 0) <= now}
    candidates = api_ids.union(due_ids)

    for pid in candidates:
        p = api_map.get(pid)
        # If we have API info, prefer its name; otherwise fall back to saved state
        name = (p.get("name") if p else None) or (proj_state.get(pid) or {}).get("name") or pid
        folder = (folder_name_for(name, pid) if p else (proj_state.get(pid) or {}).get("folder"))
        folder = folder or str(pid)
        repo_dir = os.path.join(cfg.base_dir, folder) if folder else os.path.join(cfg.base_dir, str(pid))

        # If this project is not present in the latest API set, consider it a prune candidate
        if pid not in api_ids:
            # Only attempt prune if repo exists
            if os.path.isdir(os.path.join(repo_dir, ".git")):
                try:
                    branch = detect_default_branch(repo_dir)
                    clean = is_worktree_clean(repo_dir)
                    ahead = has_unpushed_commits(repo_dir, branch)
                    if clean and ahead is False:
                        import shutil

                        shutil.rmtree(repo_dir)
                        # Remove from state if present
                        if pid in proj_state:
                            proj_state.pop(pid, None)
                    else:
                        # Mark as pending deletion and unsynced so status can report it
                        ent = proj_state.setdefault(pid, {})
                        ent["pending_delete"] = True
                        ent["unsynced"] = True
                        ent.setdefault("name", name)
                        ent.setdefault("folder", folder)
                        # Report as a non-fatal condition so user is aware
                        report_sync_failure(
                            RuntimeError("Prune skipped (dirty or unpushed): %s" % folder),
                            context=f"prune candidate {name}",
                            cli=True,
                            desktop=True,
                        )
                except Exception as e:
                    # Report failure to evaluate prune candidate but continue
                    report_sync_failure(e, context=f"prune evaluate {name}", cli=True, desktop=True)
                continue

        # For API-backed projects (or due saved entries), ensure repo present and sync if due
        entry = proj_state.get(pid) or {
            "name": name,
            "folder": folder,
            "interval_sec": MIN_SEC,
            "next_due_ts": 0,
        }
        # Keep name/folder up to date
        entry["name"] = name
        entry["folder"] = folder

        interval = int(entry.get("interval_sec", MIN_SEC) or MIN_SEC)
        next_due = int(entry.get("next_due_ts", 0) or 0)

        if next_due > now:
            proj_state[pid] = entry
            continue

        try:
            # Ensure repo exists and remote configured
            repo_path = clone_if_missing(cfg.base_dir, folder, pid, cfg.git_token)
            ensure_remote(repo_path, pid, cfg.git_token)
            branch = detect_default_branch(repo_path)

            # Compare heads to decide whether to pull
            rhead = get_remote_branch_head(repo_path, branch)
            lhead = get_local_branch_head(repo_path, branch)
            changed = (rhead != lhead) or (not rhead) or (not lhead)
        except Exception as e:
            # Report and continue with other candidates; do not raise to avoid aborting the run
            report_sync_failure(e, context=f"dynamic sync project {name}", cli=True, desktop=True)
            # bump retry to MIN_SEC to retry soon
            entry["interval_sec"] = MIN_SEC
            entry["next_due_ts"] = now + MIN_SEC
            proj_state[pid] = entry
            continue
        if changed:
            try:
                pull_remote(repo_path, branch)
            except Exception as e:
                report_sync_failure(e, context=f"dynamic pull {name}", cli=True, desktop=True)
                # schedule a quick retry
                entry["interval_sec"] = MIN_SEC
                entry["next_due_ts"] = now + MIN_SEC
                proj_state[pid] = entry
                continue
            
            interval = MIN_SEC
        else:
            interval = min(interval * 2, MAX_SEC)

        entry["interval_sec"] = interval
        entry["next_due_ts"] = now + interval
        proj_state[pid] = entry
    # After successful sync of latest set, automatically prune old projects safely
    expected = {
        folder_name_for(str(p.get("name", "")), str(p.get("id")))
        for p in projects
        if p.get("id") is not None
    }
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
                    # Also remove any schedule state entry that referenced this folder
                    for pid, ent in list(proj_state.items()):
                        if ent.get("folder") == entry:
                            proj_state.pop(pid, None)
                else:
                    # Mark lingering in schedule state for user attention
                    lingering += 1
                    for pid, ent in proj_state.items():
                        if ent.get("folder") == entry:
                            ent["pending_delete"] = True
                            ent["unsynced"] = True
                            # report the condition so it surfaces in notifications
                            report_sync_failure(
                                RuntimeError("Prune skipped (dirty or unpushed): %s" % entry),
                                context=f"prune candidate {entry}",
                                cli=True,
                                desktop=True,
                            )
            except Exception as e:
                lingering += 1
                # Report failure to evaluate prune candidate
                report_sync_failure(e, context=f"prune evaluate {entry}", cli=True, desktop=True)

    # Clean up stale schedule entries for repos that are already gone.
    # This keeps status from repeatedly showing deleted repos as pending work.
    for pid, ent in list(proj_state.items()):
        folder = ent.get("folder")
        repo_path = os.path.join(cfg.base_dir, folder) if folder else None
        repo_exists = bool(repo_path and os.path.isdir(os.path.join(repo_path, ".git")))
        if ent.get("pending_delete") and not repo_exists:
            proj_state.pop(pid, None)
        elif not repo_exists and folder and folder not in expected:
            # Old state for a repo that no longer exists locally.
            proj_state.pop(pid, None)
    # Persist updated schedule state
    save_schedule_state(state)

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
    try:
        projects = list_projects_sorted_by_last_updated(api, cookies, 1)
    except Exception as e:
        report_sync_failure(e, context="validation list projects", cli=True, desktop=False)
        raise
    if not projects:
        raise RuntimeError("No projects found for validation.")
    p = projects[0]
    pid = p["id"]
    name = p["name"]
    folder = folder_name_for(name, pid)
    repo_dir = os.path.join(cfg.base_dir, folder)
    needs_clone = not os.path.isdir(os.path.join(repo_dir, ".git"))
    if needs_clone and not cfg.git_token:
        raise RuntimeError("Missing Overleaf Git token for cloning. Run 'overleaf-pull set-git-token'.")
    try:
        repo_path = clone_if_missing(cfg.base_dir, folder, pid, cfg.git_token)
        ensure_remote(repo_path, pid, cfg.git_token)
        branch = detect_default_branch(repo_path)
        pull_remote(repo_path, branch)
    except Exception as e:
        report_sync_failure(e, context=f"validation sync {name}", cli=True, desktop=False)
        raise
    print(f"Validation sync OK for '{name}' ({pid}).")


def run_sync_once():
    cfg = load_config() or prompt_first_run()
    # Manual run should always sync everything, ignoring timers
    run_sync_once_full(cfg)


def run_sync_once_full(cfg: Config):
    """Full refresh of all projects, ignoring per-project timers.
    
    This is used by the manual 'run-once' command to ensure all projects are checked
    and pulled, regardless of their individual backoff timers.
    """
    # Require Git token for all sync operations to ensure non-interactive background runs
    if not cfg.git_token:
        raise RuntimeError("Git token is required. Run 'overleaf-pull set-git-token' and retry.")
    if cfg.git_helper:
        enable_git_helper(platform.system())

    ensure_dir(cfg.base_dir)
    
    # Proactively check and update remotes with current token to catch stale tokens early
    _check_and_update_stale_tokens(cfg)
    
    # Prefer cookies from config if present
    if cfg.cookies:
        cookies = cfg.cookies
    else:
        cookies = load_overleaf_cookies(cfg.browser, cfg.profile)
    api = create_api(cfg.host)

    try:
        projects = list_projects_sorted_by_last_updated(api, cookies, cfg.count)
    except Exception as e:
        report_sync_failure(e, context="list projects", cli=True, desktop=True)
        raise

    # Fast-fail on no internet for manual sync. Log only; do not adjust timers.
    if not _has_internet(cfg):
        _log_manual_offline()
        raise RuntimeError("No internet connectivity to Overleaf/git; aborted full sync.")
    
    # Load schedule state to adjust timers based on manual sync outcome
    state = load_schedule_state()
    proj_state = state.setdefault("projects", {})
    import time as _time
    now = int(_time.time())
    MIN_SEC = 1800
    MAX_SEC = 86400

    # For run-once-full, we refresh all API projects regardless of timers
    api_map = {str(p.get("id")): p for p in projects if p.get("id") is not None}
    api_ids = set(api_map.keys())
    # Include all API projects for full refresh
    candidates = api_ids

    for pid in candidates:
        p = api_map.get(pid)
        # If we have API info, prefer its name; otherwise fall back to saved state
        name = (p.get("name") if p else None) or (proj_state.get(pid) or {}).get("name") or pid
        folder = (folder_name_for(name, pid) if p else (proj_state.get(pid) or {}).get("folder"))
        folder = folder or str(pid)
        repo_dir = os.path.join(cfg.base_dir, folder) if folder else os.path.join(cfg.base_dir, str(pid))

        try:
            # Ensure repo exists and remote configured
            repo_path = clone_if_missing(cfg.base_dir, folder, pid, cfg.git_token)
            ensure_remote(repo_path, pid, cfg.git_token)
            branch = detect_default_branch(repo_path)

            # Compare heads to decide whether to pull
            rhead = get_remote_branch_head(repo_path, branch)
            lhead = get_local_branch_head(repo_path, branch)
            changed = (rhead != lhead) or (not rhead) or (not lhead)
        except Exception as e:
            # Report and continue with other candidates; do not raise to avoid aborting the run
            report_sync_failure(e, context=f"full sync project {name}", cli=True, desktop=True)
            # bump retry to MIN_SEC to retry soon
            entry = proj_state.setdefault(pid, {})
            entry["interval_sec"] = MIN_SEC
            entry["next_due_ts"] = now + MIN_SEC
            continue
        
        if changed:
            try:
                pull_remote(repo_path, branch)
            except Exception as e:
                report_sync_failure(e, context=f"full pull {name}", cli=True, desktop=True)
                # schedule a quick retry
                entry = proj_state.setdefault(pid, {})
                entry["interval_sec"] = MIN_SEC
                entry["next_due_ts"] = now + MIN_SEC
                continue
            
            interval = MIN_SEC
        else:
            interval = min(MIN_SEC * 2, MAX_SEC)

        # Update state for this project
        entry = proj_state.get(pid) or {
            "name": name,
            "folder": folder,
            "interval_sec": interval,
            "next_due_ts": now + interval,
        }
        entry["name"] = name
        entry["folder"] = folder
        entry["interval_sec"] = interval
        entry["next_due_ts"] = now + interval
        proj_state[pid] = entry

    # After successful sync of latest set, automatically prune old projects safely
    expected = {
        folder_name_for(str(p.get("name", "")), str(p.get("id")))
        for p in projects
        if p.get("id") is not None
    }
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
                    # Also remove any schedule state entry that referenced this folder
                    for pid, ent in list(proj_state.items()):
                        if ent.get("folder") == entry:
                            proj_state.pop(pid, None)
                else:
                    # Mark lingering in schedule state for user attention
                    lingering += 1
                    for pid, ent in proj_state.items():
                        if ent.get("folder") == entry:
                            ent["pending_delete"] = True
                            ent["unsynced"] = True
                            # report the condition so it surfaces in notifications
                            report_sync_failure(
                                RuntimeError("Prune skipped (dirty or unpushed): %s" % entry),
                                context=f"prune candidate {entry}",
                                cli=True,
                                desktop=True,
                            )
            except Exception as e:
                lingering += 1
                # Report failure to evaluate prune candidate
                report_sync_failure(e, context=f"prune evaluate {entry}", cli=True, desktop=True)

    # Clean up stale schedule entries for repos that are already gone.
    # This keeps status from repeatedly showing deleted repos as pending work.
    for pid, ent in list(proj_state.items()):
        folder = ent.get("folder")
        repo_path = os.path.join(cfg.base_dir, folder) if folder else None
        repo_exists = bool(repo_path and os.path.isdir(os.path.join(repo_path, ".git")))
        if ent.get("pending_delete") and not repo_exists:
            proj_state.pop(pid, None)
        elif not repo_exists and folder and folder not in expected:
            # Old state for a repo that no longer exists locally.
            proj_state.pop(pid, None)
    # Persist updated schedule state
    save_schedule_state(state)

    msg = f"[{datetime.now().isoformat(timespec='seconds')}] Full refresh synced {len(projects)} projects into {cfg.base_dir}"
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


def _check_and_update_stale_tokens(cfg: Config) -> None:
    """Normalize existing Overleaf repos to one origin remote with the current token.

    Older versions used an ``overleaf`` remote. If either remote points to
    git.overleaf.com, keep/update ``origin`` and remove the legacy remote.
    """
    base_dir = cfg.base_dir
    if not os.path.isdir(base_dir):
        return

    updated_count = 0
    removed_count = 0
    for entry in os.listdir(base_dir):
        repo_path = os.path.join(base_dir, entry)
        git_dir = os.path.join(repo_path, ".git")
        
        if not os.path.isdir(git_dir):
            continue
        
        try:
            from .git_ops import LEGACY_REMOTE_NAME, REMOTE_NAME, _run

            origin_res = _run(["git", "remote", "get-url", REMOTE_NAME], cwd=repo_path)
            legacy_res = _run(["git", "remote", "get-url", LEGACY_REMOTE_NAME], cwd=repo_path)
            origin_url = origin_res.stdout.strip() if origin_res.returncode == 0 else ""
            legacy_url = legacy_res.stdout.strip() if legacy_res.returncode == 0 else ""

            source_url = ""
            if "git.overleaf.com/" in origin_url:
                source_url = origin_url
            elif "git.overleaf.com/" in legacy_url:
                source_url = legacy_url

            if not source_url:
                continue

            # Extract project ID from URL
            match = re.search(r'git\.overleaf\.com/([a-f0-9]+)', source_url)
            if not match:
                continue

            project_id = match.group(1)

            # Build target URL with current token
            target_url = build_remote_url(project_id, cfg.git_token)

            # If origin differs, update it. If origin is missing, create it.
            if origin_url != target_url:
                safe_url = target_url.replace(cfg.git_token, "***") if cfg.git_token else target_url
                if origin_url:
                    print(f"$ git remote set-url {REMOTE_NAME} {safe_url} (updating Overleaf token)")
                    _run(["git", "remote", "set-url", REMOTE_NAME, target_url], cwd=repo_path)
                else:
                    print(f"$ git remote add {REMOTE_NAME} {safe_url}")
                    _run(["git", "remote", "add", REMOTE_NAME, target_url], cwd=repo_path)
                updated_count += 1

            if legacy_url:
                print(f"$ git remote remove {LEGACY_REMOTE_NAME}")
                _run(["git", "remote", "remove", LEGACY_REMOTE_NAME], cwd=repo_path)
                removed_count += 1

            branch_res = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_path)
            branch = branch_res.stdout.strip() if branch_res.returncode == 0 else ""
            if branch and branch != "HEAD":
                _run(["git", "config", f"branch.{branch}.remote", REMOTE_NAME], cwd=repo_path)
                _run(["git", "config", f"branch.{branch}.merge", f"refs/heads/{branch}"], cwd=repo_path)
        except Exception:
            # Silently skip repos that can't be processed
            pass
    
    if updated_count > 0:
        print(f"Updated {updated_count} git remotes with current token")
    if removed_count > 0:
        print(f"Removed {removed_count} legacy overleaf remotes")


def due_run(cfg: Config):
    """Run sync selectively for projects that are due based on dynamic backoff.

    Backoff: min 30 minutes (1800s), doubles up to 24h (86400s) when no changes; resets to 30m on changes.

    When plugged in (AC power) and the config allows full-sync-on-plugged-in, perform a full run.
    On battery, only projects whose timers have expired are processed to conserve resources.
    """
    plugged = is_plugged_in()
    if cfg.sync_on_plugged_in and plugged:
        run_sync(cfg)
        return

    import time
    from .git_ops import (
        clone_if_missing,
        ensure_remote,
        detect_default_branch,
        pull_remote,
        get_remote_branch_head,
        get_local_branch_head,
        enable_git_helper,
    )

    if not cfg.git_token:
        raise RuntimeError("Git token is required. Run 'overleaf-pull set-git-token' and retry.")
    if cfg.git_helper:
        enable_git_helper(platform.system())

    ensure_dir(cfg.base_dir)

    # Load state early to check if anything is actually due (avoid expensive API call if not)
    state = load_schedule_state()
    proj_state = state.setdefault("projects", {})
    now = int(time.time())

    # Treat empty state as due so fresh installs seed the schedule
    any_due = (not proj_state) or any(int(ent.get("next_due_ts", 0) or 0) <= now for ent in proj_state.values())
    if not any_due:
        # Nothing due; skip connectivity check and API calls entirely
        return

    # Cheap connectivity check before any API or cookie access
    if not _has_internet(cfg):
        _log_offline_and_push_timers()
        return
    cookies = cfg.cookies if cfg.cookies else load_overleaf_cookies(cfg.browser, cfg.profile)
    api = create_api(cfg.host)
    try:
        projects = list_projects_sorted_by_last_updated(api, cookies, cfg.count)
    except Exception as e:
        report_sync_failure(e, context="dynamic list projects", cli=True, desktop=True)
        raise
    MIN_SEC = 1800
    MAX_SEC = 86400

    synced = 0
    checked = 0

    for p in projects:
        pid = p["id"]
        name = p["name"]
        folder = folder_name_for(name, pid)
        repo_dir = os.path.join(cfg.base_dir, folder)
        entry = proj_state.get(pid) or {
            "name": name,
            "folder": folder,
            "interval_sec": MIN_SEC,
            "next_due_ts": 0,
        }
        # Keep name/folder up to date
        entry["name"] = name
        entry["folder"] = folder

        interval = int(entry.get("interval_sec", MIN_SEC) or MIN_SEC)
        next_due = int(entry.get("next_due_ts", 0) or 0)

        if next_due > now:
            # Not due yet; skip heavy checks
            proj_state[pid] = entry
            continue

        try:
            # Ensure repo exists and remote configured
            repo_path = clone_if_missing(cfg.base_dir, folder, pid, cfg.git_token)
            ensure_remote(repo_path, pid, cfg.git_token)
            branch = detect_default_branch(repo_path)

            # Compare heads to decide whether to pull
            rhead = get_remote_branch_head(repo_path, branch)
            lhead = get_local_branch_head(repo_path, branch)
            changed = (rhead != lhead) or (not rhead) or (not lhead)
        except Exception as e:
            report_sync_failure(e, context=f"dynamic sync project {name}", cli=True, desktop=True)
            raise
        checked += 1
        if changed:
            try:
                pull_remote(repo_path, branch)
            except Exception as e:
                report_sync_failure(e, context=f"dynamic pull {name}", cli=True, desktop=True)
                raise
            synced += 1
            interval = MIN_SEC
        else:
            interval = min(interval * 2, MAX_SEC)

        entry["interval_sec"] = interval
        entry["next_due_ts"] = now + interval
        proj_state[pid] = entry

    # Persist state
    save_schedule_state(state)

    # Log summary
    msg = f"[{datetime.now().isoformat(timespec='seconds')}] Synced {synced} due project(s); checked {checked}; next cadence min 30m"
    print(msg)
    try:
        logs_dir = get_logs_dir()
        with open(os.path.join(logs_dir, "app.log"), "a", encoding="utf-8") as lf:
            lf.write(msg + "\n")
    except Exception:
        pass


def _log_manual_offline():
    """Log offline condition for manual sync without modifying schedule state."""
    msg = f"[{datetime.now().isoformat(timespec='seconds')}] Manual sync aborted (no internet)"
    print(msg)
    try:
        logs_dir = get_logs_dir()
        with open(os.path.join(logs_dir, "app.log"), "a", encoding="utf-8") as lf:
            lf.write(msg + "\n")
    except Exception:
        pass

def _has_internet(cfg: Config, timeout: float = 3.0) -> bool:
    """Check basic TCP connectivity to Overleaf host and git.overleaf.com."""
    targets = [(cfg.host, 443), ("git.overleaf.com", 443)]
    for host, port in targets:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                pass
        except Exception:
            return False
    return True


def _log_offline_and_push_timers():
    """Log offline skip and push due timers forward by their current intervals to avoid immediate retries."""
    import time as _time
    now = int(_time.time())
    state = load_schedule_state()
    proj_state = state.setdefault("projects", {})
    changed = False
    for pid, ent in proj_state.items():
        nd = int(ent.get("next_due_ts", 0) or 0)
        # When offline, reschedule due projects to the minimum backoff (30 minutes)
        MIN_SEC = 1800
        if nd <= now:
            ent["next_due_ts"] = now + MIN_SEC
            changed = True
    if changed:
        save_schedule_state(state)
    msg = f"[{datetime.now().isoformat(timespec='seconds')}] Runner skipped (no internet); rescheduled due projects in 30m"
    print(msg)
    try:
        logs_dir = get_logs_dir()
        with open(os.path.join(logs_dir, "app.log"), "a", encoding="utf-8") as lf:
            lf.write(msg + "\n")
    except Exception:
        pass
