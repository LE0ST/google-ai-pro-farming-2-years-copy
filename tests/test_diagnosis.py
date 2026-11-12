"""Offline regression tests for the read-only console surface."""
from __future__ import annotations

import asyncio
import inspect
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import db
from app.config import settings
from app.main import create_app
from app.runner import diagnosis
from app.sites.google_one import discover


class ReadOnlySurfaceTests(unittest.TestCase):
    def test_application_starts_with_fresh_data_and_serves_health(self):
        async def run():
            with tempfile.TemporaryDirectory() as directory, patch.object(settings, "data_dir", Path(directory)):
                app = create_app()
                async with app.router.lifespan_context(app):
                    health = next(route for route in app.routes if getattr(route, "path", None) == "/healthz")
                    response = await health.endpoint()
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.body, b'{"ok":true,"mode":"read_only_diagnostics","browser":"Playwright-managed Chromium"}')
                    self.assertTrue((Path(directory) / "console.token").is_file())
                    self.assertTrue((Path(directory) / "db.sqlite3").is_file())
        asyncio.run(run())

    def test_no_transaction_or_secret_routes_are_registered(self):
        routes = [(route.path, method) for route in create_app().routes
                  for method in getattr(route, "methods", set())]
        paths = {path for path, _ in routes}
        self.assertIn("/accounts/{acc_id}/diagnose", paths)
        self.assertIn("/api/diagnosis/{acc_id}", paths)
        forbidden = ("/run", "/queue", "/cards", "/secret", "/checkout", "/cancel", "/subscribe")
        self.assertFalse([path for path in paths if any(part in path.lower() for part in forbidden)])
        self.assertEqual({method for path, method in routes if method == "POST"}, {"POST"})
        self.assertTrue(all(path in {"/profiles/add", "/accounts/{acc_id}/diagnose"}
                            for path, method in routes if method == "POST"))

    def test_transaction_modules_and_hook_are_absent(self):
        root = Path(__file__).resolve().parents[1]
        forbidden = (
            "app/runner/queue.py", "app/runner/operations.py", "app/vault.py",
            "app/secrets.py", "app/totp.py", "app/sites/google_one/login.py",
            "app/sites/google_one/checkout.py", "app/sites/google_one/hook.js",
            "app/sites/google_one/tokens.py", "tasks/subscribe_flow.py", "tasks/cancel.py",
        )
        for relative in forbidden:
            with self.subTest(path=relative):
                self.assertFalse((root / relative).exists())

    def test_network_policy_requires_https_read_methods_and_safe_destinations(self):
        allowed = "https://play.google.com/store/account/subscriptions"
        self.assertIsNone(diagnosis.request_block_reason(allowed, "GET"))
        self.assertEqual(diagnosis.request_block_reason(allowed, "POST"), "non_read_method")
        self.assertEqual(diagnosis.request_block_reason("http://play.google.com/store/account/subscriptions", "GET"),
                         "unsupported_scheme")
        self.assertEqual(diagnosis.request_block_reason("https://payments.google.com/", "GET"), "payment_host")
        self.assertEqual(diagnosis.request_block_reason("https://one.google.com/checkout", "GET"),
                         "sensitive_destination")
        self.assertEqual(diagnosis.request_block_reason("https://example.invalid/", "GET"), "untrusted_host")

    def test_browser_launch_keeps_sandbox_and_tls_verification(self):
        source = inspect.getsource(diagnosis.DiagnosticRunner._run)
        self.assertIn("headless=True", source)
        self.assertIn("chromium_sandbox=True", source)
        self.assertIn("ignore_https_errors=False", source)

    def test_diagnostic_does_not_capture_screenshots_or_persist_account_paths(self):
        self.assertNotIn("screenshot_path", inspect.signature(discover.diagnose_page).parameters)
        self.assertNotIn("page.screenshot", inspect.getsource(discover.diagnose_page))
        self.assertEqual(
            discover._safe_route_path("https://one.google.com/u/123456/settings?private=value"),
            "/settings",
        )
        self.assertEqual(
            discover._safe_route_path("https://one.google.com/settings/private-account-id"),
            "/settings/detail",
        )

    def test_profile_label_creation_stores_no_credentials(self):
        async def run():
            with tempfile.TemporaryDirectory() as directory, \
                 patch.object(settings, "data_dir", Path(directory)), \
                 patch.object(db, "execute", new=AsyncMock()) as execute:
                from app.main import create_app
                route = next(r for r in create_app().routes if getattr(r, "path", None) == "/profiles/add")
                response = await route.endpoint(label="demo@example.invalid")
                self.assertEqual(response.status_code, 201)
                self.assertEqual(execute.await_count, 1)
                sql, params = execute.await_args.args
                self.assertIn("INSERT INTO accounts", sql)
                self.assertIn("demo@example.invalid", params)
                self.assertNotIn("password", sql.lower())
                self.assertNotIn("secret_ref", sql.lower())
                self.assertNotIn("totp", sql.lower())
                self.assertEqual(len(list((Path(directory) / "profiles").iterdir())), 1)
        asyncio.run(run())

    def test_legacy_transaction_database_is_refused(self):
        async def run():
            import aiosqlite
            for version in (1, 2):
                with self.subTest(version=version), tempfile.TemporaryDirectory() as directory, \
                     patch.object(settings, "data_dir", Path(directory)):
                    settings.ensure_dirs()
                    async with aiosqlite.connect(settings.db_path) as connection:
                        await connection.execute("CREATE TABLE schema_meta (version INTEGER NOT NULL)")
                        await connection.execute("INSERT INTO schema_meta VALUES (?)", (version,))
                        await connection.commit()
                    with self.assertRaisesRegex(RuntimeError, "new empty GAI_DATA_DIR"):
                        await db.migrate()
        asyncio.run(run())

    def test_unversioned_legacy_database_is_refused_without_schema_changes(self):
        async def run():
            import aiosqlite
            with tempfile.TemporaryDirectory() as directory, patch.object(settings, "data_dir", Path(directory)):
                settings.ensure_dirs()
                async with aiosqlite.connect(settings.db_path) as connection:
                    await connection.execute("CREATE TABLE legacy_accounts (email TEXT)")
                    await connection.commit()
                with self.assertRaisesRegex(RuntimeError, "unversioned database"):
                    await db.migrate()
                async with aiosqlite.connect(settings.db_path) as connection:
                    names = {row[0] for row in await (await connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                    )).fetchall()}
                self.assertEqual(names, {"legacy_accounts"})
        asyncio.run(run())

    def test_diagnosis_reads_cursor_without_mutating_it(self):
        async def run():
            with tempfile.TemporaryDirectory() as directory, patch.object(settings, "data_dir", Path(directory)):
                acc_id = "acc_123456789abc"
                profile = settings.profiles_dir / f"profile_{acc_id}"
                profile.mkdir(parents=True)
                with patch.object(db, "fetch_one", AsyncMock(return_value={"id": acc_id, "step": 1})), \
                     patch.object(db, "execute", new=AsyncMock()) as write:
                    runner = diagnosis.DiagnosticRunner()
                    async def finish(*_args):
                        runner.current = None
                    runner._run = AsyncMock(side_effect=finish)
                    self.assertEqual(await runner.start(acc_id), "")
                    await runner.task
                    write.assert_not_awaited()
                    self.assertIsNone(runner.current)
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
