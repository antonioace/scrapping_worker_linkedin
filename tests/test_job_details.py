import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import linkedin_jobs as app
from job_details import DescriptionUnavailable, extract_description, select_jobs
from stack_filter import PATTERNS, POLICY_VERSION, matching_technologies


def candidate(identity, description=""):
    return {"id": str(identity), "title": "Developer", "company": "Acme", "location": "Colombia",
            "published": "", "url": f"https://www.linkedin.com/jobs/view/{identity}/",
            "description": description}


class JobDescriptionTests(unittest.TestCase):
    def test_framework_aliases_and_generic_languages(self):
        for text, expected in (
            ("React.js y TypeScript", ["React"]), ("NEXT.JS", ["Next.js"]),
            ("angular 18", ["Angular"]), ("VueJS", ["Vue"]),
            ("Svelte Kit", ["SvelteKit"]), ("Express.js y Node", ["Express"]),
            ("Nest.js", ["NestJS"]), ("Python con FastAPI", ["FastAPI"]),
            ("Python y Django", []), ("TypeScript y JavaScript", []),
            ("We express our interest in Python developers", []),
            ("American Express busca analista de datos", []), ("Reactive systems", []),
            ("React y Python con Django", ["React"]),
        ):
            with self.subTest(text=text):
                self.assertEqual(matching_technologies(text), expected)

    def test_description_scope_and_jsonld(self):
        text = extract_description('<h1>React</h1><div id="job-details">Python y Django</div>', "1")
        self.assertEqual(matching_technologies(text), [])
        public = ('<div class="show-more-less-html__markup">React en otra sección</div>'
                  '<section class="description"><div class="description__text">Python Django</div></section>')
        self.assertEqual(extract_description(public, "1"), "Python Django")
        structured = {"@graph": [{"@type": "JobPosting", "url": "https://www.linkedin.com/jobs/view/1/",
                                  "description": "<p>Experiencia con <b>Next.js</b></p>"}]}
        html = '<script type="application/ld+json">' + json.dumps(structured) + '</script>'
        self.assertIn("Next.js", extract_description(html, "1"))
        self.assertEqual(extract_description(html, "2"), "")

    def test_cached_rejections_store_no_description_and_avoid_repeated_requests(self):
        with tempfile.TemporaryDirectory() as folder:
            conn = app.database(Path(folder) / "jobs.sqlite3")
            try:
                reader = Mock(side_effect=["React con TypeScript", "Python con Django"])
                jobs = [candidate(1), candidate(2)]
                accepted, incomplete, _ = select_jobs(conn, jobs, reader)
                self.assertEqual([job["id"] for job in accepted], ["1"])
                self.assertFalse(incomplete)
                self.assertEqual(conn.execute("SELECT description FROM job_description_cache WHERE id='2'").fetchone()[0], "")
                reader.reset_mock()
                again, _, _ = select_jobs(conn, jobs, reader)
                reader.assert_not_called()
                self.assertEqual(again, accepted)
            finally:
                conn.close()

    def test_detail_limit_leaves_unread_jobs_pending_for_later_cycles(self):
        with tempfile.TemporaryDirectory() as folder:
            conn = app.database(Path(folder) / "jobs.sqlite3")
            try:
                reader = Mock(return_value="FastAPI")
                jobs = [candidate(1), candidate(2)]
                accepted, incomplete, _ = select_jobs(conn, jobs, reader, max_details=1)
                self.assertTrue(incomplete)
                self.assertEqual(len(accepted), 1)
                accepted, incomplete, _ = select_jobs(conn, jobs, reader, max_details=1)
                self.assertFalse(incomplete)
                self.assertEqual(len(accepted), 2)
                self.assertEqual(reader.call_count, 2)
            finally:
                conn.close()

    def test_unread_description_is_not_cached_or_saved(self):
        with tempfile.TemporaryDirectory() as folder:
            conn = app.database(Path(folder) / "jobs.sqlite3")
            try:
                with self.assertRaises(DescriptionUnavailable):
                    select_jobs(conn, [candidate(1)], Mock(return_value=""))
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM job_description_cache").fetchone()[0], 0)
                report = app.save_report(conn, Path(folder), app.search_url(),
                                         [candidate(1, "Python Django"), candidate(2, "FastAPI")], False, queue_sync=True)
                self.assertEqual([job["id"] for job in report["jobs"]], ["2"])
                queued = json.loads(conn.execute("SELECT payload FROM api_outbox").fetchone()[0])
                self.assertEqual([job["id"] for job in queued["jobs"]], ["2"])
            finally:
                conn.close()

    def test_backend_and_scraper_share_the_same_policy(self):
        path = Path(__file__).resolve().parents[2] / "backend/app/features/job_feed/stack_filter.py"
        if not path.is_file():
            self.skipTest("Backend sibling checkout is unavailable")
        spec = importlib.util.spec_from_file_location("backend_stack_policy", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.PATTERNS, PATTERNS)
        self.assertEqual(module.POLICY_VERSION, POLICY_VERSION)
