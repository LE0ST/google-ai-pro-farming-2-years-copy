"""User-attested visual evidence remains local and distinct from automatic reads."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.runner import visual_evidence


class VisualEvidenceTests(unittest.TestCase):
    def test_evidence_is_account_scoped_and_explicitly_attested(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            visual_evidence.save(root, "acc_one", 12)
            self.assertEqual(visual_evidence.read(root, "acc_one"), {
                "confirmed_promo_months": 12,
                "source": "user_confirmed_visual_evidence",
            })
            self.assertEqual(visual_evidence.read(root, "acc_two"), {})

    def test_invalid_or_non_attested_data_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ValueError):
                visual_evidence.save(root, "acc_one", 0)
            visual_evidence.path_for(root, "acc_one").write_text(
                json.dumps({"confirmed_promo_months": 12, "source": "automatic"}), encoding="utf-8")
            self.assertEqual(visual_evidence.read(root, "acc_one"), {})


if __name__ == "__main__":
    unittest.main()
