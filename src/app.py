"""应用服务：把访问控制、事件仓库、投影和应急策略组成事务边界。

所有写操作严格按顺序执行：
  授权（access）→ 投影业务校验 → 仓库追加（不可变）→ 投影更新。
投影校验在仓库写入**之前**完成，因此非法事件不会留下任何痕迹。
"""

import threading
from pathlib import Path

from . import access, catalog
from .errors import AccessDenied, ConflictError, DomainError, RiskLotFrozenError, ValidationError
from .policy import affected_scope, recall_balance, recommended_decisions
from .projection import Projection
from .store import EventStore
from .validator import validate_event


class ChainSupervisionService:
    def __init__(self, store_path: str | Path | None = None):
        self.store = EventStore(store_path)
        self.proj = Projection()
        for e in self.store.stream():
            self.proj.apply(e)
        self._lock = threading.RLock()

    # ------------------------------------------------------------ 写入

    def emit(self, event: dict, actor: dict) -> dict:
        with self._lock:
            event = dict(event)
            # 先形态校验，再授权，再业务不变量；任何一步拒绝都不落库
            errors = validate_event(event)
            if errors:
                raise ValidationError(errors)
            access.authorize_emit(event, actor, self.proj)
            existing = self.store.get(event.get("event_id", ""))
            if existing is not None and self._same_fact(existing, event):
                # 跨部门重复上报按仓库事件标识幂等去重：各部门转发同一事实时
                # 仅 source_department（系统加注的报送方）允许不同。
                return existing
            self.proj.validate(event)
            stored = self.store.append(event)
            self.proj.apply(stored)
            return stored

    @staticmethod
    def _same_fact(existing: dict, incoming: dict) -> bool:
        sys_stamped = {"source_department", "seq"}
        keys = set(existing) | set(incoming)
        for key in keys:
            if key in sys_stamped:
                continue
            if existing.get(key) != incoming.get(key):
                return False
        # 报送方不一致只有在双方都是部门转发时才允许；企业/平台报送
        # 与部门报送不得相互冒充。
        a, b = existing.get("source_department"), incoming.get("source_department")
        if a is not None and b is not None:
            from . import catalog as _c
            if not (a in _c.DEPARTMENTS and b in _c.DEPARTMENTS) and a != b:
                return False
        return True

    def next_version(self, aggregate_type: str, aggregate_id: str) -> int:
        return self.store.next_version(aggregate_type, aggregate_id)

    # ------------------------------------------------------------ 主体核验

    def shop_dossier(self, shop_id: str, actor: dict | None = None) -> dict:
        """电子证照与实际经营地点**分别**给出核验结论。

        典型风险画像：电子证照核验通过、平台开店通过，但场所实地核验为
        ``no_physical_store`` —— 即“外卖平台核验通过却无线下门店”。
        """
        shop = self.proj.shops.get(shop_id)
        if shop is None:
            raise ConflictError(f"店铺不存在：{shop_id}")
        biz = self.proj.businesses[shop["business_id"]]
        lic = biz["licenses"].get(shop["license_no"])
        premises = self.proj.premises.get(lic["premises_id"]) if lic else None
        e_license = lic["verified"] if lic else None
        physical = (premises["verifications"][-1]
                    if premises and premises["verifications"] else None)
        review = shop["reviews"][-1] if shop["reviews"] else None

        license_ok = e_license is not None and e_license["result"] == "pass"
        premises_ok = physical is not None and physical["result"] == "pass"
        ghost = (license_ok and not premises_ok)
        binding = review["result"] if review else "unreviewed"

        return {
            "shop_id": shop_id, "platform": shop["platform"],
            "business_id": biz["id"], "business_name": biz["name"],
            "license_no": shop["license_no"],
            "electronic_license": e_license,
            "electronic_license_ok": license_ok,
            "physical_premises": physical,
            "physical_premises_ok": premises_ok,
            "binding_review_latest": review,
            "ghost_kitchen_suspected": ghost,
            "compliant": license_ok and premises_ok and binding == "pass",
        }

    # ------------------------------------------------------------ 追溯 / 应急

    def trace(self, lot_id: str, actor: dict) -> dict:
        if not access.can_read_aggregate(actor, catalog.AGG_LOT, lot_id, self.proj):
            raise AccessDenied(f"无权追溯批次 {lot_id}")
        view = self.proj.trace(lot_id)
        if actor.get("kind") == "business":
            view = self._trace_for_business(view, actor["business_id"])
        return view

    def _trace_for_business(self, view: dict, bid: str) -> dict:
        m = access.mask_party
        view = dict(view)
        view["holdings"] = {m(p, bid): q for p, q in view["holdings"].items()}
        view["custody_chain"] = [
            {**c, "party": m(c["party"], bid),
             **({"from_party": m(c["from_party"], bid)} if "from_party" in c else {})}
            for c in view["custody_chain"]
        ]
        view["orders"] = [
            (o if o["business_id"] == bid
             else {**o, "business_id": "其他主体（已遮蔽）"})
            for o in view["orders"]
        ]
        return view

    def emergency_scope(self, sample_id: str, actor: dict) -> dict:
        if actor.get("kind") not in ("department",):
            raise AccessDenied("应急范围仅供法定部门查询")
        if not access.can_read_aggregate(actor, catalog.AGG_SAMPLE, sample_id,
                                         self.proj):
            raise AccessDenied(f"无权访问样本 {sample_id}")
        scope = affected_scope(self.proj, sample_id)
        scope["recommended_decisions"] = recommended_decisions(scope)
        return scope

    def recall_status(self, recall_id: str, actor: dict) -> dict:
        if not access.can_read_aggregate(actor, catalog.AGG_RECALL, recall_id,
                                         self.proj):
            raise AccessDenied(f"无权访问召回案件 {recall_id}")
        balance = recall_balance(self.proj, recall_id)
        if actor.get("kind") == "business":
            bid = actor["business_id"]
            balance = dict(balance)
            balance["breaches"] = []  # 违规线索不对涉事企业开放明细
        return balance

    def penalty_audit(self, case_id: str, actor: dict) -> dict:
        if actor.get("kind") != "department":
            raise AccessDenied("处罚反查仅供部门使用")
        if not access.can_read_aggregate(actor, catalog.AGG_CASE, case_id,
                                         self.proj):
            raise AccessDenied(f"无权访问案件 {case_id}")
        audit = self.proj.penalty_audit(case_id)
        # 反查链补上被证事件每次交接（批次 custody）
        for item in audit["evidence_chain"]:
            linked = self.store.get(item["event_id"])
            item["linked_event"] = linked
            if linked and linked["aggregate_type"] == catalog.AGG_LOT:
                item["custody_chain"] = list(
                    self.proj.lots[linked["aggregate_id"]]["custody"])
        return audit

    # ------------------------------------------------------------ 列表 / 公众

    def public_alerts(self) -> list[dict]:
        return [
            {"recall_id": rid, **r["alert"]}
            for rid, r in self.proj.recalls.items()
            if r["alert"] and r["alert"]["status"] == "published"
        ]

    def events_for(self, actor: dict, aggregate_type: str | None = None,
                   aggregate_id: str | None = None) -> list[dict]:
        out = []
        for e in self.store.stream(aggregate_type, aggregate_id):
            shown = access.redact_event(e, actor, self.proj)
            if shown is not None:
                out.append(shown)
        return out

    # 便于演示/测试：事件总数
    def __len__(self) -> int:
        return len(self.store)
