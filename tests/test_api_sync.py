import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import URLError

import api_sync
import linkedin_jobs as app


class ApiSyncTests(unittest.TestCase):
    def test_report_and_queue_are_saved_together(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            conn = app.database(output / "jobs.sqlite3")
            try:
                jobs = [{"id": "123", "title": "React Developer", "company": "Empresa",
                         "location": "Colombia", "published": "", "url": "https://www.linkedin.com/jobs/view/123/"}]
                report = app.save_report(conn, output, app.search_url(), jobs, False, queue_sync=True)
                queued = json.loads(conn.execute("SELECT payload FROM api_outbox").fetchone()[0])
                self.assertEqual(queued["run_id"], report["run_id"])
                self.assertEqual(queued["jobs"][0]["id"], "123")
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 1)
            finally:
                conn.close()

    def test_unacknowledged_runs_survive_failure_and_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            dbpath = Path(folder) / "jobs.sqlite3"
            conn = app.database(dbpath)
            with conn:
                run_id = api_sync.enqueue(conn, {"checked_at": app.now(), "status": "ok", "jobs": []})
            opener = MagicMock()
            opener.open.side_effect = URLError("offline")
            with patch.object(api_sync, "build_opener", return_value=opener):
                self.assertFalse(api_sync.flush_outbox(conn, "http://localhost:8000", "test-token"))
            conn.close()
            conn = app.database(dbpath)
            try:
                response = MagicMock()
                response.status = 200
                response.read.return_value = json.dumps({"run_id": run_id}).encode()
                opener.open.side_effect = None
                opener.open.return_value.__enter__.return_value = response
                with patch.object(api_sync, "build_opener", return_value=opener):
                    self.assertTrue(api_sync.flush_outbox(conn, "http://localhost:8000", "test-token"))
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM api_outbox").fetchone()[0], 0)
            finally:
                conn.close()

    def test_wrong_acknowledgement_is_not_dropped(self):
        with sqlite3.connect(":memory:") as conn:
            api_sync.init_outbox(conn)
            with conn:
                api_sync.enqueue(conn, {"checked_at": app.now(), "status": "ok", "jobs": []})
            response = MagicMock(status=200)
            response.read.return_value = b'{"run_id":"different"}'
            opener = MagicMock()
            opener.open.return_value.__enter__.return_value = response
            with patch.object(api_sync, "build_opener", return_value=opener):
                self.assertFalse(api_sync.flush_outbox(conn, "http://localhost:8000", "test-token"))
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM api_outbox").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
