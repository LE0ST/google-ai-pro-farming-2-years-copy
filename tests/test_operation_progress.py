"""Synthetic submission lifecycle: no Google, checkout or payment execution."""
import asyncio
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import settings
from app.runner import operations, itinerary
from app.sites.google_one import discover
from tasks import subscribe_flow as flow


class OperationProgressTests(unittest.TestCase):
    def test_submission_unknown_confirmed_and_interrupted(self):
        async def run():
            for confirmation in (False, True, "interrupted"):
                with tempfile.TemporaryDirectory() as directory, patch.object(settings, "data_dir", Path(directory)):
                    settings.ensure_dirs()
                    account = "acc_synthetic"
                    state = {"step": 1}
                    empty = discover.observe_subscription_text("No subscriptions")
                    rec = discover.reconcile_progress(empty, 1)
                    text = "Due today $0.00 12-month free trial"
                    async def execute(sql, params=()):
                        if "SET step =" in sql:
                            state["step"] = params[0]
                    with patch.object(flow.diagnosis, "diagnose_run_page", AsyncMock(return_value=(empty, rec))), \
                         patch.object(flow, "open_dialog", AsyncMock(return_value=text)) as dialog, \
                         patch.object(flow.guard, "judge", return_value=SimpleNamespace(safe=True, reason="synthetic")), \
                         patch.object(flow, "commit", AsyncMock(return_value=True)) as submit, \
                         patch.object(flow, "verify_committed_layer", AsyncMock(return_value=confirmation is True)), \
                         patch.object(flow.db, "execute", AsyncMock(side_effect=execute)), \
                         patch.object(flow.asyncio, "sleep", AsyncMock()), \
                         patch.object(flow, "cancel", AsyncMock()) as cancel, \
                         patch.object(flow, "resubscribe", AsyncMock(), create=True) as resubscribe:
                        if confirmation == "interrupted":
                            submit.side_effect = asyncio.CancelledError()
                            with self.assertRaises(asyncio.CancelledError):
                                await flow.run_layers(SimpleNamespace(screenshot=AsyncMock()), "demo@example.invalid", account, [1], False, local_step=1)
                            self.assertEqual(operations.read(account, 1), "submitted")
                        else:
                            result = await flow.run_layers(SimpleNamespace(screenshot=AsyncMock()), "demo@example.invalid", account, [1], False, local_step=1)
                            self.assertEqual(operations.read(account, 1), "completed" if confirmation else "pending_verification")
                            self.assertEqual(result, True if confirmation else flow.RunOutcome.NEEDS_VERIFICATION)
                        self.assertEqual(state["step"], 2 if confirmation is True else 1)
                        submit.reset_mock(side_effect=True)
                        dialog.reset_mock()
                        result = await flow.run_layers(SimpleNamespace(screenshot=AsyncMock()), "demo@example.invalid", account, [1], False, local_step=state["step"])
                        self.assertIs(result, flow.RunOutcome.NEEDS_VERIFICATION)
                        submit.assert_not_awaited()
                        dialog.assert_not_awaited()
                        cancel.assert_not_awaited()
                        resubscribe.assert_not_awaited()
                        self.assertIsNone(operations.read("acc_other", 1))
        asyncio.run(run())

    def test_bundle_date_promo_and_pending_plan_remain_independent(self):
        async def run():
            with tempfile.TemporaryDirectory() as directory:
                text = "Google AI Pro (5 TB) + YouTube Premium\nNext payment: $19.99 on May 21, 2028\nPlan changing"
                page = SimpleNamespace(url=discover.SUBSCRIPTIONS_URL, goto=AsyncMock(return_value=None),
                    inner_text=AsyncMock(return_value=text), evaluate=AsyncMock(return_value=[]))
                observed, rec = await discover.diagnose_page(page, 1, timeout_ms=100, poll_interval=0.01)
                self.assertTrue(observed.details.plan_change_pending)
                self.assertEqual(observed.bundle_next_payment_date, date(2028, 5, 21))
                self.assertIsNone(observed.renewal_date)
                self.assertIsNone(rec.detected_layer)
                self.assertIn("google_play", repr(observed.details.evidence))
                promo = discover.observe_subscription_text("Google AI Pro active USD 0/mes durante 12 meses")
                both = discover.contrast_sources(observed, promo)
                self.assertEqual(both.ai_pro_promo_months, 12)
                self.assertTrue(both.details.plan_change_pending)
                self.assertIsNone(both.renewal_date)
                _, rec = await itinerary.continue_reads(page, both, 1, Path(directory) / "itinerary.json")
                self.assertIsNone(rec.detected_layer)
                contradictory = discover.observe_subscription_text(text.replace("2028", "2029"))
                self.assertEqual(discover.contrast_sources(observed, contradictory).status, discover.SubscriptionStatus.DETECTION_FAILED)
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
