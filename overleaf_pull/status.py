import sys
import os
import concurrent.futures

from .config import load_config, prompt_first_run, get_logs_dir
from .notifier import report_sync_failure


def _tail(path: str, lines: int = 50) -> list[str]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.readlines()
        return content[-lines:]
    except Exception:
        return []


def _log_ts(line: str):
    try:
        start = line.index("[") + 1
        end = line.index("]", start)
        return __import__("datetime").datetime.fromisoformat(line[start:end])
    except Exception:
        return None


def _is_success_line(line: str) -> bool:
    return line.startswith("[") and ("] Synced" in line or "] Full refresh synced" in line)


def _success_clears_error(last_success_ts, error_ts) -> bool:
    return bool(last_success_ts and error_ts and last_success_ts >= error_ts)


def cmd_status(args):
    # Sync health check: verify local repos match remote heads
    cfg = load_config() or prompt_first_run()
    if not cfg.git_token:
        print("Git token missing. Run 'overleaf-pull set-git-token'.")
        return
    # Gather projects
    cookies = cfg.cookies if cfg.cookies else None
    if not cookies:
        from .cookies import load_overleaf_cookies
        cookies = load_overleaf_cookies(cfg.browser, cfg.profile)
    try:
        from .overleaf_api import create_api, list_projects_sorted_by_last_updated
        api = create_api(cfg.host)
        projects = list_projects_sorted_by_last_updated(api, cookies, cfg.count)
    except Exception as e:
        report_sync_failure(e, context="status list projects", cli=True, desktop=False)
        sys.exit(1)

    from .projects import folder_name_for
    from .git_ops import (
        ensure_remote,
        detect_default_branch,
        get_remote_branch_head,
        get_local_branch_head,
    )

    total = len(projects)
    if total == 0:
        print("No projects found.")
        return
    print(f"Checking status for {total} latest project(s)...", flush=True)

    def _check(p: dict):
        pid = p.get("id")
        name = p.get("name") or ""
        if not isinstance(pid, str) or not pid:
            return ("outdated", f"Invalid project entry (missing id) for {name or '(unknown)'}")
        folder = folder_name_for(name, pid)
        repo_path = os.path.join(cfg.base_dir, folder)
        if not os.path.isdir(os.path.join(repo_path, ".git")):
            return ("missing", f"Missing: {name}")
        try:
            ensure_remote(repo_path, pid, cfg.git_token)
            branch = detect_default_branch(repo_path)
            rhead = get_remote_branch_head(repo_path, branch)
            lhead = get_local_branch_head(repo_path, branch)
            if not rhead or not lhead:
                return ("outdated", f"Outdated: {name} (unable to determine heads)")
            if rhead != lhead:
                return ("outdated", f"Outdated: {name} (remote {rhead[:7]} vs local {lhead[:7]})")
            return ("up", None)
        except Exception as e:
            return ("outdated", f"Outdated: {name} (error: {e})")

    up_to_date = 0
    missing = 0
    outdated = 0
    issues: list[str] = []
    done = 0
    max_workers = min(16, total)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = [ex.submit(_check, p) for p in projects]
        for fut in concurrent.futures.as_completed(futures):
            status, msg = fut.result()
            done += 1
            if status == "up":
                up_to_date += 1
            elif status == "missing":
                missing += 1
                if msg:
                    issues.append(msg)
            else:
                outdated += 1
                if msg:
                    issues.append(msg)
    print("\n=== Summary ===")
    print(
        f"- Up to date: {up_to_date}/{total} | Missing: {missing}/{total} | Outdated: {outdated}/{total}"
    )

    # Identify old projects (not in latest set)
    expected = set()
    for p in projects:
        pid = p.get("id")
        name = p.get("name") or ""
        if isinstance(pid, str) and pid:
            expected.add(folder_name_for(name, pid))
    old_repos = []
    for entry in os.listdir(cfg.base_dir):
        path = os.path.join(cfg.base_dir, entry)
        if os.path.isdir(os.path.join(path, ".git")) and entry not in expected:
            old_repos.append(path)

    # Report old local repos and their Git status (clean/dirty, unpushed commits)
    if old_repos:
        from .git_ops import is_worktree_clean, has_unpushed_commits, detect_default_branch
        safe = []
        pending = []
        for repo in old_repos[:200]:
            try:
                branch = detect_default_branch(repo)
                clean = is_worktree_clean(repo)
                ahead = has_unpushed_commits(repo, branch)
                if clean and ahead is False:
                    safe.append((repo, branch))
                else:
                    pending.append((repo, branch, clean, ahead))
            except Exception as e:
                pending.append((repo, None, None, None))

        # Safe lingering repos: no action needed (list only)
        if safe:
            print("\n=== Lingering (to be deleted) — OK to delete if desired ===")
            for repo, branch in safe:
                print(f"- {repo} (branch {branch})")

        # Pending lingering repos: require user action
        if pending:
            print("\n=== Lingering but with pending work — ACTION REQUIRED ===")
            print("These projects are not in Overleaf's latest set and may have unsaved or unpushed work.")
            for item in pending[:200]:
                repo, branch, clean, ahead = item
                try:
                    branch = branch or detect_default_branch(repo)
                    if clean is False or ahead is True:
                        status_bits = []
                        if clean is False:
                            status_bits.append("dirty")
                        if ahead is True:
                            status_bits.append("unpushed")
                        note = " and ".join(status_bits)
                        print(f"- {repo}: branch {branch}, needs action ({note})")
                    elif ahead is False and clean is True:
                        print(f"- {repo}: branch {branch}, looks fine, but still not in latest set")
                    else:
                        print(f"- {repo}: branch {branch}, needs action (status unknown; please review)")
                except Exception as e:
                    print(f"- {repo}: error checking status: {e}")

    lingering = []
    removed = []
    if args.prune and old_repos:
        import shutil
        from .git_ops import is_worktree_clean, has_unpushed_commits
        for repo in old_repos:
            branch = detect_default_branch(repo)
            clean = is_worktree_clean(repo)
            ahead = has_unpushed_commits(repo, branch)
            if clean and ahead is False:
                try:
                    shutil.rmtree(repo)
                    removed.append(repo)
                except Exception:
                    lingering.append(repo)
            else:
                lingering.append(repo)

    if removed:
        print(f"Pruned {len(removed)} old project(s).")
    if lingering:
        print(f"Lingering old projects (cannot delete safely): {len(lingering)}")
        for r in lingering[:5]:
            print(f"- {r}")

    # Always show background runner info
    print("\n=== Scheduler & Runner ===")
    logs_dir = get_logs_dir()
    app_log = os.path.join(logs_dir, "app.log")
    runner_log = os.path.join(logs_dir, "runner.log")
    runner_err = os.path.join(logs_dir, "runner.err.log")
    has_runner_logs = (os.path.exists(runner_log) or os.path.exists(runner_err))
    # Optional: show last background worker run time
    last_run = None
    try:
        candidates = [p for p in (runner_log, runner_err) if os.path.exists(p)]
        if candidates:
            mtimes = [(p, os.path.getmtime(p)) for p in candidates]
            last_run = max(mtimes, key=lambda x: x[1])[1]
    except Exception:
        last_run = None
    # Show scheduler configuration
    mode = getattr(cfg, "scheduler_mode", "dynamic")
    print(f"Scheduler: interval={cfg.sync_interval}, mode={mode}")
    # Detect offline runner skip (from app.log)
    offline_msg = None
    offline_ts = None
    if os.path.exists(app_log):
        lines = _tail(app_log, 50)
        for line in reversed(lines):
            line = line.strip()
            if "] Runner skipped (no internet)" in line:
                offline_msg = line
                offline_ts = _log_ts(line)
                break

    # Detect last successful sync message (from app.log)
    last_success = None
    last_success_ts = None
    if os.path.exists(app_log):
        lines = _tail(app_log, 200)
        for line in reversed(lines):
            line = line.strip()
            if _is_success_line(line):
                last_success = line
                last_success_ts = _log_ts(line)
                break

    # Detect runner errors (prefer runner.err.log on macOS, fall back to app.log everywhere)
    error_msg = None
    error_ts = None
    if os.path.exists(runner_err):
        err_lines = _tail(runner_err, 200)
        for line in reversed(err_lines):
            s = line.strip()
            if not s:
                continue
            if (
                "Traceback" in s
                or "Error" in s
                or "Exception" in s
                or "ModuleNotFoundError" in s
            ):
                error_msg = s
                try:
                    error_ts = __import__("datetime").datetime.fromtimestamp(os.path.getmtime(runner_err))
                except Exception:
                    error_ts = None
                break
    if error_msg is None and os.path.exists(app_log):
        app_lines = _tail(app_log, 200)
        for line in reversed(app_lines):
            s = line.strip()
            if not s:
                continue
            if "status list projects" in s.lower():
                continue
            if s.startswith("[") and ("Overleaf Sync:" in s or "Runner skipped" in s):
                if "Failed" in s or "Failure" in s or "ERROR" in s or "Error" in s:
                    error_msg = s
                    error_ts = _log_ts(s)
                    break

    # A newer successful sync clears older runner/app-log errors for status display.
    if error_msg and _success_clears_error(last_success_ts, error_ts):
        error_msg = None
        error_ts = None

    effective_last_run = last_run
    if last_success_ts:
        success_epoch = last_success_ts.timestamp()
        if effective_last_run is None or success_epoch > effective_last_run:
            effective_last_run = success_epoch
    
    # Compute next worker run time (approximate)
    next_run_str = None
    try:
        interval_map = {"30m": 1800, "1h": 3600, "12h": 43200, "24h": 86400}
        iv = interval_map.get(cfg.sync_interval, 3600)
        if effective_last_run:
            import datetime as _dt
            nr = effective_last_run + iv
            next_run_str = _dt.datetime.fromtimestamp(nr).isoformat(timespec='seconds')
    except Exception:
        next_run_str = None

    # Determine staleness relative to interval
    is_stale = False
    try:
        if effective_last_run:
            import time as _time
            interval_map = {"30m": 1800, "1h": 3600, "12h": 43200, "24h": 86400}
            iv = interval_map.get(cfg.sync_interval, 3600)
            # Consider stale if last run is older than 1.5x interval
            is_stale = (_time.time() - effective_last_run) > (iv * 1.5)
    except Exception:
        is_stale = False

    if error_msg and (has_runner_logs or os.path.exists(app_log)):
        print(f"Background runner ERROR. {error_msg}")
        if effective_last_run:
            try:
                import datetime as _dt
                ts = _dt.datetime.fromtimestamp(effective_last_run).isoformat(timespec='seconds')
                print(f"Last sync activity: {ts}")
            except Exception:
                pass
        if next_run_str:
            print(f"Worker next run: {next_run_str} (approx)")
        print("Hint: reinstall the scheduler or fix Python environment for the runner.")
        if last_success:
            print(f"Last successful sync: {last_success}")
    elif offline_msg and (has_runner_logs or os.path.exists(app_log)) and (
        last_success_ts is None or offline_ts is None or offline_ts >= last_success_ts
    ):
        print(f"Background runner STALE (offline). {offline_msg}")
        if effective_last_run:
            try:
                import datetime as _dt
                ts = _dt.datetime.fromtimestamp(effective_last_run).isoformat(timespec='seconds')
                print(f"Last sync activity: {ts}")
            except Exception:
                pass
        if next_run_str:
            print(f"Worker next run: {next_run_str} (approx)")
        if last_success:
            print(f"Last successful sync: {last_success}")
    elif is_stale and (has_runner_logs or os.path.exists(app_log)):
        print("Background runner STALE (missed schedule?).")
        if effective_last_run:
            try:
                import datetime as _dt
                ts = _dt.datetime.fromtimestamp(effective_last_run).isoformat(timespec='seconds')
                print(f"Last sync activity: {ts}")
            except Exception:
                pass
        if next_run_str:
            print(f"Worker next run: {next_run_str} (approx)")
        if last_success:
            print(f"Last successful sync: {last_success}")
    elif last_success and (has_runner_logs or os.path.exists(app_log)):
        print(f"Background runner OK. {last_success}")
        if effective_last_run:
            try:
                import datetime as _dt
                ts = _dt.datetime.fromtimestamp(effective_last_run).isoformat(timespec='seconds')
                print(f"Last sync activity: {ts}")
            except Exception:
                pass
        if next_run_str:
            print(f"Worker next run: {next_run_str} (approx)")
    elif last_success:
        print(f"Manual sync OK. {last_success}")
    else:
        if has_runner_logs or os.path.exists(app_log):
            print("Background runner NOT SUCCESSFUL yet (no successful sync recorded).")
            if effective_last_run:
                try:
                    import datetime as _dt
                    ts = _dt.datetime.fromtimestamp(effective_last_run).isoformat(timespec='seconds')
                    print(f"Last sync activity: {ts}")
                except Exception:
                    pass
            if next_run_str:
                print(f"Worker next run: {next_run_str} (approx)")
            if last_success:
                print(f"Last successful sync: {last_success}")
        else:
            print("Everything OK. No background runs recorded yet.")

    # Show per-project timers (next due) if available
    try:
        from .config import load_schedule_state
        st = load_schedule_state()
        projs = st.get("projects", {})
        if projs:
            print("\n=== Timers (next due) ===")
            import time as _time
            items = []
            for pid, ent in projs.items():
                nd = int(ent.get("next_due_ts", 0) or 0)
                interval = int(ent.get("interval_sec", 1800) or 1800)
                pending = bool(ent.get("pending_delete"))
                unsynced = bool(ent.get("unsynced"))
                folder = ent.get("folder") or ""
                repo_path = os.path.join(cfg.base_dir, folder) if folder else ""
                repo_exists = bool(repo_path and os.path.isdir(os.path.join(repo_path, ".git")))
                items.append((nd, pid, ent.get("name") or pid, interval, pending, unsynced, repo_exists))
            items.sort(key=lambda x: x[0])
            now_ts = int(_time.time())

            def _format_future(delta_sec: int) -> str:
                mins = max(0, delta_sec // 60)
                if mins < 60:
                    return f"in {mins}m"
                hrs = mins // 60
                return f"in {hrs}h"

            interval_map = {"30m": 1800, "1h": 3600, "12h": 43200, "24h": 86400}
            scheduler_interval_sec = interval_map.get(cfg.sync_interval, 3600)
            scheduler_eta_fallback = f"in about {_format_future(scheduler_interval_sec).removeprefix('in ')}"

            runner_eta_str = None
            if next_run_str:
                try:
                    import datetime as _dt
                    runner_eta_ts = int(_dt.datetime.fromisoformat(next_run_str).timestamp())
                    runner_eta = max(0, runner_eta_ts - now_ts)
                    runner_eta_str = _format_future(runner_eta)
                except Exception:
                    runner_eta_str = None

            for nd, pid, nm, interval, pending, unsynced, repo_exists in items[:10]:
                delta = nd - now_ts
                try:
                    import datetime as _dt
                    ts = _dt.datetime.fromtimestamp(nd).isoformat(timespec='seconds')
                except Exception:
                    ts = str(nd)
                if delta <= 0:
                    overdue = -delta
                    if overdue > interval:
                        if runner_eta_str:
                            status = f"stale; next check {runner_eta_str}"
                        else:
                            status = f"stale; next check {scheduler_eta_fallback}"
                    else:
                        if runner_eta_str:
                            status = f"due now; next check {runner_eta_str}"
                        else:
                            status = f"due now; next check {scheduler_eta_fallback}"
                else:
                    status = f"next due {_format_future(delta)}"
                extra = f" (scheduled {ts})" if delta > 0 else ""
                if pending and not repo_exists:
                    status = "deleted locally; state cleanup pending"
                    marker_str = ""
                else:
                    markers = []
                    if pending:
                        markers.append("pending delete")
                    if unsynced:
                        markers.append("unsynced")
                    marker_str = f" [{' / '.join(markers)}]" if markers else ""
                print(f"- {nm}: {status}{extra}{marker_str}")
    except Exception:
        pass

    # Print brief issues overview (always after runner info)
    if issues:
        print("\n=== Issues ===")
        for msg in issues[:10]:
            print(f"- {msg}")
