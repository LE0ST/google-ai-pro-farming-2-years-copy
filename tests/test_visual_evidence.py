"""Synthetic visual evidence must never authorize a transaction."""
import json
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.runner import visual_evidence
from app.sites.google_one import discover


class VisualEvidenceTests(unittest.TestCase):
    def test_conflicting_visual_evidence_blocks_operations(self):
        from dataclasses import replace
        from types import SimpleNamespace
        from tasks import subscribe_flow as flow
        async def run():
            observed = discover.observe_subscription_text("Google AI Pro active USD 0/month for 6 months")
            rec = discover.reconcile_progress(observed, 1)
            with patch.object(flow.diagnosis, "diagnose_run_page", AsyncMock(return_value=(observed, rec))), \
                 patch.object(flow.itinerary, "continue_reads", AsyncMock(return_value=(observed, rec))), \
                 patch.object(flow.visual_evidence, "read", return_value={"confirmed_promo_months": 12, "source": "user_confirmed_visual_evidence"}), \
                 patch.object(flow, "open_dialog", AsyncMock()) as checkout, \
                 patch.object(flow, "resubscribe", AsyncMock(), create=True) as resubscribe, \
                 patch.object(flow.db, "execute", AsyncMock()) as write:
                result = await flow.run_layers(SimpleNamespace(), "demo@example.invalid", "acc_test", [1], False, local_step=1)
                self.assertIs(result, flow.RunOutcome.NEEDS_VERIFICATION)
                checkout.assert_not_awaited()
                resubscribe.assert_not_awaited()
                write.assert_not_awaited()
        asyncio.run(run())

    def test_visual_attestation_is_account_scoped_and_independent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            visual_evidence.save(root, "acc_one", 12)
            visual = visual_evidence.read(root, "acc_one")
            self.assertEqual(visual, {"confirmed_promo_months": 12, "source": "user_confirmed_visual_evidence"})
            self.assertEqual(visual_evidence.read(root, "acc_two"), {})
            for text in ("Google AI Pro active\nPlan changing",
                         "Google AI Pro + YouTube Premium\nNext payment: $19.99 on May 21, 2028\nPlan changing"):
                obs = discover.observe_subscription_text(text)
                self.assertIsNone(obs.renewal_date)
                self.assertIsNone(discover.reconcile_progress(obs, 1).detected_layer)
                self.assertTrue(obs.details.plan_change_pending)
            conflicting = discover.observe_subscription_text("Google AI Pro active USD 0/month for 6 months")
            self.assertNotEqual(visual["confirmed_promo_months"], conflicting.ai_pro_promo_months)
            self.assertIsNone(discover.reconcile_progress(conflicting, 1).detected_layer)

    def test_invalid_or_malformed_evidence_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ValueError):
                visual_evidence.save(root, "acc_one", 0)
            visual_evidence.path_for(root, "acc_one").write_text(json.dumps({"confirmed_promo_months": 12, "source": "automatic"}))
            self.assertEqual(visual_evidence.read(root, "acc_one"), {})


if __name__ == "__main__":
    unittest.main()
