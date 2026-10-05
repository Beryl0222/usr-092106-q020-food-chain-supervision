import unittest

from src.errors import ConflictError
from tests.util import HC, MR, Builder, biz, plat


class RecallTest(unittest.TestCase):
    def _recall_world(self):
        """LA 60kg：批发持有 40、在途承运 5、餐饮售出 6（送达）+ 持有 9。"""
        b = Builder()
        for bid in ("B-FARM", "B-MKT", "B-CAFE", "CARRIER"):
            b.registered_business(bid)
        b.harvest("B-FARM", "LA", 60)
        b.emit("LOT_TRANSFERRED", "food_lot", "LA",
               {"from_party": "B-FARM", "to_party": "B-MKT", "quantity": 60},
               biz("B-FARM"))
        b.emit("LOT_TRANSFERRED", "food_lot", "LA",
               {"from_party": "B-MKT", "to_party": "B-CAFE", "quantity": 15},
               biz("B-MKT"))
        b.emit("LOT_TRANSFERRED", "food_lot", "LA",
               {"from_party": "B-MKT", "to_party": "CARRIER", "quantity": 5,
                "in_transit": True}, biz("B-MKT"))
        b.emit("LICENSE_ISSUED", "food_business", "B-CAFE",
               {"license_no": "J1", "premises_id": "P1", "scope": "餐饮"}, MR)
        b.emit("SHOP_LISTED", "platform_shop", "S1",
               {"platform": "PF", "business_id": "B-CAFE", "license_no": "J1"},
               plat("PF"))
        for oid, qty in (("O1", 2), ("O2", 4)):
            b.emit("ORDER_PLACED", "food_order", oid,
                   {"shop_id": "S1", "consumer_ref": "顾客", "items": []},
                   plat("PF"))
            b.emit("ORDER_FULFILLED", "food_order", oid,
                   {"lots": [{"lot_id": "LA", "quantity": qty}]}, plat("PF"))
            b.emit("ORDER_DELIVERED", "food_order", oid,
                   {"delivered_at": "2026-09-20T12:00:00+08:00"}, plat("PF"))
        b.rule("R1", "v1")
        b.method("M1")
        b.emit("SAMPLE_DRAWN", "inspection_sample", "SP",
               {"lot_id": "LA", "sampled_by": "health"}, HC)
        b.emit("SAMPLE_TESTED", "inspection_sample", "SP",
               {"kind": "rapid", "result": "positive", "tested_by": "health",
                "method_code": "M1"}, HC)
        control = b.emit("RISK_CONTROLLED", "inspection_sample", "SP",
                         {"trigger_sample_id": "SP",
                          "decisions": ["hold", "stop_sale", "seal", "recall"],
                          "authority": "market_regulation",
                          "rule_code": "R1", "rule_version": "v1"}, MR)
        b.emit("RECALL_OPENED", "recall_case", "RC1",
               {"control_event_id": control["event_id"], "root_lot_id": "LA",
                "authority": "market_regulation"}, MR)
        return b

    def _notify_all(self, b: Builder) -> None:
        for party in ("B-MKT", "B-CAFE", "CARRIER"):
            b.emit("DOWNSTREAM_NOTIFIED", "recall_case", "RC1",
                   {"party_id": party, "lot_ids": ["LA"], "channel": "督办单"}, MR)

    def test_balance_exposes_gap_until_reports_and_dispositions(self) -> None:
        b = self._recall_world()
        self._notify_all(b)
        st = b.svc.recall_status("RC1", MR)
        lot = st["lots"][0]
        # 初始：建档60，在库 54（40+9+5）已交代，已售 6 尚未上报去向
        self.assertEqual(lot["remaining"], 54)
        self.assertEqual(lot["sold_followup_gap"], 6)
        self.assertFalse(st["balanced"])

        b.emit("RECALL_REPORTED", "recall_case", "RC1",
               {"business_id": "B-CAFE", "quantities": [
                   {"lot_id": "LA", "quantity": 2, "disposition": "recovered"},
                   {"lot_id": "LA", "quantity": 4, "disposition": "consumed"}]},
               biz("B-CAFE"))
        # 已售 6kg 的去向已交代；54kg 在库冻结（已定位），总缺口归零
        st2 = b.svc.recall_status("RC1", MR)
        self.assertEqual(st2["lots"][0]["sold_followup_gap"], 0)
        self.assertEqual(st2["lots"][0]["unaccounted_gap"], 0)
        self.assertTrue(st2["balanced"])

        # 监督销毁后持仓清零，公式仍守恒（不重复计数）
        for party, qty in (("B-MKT", 40), ("CARRIER", 5), ("B-CAFE", 9)):
            b.emit("LOT_DISPOSITIONED", "food_lot", "LA",
                   {"disposition": "destroyed", "quantity": qty, "party": party},
                   MR)
        b.emit("RECALL_RECONCILED", "recall_case", "RC1",
               {"reconciled_by": "market_regulation"}, MR)
        st3 = b.svc.recall_status("RC1", MR)
        self.assertTrue(st3["balanced"])
        self.assertEqual(st3["lots"][0]["remaining"], 0)
        self.assertEqual(st3["lots"][0]["unaccounted_gap"], 0)

    def test_over_reporting_breaks_balance(self) -> None:
        b = self._recall_world()
        self._notify_all(b)
        b.emit("RECALL_REPORTED", "recall_case", "RC1",
               {"business_id": "B-CAFE", "quantities": [
                   {"lot_id": "LA", "quantity": 999, "disposition": "consumed"}]},
               biz("B-CAFE"))
        st = b.svc.recall_status("RC1", MR)
        self.assertLess(st["lots"][0]["unaccounted_gap"], 0)
        self.assertFalse(st["balanced"])

    def test_missing_notification_keeps_unbalanced(self) -> None:
        b = self._recall_world()
        # 只通知批发和餐饮，漏掉在途承运方
        b.emit("DOWNSTREAM_NOTIFIED", "recall_case", "RC1",
               {"party_id": "B-MKT", "lot_ids": ["LA"], "channel": "x"}, MR)
        b.emit("DOWNSTREAM_NOTIFIED", "recall_case", "RC1",
               {"party_id": "B-CAFE", "lot_ids": ["LA"], "channel": "x"}, MR)
        st = b.svc.recall_status("RC1", MR)
        self.assertIn("CARRIER", st["notifications_missing"])

    def test_close_requires_reconciliation(self) -> None:
        b = self._recall_world()
        self._notify_all(b)
        with self.assertRaises(ConflictError):
            b.emit("RECALL_CLOSED", "recall_case", "RC1",
                   {"closed_by": "market_regulation"}, MR)

    def test_duplicate_notification_deduped(self) -> None:
        b = self._recall_world()
        note = {"party_id": "B-MKT", "lot_ids": ["LA"], "channel": "督办单"}
        b.emit("DOWNSTREAM_NOTIFIED", "recall_case", "RC1", note, MR)
        with self.assertRaises(ConflictError):
            b.emit("DOWNSTREAM_NOTIFIED", "recall_case", "RC1", dict(note), MR)

    def test_public_alert_only_after_reconciliation(self) -> None:
        b = self._recall_world()
        self._notify_all(b)
        with self.assertRaises(ConflictError):
            b.emit("PUBLIC_ALERT_PUBLISHED", "recall_case", "RC1",
                   {"title": "提示", "content": "内容", "lot_ids": ["LA"],
                    "published_by": "market_regulation"}, MR)
        self.assertEqual(b.svc.public_alerts(), [])

        b.emit("RECALL_RECONCILED", "recall_case", "RC1",
               {"reconciled_by": "market_regulation"}, MR)
        b.emit("PUBLIC_ALERT_PUBLISHED", "recall_case", "RC1",
               {"title": "风险提示", "content": "已核实", "lot_ids": ["LA"],
                "published_by": "market_regulation"}, MR)
        alerts = b.svc.public_alerts()
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["title"], "风险提示")

    def test_retest_negative_clears_control_and_reopens_trade(self) -> None:
        b = self._recall_world()
        # 复检阴性 → 解除控制，批次恢复流通
        b.emit("SAMPLE_RETESTED", "inspection_sample", "SP",
               {"result": "negative", "tested_by": "health",
                "method_code": "M1"}, HC)
        b.emit("RISK_CLEARED", "inspection_sample", "SP",
               {"basis": "retest_negative", "decided_by": "market_regulation"}, MR)
        self.assertEqual(b.svc.proj.lots["LA"]["status"], "active")
        # 初始阳性事件仍然存在，未被改写
        kinds = [(t["kind"], t["result"]) for t in b.svc.proj.samples["SP"]["tests"]]
        self.assertIn(("rapid", "positive"), kinds)

    def test_clear_without_supporting_record_rejected(self) -> None:
        b = self._recall_world()
        with self.assertRaises(ConflictError):
            b.emit("RISK_CLEARED", "inspection_sample", "SP",
                   {"basis": "retest_negative",
                    "decided_by": "market_regulation"}, MR)

    def test_appeal_overturn_clears_control(self) -> None:
        b = self._recall_world()
        b.emit("SAMPLE_APPEAL_DECIDED", "inspection_sample", "SP",
               {"decision": "overturned", "decided_by": "market_regulation"}, MR)
        b.emit("RISK_CLEARED", "inspection_sample", "SP",
               {"basis": "appeal_overturned",
                "decided_by": "market_regulation"}, MR)
        self.assertEqual(b.svc.proj.lots["LA"]["status"], "active")


if __name__ == "__main__":
    unittest.main()
