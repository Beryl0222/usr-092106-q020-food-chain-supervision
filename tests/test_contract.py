import json
import unittest
from pathlib import Path

from src import catalog
from src.validator import validate_event

ROOT = Path(__file__).parents[1]


class ContractTest(unittest.TestCase):
    def test_sample_matches_envelope(self) -> None:
        sample = json.loads((ROOT / "data" / "sample.json").read_text(encoding="utf-8"))
        self.assertEqual(validate_event(sample), [])

    def test_schema_enums_match_catalog(self) -> None:
        schema = json.loads(
            (ROOT / "contracts" / "domain.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(set(schema["properties"]["event_type"]["enum"]),
                         set(catalog.EVENT_TYPES))
        self.assertEqual(set(schema["properties"]["aggregate_type"]["enum"]),
                         set(catalog.AGGREGATE_TYPES))

    def test_legacy_events_still_supported(self) -> None:
        for et in ("LICENSE_VERIFIED", "LOT_TRANSFERRED", "SAMPLE_TESTED",
                   "RISK_CONTROLLED", "RECALL_RECONCILED"):
            self.assertIn(et, catalog.EVENT_TYPES)

    def test_validator_rejects_bad_envelope(self) -> None:
        errors = validate_event({"event_id": "", "event_type": "NOPE",
                                 "aggregate_type": "food_lot",
                                 "aggregate_id": "L1", "occurred_at": "bad",
                                 "version": 0, "summary": ""})
        joined = "；".join(errors)
        self.assertIn("event_id", joined)
        self.assertIn("未知 event_type", joined)
        self.assertIn("version", joined)
        self.assertIn("occurred_at", joined)


if __name__ == "__main__":
    unittest.main()
