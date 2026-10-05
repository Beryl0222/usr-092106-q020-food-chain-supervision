import tempfile
import unittest
from pathlib import Path

from src.errors import ConflictError
from src.store import EventStore
from tests.util import MR, Builder


def _rule_event(eid: str = "E1", version: int = 1) -> dict:
    return {
        "event_id": eid, "event_type": "RULE_PUBLISHED",
        "aggregate_type": "risk_rule", "aggregate_id": "R1",
        "occurred_at": "2026-09-20T08:00:00+08:00",
        "version": version, "summary": "规则发布",
        "payload": {"rule_code": "R1", "version": "v1",
                    "content_ref": "rules/R1.md",
                    "effective_at": "2026-01-01T00:00:00+08:00"},
    }


class StoreTest(unittest.TestCase):
    def test_idempotent_dedup_same_event_id(self) -> None:
        svc = Builder().svc
        first = svc.emit(_rule_event(), MR)
        second = svc.emit(_rule_event(), MR)
        self.assertEqual(first["event_id"], second["event_id"])
        self.assertEqual(len(svc), 1)

    def test_same_id_different_payload_rejected(self) -> None:
        store = EventStore()
        store.append(_rule_event())
        altered = _rule_event()
        altered["payload"]["content_ref"] = "rules/TAMPERED.md"
        with self.assertRaises(ConflictError):
            store.append(altered)

    def test_version_stream_must_be_contiguous(self) -> None:
        store = EventStore()
        store.append(_rule_event(version=1))
        with self.assertRaises(ConflictError):
            store.append(_rule_event("E2", version=3))

    def test_jsonl_replay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "log.jsonl"
            svc_store = EventStore(path)
            svc_store.append(_rule_event())
            replayed = EventStore(path)
            self.assertEqual(len(replayed), 1)
            self.assertEqual(replayed.get("E1")["payload"]["rule_code"], "R1")

    def test_initial_evidence_immutable_after_followups(self) -> None:
        b = Builder()
        b.rule("R1", "v1")
        original = b.svc.store.get("T-0001")
        # 后续发布新版本，不影响旧事件
        b.emit("RULE_PUBLISHED", "risk_rule", "R1",
               {"rule_code": "R1", "version": "v2", "content_ref": "rules/R1-v2.md",
                "effective_at": "2026-09-01T00:00:00+08:00"}, MR)
        again = b.svc.store.get("T-0001")
        self.assertEqual(again["payload"]["version"], "v1")
        self.assertEqual(original, again)


if __name__ == "__main__":
    unittest.main()
