import unittest

from src.errors import ConflictError, RiskLotFrozenError
from tests.util import HC, MR, Builder, biz, plat


class EmergencyTest(unittest.TestCase):
    def _world(self) -> Builder:
        """田间100 → 分装 A60/B40 → A: 批发40、冷链在途5、餐饮15；
        餐饮出 3 单共 6kg（2 已送达，1 已出餐待配送）。"""
        b = Builder()
        for bid in ("B-FARM", "B-MKT", "B-CAFE", "B-OTHER"):
            b.registered_business(bid)
        b.harvest("B-FARM", "L0", 100)
        b.emit("LOT_PACKED", "food_lot", "L0",
               {"outputs": [
                   {"lot_id": "LA", "quantity": 60, "unit": "kg"},
                   {"lot_id": "LB", "quantity": 40, "unit": "kg"}]},
               biz("B-FARM"))
        b.emit("LOT_TRANSFERRED", "food_lot", "LA",
               {"from_party": "B-FARM", "to_party": "B-MKT", "quantity": 60},
               biz("B-FARM"))
        b.emit("LOT_RELABELED", "food_lot", "LA",
               {"old_label": "散装", "new_label": "精品无公害",
                "at_party": "B-MKT"}, biz("B-MKT"))
        b.emit("LOT_TRANSFERRED", "food_lot", "LA",
               {"from_party": "B-MKT", "to_party": "B-CAFE", "quantity": 15},
               biz("B-MKT"))
        b.emit("LOT_TRANSFERRED", "food_lot", "LA",
               {"from_party": "B-MKT", "to_party": "B-OTHER", "quantity": 5,
                "in_transit": True}, biz("B-MKT"))
        # 店铺/许可证最小建档
        b.emit("LICENSE_ISSUED", "food_business", "B-CAFE",
               {"license_no": "J1", "premises_id": "P1", "scope": "餐饮"}, MR)
        b.emit("SHOP_LISTED", "platform_shop", "S1",
               {"platform": "PF", "business_id": "B-CAFE", "license_no": "J1"},
               plat("PF"))
        for oid, qty, delivered in (("O1", 2, True), ("O2", 4, True),
                                   ("O3", 1, False)):
            b.emit("ORDER_PLACED", "food_order", oid,
                   {"shop_id": "S1", "consumer_ref": "顾客",
                    "items": [{"name": "菠菜", "qty": 1}]}, plat("PF"))
            b.emit("ORDER_FULFILLED", "food_order", oid,
                   {"lots": [{"lot_id": "LA", "quantity": qty}]}, plat("PF"))
            if delivered:
                b.emit("ORDER_DELIVERED", "food_order", oid,
                       {"delivered_at": "2026-09-20T12:00:00+08:00"}, plat("PF"))
        return b

    def _positive_control(self, b: Builder):
        b.rule("R1", "v1")
        b.method("M1")
        b.emit("SAMPLE_DRAWN", "inspection_sample", "SP",
               {"lot_id": "LA", "sampled_by": "health"}, HC)
        b.emit("SAMPLE_TESTED", "inspection_sample", "SP",
               {"kind": "rapid", "result": "positive", "tested_by": "health",
                "method_code": "M1"}, HC)
        scope = b.svc.emergency_scope("SP", MR)
        b.emit("RISK_CONTROLLED", "inspection_sample", "SP",
               {"trigger_sample_id": "SP",
                "decisions": scope["recommended_decisions"],
                "authority": "market_regulation",
                "rule_code": "R1", "rule_version": "v1"}, MR)
        return scope

    def test_scope_classifies_intransit_onsale_pending_delivered(self) -> None:
        b = self._world()
        scope = self._positive_control(b)
        in_transit = {(x["party_id"], x["lot_id"]) for x in scope["in_transit"]}
        self.assertIn(("B-OTHER", "LA"), in_transit)
        on_sale = {(x["party_id"], x["lot_id"]) for x in scope["on_sale"]}
        self.assertIn(("B-MKT", "LA"), on_sale)
        self.assertIn(("B-CAFE", "LA"), on_sale)
        pending = {x["order_id"] for x in scope["pending_orders"]}
        delivered = {x["order_id"] for x in scope["delivered_orders"]}
        self.assertEqual(pending, {"O3"})
        self.assertEqual(delivered, {"O1", "O2"})
        self.assertIn("B-OTHER", scope["notify_parties"])
        self.assertIn("B-CAFE", scope["notify_parties"])

    def test_control_covers_descendant_lots(self) -> None:
        b = self._world()
        scope = self._positive_control(b)
        # LA 自身受控；样本批次的后代（此处无）同样会覆盖
        self.assertEqual(b.svc.proj.lots["LA"]["status"], "controlled")
        self.assertIn("LA", scope["affected_lot_ids"])
        # 母批 L0 不在控制范围（只向下游闭合，不牵连源头未检出部分），
        # 但反向追溯能定位田间
        self.assertNotIn("L0", scope["affected_lot_ids"])
        self.assertEqual(b.svc.trace("LA", MR)["upstream"][0]["lot_id"], "L0")

    def test_platform_cannot_fulfill_after_control(self) -> None:
        b = self._world()
        self._positive_control(b)
        b.emit("ORDER_PLACED", "food_order", "O4",
               {"shop_id": "S1", "consumer_ref": "新客",
                "items": [{"name": "菠菜", "qty": 1}]}, plat("PF"))
        with self.assertRaises(RiskLotFrozenError):
            b.emit("ORDER_FULFILLED", "food_order", "O4",
                   {"lots": [{"lot_id": "LA", "quantity": 1}]}, plat("PF"))

    def test_late_order_is_followup_state_not_evidence_change(self) -> None:
        b = self._world()
        self._positive_control(b)
        # 迟到状态可以在已送达订单之后追加，不触碰任何既有事件
        ev = b.emit("ORDER_LATE", "food_order", "O2",
                    {"minutes_late": 30}, plat("PF"))
        self.assertEqual(ev["version"], 4)  # placed/fulfilled/delivered 之后
        self.assertEqual(b.svc.proj.orders["O2"]["late"]["minutes_late"], 30)
        self.assertEqual(b.svc.proj.orders["O2"]["status"], "delivered")


if __name__ == "__main__":
    unittest.main()
