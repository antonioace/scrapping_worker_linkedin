import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import sync_playwright

import linkedin_jobs as app


AUTH_CARD = '''
<li class="jobs-search-results__list-item" data-occludable-job-id="4480127140">
  <div class="job-card-container" data-job-id="4480127140">
    <a class="job-card-list__title--link" href="/jobs/view/4480127140/?trackingId=private">
      <strong>Developer React</strong><span>Developer React with verification</span>
    </a>
    <div class="artdeco-entity-lockup__subtitle">Empresa Uno</div>
    <div class="artdeco-entity-lockup__caption">Colombia (Remoto)</div>
    <time datetime="2026-10-22">hace 2 horas</time>
  </div>
</li>'''
PUBLIC_CARD = '''
<div class="base-card base-search-card">
  <a class="base-card__full-link" href="https://co.linkedin.com/jobs/view/senior-react-developer-at-ejemplo-4480127141?trackingId=other"></a>
  <h3 class="base-search-card__title">Senior React Developer</h3>
  <h4 class="base-search-card__subtitle">Empresa Dos</h4>
  <span class="job-search-card__location">Bogotá, Colombia</span>
  <time>hace 3 horas</time>
</div>'''


class ExtractionTests(unittest.TestCase):
    def test_exact_filters_and_pagination(self):
        params = parse_qs(urlsplit(app.search_url()).query)
        self.assertEqual(params["keywords"], ["developer react"])
        self.assertEqual(params["location"], ["Colombia"])
        for key, value in {"f_AL": "true", "f_TPR": "r86400", "f_WT": "2", "sortBy": "DD"}.items():
            self.assertEqual(params[key], [value])
        self.assertNotIn("currentJobId", params)
        next_params = parse_qs(urlsplit(app.page_url(app.search_url(), 25)).query)
        self.assertEqual(next_params["start"], ["25"])
        self.assertEqual(next_params["location"], ["Colombia"])

    def test_both_card_layouts_and_nested_duplicates(self):
        jobs = app.parse_jobs(AUTH_CARD + PUBLIC_CARD + AUTH_CARD)
        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[0]["title"], "Developer React")
        self.assertEqual(jobs[0]["company"], "Empresa Uno")
        self.assertEqual(jobs[0]["published"], "2026-10-22")
        self.assertEqual(jobs[1]["id"], "4480127141")
        self.assertEqual(jobs[1]["url"], "https://www.linkedin.com/jobs/view/4480127141/")
        self.assertEqual(jobs[1]["location"], "Bogotá, Colombia")

    def test_invalid_cards_do_not_become_jobs(self):
        self.assertEqual(app.parse_jobs('<div class="base-card"><h3>Publicidad</h3></div>'), [])


class ReportTests(unittest.TestCase):
    def jobs(self, html):
        return [dict(job, description="Experiencia con React y TypeScript.") for job in app.parse_jobs(html)]

    def test_new_jobs_survive_restart_and_csv_is_readable(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            dbpath = output / "jobs.sqlite3"
            conn = app.database(dbpath)
            try:
                first = app.save_report(conn, output, app.search_url(), self.jobs(AUTH_CARD), True)
                self.assertEqual(first["new_count"], 1)
                first_seen = first["jobs"][0]["first_seen"]
            finally:
                conn.close()
            conn = app.database(dbpath)
            try:
                second = app.save_report(conn, output, app.search_url(), self.jobs(AUTH_CARD + PUBLIC_CARD), False)
                self.assertEqual(second["new_count"], 1)
                self.assertFalse(second["jobs"][0]["new"])
                self.assertEqual(second["jobs"][0]["first_seen"], first_seen)
                self.assertEqual(second["jobs"][1]["id"], "4480127141")
                self.assertIn("Bogotá", (output / "latest.csv").read_text(encoding="utf-8-sig"))
                self.assertEqual(json.loads((output / "latest.json").read_text(encoding="utf-8"))["total_found"], 2)
                self.assertIn("Ya vistas", (output / "latest.md").read_text(encoding="utf-8"))
            finally:
                conn.close()

    def test_failed_report_does_not_mark_jobs_seen(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            conn = app.database(output / "jobs.sqlite3")
            try:
                with patch.object(app, "atomic_write", side_effect=OSError("disco lleno")):
                    with self.assertRaises(OSError):
                        app.save_report(conn, output, app.search_url(), self.jobs(AUTH_CARD), False)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 0)
            finally:
                conn.close()

    def test_empty_results_and_csv_formula_protection(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            conn = app.database(output / "jobs.sqlite3")
            try:
                report = app.save_report(conn, output, app.search_url(), [], False)
                self.assertEqual(report["total_found"], 0)
                job = dict(self.jobs(AUTH_CARD)[0], company="=malicious()")
                app.save_report(conn, output, app.search_url(), [job], False)
                self.assertIn("'=malicious()", (output / "latest.csv").read_text(encoding="utf-8-sig"))
            finally:
                conn.close()


class BrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.page = self.browser.new_page()

    def tearDown(self):
        self.page.close()

    def serve(self, html, status=200):
        self.page.route("**/*", lambda route: route.fulfill(status=status, content_type="text/html", body=html))

    def test_collection_in_real_chromium(self):
        self.serve('<html><body><ul>' + AUTH_CARD + PUBLIC_CARD + '</ul><button aria-label="View next page" disabled>Next</button></body></html>')
        jobs, limited = app.collect_jobs(self.page, app.search_url(), 3)
        self.assertEqual(len(jobs), 2)
        self.assertFalse(limited)

    def test_explicit_empty_state(self):
        self.serve('<div class="jobs-search-no-results-banner">No hay ofertas</div>')
        jobs, limited = app.collect_jobs(self.page, app.search_url(), 1)
        self.assertEqual(jobs, [])
        self.assertFalse(limited)

    def test_description_is_read_only_from_the_job_detail(self):
        self.serve('<h1>React Developer</h1><div id="job-details"><p>Python y FastAPI.</p></div><footer>Angular</footer>')
        text = app.read_job_description(self.page, {"id": "123", "url": "https://www.linkedin.com/jobs/view/123/"})
        self.assertEqual(text, "Python y FastAPI.")

    def test_login_and_rate_limit_stop_collection(self):
        self.serve('<input id="username" name="session_key">')
        with self.assertRaises(app.SessionRequired):
            app.collect_jobs(self.page, app.search_url(), 1)
        self.page.unroute("**/*")
        self.serve("Too many requests", status=429)
        with self.assertRaises(app.AccessBlocked):
            app.collect_jobs(self.page, app.search_url(), 1)


if __name__ == "__main__":
    unittest.main()
