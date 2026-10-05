import json
import unittest
from pathlib import Path

from src import catalog
from src.access import AccessPolicy, Actor
from src.scenario import (
    CONTROLLED_AT,
    RECALL_CASE_ID,
    RISK_CASE_ID,
    build,
)
from src.store import EventError, EventStore
from src.supervision import Supervision, SupervisionError
from src.trace import FoodChainGraph
from src.validator import validate_event

ROOT = Path(__file__).parents[1]


def make_event(**overrides) -> dict:
    event = {
        "event_id": "e-1",
        "event_type": "RULE_VERSION_PUBLISHED",
        "aggregate_type": "rule_set",
        "aggregate_id": "rs-1",
        "occurred_at": "2026-10-01T00:00:00+08:00",
        "version": 1,
        "summary": "测试事件",
        "payload": {"scope": "x", "version": "v1", "effective_at": "2026-10-01T00:00:00+08:00"},
    }
    event.update(overrides)
    return event


class ContractTest(unittest.TestCase):
    def test_sample_matches_envelope(self) -> None:
        sample = json.loads((ROOT / "data" / "sample.json").read_text(encoding="utf-8"))
        self.assertEqual(validate_event(sample), [])

    def test_scenario_data_is_valid_and_rebuilds(self) -> None:
        records = json.loads((ROOT / "data" / "scenario_chain.json").read_text(encoding="utf-8"))
        for record in records:
            self.assertEqual(validate_event(record), [], record["event_id"])
        store = EventStore()
        for record in records:  # 含跨部门重复报送的幂等路径
            store.append(json.loads(json.dumps(record)))
        self.assertEqual(len(store), len(records))

    def test_schema_enum_matches_catalog(self) -> None:
        schema = json.loads((ROOT / "contracts" / "domain.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(set(schema["properties"]["event_type"]["enum"]), set(catalog.EVENT_TYPES))
        self.assertEqual(set(schema["properties"]["aggregate_type"]["enum"]), set(catalog.AGGREGATES))

    def test_payload_required_and_aggregate_binding(self) -> None:
        bad = make_event(event_type="LOT_TRANSFERRED", aggregate_type="food_lot", payload={})
        errors = validate_event(bad)
        self.assertTrue(any("payload 缺少字段" in e for e in errors))
        wrong = make_event(event_type="LOT_TRANSFERRED", aggregate_type="food_business")
        self.assertTrue(any("聚合类型必须是" in e for e in validate_event(wrong)))


class StoreTest(unittest.TestCase):
    def test_duplicate_idempotent_conflict_rejected(self) -> None:
        store = EventStore()
        event = make_event()
        first = store.append(event)
        second = store.append(json.loads(json.dumps(event)))
        self.assertTrue(first.accepted)
        self.assertTrue(second.deduped)
        self.assertEqual(len(store), 1)

        tampered = make_event(summary="被改写的摘要")
        with self.assertRaises(EventError):
            store.append(tampered)

    def test_aggregate_version_must_be_monotonic(self) -> None:
        store = EventStore()
        store.append(make_event(version=1))
        with self.assertRaises(EventError):
            store.append(make_event(event_id="e-2", version=3,
                                    payload={"scope": "x", "version": "v2",
                                             "effective_at": "2026-10-02T00:00:00+08:00"}))


class TraceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store, self.graph, self.sup = build()

    def test_origin_and_affected_across_relabel_and_split(self) -> None:
        # 向下追踪：田间批次穿透换包与拆分
        self.assertEqual(
            self.graph.affected_lots("lot-veg-01"),
            {"lot-veg-01", "lot-veg-01p", "lot-veg-01p-a", "lot-veg-01p-b"},
        )
        # 向上溯源：幽灵店铺所用批次能追到田间生产者，且保留换包边
        origins = self.graph.origin_lots("lot-veg-01p-a")
        self.assertIn("lot-veg-01", origins)
        edge_kinds = {
            (e["parent"], e["child"]): e["kind"] for e in self.graph.genealogy_edges()
        }
        self.assertEqual(edge_kinds[("lot-veg-01", "lot-veg-01p")], "relabel")
        self.assertEqual(edge_kinds[("lot-veg-01p", "lot-veg-01p-a")], "split")

    def test_platform_pass_without_physical_store_is_flagged(self) -> None:
        findings = self.graph.suspicious_shops()
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["shop_id"], "shop-weipin-01")
        self.assertEqual(findings[0]["field_result"], "no_physical_store")

    def test_beneficial_owner_links_businesses(self) -> None:
        self.assertEqual(
            self.graph.businesses_by_beneficial_owner("厉强"),
            ["bz-restaurant-group-01", "bz-restaurant-group-02"],
        )


class SupervisionFlowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store, self.graph, self.sup = build()
        self.policy = AccessPolicy(self.store, self.graph, self.sup)

    def test_impact_scope_snapshot_at_control(self) -> None:
        case = self.sup.risk_cases[RISK_CASE_ID]
        snapshot = case.scope_snapshot
        in_transit = {row["lot_id"]: row["quantity"] for row in snapshot["in_transit"]}
        on_hand = {row["lot_id"]: row["quantity"] for row in snapshot["on_hand"]}
        self.assertEqual(in_transit, {"lot-veg-01p-a": 300})
        self.assertEqual(on_hand, {"lot-veg-01p-b": 700})  # 母批已扣减，不重复计
        self.assertEqual(
            set(snapshot["notify_shops"]), {"shop-weipin-01"}
        )
        self.assertIn("bz-distributor-01", snapshot["notify_holders"])
        self.assertIn("bz-wholesale-01", snapshot["notify_holders"])

    def test_recall_quantities_and_fates_reconcile(self) -> None:
        status = self.sup.recall_status(RECALL_CASE_ID)
        by_lot = {row["lot_id"]: row for row in status["lots"]}
        self.assertEqual(by_lot["lot-veg-01p-a"]["unresolved"], 0)
        self.assertEqual(by_lot["lot-veg-01p-b"]["unresolved"], 0)
        self.assertTrue(status["closed"])
        self.assertEqual(
            status["by_fate"]["destroyed"]["lot-veg-01p-a"], 300
        )
        self.assertEqual(
            status["by_fate"]["harmless_treated"]["lot-veg-01p-b"], 100
        )

    def test_movement_while_controlled_is_detected(self) -> None:
        violations = self.sup.movement_violations()
        kinds = []
        for v in violations:
            kinds.append((v["kind"], v.get("order_id") or v.get("lot_id")))
        self.assertIn(("transfer", "lot-veg-01p-b"), kinds)          # 控制后违规外运
        self.assertIn(("sale_while_controlled", "or-1003"), kinds)  # 平台仍接单
        # 控制前订单不算违规
        self.assertNotIn(("sale_while_controlled", "or-1001"), kinds)

    def test_late_order_is_subsequent_state_only(self) -> None:
        arrivals = self.sup.subsequent_arrivals(RISK_CASE_ID)
        late = {a["order_id"]: a for a in arrivals}
        self.assertIn("or-1002", late)
        self.assertTrue(late["or-1002"]["late"])  # 成交于控制前、补录于控制后
        # 最初订单事件仍原样保留
        self.assertEqual(self.store.get("order-1002")["payload"]["status"], "completed")

    def test_decision_audit_reaches_evidence_rule_and_handovers(self) -> None:
        audit = self.sup.decision_audit("ec-ws-decision")
        self.assertEqual(audit["rule"]["version"], "2026.03")
        self.assertTrue(audit["evidence"]["ev-sample-01"]["chain_intact"])
        handover_events = {h["event_id"] for h in audit["lot_handovers"]}
        # 换包前后的交接与冷链交接都在
        self.assertIn("lot-veg-01-transit", handover_events)
        self.assertIn("lot-a-cold", handover_events)
        self.assertIn("lot-b-illegal-move", handover_events)
        # 申诉是追加状态，处罚决定事件本身未被修改
        self.assertEqual(
            self.store.get("ec-ws-decision")["payload"]["decision_type"],
            "fine_and_confiscation",
        )

    def test_evidence_chain_break_is_visible(self) -> None:
        # 构造一次哈希不符的证据交接
        store = EventStore()
        store.append(make_event())
        graph = FoodChainGraph.from_store(store)
        sup = Supervision(store, graph)
        sealed = {
            "event_id": "ev-x-1", "event_type": "EVIDENCE_SEALED", "aggregate_type": "evidence_item",
            "aggregate_id": "ev-x", "occurred_at": "2026-10-01T09:00:00+08:00", "version": 1,
            "summary": "封存", "payload": {"sealed_by": "market_regulation",
            "custody_location": "柜1", "related_case_id": "c1", "content_hash": "h1"},
        }
        handover = {
            "event_id": "ev-x-2", "event_type": "EVIDENCE_HANDOVER", "aggregate_type": "evidence_item",
            "aggregate_id": "ev-x", "occurred_at": "2026-10-01T10:00:00+08:00", "version": 2,
            "summary": "交接", "payload": {"from_party": "a", "to_party": "b",
            "handler": "钱某", "hash_check": "h-tampered"},
        }
        sup.submit(sealed)
        sup.submit(handover)
        self.assertFalse(sup.evidence["ev-x"]["handovers"][-1]["hash_check"] == "h1")


class RuleGuardTest(unittest.TestCase):
    def _rapid_store(self) -> tuple[EventStore, Supervision]:
        store = EventStore()
        graph = FoodChainGraph()
        sup = Supervision(store, graph)
        for event in (
            {
                "event_id": "lot-1", "event_type": "LOT_REGISTERED", "aggregate_type": "food_lot",
                "aggregate_id": "lot-1", "occurred_at": "2026-10-01T00:00:00+08:00", "version": 1,
                "summary": "批次", "payload": {"product_name": "菜", "producer_business_id": "b1",
                "quantity": 10, "unit": "kg"},
            },
            {
                "event_id": "s-1", "event_type": "SAMPLE_DRAWN", "aggregate_type": "inspection_sample",
                "aggregate_id": "s-1", "occurred_at": "2026-10-01T01:00:00+08:00", "version": 1,
                "summary": "抽样", "payload": {"lot_id": "lot-1", "draw_department": "market_regulation",
                "drawn_at": "2026-10-01T01:00:00+08:00"},
            },
        ):
            sup.submit(event)
        return store, sup

    def test_control_requires_rapid_positive(self) -> None:
        store, sup = self._rapid_store()
        bad_control = {
            "event_id": "rc-1", "event_type": "RISK_CONTROLLED", "aggregate_type": "risk_case",
            "aggregate_id": "rc-1", "occurred_at": "2026-10-01T02:00:00+08:00", "version": 1,
            "summary": "无快检阳性的控制",
            "payload": {"source_sample_id": "s-1", "lot_id": "lot-1", "measures": ["封存"]},
        }
        with self.assertRaises(SupervisionError):
            sup.submit(bad_control)

    def test_lift_requires_negative_lab_and_rapid_cannot_relabel(self) -> None:
        store, sup = self._rapid_store()
        sup.submit({
            "event_id": "s-1t", "event_type": "SAMPLE_TESTED", "aggregate_type": "inspection_sample",
            "aggregate_id": "s-1", "occurred_at": "2026-10-01T02:00:00+08:00", "version": 2,
            "summary": "快检阳性", "payload": {"lot_id": "lot-1", "method_id": "m1",
            "category": "rapid", "analyte": "农残", "result": "positive"},
        })
        ids = sup.issue_control("rc-1", "s-1", ["封存"], now="2026-10-01T03:00:00+08:00")
        # 阳性正式结论不能解封
        sup.submit({
            "event_id": "s-1lab+", "event_type": "LAB_RETEST_RECORDED",
            "aggregate_type": "inspection_sample", "aggregate_id": "s-1",
            "occurred_at": "2026-10-02T02:00:00+08:00", "version": 3, "summary": "复检阳性",
            "payload": {"original_sample_id": "s-1", "method_id": "m2", "conclusion": "positive"},
        })
        with self.assertRaises(SupervisionError):
            sup.submit({
                "event_id": "rc-1lift1", "event_type": "CONTROL_LIFTED",
                "aggregate_type": "risk_case", "aggregate_id": "rc-1",
                "occurred_at": "2026-10-02T03:00:00+08:00", "version": 3,
                "summary": "试图解封", "payload": {"basis_event_id": "s-1lab+", "reason": "复检阳性"},
            })
        # 阴性复检可解封
        sup.submit({
            "event_id": "s-1lab-", "event_type": "LAB_RETEST_RECORDED",
            "aggregate_type": "inspection_sample", "aggregate_id": "s-1",
            "occurred_at": "2026-10-03T02:00:00+08:00", "version": 4, "summary": "复检阴性",
            "payload": {"original_sample_id": "s-1", "method_id": "m2", "conclusion": "negative"},
        })
        sup.submit({
            "event_id": "rc-1lift2", "event_type": "CONTROL_LIFTED",
            "aggregate_type": "risk_case", "aggregate_id": "rc-1",
            "occurred_at": "2026-10-03T03:00:00+08:00", "version": 3,
            "summary": "复检阴性，解除控制",
            "payload": {"basis_event_id": "s-1lab-", "reason": "实验室复检阴性"},
        })
        self.assertIsNotNone(sup.risk_cases["rc-1"].lifted)
        self.assertIn(ids[0], [e["event_id"] for e in store.by_type("RISK_CONTROLLED")])

    def test_public_alert_rejects_rapid_basis(self) -> None:
        store, sup = self._rapid_store()
        with self.assertRaises(SupervisionError):
            sup.submit({
                "event_id": "al-bad", "event_type": "PUBLIC_ALERT_PUBLISHED",
                "aggregate_type": "public_alert", "aggregate_id": "al-bad",
                "occurred_at": "2026-10-01T04:00:00+08:00", "version": 1,
                "summary": "不能基于快检发提示",
                "payload": {"title": "x", "basis_event_ids": ["s-1"],
                            "risk_level": "low", "content": "x"},
            })

    def test_decision_requires_sealed_evidence_and_published_rule(self) -> None:
        store, sup = self._rapid_store()
        sup.submit({
            "event_id": "ec-1", "event_type": "ENFORCEMENT_OPENED",
            "aggregate_type": "enforcement_case", "aggregate_id": "ec-1",
            "occurred_at": "2026-10-01T05:00:00+08:00", "version": 1, "summary": "立案",
            "payload": {"business_id": "b1", "opened_reason": "测试"},
        })
        with self.assertRaises(SupervisionError):
            sup.submit({
                "event_id": "ec-1d", "event_type": "ENFORCEMENT_DECISION",
                "aggregate_type": "enforcement_case", "aggregate_id": "ec-1",
                "occurred_at": "2026-10-01T06:00:00+08:00", "version": 2, "summary": "缺证据规则",
                "payload": {"decision_type": "fine", "rule_set_id": "rs-x",
                            "rule_version": "v9", "evidence_ids": ["ev-missing"]},
            })


class AccessTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store, self.graph, self.sup = build()
        self.policy = AccessPolicy(self.store, self.graph, self.sup)

    def _types(self, actor: Actor) -> set[str]:
        return {e["event_type"] for e in self.policy.visible_events(actor)}

    def test_public_sees_only_verified_alerts(self) -> None:
        types = self._types(Actor("public"))
        self.assertEqual(types, {"PUBLIC_ALERT_PUBLISHED", "PUBLIC_ALERT_UPDATED"})
        alerts = self.policy.public_alerts()
        self.assertEqual(alerts[0]["status"], "resolved")

    def test_agriculture_cannot_see_enforcement_and_complaints(self) -> None:
        types = self._types(Actor("agriculture"))
        self.assertIn("LOT_RELABELED", types)
        self.assertNotIn("ENFORCEMENT_DECISION", types)
        self.assertNotIn("COMPLAINT_FILED", types)

    def test_police_sees_only_referred_case(self) -> None:
        types = self._types(Actor("police"))
        visible_cases = {
            e["aggregate_id"] for e in self.policy.visible_events(Actor("police"))
            if e["aggregate_type"] == "enforcement_case"
        }
        self.assertEqual(visible_cases, {"ec-restaurant-01"})
        # 移送证据可见，未移送证据不可见
        visible_events = self.policy.visible_events(Actor("police"))
        evidence_ids = {e["aggregate_id"] for e in visible_events if e["aggregate_type"] == "evidence_item"}
        self.assertEqual(evidence_ids, {"ev-relabel-01"})
        self.assertIn("BENEFICIAL_OWNER_DECLARED", types)  # 涉案主体受益人

    def test_complainant_identity_protected(self) -> None:
        complaint = self.store.get("complaint-01")
        agriculture_view = self.policy.redact(complaint, Actor("agriculture"))
        # 农业角色连事件都不可见；对可见的卫健/企业以外角色校验遮蔽逻辑
        masked = self.policy.redact(complaint, Actor("market_regulation"))
        self.assertIn("138", masked["payload"]["complainant_ref"])  # 主办部门可见
        police_view = self.policy.redact(complaint, Actor("police"))
        self.assertNotIn("138", police_view["payload"]["complainant_ref"])

    def test_enterprise_dashboard_and_recall_tracking(self) -> None:
        dashboard = self.policy.enterprise_dashboard("bz-wholesale-01")
        self.assertIn("lot-veg-01p-b", dashboard["lots"])
        self.assertEqual(len(dashboard["recalls"]), 1)
        self.assertTrue(dashboard["recalls"][0]["closed"])
        self.assertIn("ca-01", dashboard["corrective_actions"])
        # 企业看不到举报件和受益人声明
        raw = self.policy.visible_events(Actor("enterprise", "bz-wholesale-01"))
        self.assertFalse(any(e["event_type"] == "COMPLAINT_FILED" for e in raw))
        self.assertFalse(any(e["event_type"] == "BENEFICIAL_OWNER_DECLARED" for e in raw))
        # 只能看到针对自己的处罚
        decision_ids = {e["aggregate_id"] for e in raw if e["event_type"] == "ENFORCEMENT_DECISION"}
        self.assertEqual(decision_ids, {"ec-wholesale-01"})


if __name__ == "__main__":
    unittest.main()
