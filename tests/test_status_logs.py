from datetime import datetime
import unittest

from overleaf_pull.status import _is_success_line, _success_clears_error


class StatusLogTests(unittest.TestCase):
    def test_full_refresh_counts_as_success(self) -> None:
        line = "[2026-06-22T11:09:26] Full refresh synced 10 projects into /tmp/Overleaf"

        self.assertTrue(_is_success_line(line))

    def test_newer_success_clears_older_error(self) -> None:
        error_ts = datetime.fromisoformat("2026-06-05T22:41:06")
        success_ts = datetime.fromisoformat("2026-06-22T11:09:26")

        self.assertTrue(_success_clears_error(success_ts, error_ts))

    def test_older_success_does_not_clear_newer_error(self) -> None:
        success_ts = datetime.fromisoformat("2026-06-05T22:41:06")
        error_ts = datetime.fromisoformat("2026-06-22T11:09:26")

        self.assertFalse(_success_clears_error(success_ts, error_ts))


if __name__ == "__main__":
    unittest.main()
