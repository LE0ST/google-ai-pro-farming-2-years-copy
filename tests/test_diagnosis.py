"""Offline diagnostic action tests. No browser or external network is started."""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

os.environ["GAI_DATA_DIR"] = tempfile.mkdtemp(prefix="gai-diagnostic-test-")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import db  # noqa: E402
from app.main import create_app  # noqa: E402
from app.config import settings  # noqa: E402
from app.runner import diagnosis  # noqa: E402
from app.sites.google_one import discover  # noqa: E402


async def request(app, path: str, method: str, headers: list[tuple[bytes, bytes]], body: bytes = b""):
    messages = []
    sent = False

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
             "method": method, "scheme": "http", "path": path, "raw_path": path.encode(),
             "query_string": b"", "headers": headers, "client": ("127.0.0.1", 1),
             "server": ("127.0.0.1", 8765), "root_path": ""}
    await app(scope, receive, send)
    return next(m["status"] for m in messages if m["type"] == "http.response.start")


class DiagnosticTests(unittest.TestCase):
    def test_active_verification_visible_in_state_and_template(self):
        import json
        from jinja2 import Environment, FileSystemLoader
        from app.runner.outcomes import ACTIVE_VERIFICATION_MESSAGE
        async def run():
            row = {"id": "acc_123456789abc", "email": "demo@example.invalid", "totp_ref": None,
                   "status": "active_needs_verification", "step": 1, "step_message": ACTIVE_VERIFICATION_MESSAGE}
            app = create_app()
            endpoint = next(route.endpoint for route in app.routes if getattr(route, "path", None) == "/api/state")
            with patch.object(db, "fetch_all", AsyncMock(return_value=[row])):
                response = await endpoint()
            result = json.loads(response.body)["accounts"][row["id"]]
            self.assertEqual(result["step"], 1)
            self.assertEqual(result["status"], "active_needs_verification")
            self.assertEqual(result["message"], ACTIVE_VERIFICATION_MESSAGE)
            env = Environment(loader=FileSystemLoader(str(Path(__file__).resolve().parents[1] / "app/templates")))
            html = env.get_template("index.html").render(accounts=[{**row, "message": result["message"]}], cards=[])
            self.assertIn("Active — stage needs verification", html)
            self.assertIn(ACTIVE_VERIFICATION_MESSAGE, html)
        asyncio.run(run())

    def test_terminal_worker_closes_browser_without_transactions(self):
        from tasks import subscribe_flow as flow
        from app.runner.outcomes import RunOutcome, ACTIVE_NEEDS_VERIFICATION_EXIT
        async def run():
            page = SimpleNamespace()
            ctx = SimpleNamespace(pages=[page], close=AsyncMock())
            manager = SimpleNamespace(__aenter__=AsyncMock(), __aexit__=AsyncMock())
            from unittest.mock import MagicMock
            manager = MagicMock()
            manager.__aenter__ = AsyncMock(return_value=SimpleNamespace())
            manager.__aexit__ = AsyncMock(return_value=False)
            with patch.object(sys, "argv", ["subscribe_flow.py", "demo@example.invalid"]), \
                 patch.object(flow, "async_playwright", return_value=manager), \
                 patch.object(flow.db, "migrate", AsyncMock()), \
                 patch.object(flow.db, "fetch_one", AsyncMock(return_value={"step": 1})), \
                 patch.object(flow.db, "execute", AsyncMock()) as write, \
                 patch.object(flow.vault, "account_creds", AsyncMock(return_value=("acc_test", "fictitious", None))), \
                 patch.object(flow.browser, "launch", AsyncMock(return_value=ctx)), \
                 patch.object(flow.nav, "goto", AsyncMock()), \
                 patch.object(flow.egress, "observe", AsyncMock(return_value={"country": "US", "ip": "127.0.0.1", "org": "mock"})), \
                 patch.object(flow.egress, "matches", return_value=True), \
                 patch.object(flow.login, "ensure_signed_in", AsyncMock(return_value=True)), \
                 patch.object(flow, "run_layers", AsyncMock(return_value=RunOutcome.ACTIVE_NEEDS_VERIFICATION)), \
                 patch.object(flow, "cancel", AsyncMock()) as cancel, \
                 patch.object(flow, "release_card", AsyncMock()) as release:
                self.assertEqual(await flow.main(), ACTIVE_NEEDS_VERIFICATION_EXIT)
                ctx.close.assert_awaited_once()
                cancel.assert_not_awaited()
                release.assert_not_awaited()
                write.assert_not_awaited()
        asyncio.run(run())

    def test_run_active_terminal_outcome_and_queue_persistence(self):
        from tasks import subscribe_flow as flow
        from app.runner import queue
        from app.runner.outcomes import RunOutcome, ACTIVE_NEEDS_VERIFICATION_EXIT

        async def run():
            for text in ("Google AI Pro active", "Google AI Pro active USD 0/month for 12 months"):
                first = discover.observe_subscription_text(text)
                obs = discover.contrast_sources(first, first)
                rec = discover.reconcile_progress(obs, 1)
                with patch.object(flow.diagnosis, "diagnose_run_page", AsyncMock(return_value=(obs, rec))), \
                     patch.object(flow, "open_dialog", AsyncMock()) as checkout, \
                     patch.object(flow, "cancel", AsyncMock()) as cancel, \
                     patch.object(db, "execute", AsyncMock()) as write:
                    outcome = await flow.run_layers(SimpleNamespace(), "demo@example.invalid", "acc_test", [1], False, local_step=1)
                    self.assertIs(outcome, RunOutcome.ACTIVE_NEEDS_VERIFICATION)
                    checkout.assert_not_awaited()
                    cancel.assert_not_awaited()
                    write.assert_not_awaited()
            settings.ensure_dirs()
            for code, expected in ((ACTIVE_NEEDS_VERIFICATION_EXIT, "active_needs_verification"), (1, "failed")):
                proc = SimpleNamespace(returncode=code, wait=AsyncMock(return_value=code))
                runner = queue.Runner()
                with patch.object(db, "fetch_one", AsyncMock(return_value={"email": "demo@example.invalid", "step": 1})), \
                     patch.object(db, "execute", AsyncMock()) as write, \
                     patch.object(queue.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)):
                    await runner._run("acc_123456789abc")
                    sql, params = write.await_args.args
                    self.assertEqual(params[0], expected)
                    self.assertTrue(params[1])
                    self.assertNotIn("SET step =", sql)
                    self.assertIsNone(runner.current)
                    self.assertIsNone(runner.proc)
        asyncio.run(run())

    def test_one_transient_body_timeout_and_late_plan(self):
        async def run():
            page = SimpleNamespace(url=discover.ONE_SETTINGS_URL,
                goto=AsyncMock(return_value=None), inner_text=AsyncMock(side_effect=[
                    TimeoutError("private-token"), "", "Loading",
                    "Plan actual Google AI Pro active USD 0/mes durante 12 meses"]),
                evaluate=AsyncMock(return_value=[]))
            with patch.object(db, "execute") as write:
                obs, rec = await discover.diagnose_page(page, 1, source_url=discover.ONE_SETTINGS_URL,
                    timeout_ms=200, poll_interval=0.01)
                write.assert_not_called()
            self.assertEqual(page.inner_text.await_count, 4)
            self.assertEqual(obs.status, discover.SubscriptionStatus.ACTIVE_DURATION)
            self.assertEqual(obs.ai_pro_promo_months, 12)
            self.assertEqual(obs.details.ai_pro_promo_source, "google_one")
            self.assertIsNone(obs.renewal_date)
            self.assertIsNone(obs.ai_pro_promo_end_date)
            self.assertEqual(rec.local_step, 1)
            self.assertEqual(rec.status, discover.ReconciliationStatus.INDETERMINATE)
            self.assertIn("TimeoutError", repr(obs.read_events))
            self.assertNotIn("private-token", repr(obs))
        asyncio.run(run())

    def test_one_permanent_timeout_is_bounded_and_preserves_play_evidence(self):
        async def run():
            page = SimpleNamespace(url=discover.ONE_SETTINGS_URL, goto=AsyncMock(return_value=None),
                inner_text=AsyncMock(side_effect=TimeoutError("private-token")))
            obs, rec = await discover.diagnose_page(page, 1, source_url=discover.ONE_SETTINGS_URL,
                timeout_ms=40, poll_interval=0.01)
            self.assertGreater(page.inner_text.await_count, 1)
            self.assertEqual(obs.status, discover.SubscriptionStatus.DETECTION_FAILED)
            self.assertIn("page-read timeout", obs.reason)
            self.assertLess(obs.elapsed_ms, 1000)
            play = discover.observe_subscription_text(
                "Google AI Pro + YouTube Premium Next payment: $19.99 on May 21, 2028")
            combined = discover.contrast_sources(play, obs)
            self.assertEqual(combined.details, play.details)
            self.assertIsNone(combined.renewal_date)
            self.assertEqual(rec.local_step, 1)
            self.assertEqual(rec.status, discover.ReconciliationStatus.INDETERMINATE)
        asyncio.run(run())

    def test_one_navigation_has_separate_extraction_budget(self):
        async def run():
            async def navigate(*args, **kwargs):
                await asyncio.sleep(0.04)
            async def body(*args, **kwargs):
                await asyncio.sleep(0.04)
                return "Current plan Google AI Pro active USD 0/month for 12 months"
            page = SimpleNamespace(url=discover.ONE_SETTINGS_URL, goto=navigate, inner_text=body,
                evaluate=AsyncMock(return_value=[]))
            obs, _ = await discover.diagnose_page(page, 1, source_url=discover.ONE_SETTINGS_URL, timeout_ms=70)
            self.assertEqual(obs.status, discover.SubscriptionStatus.ACTIVE_DURATION)
            self.assertEqual([e["stage"] for e in obs.read_events], ["navigation", "navigation-result", "body-extraction", "render-observation", "final-classification"])
        asyncio.run(run())

    def test_one_partial_page_and_navigation_error_remain_indeterminate(self):
        async def run():
            page = SimpleNamespace(url=discover.ONE_SETTINGS_URL, goto=AsyncMock(return_value=None),
                inner_text=AsyncMock(return_value="Loading current plan"), evaluate=AsyncMock(return_value=[]))
            obs, rec = await discover.diagnose_page(page, 1, source_url=discover.ONE_SETTINGS_URL,
                timeout_ms=40, poll_interval=0.01)
            self.assertEqual(obs.status, discover.SubscriptionStatus.DETECTION_FAILED)
            self.assertEqual(obs.reason, "loading")
            self.assertIsNone(rec.detected_layer)
            page.goto.side_effect = TimeoutError("secret")
            obs, _ = await discover.diagnose_page(page, 1, source_url=discover.ONE_SETTINGS_URL, timeout_ms=40)
            self.assertIn("navigation timeout", obs.reason)
            self.assertNotIn("secret", repr(obs))
        asyncio.run(run())

    def test_one_settings_links_to_late_current_plan_without_recursive_navigation(self):
        async def run():
            page = SimpleNamespace(url=discover.ONE_SETTINGS_URL)
            async def navigate(url, **kwargs):
                page.url = url
            page.goto = AsyncMock(side_effect=navigate)
            page.inner_text = AsyncMock(side_effect=["Email preferences", "Loading",
                "Current plan Google AI Pro active USD 0/month for 12 months"])
            async def evaluate(script):
                return ["https://one.google.com/plans"] if "querySelectorAll('a[href]')" in script else []
            page.evaluate = evaluate
            obs, _ = await discover.diagnose_page(page, 1, source_url=discover.ONE_SETTINGS_URL,
                timeout_ms=1000, poll_interval=0.01)
            self.assertEqual(obs.status, discover.SubscriptionStatus.ACTIVE_DURATION)
            self.assertEqual(obs.details.ai_pro_promo_source, "google_one_plan_details")
            self.assertEqual(page.goto.await_count, 2)
            self.assertIsNone(obs.renewal_date)
        asyncio.run(run())

    def test_settings_preferences_with_nonfatal_pageerror_are_not_applicable(self):
        async def run():
            callbacks = {}
            page = SimpleNamespace(url=discover.ONE_SETTINGS_URL,
                inner_text=AsyncMock(return_value="Email preferences Manage email notifications"),
                evaluate=AsyncMock(return_value={"title": 1, "navigation": 1, "links": 2,
                                                "plan_terms": 0, "preference_terms": 1}))
            page.on = lambda event, callback: callbacks.__setitem__(event, callback)
            page.remove_listener = lambda *args: None
            async def navigate(*args, **kwargs):
                callbacks["pageerror"](RuntimeError("private content"))
                return SimpleNamespace(status=200)
            page.goto = AsyncMock(side_effect=navigate)
            obs, rec = await discover.diagnose_page(page, 1, source_url=discover.ONE_SETTINGS_URL)
            callbacks["pageerror"](RuntimeError("private content"))
            self.assertEqual(obs.status, discover.SubscriptionStatus.NOT_APPLICABLE)
            self.assertEqual(rec.status, discover.ReconciliationStatus.INDETERMINATE)
            self.assertIn("page-structure", [e["stage"] for e in obs.read_events])
            self.assertNotIn("private content", repr(obs))
            self.assertEqual(diagnosis.guard_not_applicable(obs, 1).status,
                             discover.SubscriptionStatus.NOT_APPLICABLE)
        asyncio.run(run())

    def test_ai_pro_duration_does_not_infer_renewal_or_cursor(self):
        for text in ("Google AI Pro (5 TB) active · USD 0/month for 12 months · then USD 19.99/month",
                     "Google AI Pro (5 TB) activo · USD 0/mes durante 12 meses · después USD 19.99/mes"):
            observation = discover.observe_subscription_text(text)
            self.assertEqual(observation.status, discover.SubscriptionStatus.ACTIVE_DURATION)
            self.assertEqual(observation.ai_pro_promo_months, 12)
            self.assertIsNone(observation.renewal_date)
            self.assertIsNone(observation.ai_pro_promo_end_date)
            reconciliation = discover.reconcile_progress(observation, 1)
            self.assertEqual(reconciliation.status, discover.ReconciliationStatus.INDETERMINATE)
            self.assertIsNone(reconciliation.detected_layer)
        addon = discover.observe_subscription_text("YouTube Premium active · USD 0/month for 12 months")
        self.assertIsNone(addon.ai_pro_promo_months)
        self.assertNotEqual(addon.status, discover.SubscriptionStatus.ACTIVE_DURATION)
        first = discover.observe_subscription_text("Google AI Pro active · USD 0/month for 12 months")
        second = discover.observe_subscription_text("Google AI Pro active · USD 0/month for 6 months")
        self.assertEqual(discover.contrast_sources(first, second).status, discover.SubscriptionStatus.DETECTION_FAILED)
        self.assertEqual(discover.merge_observations(first, second).status, discover.SubscriptionStatus.INCONSISTENT)

    def test_condition_reporting_rejects_non_boolean_and_private_data(self):
        page = SimpleNamespace(evaluate=AsyncMock(return_value={"page_subscription": True,
            "selected_tab": False, "empty_title": "private@example.invalid", "title_tags": ["H1", "PRIVATE"],
            "container_tag": "DIV"}))
        result = asyncio.run(diagnosis.empty_state_conditions(page))
        self.assertIs(result["selected_tab"], False)
        self.assertIsNone(result["empty_title"])
        self.assertIn("selected_tab", result["blocking_conditions"])
        self.assertNotIn("private@example.invalid", repr(result))
        self.assertEqual(result["title_tags"], ["H1"])
        self.assertEqual(len(diagnosis.detector_version()["sha256"]), 64)

    def test_settings_errors_do_not_become_not_applicable(self):
        settings_page = discover.SubscriptionObservation(discover.SubscriptionStatus.NOT_APPLICABLE)
        self.assertEqual(diagnosis.guard_not_applicable(settings_page, 0).status,
                         discover.SubscriptionStatus.NOT_APPLICABLE)
        self.assertEqual(diagnosis.guard_not_applicable(settings_page, 1).status,
                         discover.SubscriptionStatus.NOT_APPLICABLE)

    def test_failure_categories_and_redacted_javascript_errors(self):
        failure = discover.SubscriptionObservation(discover.SubscriptionStatus.DETECTION_FAILED,
                                                   reason="unauthenticated")
        self.assertEqual(diagnosis.source_failure_category(failure, 2), "authentication_required")
        failure = discover.SubscriptionObservation(discover.SubscriptionStatus.DETECTION_FAILED,
                                                   reason="unknown content")
        self.assertEqual(diagnosis.source_failure_category(failure, 2), "rendering_failed")
        self.assertEqual(diagnosis.source_failure_category(failure, 0), "insufficient_evidence")
        evidence = diagnosis.NetworkEvidence()
        evidence.page_error(Exception("TypeError: sensitive@example.invalid secret=123456"))
        output = repr(evidence.summary())
        self.assertIn("TypeError", output)
        self.assertNotIn("sensitive", output)
        self.assertNotIn("123456", output)

    def test_sources_require_explicit_agreement(self):
        empty = discover.SubscriptionObservation(discover.SubscriptionStatus.NO_SUBSCRIPTION, months_remaining=0)
        active = discover.SubscriptionObservation(discover.SubscriptionStatus.ACTIVE_UNKNOWN)
        incomplete = discover.SubscriptionObservation(discover.SubscriptionStatus.DETECTION_FAILED)
        preferences = discover.SubscriptionObservation(discover.SubscriptionStatus.NOT_APPLICABLE)
        self.assertEqual(discover.contrast_sources(empty, preferences).status, discover.SubscriptionStatus.NO_SUBSCRIPTION)
        self.assertEqual(discover.contrast_sources(empty, empty).certainty, "corroborated")
        self.assertEqual(discover.contrast_sources(active, active).status, discover.SubscriptionStatus.ACTIVE_UNKNOWN)
        for first, second in ((empty, active), (empty, incomplete), (incomplete, incomplete)):
            combined = discover.contrast_sources(first, second)
            self.assertEqual(combined.status, discover.SubscriptionStatus.DETECTION_FAILED)
            self.assertEqual(discover.reconcile_progress(combined, 0).status, discover.ReconciliationStatus.INDETERMINATE)

    def test_request_filter(self):
        self.assertTrue(diagnosis.request_allowed("https://play.google.com/store/account/subscriptions", "GET"))
        self.assertFalse(diagnosis.request_allowed("https://play.google.com/checkout", "GET"))
        self.assertFalse(diagnosis.request_allowed("https://payments.google.com/", "GET"))
        self.assertFalse(diagnosis.request_allowed("https://play.google.com/anything", "POST"))
        self.assertFalse(diagnosis.request_allowed("https://example.invalid/", "GET"))
        self.assertFalse(diagnosis.request_allowed("https://play.google.com/page?operation=checkout", "GET"))
        self.assertEqual(diagnosis.destination_category("https://play.google.com/log?token=secret"), "play_telemetry")
        self.assertEqual(diagnosis.destination_category("https://www.googletagmanager.com/test?token=secret"), "tag_manager")
        self.assertEqual(diagnosis.request_block_reason("https://play.google.com/log", "POST"), "non_read_method")

    def test_network_categories_never_include_sensitive_url_data(self):
        request_obj = SimpleNamespace(url="https://play.google.com/_/PlayStoreUi/data/batchexecute?token=secret@example.invalid",
                                      method="POST", resource_type="xhr")
        self.assertEqual(diagnosis.request_block_reason(request_obj.url, request_obj.method), "non_read_method")
        evidence = diagnosis.NetworkEvidence()
        evidence.requested(request_obj)
        evidence.record("blocked", request_obj, "non_read_method")
        evidence.failed(request_obj)
        evidence.response(SimpleNamespace(request=request_obj, status=403))
        output = repr(evidence.summary())
        self.assertIn("play_batch_rpc", output)
        self.assertIn("403", output)
        self.assertNotIn("secret", output)
        self.assertNotIn("example.invalid", output)
        self.assertEqual(evidence.summary()["pending_requests"], 0)
        self.assertFalse(diagnosis.contextual_network_safe(evidence))

    def test_contextual_inference_rejects_pending_or_blocked_application_requests(self):
        evidence = diagnosis.NetworkEvidence()
        self.assertTrue(diagnosis.contextual_network_safe(evidence))
        telemetry = SimpleNamespace(url="https://play.google.com/log", method="POST", resource_type="other")
        evidence.record("blocked", telemetry, "non_read_method")
        self.assertTrue(diagnosis.contextual_network_safe(evidence))
        data = SimpleNamespace(url="https://play.google.com/_/app/data/request", method="POST", resource_type="other")
        evidence.requested(data)
        self.assertFalse(diagnosis.contextual_network_safe(evidence))
        evidence.completed(data)
        evidence.record("blocked", data, "non_read_method")
        self.assertFalse(diagnosis.contextual_network_safe(evidence))

    def test_shared_network_reasons_for_equivalent_run_and_diagnostic_evidence(self):
        data = SimpleNamespace(url="https://play.google.com/_/app/data/request", method="POST", resource_type="fetch")
        for passive in (False, True):
            evidence = diagnosis.NetworkEvidence(passive=passive)
            self.assertIsNone(diagnosis.contextual_network_reason(evidence))
            evidence.requested(data)
            self.assertEqual(diagnosis.contextual_network_reason(evidence), "data_request_pending")
            evidence.record("blocked", data, "non_read_method")
            self.assertEqual(diagnosis.contextual_network_reason(evidence), "data_request_blocked")
        self.assertEqual(diagnosis.contextual_network_reason(None), "data_request_unverified")
        passive = diagnosis.NetworkEvidence(passive=True)
        passive.requested(data)
        passive.completed(data)
        self.assertEqual(diagnosis.contextual_network_reason(passive), "data_request_unverified")
        get_data = SimpleNamespace(url="https://play.google.com/_/app/data/request", method="GET", resource_type="xhr")
        filtered = diagnosis.NetworkEvidence()
        filtered.record("blocked", get_data, "sensitive_destination")
        self.assertEqual(diagnosis.contextual_network_reason(filtered), "data_request_blocked")

    def test_run_observer_never_changes_requests_or_progress(self):
        async def run():
            listeners = {}
            class Context:
                service_workers = []
                def on(self, event, callback): listeners[event] = callback
                def remove_listener(self, event, callback): self.assert_callback(event, callback)
                def assert_callback(self, event, callback):
                    assert listeners.pop(event) == callback
            context = Context()
            page = SimpleNamespace(context=context, evaluate=AsyncMock(return_value=0))
            observed = discover.SubscriptionObservation(discover.SubscriptionStatus.DETECTION_FAILED,
                                                        reason="data_request_unverified")
            reconciliation = discover.reconcile_progress(observed, 0)
            async def fake_diagnose(_page, _step, *, contextual_guard):
                self.assertIsNone(await contextual_guard())
                req = SimpleNamespace(url="https://play.google.com/_/app/data/request", method="POST", resource_type="fetch")
                listeners["request"](req)
                self.assertEqual(await contextual_guard(), "data_request_pending")
                listeners["requestfinished"](req)
                self.assertEqual(await contextual_guard(), "data_request_unverified")
                return observed, reconciliation
            with patch.object(discover, "diagnose_page", side_effect=fake_diagnose), \
                 patch.object(db, "execute", new=AsyncMock()) as write:
                result, _ = await diagnosis.diagnose_run_page(page, 0)
                self.assertEqual(result.reason, "data_request_unverified")
                write.assert_not_awaited()
            self.assertFalse(listeners)
        asyncio.run(run())

    def test_run_stops_before_cursor_write_when_network_is_unverified(self):
        from tasks import subscribe_flow

        async def run():
            observation = discover.SubscriptionObservation(discover.SubscriptionStatus.DETECTION_FAILED,
                                                           reason="data_request_unverified")
            result = (observation, discover.reconcile_progress(observation, 0))
            with patch.object(diagnosis, "diagnose_run_page", new=AsyncMock(return_value=result)), \
                 patch.object(db, "execute", new=AsyncMock()) as execute, \
                 patch.object(subscribe_flow, "open_dialog", new=AsyncMock()) as dialog:
                finished = await subscribe_flow.run_layers(object(), "synthetic@example.invalid",
                                                           "acc_0123456789ab", [1, 2, 3], dry=True,
                                                           resume_from_state=True)
                self.assertFalse(finished)
                execute.assert_not_awaited()
                dialog.assert_not_awaited()
        asyncio.run(run())

    def test_semantic_evidence_accepts_counts_only(self):
        page = SimpleNamespace(evaluate=AsyncMock(return_value={"headings": 1, "main_regions": 1,
            "status_regions": 0, "list_regions": 0, "loading_indicators": 0, "subscription_actions": 0,
            "subscription_links": 2, "explicit_empty_regions": 0,
            "extra": "private@example.invalid"}))
        result = asyncio.run(diagnosis.semantic_evidence(page))
        self.assertTrue(result["available"])
        self.assertNotIn("private", repr(result))
        page.evaluate = AsyncMock(return_value={"headings": "private@example.invalid"})
        self.assertFalse(asyncio.run(diagnosis.semantic_evidence(page))["available"])

    def test_endpoint_authorization_and_same_origin(self):
        async def run():
            app = create_app()
            path = "/accounts/acc_0123456789ab/diagnose"
            base = [(b"host", b"127.0.0.1:8765"), (b"content-type", b"application/x-www-form-urlencoded")]
            with patch.object(settings, "console_token", "synthetic-token"), \
                 patch.object(diagnosis.runner, "start", new=AsyncMock(return_value="")) as start:
                self.assertEqual(await request(app, path, "POST", base), 401)
                auth = base + [(b"x-console-token", b"synthetic-token")]
                self.assertEqual(await request(app, path, "POST", auth + [(b"origin", b"http://other.invalid")]), 403)
                self.assertEqual(await request(app, path, "POST", auth, b"screenshot=false"), 202)
                start.assert_awaited_once_with("acc_0123456789ab", False)
                with patch.dict(diagnosis.runner.results, {"acc_0123456789ab": {"status": "finished"}}):
                    self.assertEqual(await request(app, "/api/diagnosis/acc_0123456789ab", "GET", auth), 200)
        asyncio.run(run())

    def test_selected_profile_only_and_no_progress_writes(self):
        async def run():
            acc_a, acc_b = "acc_0123456789ab", "acc_abcdefabcdef"
            root = Path(settings.profiles_dir)
            (root / f"profile_{acc_a}").mkdir(parents=True, exist_ok=True)
            (root / f"profile_{acc_b}").mkdir(parents=True, exist_ok=True)
            marker = root / f"profile_{acc_b}" / "marker"
            marker.write_text("unchanged", encoding="utf-8")
            page = SimpleNamespace(on=Mock(), evaluate=AsyncMock(return_value={"headings": 1, "main_regions": 1,
                "status_regions": 0, "list_regions": 0, "loading_indicators": 0, "subscription_actions": 0,
                "subscription_links": 0, "explicit_empty_regions": 0}))
            context = SimpleNamespace(pages=[page], on=Mock(), route=AsyncMock(), set_offline=AsyncMock(), close=AsyncMock())
            launch = AsyncMock(return_value=context)
            playwright = SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch))

            class Manager:
                async def __aenter__(self): return playwright
                async def __aexit__(self, *args): return False

            observation = discover.SubscriptionObservation(discover.SubscriptionStatus.NO_SUBSCRIPTION,
                                                           months_remaining=0, reason="synthetic")
            reconciliation = discover.reconcile_progress(observation, 2)
            runner = diagnosis.DiagnosticRunner()
            with patch.object(db, "fetch_one", new=AsyncMock(return_value={"id": acc_a, "step": 2})), \
                 patch.object(db, "execute", new=AsyncMock()) as execute, \
                 patch.object(diagnosis, "async_playwright", return_value=Manager()), \
                 patch.object(diagnosis.browser, "resolve_browser", return_value={}), \
                 patch.object(discover, "diagnose_page", new=AsyncMock(return_value=(observation, reconciliation))):
                self.assertEqual(await runner.start(acc_a), "")
                self.assertEqual(await runner.start(acc_b), "browser or queue busy")
                await runner.task
                execute.assert_not_awaited()
                self.assertEqual(launch.await_args.kwargs["user_data_dir"], str(root / f"profile_{acc_a}"))
                self.assertTrue(launch.await_args.kwargs["chromium_sandbox"])
                self.assertTrue(launch.await_args.kwargs["offline"])
                context.set_offline.assert_awaited_once_with(False)
                handler = context.route.await_args.args[1]
                blocked = SimpleNamespace(request=SimpleNamespace(url="https://payments.google.com/", method="GET"),
                                          abort=AsyncMock(), continue_=AsyncMock())
                await handler(blocked)
                blocked.abort.assert_awaited_once()
                blocked.continue_.assert_not_awaited()
                self.assertEqual(runner.results[acc_a]["local_step"], 2)
                self.assertEqual(runner.results[acc_a]["reconciliation"], "mismatch")
                self.assertEqual(marker.read_text(encoding="utf-8"), "unchanged")
                self.assertNotIn(acc_b, runner.results)
        asyncio.run(run())

    def test_run_and_diagnose_coherent_on_identical_simulated_observations(self):
        from tasks import subscribe_flow

        async def run():
            listeners = {}

            class Context:
                # Include a non-Play service worker from login (one.google.com) to verify lifecycle isolation.
                service_workers = [SimpleNamespace(url="https://one.google.com/sw.js")]
                def on(self, event, callback): listeners[event] = callback
                def remove_listener(self, event, callback):
                    assert listeners.get(event) == callback
                    listeners.pop(event, None)

            context = Context()
            page_listeners = {}

            async def goto(_url, **_kwargs):
                doc_req = SimpleNamespace(
                    url=discover.SUBSCRIPTIONS_URL, method="GET", resource_type="document",
                    frame=SimpleNamespace(page=page),
                )
                rpc_req = SimpleNamespace(
                    url="https://play.google.com/_/PlayStoreUi/data/batchexecute?rpcids=read",
                    method="GET", resource_type="xhr", frame=SimpleNamespace(page=page),
                )
                telemetry_req = SimpleNamespace(
                    url="https://play.google.com/log?format=json",
                    method="POST", resource_type="fetch", frame=SimpleNamespace(page=page),
                )
                other_tab_req = SimpleNamespace(
                    url="https://play.google.com/_/PlayStoreUi/data/batchexecute",
                    method="POST", resource_type="fetch", frame=SimpleNamespace(page=object()),
                )
                if "request" in listeners:
                    for req in (doc_req, rpc_req, telemetry_req, other_tab_req):
                        listeners["request"](req)
                    listeners["response"](SimpleNamespace(request=doc_req, status=200))
                    listeners["response"](SimpleNamespace(request=rpc_req, status=200))
                    listeners["requestfinished"](doc_req)
                    listeners["requestfinished"](rpc_req)
                    # Leave telemetry_req pending to verify non-data beacons do not block Run.
                return SimpleNamespace(status=200)

            async def evaluate(script: str):
                if "serviceWorker" in script:
                    return 0
                return ["contextual-empty"]

            page = SimpleNamespace(
                url=discover.SUBSCRIPTIONS_URL,
                context=context,
                goto=goto,
                inner_text=AsyncMock(return_value="Google Play\nSuscripciones\nBuscar suscripciones"),
                evaluate=evaluate,
                on=lambda event, cb: page_listeners.__setitem__(event, cb),
                remove_listener=lambda event, cb: page_listeners.pop(event, None) if page_listeners.get(event) == cb else None,
                screenshot=AsyncMock(),
            )

            # 1) Diagnose Subscription path with same traffic (telemetry POST blocked by route filter).
            diag_evidence = diagnosis.NetworkEvidence(passive=False)
            doc_req = SimpleNamespace(url=discover.SUBSCRIPTIONS_URL, method="GET", resource_type="document")
            rpc_req = SimpleNamespace(url="https://play.google.com/_/PlayStoreUi/data/batchexecute?rpcids=read", method="GET", resource_type="xhr")
            telemetry_req = SimpleNamespace(url="https://play.google.com/log?format=json", method="POST", resource_type="fetch")
            for req in (doc_req, rpc_req):
                diag_evidence.requested(req)
                diag_evidence.record("allowed", req, "read_method")
                diag_evidence.response(SimpleNamespace(request=req, status=200))
                diag_evidence.completed(req)
            diag_evidence.requested(telemetry_req)
            diag_evidence.record("blocked", telemetry_req, "non_read_method")
            self.assertEqual(diag_evidence.summary()["pending_requests"], 0)

            diag_play_obs, _ = await discover.diagnose_page(
                page, 0, timeout_ms=200, poll_interval=0.01,
                contextual_guard=lambda: diagnosis.contextual_network_reason(diag_evidence),
            )
            one_obs = discover.SubscriptionObservation(discover.SubscriptionStatus.NOT_APPLICABLE,
                                                       reason="settings show only email preferences")
            diag_combined = discover.contrast_sources(diag_play_obs, one_obs)
            diag_rec = discover.reconcile_progress(diag_combined, 0)

            # 2) Run path (diagnose_run_page + run_layers) with identical page and local_step=0.
            run_obs, run_rec = await diagnosis.diagnose_run_page(page, 0, timeout_ms=200, poll_interval=0.01)
            self.assertEqual(diag_combined.status, discover.SubscriptionStatus.NO_SUBSCRIPTION)
            self.assertEqual(run_obs.status, discover.SubscriptionStatus.NO_SUBSCRIPTION)
            self.assertEqual(diag_rec.status, discover.ReconciliationStatus.MATCH)
            self.assertEqual(run_rec.status, discover.ReconciliationStatus.MATCH)
            self.assertEqual(diag_rec.local_step, 0)
            self.assertEqual(run_rec.local_step, 0)
            self.assertEqual(diag_rec.detected_layer, 1)
            self.assertEqual(run_rec.detected_layer, 1)
            self.assertFalse(listeners)
            self.assertFalse(page_listeners)

            opened = []
            async def fake_open(_page, layer: int) -> str:
                opened.append(layer)
                return ""

            with patch.object(subscribe_flow, "open_dialog", side_effect=fake_open), \
                 patch.object(db, "execute", new=AsyncMock()):
                await subscribe_flow.run_layers(
                    page, "synthetic@example.invalid", "acc_0123456789ab",
                    [1, 2, 3], dry=True, resume_from_state=True, local_step=0,
                )
            self.assertEqual(opened, [1])

            # 3) Active Play service worker on play.google.com must fail closed in Run.
            context.service_workers = [SimpleNamespace(url="https://play.google.com/sw.js")]
            sw_obs, sw_rec = await diagnosis.diagnose_run_page(page, 0, timeout_ms=80, poll_interval=0.01)
            self.assertEqual(sw_obs.status, discover.SubscriptionStatus.DETECTION_FAILED)
            self.assertEqual(sw_obs.reason, "data_request_unverified")
            self.assertEqual(sw_rec.status, discover.ReconciliationStatus.INDETERMINATE)

        asyncio.run(run())

    def test_official_plan_details_query_navigation_and_confirmed_second_account(self):
        async def run():
            visited_urls = []
            play_html_text = (
                "Google AI Pro (5 TB) + YouTube Premium Individual Add On\n"
                "Next payment: $11.99 on Feb 17, 2027"
            )
            one_html_text = (
                "Plan actual\n"
                "Google AI Pro (5 TB): USD 0/mes durante 12 meses; después USD 19.99/mes.\n"
                "YouTube Premium Individual: USD 0/mes durante 1 mes; después USD 11.99/mes."
            )

            class QueryPage:
                url = discover.SUBSCRIPTIONS_URL
                context = None

                async def goto(self, target_url: str, **_kwargs):
                    visited_urls.append(target_url)
                    self.url = target_url
                    return SimpleNamespace(status=200)

                async def inner_text(self, _selector: str, **_kwargs) -> str:
                    if "one.google.com" in self.url:
                        return one_html_text
                    return play_html_text

                async def evaluate(self, script: str):
                    if "querySelectorAll('a[href]')" in script:
                        return [
                            discover.ONE_SETTINGS_URL,
                            "https://play.google.com/store/account/subscriptions/cancel?sub=1",
                            "https://payments.google.com/payments/home",
                        ]
                    return []

            page = QueryPage()
            obs, rec = await discover.diagnose_page(page, 1, timeout_ms=300, poll_interval=0.01)
            # Only official read-only URLs are visited via GET navigation; cancel/payment links are rejected.
            self.assertEqual(visited_urls, [discover.SUBSCRIPTIONS_URL, discover.ONE_SETTINGS_URL])
            self.assertEqual(obs.status, discover.SubscriptionStatus.ACTIVE_DURATION)
            self.assertIsNone(obs.renewal_date)
            self.assertIsNone(obs.months_remaining)
            self.assertEqual(obs.price, "USD 19.99")
            self.assertEqual(obs.primary_service, "Google AI Pro (5 TB)")
            self.assertEqual(obs.bundle_next_payment_date.isoformat(), "2027-02-17")
            self.assertEqual(obs.bundle_next_payment_price, "$11.99")
            self.assertEqual(obs.ai_pro_promo_months, 12)
            self.assertEqual(obs.details.ai_pro_promo_source, "google_one_plan_details")
            self.assertEqual(obs.addon_promo_months, 1)
            self.assertEqual(rec.status, discover.ReconciliationStatus.INDETERMINATE)
            self.assertEqual(rec.local_step, 1)
            self.assertIsNone(rec.detected_layer)

            # Reject non-official or sensitive detail URLs explicitly.
            with self.assertRaises(ValueError):
                await discover.read_official_plan_details(
                    page, obs, detail_urls=["https://play.google.com/store/account/subscriptions/cancel"]
                )

            # A promo end date remains distinct from AI Pro renewal and the add-on payment.
            one_html_with_date = (
                "Plan actual\n"
                "Google AI Pro (5 TB): USD 0/mes durante 12 meses (vence el May 21, 2028); después USD 19.99/mes.\n"
                "YouTube Premium Individual: USD 0/mes durante 1 mes (próximo cobro Feb 17, 2027); después USD 11.99/mes."
            )
            one_html_text = one_html_with_date
            visited_urls.clear()
            obs_known, rec_known = await discover.diagnose_page(page, 2, timeout_ms=300, poll_interval=0.01)
            self.assertEqual(obs_known.status, discover.SubscriptionStatus.ACTIVE_DURATION)
            self.assertIsNone(obs_known.renewal_date)
            self.assertEqual(obs_known.ai_pro_promo_end_date.isoformat(), "2028-05-21")
            self.assertEqual(obs_known.bundle_next_payment_date.isoformat(), "2027-02-17")
            self.assertEqual(obs_known.addon_renewal_date.isoformat(), "2027-02-17")
            self.assertEqual(rec_known.status, discover.ReconciliationStatus.INDETERMINATE)
            self.assertIsNone(rec_known.detected_layer)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
