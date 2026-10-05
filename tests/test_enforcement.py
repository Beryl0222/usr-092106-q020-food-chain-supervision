import unittest

from src.errors import ConflictError
from tests.util import MR, PS, Builder


class EnforcementTest(unittest.TestCase):
    def _case_world(self) -> tuple[Builder, dict]:
        b = Builder()
        b.rule("R1", "v1")
        b.registered_business("B1")
        b.harvest("B1", "L1", 5)
        held = b.emit("LOT_HELD", "food_lot", "L1",
                      {"custodian": "public_security", "reason": "涉案扣押"}, PS)
        b.emit("CASE_OPENED", "enforcement_case", "CASE1",
               {"basis_event_ids": [held["event_id"]],
                "subject_business_id": "B1", "opened_by": "public_security"}, PS)
        return b, held

    def test_penalty_requires_locked_evidence_and_published_rule(self) -> None:
        b, held = self._case_world()
        # 未保管证据即处罚 → 拒绝
        with self.assertRaises(ConflictError):
            b.emit("ENFORCEMENT_DECIDED", "enforcement_case", "CASE1",
                   {"penalties": ["罚款"], "evidence_event_ids": [held["event_id"]],
                    "rule_code": "R1", "rule_version": "v1",
                    "decided_by": "market_regulation"}, MR)
        # 完成证据保管
        b.emit("EVIDENCE_LOCKED", "enforcement_case", "CASE1",
               {"linked_event_id": held["event_id"],
                "custodian": "public_security", "storage_ref": "vault://L1",
                "hash_alg": "sha256", "digest": "abc"}, PS)
        # 引用未发布规则版本 → 拒绝
        with self.assertRaises(ConflictError):
            b.emit("ENFORCEMENT_DECIDED", "enforcement_case", "CASE1",
                   {"penalties": ["罚款"], "evidence_event_ids": [held["event_id"]],
                    "rule_code": "R9", "rule_version": "v9",
                    "decided_by": "market_regulation"}, MR)
        # 合法处罚
        b.emit("ENFORCEMENT_DECIDED", "enforcement_case", "CASE1",
               {"penalties": ["罚款 10000"],
                "evidence_event_ids": [held["event_id"]],
                "rule_code": "R1", "rule_version": "v1",
                "decided_by": "market_regulation"}, MR)
        audit = b.svc.penalty_audit("CASE1", PS)
        self.assertEqual(audit["penalty"]["rule_version"], "v1")
        chain = audit["evidence_chain"][0]
        self.assertEqual(chain["custody"][0]["storage_ref"], "vault://L1")
        # 被证事件是 LOT_HELD，其批次交接链可回溯到田间建档
        ops = [c["op"] for c in chain["custody_chain"]]
        self.assertIn("harvest", ops)
        self.assertIn("held", ops)

    def test_unknown_basis_event_blocks_case_open(self) -> None:
        b = Builder()
        with self.assertRaises(ConflictError):
            b.emit("CASE_OPENED", "enforcement_case", "CASE1",
                   {"basis_event_ids": ["EVT-GHOST"],
                    "opened_by": "public_security"}, PS)

    def test_correction_submitted_by_business_tracked_on_case(self) -> None:
        b, _ = self._case_world()
        from tests.util import biz
        b.emit("CORRECTION_SUBMITTED", "enforcement_case", "CASE1",
               {"business_id": "B1", "description": "已完成整改"}, biz("B1"))
        self.assertEqual(b.svc.proj.cases["CASE1"]["corrections"][0]["description"],
                         "已完成整改")


if __name__ == "__main__":
    unittest.main()
