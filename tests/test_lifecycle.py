import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import linkedin_jobs as app


def options(folder: str) -> argparse.Namespace:
    root = Path(folder)
    return argparse.Namespace(
        profile=root / "profile", output=root / "reports", api_url="", api_token="",
        sync_only=False, cookies_file=root / "cookies.json", keywords="react", location="Colombia",
    )


class LifecycleTests(unittest.TestCase):
    def test_missing_cookie_file_stops_before_starting_chromium(self):
        with tempfile.TemporaryDirectory() as folder:
            args = options(folder)
            with patch.object(app, "arguments", return_value=args), \
                 patch.object(app, "sync_playwright") as playwright:
                self.assertEqual(app.main(), 1)
                playwright.assert_not_called()
            status = json.loads((args.output / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(status["status"], "stopped")

    def test_stop_is_queued_even_when_api_is_unreachable(self):
        with tempfile.TemporaryDirectory() as folder:
            args = options(folder)
            args.api_url = "http://unreachable.example.test"
            args.api_token = "test-token"
            with patch.object(app, "flush_outbox", return_value=False):
                app.save_stopped_status(args, app.search_url(), "Operator stopped the monitor")
            conn = app.database(args.output / "jobs.sqlite3")
            try:
                queued = json.loads(conn.execute("SELECT payload FROM api_outbox").fetchone()[0])
                self.assertEqual(queued["status"], "stopped")
                self.assertNotIn("api_token", queued)
            finally:
                conn.close()

    def test_operator_stop_records_inactive_status(self):
        with tempfile.TemporaryDirectory() as folder:
            args = options(folder)
            args.cookies_file.write_text('[{"name":"li_at","value":"test-session"}]', encoding="utf-8")
            with patch.object(app, "arguments", return_value=args), \
                 patch.object(app, "sync_playwright", return_value=MagicMock()), \
                 patch.object(app, "monitor", side_effect=KeyboardInterrupt):
                self.assertEqual(app.main(), 0)
            status = json.loads((args.output / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(status["status"], "stopped")


if __name__ == "__main__":
    unittest.main()
