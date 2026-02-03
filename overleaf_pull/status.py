import os
import concurrent.futures

from .config import load_config, prompt_first_run, get_logs_dir


def _tail(path: str, lines: int = 50) -> list[str]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.readlines()
        return content[-lines:]
    except Exception:
        return []


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
    from .overleaf_api import create_api, list_projects_sorted_by_last_updated
    api = create_api(cfg.host)
    projects = list_projects_sorted_by_last_updated(api, cookies, cfg.count)

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
    if os.path.exists(app_log):
        lines = _tail(app_log, 50)
        for line in reversed(lines):
            line = line.strip()
            if "] Runner skipped (no internet)" in line:
                offline_msg = line
                break

    # Detect last successful sync message (from app.log)
    last_success = None
    if os.path.exists(app_log):
        lines = _tail(app_log, 200)
        for line in reversed(lines):
            line = line.strip()
            if line.startswith("[") and "] Synced" in line:
                last_success = line
                break

    # Detect runner errors (from runner.err.log)
    error_msg = None
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
                break
    
    # Compute next worker run time (approximate)
    next_run_str = None
    try:
        interval_map = {"30m": 1800, "1h": 3600, "12h": 43200, "24h": 86400}
        iv = interval_map.get(cfg.sync_interval, 3600)
        if last_run:
            import datetime as _dt
            nr = last_run + iv
            next_run_str = _dt.datetime.fromtimestamp(nr).isoformat(timespec='seconds')
    except Exception:
        next_run_str = None

    # Determine staleness relative to interval
    is_stale = False
    try:
        if last_run:
            import time as _time
            interval_map = {"30m": 1800, "1h": 3600, "12h": 43200, "24h": 86400}
            iv = interval_map.get(cfg.sync_interval, 3600)
            # Consider stale if last run is older than 1.5x interval
            is_stale = (_time.time() - last_run) > (iv * 1.5)
    except Exception:
        is_stale = False

    if error_msg and has_runner_logs:
        print(f"Background runner ERROR. {error_msg}")
        if last_run:
            try:
                import datetime as _dt
                ts = _dt.datetime.fromtimestamp(last_run).isoformat(timespec='seconds')
                print(f"Last worker activity: {ts}")
            except Exception:
                pass
        if next_run_str:
            print(f"Worker next run: {next_run_str} (approx)")
        print("Hint: reinstall the scheduler or fix Python environment for the runner.")
        if last_success:
            print(f"Last successful sync (worker): {last_success}")
    elif offline_msg and has_runner_logs:
        print(f"Background runner STALE (offline). {offline_msg}")
        if last_run:
            try:
                import datetime as _dt
                ts = _dt.datetime.fromtimestamp(last_run).isoformat(timespec='seconds')
                print(f"Last worker activity: {ts}")
            except Exception:
                pass
        if next_run_str:
            print(f"Worker next run: {next_run_str} (approx)")
        if last_success:
            print(f"Last successful sync (worker): {last_success}")
    elif is_stale and has_runner_logs:
        print("Background runner STALE (missed schedule?).")
        if last_run:
            try:
                import datetime as _dt
                ts = _dt.datetime.fromtimestamp(last_run).isoformat(timespec='seconds')
                print(f"Last worker activity: {ts}")
            except Exception:
                pass
        if next_run_str:
            print(f"Worker next run: {next_run_str} (approx)")
        if last_success:
            print(f"Last successful sync (worker): {last_success}")
    elif last_success and has_runner_logs:
        print(f"Background runner OK. {last_success}")
        if last_run:
            try:
                import datetime as _dt
                ts = _dt.datetime.fromtimestamp(last_run).isoformat(timespec='seconds')
                print(f"Last worker activity: {ts}")
            except Exception:
                pass
        if next_run_str:
            print(f"Worker next run: {next_run_str} (approx)")
    elif last_success:
        print(f"Manual sync OK. {last_success}")
    else:
        if has_runner_logs:
            print("Background runner NOT SUCCESSFUL yet (no successful sync recorded).")
            if last_run:
                try:
                    import datetime as _dt
                    ts = _dt.datetime.fromtimestamp(last_run).isoformat(timespec='seconds')
                    print(f"Last worker activity: {ts}")
                except Exception:
                    pass
            if next_run_str:
                print(f"Worker next run: {next_run_str} (approx)")
            if last_success:
                print(f"Last successful sync (worker): {last_success}")
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
                items.append((nd, ent.get("name") or pid, interval))
            items.sort(key=lambda x: x[0])
            now_ts = int(_time.time())
            for nd, nm, interval in items[:10]:
                delta = nd - now_ts
                try:
                    import datetime as _dt
                    ts = _dt.datetime.fromtimestamp(nd).isoformat(timespec='seconds')
                except Exception:
                    ts = str(nd)
                if delta <= 0:
                    overdue = -delta
                    if overdue > interval:
                        status = f"stale (overdue by {overdue//3600}h)"
                    else:
                        status = "due now"
                else:
                    mins = delta // 60
                    if mins < 60:
                        status = f"in {mins}m"
                    else:
                        hrs = mins // 60
                        status = f"in {hrs}h"
                print(f"- {nm}: {status} (scheduled {ts})")
    except Exception:
        pass

    # Print brief issues overview (always after runner info)
    if issues:
        print("\n=== Issues ===")
        for msg in issues[:10]:
            print(f"- {msg}")
