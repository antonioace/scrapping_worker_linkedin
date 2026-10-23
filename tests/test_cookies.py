import argparse
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import linkedin_jobs as app
from linkedin_cookies import CookieFileError, load_cookies


class CookieImportTests(unittest.TestCase):
    def load(self, data):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "cookies.json"
            path.write_text(json.dumps(data), encoding="utf-8-sig")
            return load_cookies(path)

    def test_export_normalizes_browser_fields_and_skips_expired_auxiliary_cookie(self):
        cookies = self.load({"cookies": [
            {"name": "li_at", "value": "test-session", "domain": ".linkedin.com",
             "expirationDate": time.time() + 3600, "sameSite": "no_restriction", "hostOnly": False},
            {"name": "JSESSIONID", "value": '"test-csrf"', "sameSite": "unspecified"},
            {"name": "old", "value": "expired", "expires": 1},
        ]})
        self.assertEqual(len(cookies), 2)
        self.assertEqual(cookies[0]["sameSite"], "None")
        self.assertNotIn("hostOnly", cookies[0])
        self.assertNotIn("expirationDate", cookies[0])
        self.assertEqual(cookies[1]["value"], '"test-csrf"')

    def test_rejects_invalid_sessions_without_echoing_cookie_values(self):
        for data in (
            [{"name": "li_at", "value": ""}],
            [{"name": "li_at", "value": "private-test-value", "domain": "linkedin.com.evil.test"}],
            [{"name": "li_at", "value": "private-test-value", "expires": 1}],
            [{"name": "li_at", "value": "private-test-value", "httpOnly": "true"}],
            [{"name": "JSESSIONID", "value": "private-test-value"}],
        ):
            with self.subTest(data_type=type(data).__name__):
                with self.assertRaises(CookieFileError) as error:
                    self.load(data)
                self.assertNotIn("private-test-value", str(error.exception))

    def test_first_start_with_json_skips_interactive_login(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cookie_file = root / "cookies.json"
            cookie_file.write_text('[{"name":"li_at","value":"test-session"}]', encoding="utf-8")
            args = argparse.Namespace(
                profile=root / "missing-profile", cookies_file=cookie_file,
                output=root / "reports", api_url="", api_token="",
                sync_only=False, login=False, require_session=True,
                keywords="react", location="Colombia",
            )
            with patch.object(app, "arguments", return_value=args), \
                 patch.object(app, "sync_playwright", return_value=MagicMock()), \
                 patch.object(app, "login") as login, patch.object(app, "monitor") as monitor:
                self.assertEqual(app.main(), 0)
                login.assert_not_called()
                self.assertEqual(monitor.call_args.kwargs["cookies"][0]["name"], "li_at")

    def test_monitor_installs_cookies_before_first_query_and_closes_profile(self):
        with tempfile.TemporaryDirectory() as folder:
            playwright = MagicMock()
            context = playwright.chromium.launch_persistent_context.return_value
            context.cookies.return_value = [{"name": "li_at"}]
            args = argparse.Namespace(profile=Path(folder) / "profile", visible=False,
                                      output=Path(folder) / "reports", api_url="", once=True, max_pages=1)
            cookies = [{"name": "li_at", "value": "test-session", "domain": ".linkedin.com", "path": "/"}]
            with patch.object(app, "collect_jobs", return_value=([], False)) as collect:
                app.monitor(playwright, args, app.search_url(), cookies=cookies)
                context.clear_cookies.assert_called_once_with()
                context.add_cookies.assert_called_once_with(cookies)
                collect.assert_called_once()
                context.close.assert_called_once()

    def test_browser_import_error_does_not_expose_values(self):
        context = MagicMock()
        playwright = MagicMock()
        playwright.chromium.launch_persistent_context.return_value = context
        context.add_cookies.side_effect = app.BrowserError("private-test-value")
        args = argparse.Namespace(profile=Path("test-profile"), visible=False)
        with self.assertRaises(app.SessionRequired) as error:
            app.monitor(playwright, args, app.search_url(), cookies=[])
        self.assertNotIn("private-test-value", str(error.exception))
        context.close.assert_called_once()

    def test_explicit_login_ignores_cookie_path_from_environment(self):
        with patch.dict(os.environ, {"LINKEDIN_COOKIES_FILE": "missing.json"}), \
             patch("sys.argv", ["linkedin_jobs.py", "--login"]):
            self.assertIsNone(app.arguments().cookies_file)


if __name__ == "__main__":
    unittest.main()
