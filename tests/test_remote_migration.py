import contextlib
import io
import subprocess
import tempfile
import unittest
from pathlib import Path

from overleaf_pull.config import Config
from overleaf_pull.sync import _check_and_update_stale_tokens


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )


def init_repo(repo: Path) -> None:
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "master"], cwd=repo, check=True, capture_output=True, text=True)
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    (repo / "README.md").write_text("test\n", encoding="utf-8")
    git(repo, "add", "README.md")
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True, text=True)


class RemoteMigrationTests(unittest.TestCase):
    def test_overleaf_repo_uses_origin_and_removes_legacy_remote(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = base / "project"
            init_repo(repo)
            git(repo, "remote", "add", "origin", "https://git:old@git.overleaf.com/abc123")
            git(repo, "remote", "add", "overleaf", "https://git:legacy@git.overleaf.com/abc123")

            cfg = Config(base_dir=str(base), git_token="new")
            with contextlib.redirect_stdout(io.StringIO()):
                _check_and_update_stale_tokens(cfg)

            origin = git(repo, "remote", "get-url", "origin")
            legacy = git(repo, "remote", "get-url", "overleaf")
            branch_remote = git(repo, "config", "branch.master.remote")
            branch_merge = git(repo, "config", "branch.master.merge")

            self.assertEqual(origin.stdout.strip(), "https://git:new@git.overleaf.com/abc123")
            self.assertNotEqual(legacy.returncode, 0)
            self.assertEqual(branch_remote.stdout.strip(), "origin")
            self.assertEqual(branch_merge.stdout.strip(), "refs/heads/master")

    def test_non_overleaf_origin_is_left_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = base / "tool"
            init_repo(repo)
            original = "https://github.com/LeanderK/overleaf_sync.git"
            git(repo, "remote", "add", "origin", original)

            cfg = Config(base_dir=str(base), git_token="new")
            with contextlib.redirect_stdout(io.StringIO()):
                _check_and_update_stale_tokens(cfg)

            origin = git(repo, "remote", "get-url", "origin")

            self.assertEqual(origin.stdout.strip(), original)


if __name__ == "__main__":
    unittest.main()
