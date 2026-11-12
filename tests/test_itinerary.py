"""Offline itinerary continuation, restart and transaction-isolation regressions."""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.runner import itinerary
from app.sites.google_one import discover


class ItineraryTests(unittest.TestCase):
    def test_no_links_and_timeout_remain_blocked_without_progress(self):
        async def run():
            obs = discover.observe_subscription_text("Google AI Pro active")
            for links in ([], ["https://one.google.com/plans"]):
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "state.json"
                    async def hanging(*args, **kwargs):
                        await asyncio.sleep(10)
                    with patch.object(discover, "discover_plan_detail_urls", AsyncMock(return_value=links)), \
                         patch.object(discover, "read_official_plan_details", side_effect=hanging), \
                         patch.object(itinerary, "MAX_SECONDS", 0.02):
                        result, rec = await itinerary.continue_reads(SimpleNamespace(url=discover.ONE_SETTINGS_URL), obs, 1, path)
                    self.assertEqual(result, obs)
                    self.assertEqual(rec.local_step, 1)
                    self.assertIsNone(rec.detected_layer)
                    self.assertEqual(json.loads(path.read_text())["phase"], "blocked")
        asyncio.run(run())

    def test_query_chain_limit_idempotence_and_cursor(self):
        async def run():
            obs = discover.observe_subscription_text("Google AI Pro active")
            page = SimpleNamespace(url=discover.ONE_SETTINGS_URL)
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "checkpoint.json"
                links = ["https://one.google.com/plans", "https://one.google.com/settings", "https://one.google.com/about/plans", "https://one.google.com/settings/plan"]
                with patch.object(discover, "discover_plan_detail_urls", AsyncMock(return_value=links)), \
                     patch.object(discover, "read_official_plan_details", AsyncMock(return_value=obs)) as read:
                    result, rec = await itinerary.continue_reads(page, obs, 1, path)
                    self.assertEqual(read.await_count, 3)
                    self.assertEqual(rec.local_step, 1)
                    self.assertIsNone(rec.detected_layer)
                    saved = json.loads(path.read_text())
                    self.assertEqual(saved["phase"], "blocked")
                    self.assertEqual(len(set(saved["completed"])), 3)
                    await itinerary.continue_reads(page, obs, 1, path)
                    self.assertEqual(read.await_count, 3)
                    self.assertEqual(result, obs)
                    self.assertEqual(itinerary.route_key(page.url), itinerary.route_key("https://one.google.com/settings?hl=es"))
        asyncio.run(run())

    def test_interruption_resumes_only_incomplete_query(self):
        async def run():
            obs = discover.observe_subscription_text("Google AI Pro active")
            page = SimpleNamespace(url=discover.ONE_SETTINGS_URL)
            urls = ["https://one.google.com/plans", "https://one.google.com/about/plans"]
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "checkpoint.json"
                with patch.object(discover, "discover_plan_detail_urls", AsyncMock(return_value=urls)), \
                     patch.object(discover, "read_official_plan_details", AsyncMock(side_effect=[obs, asyncio.CancelledError()])):
                    with self.assertRaises(asyncio.CancelledError):
                        await itinerary.continue_reads(page, obs, 1, path)
                with patch.object(discover, "discover_plan_detail_urls", AsyncMock(return_value=urls)), \
                     patch.object(discover, "read_official_plan_details", AsyncMock(return_value=obs)) as read:
                    await itinerary.continue_reads(page, obs, 1, path)
                    self.assertEqual(read.await_count, 1)
                    self.assertEqual(read.await_args.kwargs["detail_urls"], [urls[1]])
        asyncio.run(run())

    def test_duration_and_conflict_do_not_authorize_progress(self):
        async def run():
            active = discover.observe_subscription_text("Google AI Pro active")
            duration = discover.observe_subscription_text("Current plan Google AI Pro active USD 0/month for 12 months")
            conflict = discover.SubscriptionObservation(discover.SubscriptionStatus.INCONSISTENT)
            for result in (duration, conflict):
                with tempfile.TemporaryDirectory() as directory, \
                     patch.object(discover, "discover_plan_detail_urls", AsyncMock(return_value=["https://one.google.com/plans"])), \
                     patch.object(discover, "read_official_plan_details", AsyncMock(return_value=result)) as read:
                    actual, rec = await itinerary.continue_reads(SimpleNamespace(url=discover.ONE_SETTINGS_URL), active, 1, Path(directory) / "state.json")
                    self.assertEqual(actual, result)
                    self.assertEqual(rec.local_step, 1)
                    self.assertIsNone(rec.detected_layer)
                    read.assert_awaited_once()
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
