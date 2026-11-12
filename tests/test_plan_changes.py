"""Read-only change diagnostics with synthetic schedules and no transaction imports."""
import asyncio
import tempfile
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import settings
from app.runner import itinerary, operations
from app.sites.google_one import discover


class PlanChangeTests(unittest.TestCase):
    def test_scheduled_addon_change_date_is_not_primary_renewal(self):
        text = "Google AI Pro active\nPlan changing: YouTube Premium removal effective on May 21, 2028"
        obs = discover.observe_subscription_text(text)
        change = obs.details.plan_change_observations[0]
        self.assertEqual(change["scope"], "addon")
        self.assertEqual(change["change_type"], "addon_removal")
        self.assertEqual(change["effective_date"], "2028-05-21")
        self.assertIsNone(change["resulting_plan"])
        self.assertIsNone(obs.renewal_date)
        self.assertIsNone(obs.bundle_next_payment_date)
        self.assertTrue(obs.details.plan_change_pending)

    def test_completed_unknown_and_no_change(self):
        for clause, status, pending in (("Plan change completed", "completed", False),
                ("Plan change status unknown", "unknown", True), ("Plan changing", "pending", True)):
            obs = discover.observe_subscription_text("Google AI Pro active\n" + clause)
            self.assertEqual(obs.details.plan_change_observations[0]["status"], status)
            self.assertEqual(obs.details.plan_change_pending, pending)
            self.assertIsNone(obs.details.plan_change_observations[0]["effective_date"])
        self.assertFalse(discover.observe_subscription_text("Google AI Pro active").details.plan_change_observations)
        pending = discover.observe_subscription_text("Google AI Pro active\nPlan changing")
        completed = discover.observe_subscription_text("Google AI Pro active\nPlan change completed")
        conflict = discover.contrast_sources(pending, completed)
        self.assertTrue(conflict.details.plan_change_pending)
        self.assertEqual({v["status"] for v in conflict.details.plan_change_observations}, {"pending", "completed"})
        self.assertIsNone(discover.reconcile_progress(conflict, 1).detected_layer)

    def test_zero_extra_queries_does_not_mean_no_checks_or_pending_submission(self):
        async def run():
            with tempfile.TemporaryDirectory() as directory, patch.object(settings, "data_dir", Path(directory)):
                obs = discover.observe_subscription_text("Google AI Pro active\nPlan changing")
                from dataclasses import replace
                obs = replace(obs, read_events=({"stage": "navigation-result", "final_host": "play.google.com", "final_path": "/store/account/subscriptions"},))
                page = SimpleNamespace(url=discover.ONE_SETTINGS_URL)
                path = itinerary.checkpoint_path(settings.artifacts_dir, "acc_synthetic")
                with patch.object(discover, "discover_plan_detail_urls", AsyncMock(return_value=[])), \
                     patch.object(discover, "read_official_plan_details", AsyncMock()) as query:
                    _, rec = await itinerary.continue_reads(page, obs, 1, path)
                    result = itinerary.public_status(settings.artifacts_dir, "acc_synthetic")
                    self.assertEqual(result["completed_queries"], 0)
                    self.assertIn("google_play_read", result["checks_performed"])
                    self.assertIn("detail_links_examined", result["checks_performed"])
                    self.assertEqual(result["link_search_result"], "no_verified_links")
                    self.assertEqual(result["next_action"], "inspect_plan_change")
                    self.assertEqual(len(result["blockers"]), 2)
                    self.assertFalse(operations.summary("acc_synthetic")["pending_operation"])
                    self.assertEqual(rec.local_step, 1)
                    query.assert_not_awaited()
        asyncio.run(run())

    def test_change_details_follow_only_verified_read_links(self):
        async def run():
            base = discover.observe_subscription_text("Google AI Pro active\nPlan changing")
            text = "Google AI Pro active\nPlan will change to Google AI Pro on May 21, 2028"
            page = SimpleNamespace(url=discover.SUBSCRIPTIONS_URL,
                goto=AsyncMock(return_value=None), inner_text=AsyncMock(return_value=text), evaluate=AsyncMock(return_value=[]))
            async def navigate(url, **kwargs):
                page.url = url
            page.goto.side_effect = navigate
            result = await discover.read_official_plan_details(page, base, timeout_ms=200,
                detail_urls=["https://play.google.com/store/account/subscriptions/details"])
            self.assertIsNone(result.renewal_date)
            self.assertTrue(any(c["effective_date"] == "2028-05-21" for c in result.details.plan_change_observations))
            self.assertTrue(any(c["source"] == "google_play" for c in result.details.plan_change_observations))
            self.assertTrue(all(call.kwargs.get("wait_until") == "domcontentloaded" for call in page.goto.await_args_list))
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
