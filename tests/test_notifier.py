import unittest

from overleaf_pull.notifier import _failure_payload, _short_reason


class NotifierTests(unittest.TestCase):
    def test_http_503_is_reported_as_overleaf_outage(self) -> None:
        error = RuntimeError(
            "git pull failed: fatal: unable to access URL: "
            "the requested URL returned error: 503"
        )

        payload = _failure_payload(error, context="dynamic pull project")

        self.assertEqual(
            payload,
            (
                "Overleaf Sync: Pull Failed",
                "probable Overleaf-side outage (HTTP 503)",
                "overleaf-outage",
            ),
        )
        self.assertEqual(
            _short_reason(error, context="dynamic pull project"),
            "probable Overleaf-side outage (HTTP 503)",
        )

    def test_non_503_pull_failure_keeps_generic_message(self) -> None:
        error = RuntimeError("git pull failed: repository is corrupt")

        self.assertEqual(
            _failure_payload(error, context="dynamic pull project"),
            (
                "Overleaf Sync: Pull Failed",
                "git pull failed",
                "git-pull-failure",
            ),
        )


if __name__ == "__main__":
    unittest.main()
