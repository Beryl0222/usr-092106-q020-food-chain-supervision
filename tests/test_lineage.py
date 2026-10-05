import unittest

from src.errors import ConflictError, RiskLotFrozenError
from tests.util import AG, HC, MR, Builder, biz


class LineageTest(unittest.TestCase):
    def _chain(self) -> Builder:
        b = Builder()
        b.registered_business("B1")
        b.registered_business("B2")
        b.harvest("B1", "L0", 100)
        return b

    def test_split_conservation_rejects_overdraw(self) -> None:
        b = self._chain()
        with self.assertRaises(ConflictError):
            b.emit("LOT_PACKED", "food_lot", "L0",
                   {"outputs": [
                       {"lot_id": "L0-A", "quantity": 70, "unit": "kg"},
                       {"lot_id": "L0-B", "quantity": 40, "unit": "kg"}]},
                   biz("B1"))
        self.assertNotIn("L0-A", b.svc.proj.lots)

    def test_split_then_relabel_keeps_bidirectional_trace(self) -> None:
        b = self._chain()
        b.emit("LOT_PACKED", "food_lot", "L0",
               {"outputs": [
                   {"lot_id": "L0-A", "quantity": 60, "unit": "kg"},
                   {"lot_id": "L0-B", "quantity": 40, "unit": "kg"}]},
               biz("B1"))
        b.emit("LOT_RELABELED", "food_lot", "L0-A",
               {"old_label": "散装", "new_label": "精品", "at_party": "B1"},
               biz("B1"))
        forward = b.svc.trace("L0", MR)
        self.assertEqual({d["lot_id"] for d in forward["downstream"]},
                         {"L0-A", "L0-B"})
        back = b.svc.trace("L0-A", MR)
        self.assertEqual([u["lot_id"] for u in back["upstream"]], ["L0"])
        self.assertEqual(back["label_history"][0]["new"], "精品")
        closure = b.svc.proj.lineage_closure("L0")
        self.assertEqual(set(closure["descendants"]), {"L0-A", "L0-B"})

    def test_merge_blends_batches_and_traces_both_sources(self) -> None:
        b = self._chain()
        b.harvest("B1", "L1", 20, "青菜")
        b.emit("LOT_MERGED", "food_lot", "LM",
               {"inputs": [{"lot_id": "L0", "quantity": 30},
                           {"lot_id": "L1", "quantity": 10}],
                "product_name": "混合菜", "unit": "kg"}, biz("B1"))
        merged = b.svc.proj.lots["LM"]
        self.assertEqual(merged["quantity"], 40)
        trace = b.svc.trace("LM", MR)
        self.assertEqual({u["lot_id"] for u in trace["upstream"]}, {"L0", "L1"})

    def test_transfer_requires_holding(self) -> None:
        b = self._chain()
        with self.assertRaises(ConflictError):
            b.emit("LOT_TRANSFERRED", "food_lot", "L0",
                   {"from_party": "B2", "to_party": "B1", "quantity": 5},
                   biz("B2"))

    def test_controlled_lot_freezes_relabel_and_transfer(self) -> None:
        b = self._chain()
        b.rule("R1", "v1")
        b.method("M1")
        b.emit("SAMPLE_DRAWN", "inspection_sample", "S1",
               {"lot_id": "L0", "sampled_by": "health"}, HC)
        b.emit("SAMPLE_TESTED", "inspection_sample", "S1",
               {"kind": "rapid", "result": "positive", "tested_by": "health",
                "method_code": "M1"}, HC)
        b.emit("RISK_CONTROLLED", "inspection_sample", "S1",
               {"trigger_sample_id": "S1", "decisions": ["hold"],
                "authority": "market_regulation",
                "rule_code": "R1", "rule_version": "v1"}, MR)
        with self.assertRaises(RiskLotFrozenError):
            b.emit("LOT_RELABELED", "food_lot", "L0",
                   {"old_label": "a", "new_label": "b", "at_party": "B1"},
                   biz("B1"))
        with self.assertRaises(RiskLotFrozenError):
            b.emit("LOT_TRANSFERRED", "food_lot", "L0",
                   {"from_party": "B1", "to_party": "B2", "quantity": 1},
                   biz("B1"))

    def test_observed_violation_records_breach_without_legal_move(self) -> None:
        b = self._chain()
        b.rule("R1", "v1")
        b.method("M1")
        b.emit("SAMPLE_DRAWN", "inspection_sample", "S1",
               {"lot_id": "L0", "sampled_by": "health"}, HC)
        b.emit("SAMPLE_TESTED", "inspection_sample", "S1",
               {"kind": "rapid", "result": "positive", "tested_by": "health",
                "method_code": "M1"}, HC)
        b.emit("RISK_CONTROLLED", "inspection_sample", "S1",
               {"trigger_sample_id": "S1", "decisions": ["hold"],
                "authority": "market_regulation",
                "rule_code": "R1", "rule_version": "v1"}, MR)
        b.emit("LOT_TRANSFERRED", "food_lot", "L0",
               {"from_party": "B1", "to_party": "BX",
                "observed_violation": True, "detail": "暗访发现转运"},
               MR)
        lot = b.svc.proj.lots["L0"]
        # 合法持仓不变：B1 仍持有 100，BX 没有因违规获得持仓
        self.assertEqual(lot["holdings"], {"B1": 100})
        self.assertEqual(len(lot["breaches"]), 1)
        self.assertEqual(lot["breaches"][0]["breach_type"], "movement")


if __name__ == "__main__":
    unittest.main()
