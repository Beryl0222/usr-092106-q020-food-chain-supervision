"""按法定职责的访问投影。

角色即视角：agriculture / market_regulation / health / police / joint_office
为部门视角；enterprise 携带 business_id，只能看与己有关的记录；public 只看
核实后发布的风险提示。

两层保护：
- 聚合级（can_read）：未定案的执法信息、举报件、证据保管链不对无关角色开放；
  公安只能看已移送案件；企业只看本主体相关记录。
- 字段级（redact）：举报人信息、受益人信息即使事件可见也按角色遮蔽。
"""

from __future__ import annotations

from dataclasses import dataclass

# 各部门依职责可见的事件类型（联合办与市场监管为全量）
_LOT_EVENTS = {
    "LOT_REGISTERED",
    "LOT_SPLIT",
    "LOT_MERGED",
    "LOT_RELABELED",
    "PROCESSING_RECORDED",
    "LOT_TRANSFERRED",
    "COLD_CHAIN_HANDOVER",
}
_TEST_EVENTS = {
    "SAMPLE_DRAWN",
    "TEST_METHOD_REGISTERED",
    "SAMPLE_TESTED",
    "LAB_RETEST_RECORDED",
}
_RISK_EVENTS = {
    "RISK_CONTROLLED",
    "DOWNSTREAM_NOTIFIED",
    "CONTROL_LIFTED",
    "RECALL_ORDERED",
    "RECALL_RECONCILED",
}
_SUBJECT_EVENTS = {
    "SUBJECT_REGISTERED",
    "PREMISES_LICENSED",
    "PREMISES_VERIFIED",
    "PLATFORM_SHOP_LISTED",
    "DELIVERY_PLATFORM_VERIFIED",
}
_ENFORCEMENT_EVENTS = {
    "ENFORCEMENT_OPENED",
    "ENFORCEMENT_DECISION",
    "APPEAL_FILED",
    "CASE_REFERRAL",
    "EVIDENCE_SEALED",
    "EVIDENCE_HANDOVER",
}

ROLE_EVENT_TYPES: dict[str, set[str]] = {
    "agriculture": _LOT_EVENTS | _TEST_EVENTS | _RISK_EVENTS
    | {"SUBJECT_REGISTERED"}
    | {"PUBLIC_ALERT_PUBLISHED", "PUBLIC_ALERT_UPDATED", "RULE_VERSION_PUBLISHED"},
    "health": _TEST_EVENTS | _RISK_EVENTS
    | {"PUBLIC_ALERT_PUBLISHED", "PUBLIC_ALERT_UPDATED", "RULE_VERSION_PUBLISHED"},
    "market_regulation": set(),   # 全量，见 can_read
    "joint_office": set(),        # 全量，见 can_read
}

FULL_ACCESS_ROLES = {"market_regulation", "joint_office"}
COMPLAINT_VISIBLE_ROLES = {"market_regulation", "joint_office"}
BENEFICIAL_OWNER_ROLES = {"market_regulation", "joint_office", "police"}

MASK = "［依法遮蔽］"


@dataclass(frozen=True)
class Actor:
    role: str
    business_id: str | None = None  # 仅 enterprise 使用


class AccessPolicy:
    def __init__(self, store, graph, supervision) -> None:
        self.store = store
        self.graph = graph
        self.sup = supervision

    # —— 聚合级可见性 ——

    def can_read(self, event: dict, actor: Actor) -> bool:
        role = actor.role
        etype = event["event_type"]

        if role == "public":
            return etype in {"PUBLIC_ALERT_PUBLISHED", "PUBLIC_ALERT_UPDATED"}

        if role in FULL_ACCESS_ROLES:
            return True

        if role == "enterprise":
            return self._enterprise_can_read(event, actor.business_id)

        if role == "police":
            return self._police_can_read(event)

        allowed = ROLE_EVENT_TYPES.get(role, set())
        return etype in allowed

    def _enterprise_can_read(self, event: dict, business_id: str | None) -> bool:
        if not business_id:
            return False
        etype = event["event_type"]
        agg_type = event["aggregate_type"]
        p = event.get("payload", {})

        # 本主体档案、场所、店铺
        if agg_type == "food_business" and event["aggregate_id"] == business_id:
            return etype != "BENEFICIAL_OWNER_DECLARED"  # 受益人信息不对企业自助通道开放
        if agg_type in {"premises", "platform_shop"}:
            target = (
                self.graph.premises.get(event["aggregate_id"])
                if agg_type == "premises"
                else self.graph.shops.get(event["aggregate_id"])
            )
            return bool(target and getattr(target, "business_id", None) == business_id)

        # 本主体经手的批次与交接
        if agg_type == "food_lot":
            return event["aggregate_id"] in self._lots_of_business(business_id)

        # 本主体订单
        if agg_type == "order":
            return p.get("business_id") == business_id

        # 影响本主体的控制与召回
        if agg_type == "risk_case":
            case = self.sup.risk_cases.get(event["aggregate_id"])
            return bool(case and business_id in case.scope_snapshot.get("notify_holders", []))
        if agg_type == "recall_case":
            recall = self.sup.recalls.get(event["aggregate_id"])
            if not recall:
                return False
            my_lots = self._lots_of_business(business_id)
            return any(lot in my_lots for lot in recall["lot_ids"])

        # 本主体提交的纠正、针对本主体的处罚决定（已作出的决定）、申诉
        if agg_type == "corrective_action":
            return p.get("business_id") == business_id
        if etype == "ENFORCEMENT_DECISION":
            case = self.sup.enforcement.get(event["aggregate_id"])
            return bool(case and case.get("business_id") == business_id)
        if etype == "APPEAL_FILED":
            case = self.sup.enforcement.get(event["aggregate_id"])
            return bool(case and case.get("business_id") == business_id)

        # 规则与已发布提示对企业可见；举报、立案侦查、证据不对企业开放
        return etype in {
            "RULE_VERSION_PUBLISHED",
            "PUBLIC_ALERT_PUBLISHED",
            "PUBLIC_ALERT_UPDATED",
        }

    def _police_can_read(self, event: dict) -> bool:
        etype = event["event_type"]
        if etype in {"PUBLIC_ALERT_PUBLISHED", "PUBLIC_ALERT_UPDATED", "RULE_VERSION_PUBLISHED"}:
            return True
        referred_businesses = self._referred_business_ids()
        # 移送案件所涉主体档案与受益人信息
        if etype == "SUBJECT_REGISTERED" or etype == "BENEFICIAL_OWNER_DECLARED":
            return event["aggregate_id"] in referred_businesses
        if etype in {"PREMISES_LICENSED", "PREMISES_VERIFIED"}:
            premises = self.graph.premises.get(event["aggregate_id"])
            return bool(premises and premises.business_id in referred_businesses)
        # 仅已移送公安的执法案
        if event["aggregate_type"] in {"enforcement_case", "evidence_item", "complaint"}:
            referred_cases = {
                case_id
                for case_id, case in self.sup.enforcement.items()
                if case.get("referral") and case["referral"].get("to_department") == "police"
            }
            if event["aggregate_type"] == "enforcement_case":
                return event["aggregate_id"] in referred_cases
            if event["aggregate_type"] == "evidence_item":
                referenced = {
                    ev_id
                    for case_id in referred_cases
                    for ev_id in self.sup.enforcement[case_id]["referral"]["evidence_ids"]
                }
                return event["aggregate_id"] in referenced
            if event["aggregate_type"] == "complaint":
                complaint = self.sup.complaints.get(event["aggregate_id"])
                return bool(complaint and complaint.get("business_id") in referred_businesses)
        # 移送案件涉及的批次交接
        if event["aggregate_type"] == "food_lot":
            return event["aggregate_id"] in self._referred_lot_ids()
        return False

    def _referred_business_ids(self) -> set[str]:
        return {
            case.get("business_id")
            for case in self.sup.enforcement.values()
            if case.get("referral")
            and case["referral"].get("to_department") == "police"
            and case.get("business_id")
        }

    def _referred_lot_ids(self) -> set[str]:
        lots: set[str] = set()
        for case in self.sup.enforcement.values():
            if not case.get("referral") or case["referral"].get("to_department") != "police":
                continue
            for decision in case["decisions"]:
                for lot_id in decision.get("lot_ids", ()):
                    lots.update(self.graph.origin_lots(lot_id))
                    lots.update(self.graph.affected_lots(lot_id))
        return lots

    def _lots_of_business(self, business_id: str) -> set[str]:
        return {
            lot.lot_id
            for lot in self.graph.lots.values()
            if business_id
            in {
                lot.producer_business_id,
                lot.holder_id,
                lot.transit_from,
                lot.transit_to,
            }
        }

    # —— 字段级遮蔽 ——

    def redact(self, event: dict, actor: Actor) -> dict:
        etype = event["event_type"]
        result = {
            key: (dict(value) if isinstance(value, dict) else value)
            for key, value in event.items()
        }
        payload = dict(event.get("payload", {}))

        if etype == "COMPLAINT_FILED" and actor.role not in COMPLAINT_VISIBLE_ROLES:
            if "complainant_ref" in payload:
                payload["complainant_ref"] = MASK
        if etype == "BENEFICIAL_OWNER_DECLARED" and actor.role not in BENEFICIAL_OWNER_ROLES:
            for key in ("owner_name", "owner_id_no", "contact"):
                if key in payload:
                    payload[key] = MASK

        result["payload"] = payload
        return result

    # —— 投影 ——

    def visible_events(self, actor: Actor) -> list[dict]:
        return [
            self.redact(event, actor)
            for event in self.store.stream()
            if self.can_read(event, actor)
        ]

    def public_alerts(self) -> list[dict]:
        """公众视图：只有核实后发布的提示及其后续状态，绝不暴露快检原始记录。"""
        out = []
        for event in self.store.by_type("PUBLIC_ALERT_PUBLISHED"):
            alert = self.sup.alerts[event["aggregate_id"]]
            out.append(
                {
                    "alert_id": alert["alert_id"],
                    "title": alert["title"],
                    "risk_level": alert["risk_level"],
                    "status": alert["status"],
                    "published_at": event["occurred_at"],
                    "updates": alert["updates"],
                }
            )
        return out

    def enterprise_dashboard(self, business_id: str) -> dict:
        """企业视图：跟踪本批次/召回、提交纠正；不暴露举报与未定案信息。"""
        actor = Actor(role="enterprise", business_id=business_id)
        business = self.graph.businesses.get(business_id)
        my_lots = self._lots_of_business(business_id)

        controls = []
        recalls = []
        for case_id, case in self.sup.risk_cases.items():
            if business_id in case.scope_snapshot.get("notify_holders", []):
                controls.append(
                    {
                        "risk_case_id": case_id,
                        "lot_id": case.lot_id,
                        "measures": case.measures,
                        "lifted": bool(case.lifted),
                        "notifications": case.notifications,
                    }
                )
                for recall_id, recall in self.sup.recalls.items():
                    if recall["risk_case_id"] == case_id and any(
                        lot in my_lots for lot in recall["lot_ids"]
                    ):
                        status = self.sup.recall_status(recall_id)
                        recalls.append(
                            {
                                "recall_case_id": recall_id,
                                "my_lot_entries": [
                                    lot for lot in status["lots"] if lot["lot_id"] in my_lots
                                ],
                                "closed": status["closed"],
                            }
                        )

        decisions = [
            self.redact(event, actor)
            for event in self.store.by_type("ENFORCEMENT_DECISION")
            if self.can_read(event, actor)
        ]
        return {
            "business_id": business_id,
            "business_name": business.business_name if business else None,
            "premises": [pid for pid, pr in self.graph.premises.items() if pr.business_id == business_id],
            "shops": [sid for sid, sh in self.graph.shops.items() if sh.business_id == business_id],
            "lots": sorted(my_lots),
            "active_controls": controls,
            "recalls": recalls,
            "corrective_actions": [
                action_id
                for action_id, action in self.sup.corrective.items()
                if action["submission"].get("business_id") == business_id
            ],
            "decisions_against_me": decisions,
        }
