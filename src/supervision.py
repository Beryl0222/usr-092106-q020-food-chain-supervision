"""监督业务：快检控制、召回闭环、复核解封、执法与证据反查、风险提示。

规则要点：
- 快检阳性只产生控制措施，立即固化在途/在售范围快照并通知下游；
- 复检、申诉、迟到订单只能追加为后续状态，不改写最初证据；
- 解封必须依据实验室复检阴性结论；快检自身不能解封；
- 召回数量与去向持续核对；
- 处罚决定必须能反查证据封存、规则版本与批次每次交接；
- 面向公众的提示只能以已核实事件为依据。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .store import EventStore
from .trace import FoodChainGraph

VERIFIED_POSITIVE_BASIS = {"LAB_RETEST_RECORDED", "ENFORCEMENT_DECISION"}
RECONCILIATION_FATES = {
    "destroyed",          # 销毁
    "returned",           # 退货
    "harmless_treated",   # 无害化处理
    "sold_before_order",  # 召回令送达前已售出（配合迟到订单核对）
    "missing",            # 企业申报缺失
}


class SupervisionError(ValueError):
    """业务规则被违反。"""


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


@dataclass
class RiskCase:
    event_id: str
    risk_case_id: str
    source_sample_id: str
    lot_id: str
    measures: list[str]
    scope_snapshot: dict
    notifications: list[dict]
    lifted: dict | None = None


class Supervision:
    def __init__(self, store: EventStore, graph: FoodChainGraph | None = None) -> None:
        self.store = store
        self.graph = graph or FoodChainGraph.from_store(store)
        self.samples: dict[str, dict] = {}
        self.methods: dict[str, dict] = {}
        self.risk_cases: dict[str, RiskCase] = {}
        self.recalls: dict[str, dict] = {}
        self.enforcement: dict[str, dict] = {}
        self.evidence: dict[str, dict] = {}
        self.rules: dict[str, list[dict]] = {}
        self.alerts: dict[str, dict] = {}
        self.corrective: dict[str, dict] = {}
        self.complaints: dict[str, dict] = {}
        for event in store.stream():
            self.apply(event)

    def _apply_all(self, event: dict) -> None:
        self.graph.apply(event)
        self.apply(event)

    # —— 折叠 ——

    def apply(self, event: dict) -> None:
        etype = event["event_type"]
        agg_id = event["aggregate_id"]
        p = event.get("payload", {})

        if etype == "SAMPLE_DRAWN":
            self.samples[agg_id] = {"sample_id": agg_id, "events": [], **p}
        elif etype == "SAMPLE_TESTED":
            sample = self.samples.setdefault(agg_id, {"sample_id": agg_id, "events": []})
            sample["events"].append({"event_id": event["event_id"], **p})
            sample.update(p)
        elif etype == "LAB_RETEST_RECORDED":
            sample = self.samples.setdefault(
                p["original_sample_id"], {"sample_id": p["original_sample_id"], "events": []}
            )
            sample["events"].append({"event_id": event["event_id"], **p})
            sample.setdefault("retests", []).append({"event_id": event["event_id"], **p})
        elif etype == "TEST_METHOD_REGISTERED":
            self.methods[agg_id] = {"method_id": agg_id, **p}
        elif etype == "RISK_CONTROLLED":
            self.risk_cases[agg_id] = RiskCase(
                event_id=event["event_id"],
                risk_case_id=agg_id,
                source_sample_id=p["source_sample_id"],
                lot_id=p["lot_id"],
                measures=list(p["measures"]),
                scope_snapshot=p.get("scope_snapshot", {}),
                notifications=[],
            )
        elif etype == "DOWNSTREAM_NOTIFIED":
            case = self.risk_cases.get(agg_id)
            if case:
                case.notifications.append({"event_id": event["event_id"], **p})
        elif etype == "CONTROL_LIFTED":
            case = self.risk_cases.get(agg_id)
            if case:
                case.lifted = {"event_id": event["event_id"], **p}
        elif etype == "RECALL_ORDERED":
            self.recalls[agg_id] = {
                "recall_case_id": agg_id,
                "risk_case_id": p["risk_case_id"],
                "lot_ids": list(p["lot_ids"]),
                "ordered_at": event["occurred_at"],
                "reconciliations": [],
            }
        elif etype == "RECALL_RECONCILED":
            recall = self.recalls.get(agg_id)
            if recall:
                recall["reconciliations"].append(
                    {"event_id": event["event_id"], "at": event["occurred_at"], "entries": p["entries"]}
                )
        elif etype == "COMPLAINT_FILED":
            self.complaints[agg_id] = {"complaint_id": agg_id, **p}
        elif etype == "ENFORCEMENT_OPENED":
            self.enforcement.setdefault(
                agg_id, {"case_id": agg_id, "decisions": [], "appeals": [], "referral": None}
            ).update({"business_id": p["business_id"], "opened_reason": p["opened_reason"]})
        elif etype == "ENFORCEMENT_DECISION":
            case = self.enforcement.setdefault(
                agg_id, {"case_id": agg_id, "decisions": [], "appeals": [], "referral": None}
            )
            case["decisions"].append({"event_id": event["event_id"], "at": event["occurred_at"], **p})
        elif etype == "APPEAL_FILED":
            case = self.enforcement.setdefault(
                agg_id, {"case_id": agg_id, "decisions": [], "appeals": [], "referral": None}
            )
            case["appeals"].append({"event_id": event["event_id"], **p})
        elif etype == "CASE_REFERRAL":
            case = self.enforcement.setdefault(
                agg_id, {"case_id": agg_id, "decisions": [], "appeals": [], "referral": None}
            )
            case["referral"] = {"event_id": event["event_id"], **p}
        elif etype == "EVIDENCE_SEALED":
            self.evidence[agg_id] = {"evidence_id": agg_id, "seal": {**p}, "handovers": []}
        elif etype == "EVIDENCE_HANDOVER":
            item = self.evidence.setdefault(agg_id, {"evidence_id": agg_id, "seal": None, "handovers": []})
            item["handovers"].append({"event_id": event["event_id"], **p})
        elif etype == "RULE_VERSION_PUBLISHED":
            self.rules.setdefault(agg_id, []).append(
                {"event_id": event["event_id"], "version": p["version"], "effective_at": p["effective_at"], **p}
            )
        elif etype == "CORRECTIVE_ACTION_SUBMITTED":
            self.corrective[agg_id] = {
                "action_id": agg_id, "submission": p, "review": None
            }
        elif etype == "CORRECTIVE_ACTION_REVIEWED":
            action = self.corrective.get(agg_id)
            if action:
                action["review"] = p
        elif etype == "PUBLIC_ALERT_PUBLISHED":
            self.alerts[agg_id] = {
                "alert_id": agg_id, "title": p["title"], "risk_level": p["risk_level"],
                "basis_event_ids": list(p["basis_event_ids"]), "updates": [],
                "published_event_id": event["event_id"], "status": "published",
            }
        elif etype == "PUBLIC_ALERT_UPDATED":
            alert = self.alerts.get(agg_id)
            if alert:
                alert["updates"].append({"event_id": event["event_id"], **p})
                alert["status"] = p["status"]

    def methods_setdefault(self) -> dict:
        if not hasattr(self, "methods"):
            self.methods = {}
        return self.methods

    # —— 提交网关：先过业务规则，再入不可变仓库 ——

    def submit(self, event: dict) -> str:
        errors = self.validate(event)
        if errors:
            raise SupervisionError("；".join(errors))
        self.store.append(event)
        self._apply_all(event)
        return event["event_id"]

    # —— 规则校验（供入库网关在 append 前调用） ——

    def validate(self, event: dict) -> list[str]:
        etype = event["event_type"]
        p = event.get("payload", {})
        errors: list[str] = []

        if etype == "RISK_CONTROLLED":
            sample = self.samples.get(p["source_sample_id"])
            if sample is None:
                errors.append("控制措施必须对应已登记样本")
            elif not any(
                e.get("category") == "rapid" and e.get("result") == "positive"
                for e in sample.get("events", [])
            ):
                errors.append("只有快检（rapid）阳性才能触发控制措施")
            if event["aggregate_id"] in self.risk_cases:
                errors.append("同一风险案重复建案；后续状态请追加通知/核对事件")
        elif etype == "CONTROL_LIFTED":
            case = self.risk_cases.get(event["aggregate_id"])
            if case is None:
                errors.append("解封对象不存在")
            else:
                basis = self.store.get(p["basis_event_id"])
                if basis is None or basis["event_type"] != "LAB_RETEST_RECORDED":
                    errors.append("解封依据必须是实验室复检结论，快检本身不能解封")
                elif basis["payload"].get("original_sample_id") != case.source_sample_id:
                    errors.append("复检样本与受控样本不一致")
                elif basis["payload"].get("conclusion") != "negative":
                    errors.append("只有复检阴性结论可以解封")
        elif etype == "RECALL_ORDERED":
            case = self.risk_cases.get(p["risk_case_id"])
            if case is None:
                errors.append("召回必须挂接已立的风险案")
            elif case.lifted:
                errors.append("风险批次已解封，不能再以该案召回")
        elif etype == "RECALL_RECONCILED":
            recall = self.recalls.get(event["aggregate_id"])
            if recall is None:
                errors.append("核对记录必须挂接已下的召回案")
            for entry in p.get("entries", []):
                if entry.get("fate") not in RECONCILIATION_FATES:
                    errors.append(f"未知去向：{entry.get('fate')}")
                if not isinstance(entry.get("quantity"), (int, float)) or entry["quantity"] < 0:
                    errors.append("召回数量必须为非负数")
        elif etype == "ENFORCEMENT_DECISION":
            for evidence_id in p.get("evidence_ids", []):
                item = self.evidence.get(evidence_id)
                if item is None or item["seal"] is None:
                    errors.append(f"证据 {evidence_id} 尚未封存，不能引用")
            versions = self.rules.get(p.get("rule_set_id", ""), [])
            if not any(v["version"] == p.get("rule_version") for v in versions):
                errors.append("处罚引用的规则版本不存在或未发布")
        elif etype == "PUBLIC_ALERT_PUBLISHED":
            for basis_id in p.get("basis_event_ids", []):
                basis = self.store.get(basis_id)
                if basis is None:
                    errors.append(f"发布依据 {basis_id} 不存在")
                elif basis["event_type"] not in VERIFIED_POSITIVE_BASIS:
                    errors.append(
                        f"{basis_id} 不是已核实结论（快检阳性不得直接对外发布）"
                    )
        return errors

    # —— 命令：快检阳性 → 控制 + 通知 ——

    def issue_control(
        self,
        risk_case_id: str,
        sample_id: str,
        measures: list[str],
        *,
        now: str,
        source_department: str = "market_regulation",
    ) -> list[str]:
        """根据快检阳性立即圈定范围并通知全部下游，返回追加的事件标识。"""
        sample = self.samples.get(sample_id)
        if sample is None:
            raise SupervisionError("样本不存在")
        rapid_event = next(
            (
                e
                for e in sample.get("events", [])
                if e.get("category") == "rapid" and e.get("result") == "positive"
            ),
            None,
        )
        if rapid_event is None:
            raise SupervisionError("没有快检阳性结果，不能采取控制措施")
        lot_id = sample["lot_id"]
        scope = self.graph.impact_scope(lot_id)
        event_ids = []
        control = {
            "event_id": f"risk-{risk_case_id}-v1",
            "event_type": "RISK_CONTROLLED",
            "aggregate_type": "risk_case",
            "aggregate_id": risk_case_id,
            "occurred_at": now,
            "recorded_at": now,
            "version": 1,
            "source_department": source_department,
            "summary": f"快检阳性触发控制：{sample_id} / {lot_id}",
            "payload": {
                "source_sample_id": sample_id,
                "lot_id": lot_id,
                "measures": measures,
                "scope_snapshot": scope,
            },
        }
        self.store.append(control)
        self._apply_all(control)
        event_ids.append(control["event_id"])

        notice = {
            "event_id": f"risk-{risk_case_id}-v2",
            "event_type": "DOWNSTREAM_NOTIFIED",
            "aggregate_type": "risk_case",
            "aggregate_id": risk_case_id,
            "occurred_at": now,
            "recorded_at": now,
            "version": 2,
            "source_department": source_department,
            "summary": "通知在途承运方、在售持有者与平台店铺",
            "payload": {
                "notice_targets": sorted(
                    set(scope["notify_holders"]) | set(scope["notify_shops"])
                )
            },
        }
        self.store.append(notice)
        self._apply_all(notice)
        event_ids.append(notice["event_id"])
        return event_ids

    # —— 查询：召回核对 ——

    def recall_status(self, recall_case_id: str) -> dict:
        recall = self.recalls[recall_case_id]
        case = self.risk_cases[recall["risk_case_id"]]
        expected: dict[str, float] = {}
        unit_by_lot: dict[str, str] = {}
        for bucket in ("in_transit", "on_hand"):
            for item in case.scope_snapshot.get(bucket, []):
                if item["lot_id"] in recall["lot_ids"]:
                    expected[item["lot_id"]] = expected.get(item["lot_id"], 0.0) + float(
                        item["quantity"]
                    )
                    unit_by_lot[item["lot_id"]] = item["unit"]
        reported: dict[str, float] = {lot: 0.0 for lot in expected}
        by_fate: dict[str, dict[str, float]] = {}
        for reconciliation in recall["reconciliations"]:
            for entry in reconciliation["entries"]:
                lot = entry["lot_id"]
                reported[lot] = reported.get(lot, 0.0) + float(entry["quantity"])
                by_fate.setdefault(entry["fate"], {}).setdefault(lot, 0.0)
                by_fate[entry["fate"]][lot] += float(entry["quantity"])
        lots = []
        for lot, want in expected.items():
            got = reported.get(lot, 0.0)
            lots.append(
                {
                    "lot_id": lot,
                    "expected": want,
                    "reported": got,
                    "unresolved": round(want - got, 6),
                    "over_reported": got > want,
                    "unit": unit_by_lot[lot],
                }
            )
        return {
            "recall_case_id": recall_case_id,
            "closed": all(lot["unresolved"] == 0 for lot in lots)
            and not any(lot["over_reported"] for lot in lots),
            "lots": lots,
            "by_fate": by_fate,
        }

    # —— 查询：迟到订单与控制后的违规流动 ——

    def subsequent_arrivals(self, risk_case_id: str) -> list[dict]:
        """控制之后才被系统接收、但成交于控制前后的订单：只作后续状态。"""
        case = self.risk_cases[risk_case_id]
        controlled_at = _parse(self.store.get(case.event_id)["occurred_at"])
        arrivals = []
        for lot_id in case.scope_snapshot["affected_lot_ids"]:
            for order in self.graph.orders_by_lot.get(lot_id, []):
                recorded_at = order.get("recorded_at")
                if recorded_at and _parse(recorded_at) > controlled_at:
                    arrivals.append(
                        {
                            "order_id": order["order_id"],
                            "lot_id": lot_id,
                            "placed_at": order["placed_at"],
                            "recorded_at": recorded_at,
                            "late": _parse(order["placed_at"]) <= controlled_at,
                        }
                    )
        return arrivals

    def movement_violations(self) -> list[dict]:
        """控制生效后、解封前，受控批次仍被交接/销售的情况。"""
        violations = []
        for case in self.risk_cases.values():
            controlled_at = _parse(self.store.get(case.event_id)["occurred_at"])
            lifted_at = (
                _parse(self.store.get(case.lifted["event_id"])["occurred_at"])
                if case.lifted
                else None
            )
            for lot_id in case.scope_snapshot.get("affected_lot_ids", []):
                for handover in self.graph.handovers_by_lot.get(lot_id, []):
                    at = _parse(handover["at"])
                    if at >= controlled_at and (lifted_at is None or at < lifted_at):
                        violations.append(
                            {"risk_case_id": case.risk_case_id, "lot_id": lot_id, **handover}
                        )
                for order in self.graph.orders_by_lot.get(lot_id, []):
                    at = _parse(order["placed_at"])
                    if at >= controlled_at and (lifted_at is None or at < lifted_at):
                        violations.append(
                            {
                                "risk_case_id": case.risk_case_id,
                                "lot_id": lot_id,
                                "kind": "sale_while_controlled",
                                "order_id": order["order_id"],
                                "at": order["placed_at"],
                            }
                        )
        return violations

    # —— 处罚反查：证据保管、规则版本、每次交接 ——

    def decision_audit(self, decision_event_id: str) -> dict:
        decision = self.store.get(decision_event_id)
        if decision is None or decision["event_type"] != "ENFORCEMENT_DECISION":
            raise SupervisionError("不是处罚决定事件")
        p = decision["payload"]
        evidence_bundle = {}
        for evidence_id in p["evidence_ids"]:
            item = self.evidence[evidence_id]
            sealed_hash = item["seal"]["content_hash"]
            chain_intact = all(
                handover["hash_check"] == sealed_hash for handover in item["handovers"]
            )
            evidence_bundle[evidence_id] = {
                "seal": item["seal"],
                "handovers": item["handovers"],
                "chain_intact": chain_intact,
            }
        rule = next(
            v
            for v in self.rules[p["rule_set_id"]]
            if v["version"] == p["rule_version"]
        )
        related_case = self.enforcement[decision["aggregate_id"]]
        lot_ids = list(p.get("lot_ids", ()))
        seen_handovers: set[str] = set()
        handovers = []
        for lot_id in lot_ids:
            for handover in self.lot_audit_trail(lot_id):
                if handover["event_id"] not in seen_handovers:
                    seen_handovers.add(handover["event_id"])
                    handovers.append(handover)
        return {
            "decision_event_id": decision_event_id,
            "enforcement_case_id": decision["aggregate_id"],
            "rule": {
                "rule_set_id": p["rule_set_id"],
                "version": p["rule_version"],
                "published_event_id": rule["event_id"],
                "effective_at": rule["effective_at"],
            },
            "evidence": evidence_bundle,
            "lot_handovers": handovers,
            "appeals": related_case["appeals"],
            "referral": related_case["referral"],
        }

    def lot_audit_trail(self, lot_id: str) -> list[dict]:
        """供处罚反查批次的每次交接（含冷链与换包前后，按时间去重）。"""
        records: dict[str, dict] = {}
        for handover in self.graph.handovers_of(lot_id, direction="up"):
            records[handover["event_id"]] = handover
        for handover in self.graph.handovers_of(lot_id, direction="down"):
            records.setdefault(handover["event_id"], handover)
        return sorted(records.values(), key=lambda r: r["at"])
