import unittest

from src import access
from src.errors import AccessDenied
from tests.util import HC, MR, PS, AG, JO, PUBLIC, Builder, biz, plat


class AccessTest(unittest.TestCase):
    def _world(self) -> Builder:
        b = Builder()
        b.registered_business("B1")
        b.registered_business("B2")
        b.emit("BENEFICIAL_OWNER_LINKED", "food_business", "B1",
               {"owner_name": "李某", "id_type": "身份证", "id_ref": "3301****"}, MR)
        b.emit("LICENSE_ISSUED", "food_business", "B1",
               {"license_no": "J1", "premises_id": "P1", "scope": "餐饮",
                "address": "地址A"}, MR)
        b.emit("LICENSE_VERIFIED", "food_business", "B1",
               {"license_no": "J1", "result": "pass",
                "verified_by": "market_regulation"}, MR)
        b.emit("PREMISES_VERIFIED", "licensed_premises", "P1",
               {"result": "no_physical_store",
                "verified_by": "market_regulation"}, MR)
        b.emit("SHOP_LISTED", "platform_shop", "SH1",
               {"platform": "PF", "business_id": "B1", "license_no": "J1"},
               plat("PF"))
        b.harvest("B1", "L1", 10)
        b.harvest("B2", "L2", 10)
        # 投诉（举报人信息）
        b.emit("COMPLAINT_FILED", "complaint", "C1",
               {"subject_type": "food_business", "subject_id": "B1",
                "content": "疑似使用问题蔬菜", "reporter_ref": "举报人王某",
                "reporter_contact": "139****0000"}, PUBLIC)
        return b

    def test_department_legal_mandate_matrix(self) -> None:
        b = self._world()
        # 农业农村不读店铺/订单
        self.assertFalse(access.can_read_aggregate(AG, "platform_shop", "SH1",
                                                   b.svc.proj))
        self.assertTrue(access.can_read_aggregate(MR, "platform_shop", "SH1",
                                                  b.svc.proj))
        # 卫健不读案件
        self.assertFalse(access.can_read_aggregate(HC, "enforcement_case", "X",
                                                   b.svc.proj))
        # 联合办公室跨域
        self.assertTrue(access.can_read_aggregate(JO, "platform_shop", "SH1",
                                                  b.svc.proj))

    def test_owner_pii_masked_for_agriculture_visible_to_mr(self) -> None:
        b = self._world()
        ag_view = b.svc.events_for(AG, "food_business", "B1")
        owner = [e for e in ag_view if e["event_type"] == "BENEFICIAL_OWNER_LINKED"]
        self.assertEqual(owner[0]["payload"]["owner_name"], access.MASK)

        mr_view = b.svc.events_for(MR, "food_business", "B1")
        owner_full = [e for e in mr_view
                      if e["event_type"] == "BENEFICIAL_OWNER_LINKED"]
        self.assertEqual(owner_full[0]["payload"]["owner_name"], "李某")

    def test_reporter_protected_from_health_visible_to_police(self) -> None:
        b = self._world()
        hc_view = b.svc.events_for(HC, "complaint", "C1")
        self.assertEqual(hc_view[0]["payload"]["reporter_ref"], access.MASK)
        ps_view = b.svc.events_for(PS, "complaint", "C1")
        self.assertEqual(ps_view[0]["payload"]["reporter_ref"], "举报人王某")

    def test_business_scoped_to_own_lots(self) -> None:
        b = self._world()
        self.assertTrue(access.can_read_aggregate(biz("B1"), "food_lot", "L1",
                                                  b.svc.proj))
        self.assertFalse(access.can_read_aggregate(biz("B1"), "food_lot", "L2",
                                                   b.svc.proj))
        with self.assertRaises(AccessDenied):
            b.svc.trace("L2", biz("B1"))

    def test_business_cannot_see_beneficial_owners_or_reporters(self) -> None:
        b = self._world()
        events = b.svc.events_for(biz("B1"), "food_business", "B1")
        self.assertFalse(any(e["event_type"] == "BENEFICIAL_OWNER_LINKED"
                             for e in events))
        comp = b.svc.events_for(biz("B1"), "complaint", "C1")
        self.assertEqual(comp, [])

    def test_platform_only_sees_own_shops_and_orders(self) -> None:
        b = self._world()
        self.assertTrue(access.can_read_aggregate(plat("PF"), "platform_shop",
                                                  "SH1", b.svc.proj))
        self.assertFalse(access.can_read_aggregate(plat("OTHER"), "platform_shop",
                                                   "SH1", b.svc.proj))
        self.assertFalse(access.can_read_aggregate(plat("PF"), "food_lot", "L1",
                                                   b.svc.proj))

    def test_public_sees_only_verified_alerts(self) -> None:
        b = self._world()
        self.assertEqual(b.svc.events_for(PUBLIC), [])
        b.rule("R1", "v1")
        b.method("M1")
        b.emit("SAMPLE_DRAWN", "inspection_sample", "S1",
               {"lot_id": "L1", "sampled_by": "health"}, HC)
        b.emit("SAMPLE_TESTED", "inspection_sample", "S1",
               {"kind": "rapid", "result": "positive", "tested_by": "health",
                "method_code": "M1"}, HC)
        control = b.emit("RISK_CONTROLLED", "inspection_sample", "S1",
                         {"trigger_sample_id": "S1", "decisions": ["hold"],
                          "authority": "market_regulation",
                          "rule_code": "R1", "rule_version": "v1"}, MR)
        b.emit("RECALL_OPENED", "recall_case", "RC1",
               {"control_event_id": control["event_id"], "root_lot_id": "L1",
                "authority": "market_regulation"}, MR)
        # 未核实前公众无任何信息
        self.assertEqual(b.svc.public_alerts(), [])

    def test_department_cannot_emit_outside_mandate(self) -> None:
        b = Builder()
        with self.assertRaises(AccessDenied):
            b.emit("BUSINESS_REGISTERED", "food_business", "B9",
                   {"name": "X", "registered_by": "market_regulation"}, AG)

    def test_payload_actor_field_must_match_department(self) -> None:
        b = Builder()
        with self.assertRaises(AccessDenied):
            b.emit("RULE_PUBLISHED", "risk_rule", "R1",
                   {"rule_code": "R1", "version": "v1",
                    "content_ref": "r.md",
                    "effective_at": "2026-01-01T00:00:00+08:00"}, HC)


if __name__ == "__main__":
    unittest.main()
