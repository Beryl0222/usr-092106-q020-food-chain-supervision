"""访问控制与敏感信息遮蔽。

访问者模型（actor）：
- ``{"kind": "department", "department": "agriculture|market_regulation|health|public_security|joint_office"}``
- ``{"kind": "business", "business_id": "..."}``     经营主体（含农户、餐饮）
- ``{"kind": "platform", "platform": "..."}``        外卖/电商平台
- ``{"kind": "public"}``                              公众

原则：
- 部门读范围服从法定职责（DEPARTMENT_READ_RIGHTS）；联合食品安全办公室
  可跨域读取以协调处置，但具体行政行为仍由法定部门作出；
- 企业只能读到与自身相关的主体、场所、店铺、批次、订单、召回与处罚，
  其他企业的未定案商业信息整体不可见；
- 公众只能读到“已核实后发布”的风险提示；
- 受益人身份、消费者信息、举报人信息按最小必要遮蔽；
- 遮蔽只发生在读取副本上，事件存储中的原文不变。
"""

from . import catalog
from .errors import AccessDenied

DEPARTMENTS = catalog.DEPARTMENTS

# 部门可读取的聚合类型（联合办公室默认全部）
ALL = catalog.AGGREGATE_TYPES
DEPARTMENT_READ_RIGHTS: dict[str, frozenset[str]] = {
    "agriculture": frozenset({
        catalog.AGG_BUSINESS, catalog.AGG_LOT, catalog.AGG_HANDOFF,
        catalog.AGG_SAMPLE, catalog.AGG_METHOD, catalog.AGG_RECALL,
        catalog.AGG_RULE, catalog.AGG_CASE,
    }),
    "market_regulation": frozenset(ALL),
    "health": frozenset({
        catalog.AGG_BUSINESS, catalog.AGG_PREMISES, catalog.AGG_LOT,
        catalog.AGG_SAMPLE, catalog.AGG_METHOD, catalog.AGG_ORDER,
        catalog.AGG_RECALL, catalog.AGG_COMPLAINT, catalog.AGG_RULE,
    }),
    "public_security": frozenset(ALL),
    "joint_office": frozenset(ALL),
}

# 部门可写入的事件类型
_D = catalog
DEPARTMENT_EMIT: dict[str, frozenset[str]] = {
    "agriculture": frozenset({
        _D.EVT_LOT_HARVESTED, _D.EVT_LOT_TRANSFERRED,
        _D.EVT_SAMPLE_DRAWN, _D.EVT_SAMPLE_TESTED,
    }),
    "market_regulation": frozenset({
        _D.EVT_BUSINESS_REGISTERED, _D.EVT_BENEFICIAL_OWNER_LINKED,
        _D.EVT_LICENSE_ISSUED, _D.EVT_LICENSE_VERIFIED,
        _D.EVT_PREMISES_VERIFIED, _D.EVT_SHOP_BINDING_REVIEWED,
        _D.EVT_LOT_HARVESTED, _D.EVT_LOT_TRANSFERRED, _D.EVT_LOT_RELABELED,
        _D.EVT_LOT_HELD, _D.EVT_LOT_DISPOSITIONED,
        _D.EVT_SAMPLE_DRAWN, _D.EVT_SAMPLE_TESTED, _D.EVT_SAMPLE_RETESTED,
        _D.EVT_SAMPLE_APPEAL_DECIDED,
        _D.EVT_RISK_CONTROLLED, _D.EVT_RISK_CLEARED,
        _D.EVT_RECALL_OPENED, _D.EVT_DOWNSTREAM_NOTIFIED,
        _D.EVT_RECALL_RECONCILED, _D.EVT_RECALL_CLOSED,
        _D.EVT_PUBLIC_ALERT_PUBLISHED, _D.EVT_PUBLIC_ALERT_RETRACTED,
        _D.EVT_METHOD_PUBLISHED, _D.EVT_RULE_PUBLISHED,
        _D.EVT_COMPLAINT_FILED, _D.EVT_CASE_OPENED,
        _D.EVT_ENFORCEMENT_DECIDED, _D.EVT_EVIDENCE_LOCKED,
        _D.EVT_CONTROL_BREACH_RECORDED,
    }),
    "health": frozenset({
        _D.EVT_METHOD_PUBLISHED, _D.EVT_SAMPLE_DRAWN, _D.EVT_SAMPLE_TESTED,
        _D.EVT_SAMPLE_RETESTED, _D.EVT_RISK_CONTROLLED,
        _D.EVT_RISK_CLEARED, _D.EVT_PUBLIC_ALERT_PUBLISHED,
        _D.EVT_PUBLIC_ALERT_RETRACTED,
    }),
    "public_security": frozenset({
        _D.EVT_LOT_HELD, _D.EVT_CASE_OPENED, _D.EVT_EVIDENCE_LOCKED,
        _D.EVT_ENFORCEMENT_DECIDED, _D.EVT_CONTROL_BREACH_RECORDED,
    }),
    "joint_office": frozenset({
        _D.EVT_RISK_CONTROLLED, _D.EVT_RISK_CLEARED, _D.EVT_RECALL_OPENED,
        _D.EVT_DOWNSTREAM_NOTIFIED, _D.EVT_RECALL_RECONCILED,
        _D.EVT_RECALL_CLOSED, _D.EVT_PUBLIC_ALERT_PUBLISHED,
        _D.EVT_PUBLIC_ALERT_RETRACTED, _D.EVT_RULE_PUBLISHED,
        _D.EVT_CONTROL_BREACH_RECORDED,
    }),
}

BUSINESS_EMIT = frozenset({
    _D.EVT_LOT_HARVESTED, _D.EVT_LOT_PACKED, _D.EVT_LOT_SPLIT,
    _D.EVT_LOT_MERGED, _D.EVT_LOT_RELABELED, _D.EVT_LOT_TRANSFERRED,
    _D.EVT_RECALL_REPORTED, _D.EVT_CORRECTION_SUBMITTED,
})
PLATFORM_EMIT = frozenset({
    _D.EVT_SHOP_LISTED, _D.EVT_ORDER_PLACED, _D.EVT_ORDER_FULFILLED,
    _D.EVT_ORDER_DELIVERED, _D.EVT_ORDER_LATE,
    _D.EVT_ORDER_RECALLED_FROM_CONSUMER,
})
PUBLIC_EMIT = frozenset({_D.EVT_COMPLAINT_FILED})

# 载荷中“代表作出行为的部门”的字段，必须与报送部门一致
ACTOR_PAYLOAD_FIELDS = (
    "verified_by", "reviewed_by", "sampled_by", "tested_by", "decided_by",
    "authority", "published_by", "retracted_by", "reconciled_by",
    "closed_by", "opened_by", "custodian",
)

# PII 可见的部门范围
OWNER_PII_DEPARTMENTS = frozenset(
    {"market_regulation", "public_security", "joint_office"})
CONSUMER_PII_DEPARTMENTS = frozenset(
    {"market_regulation", "health", "public_security", "joint_office"})
REPORTER_PII_DEPARTMENTS = frozenset(
    {"market_regulation", "public_security", "joint_office"})

MASK = "******（依法遮蔽）"

SOURCE_KIND = ("enterprise", "platform")  # 非部门报送方标记


# ============================================================ 写入授权

def authorize_emit(event: dict, actor: dict, proj) -> None:
    et = event["event_type"]
    kind = actor.get("kind")

    if kind == "department":
        dept = actor["department"]
        if dept not in catalog.DEPARTMENTS:
            raise AccessDenied(f"未知部门：{dept}")
        if et not in DEPARTMENT_EMIT[dept]:
            raise AccessDenied(f"{dept} 法定职责不覆盖事件 {et}")
        if event.get("source_department") not in (None, dept):
            raise AccessDenied("source_department 必须与报送部门一致")
        p = event.get("payload", {})
        for f in ACTOR_PAYLOAD_FIELDS:
            if f in p and p[f] in catalog.DEPARTMENTS and p[f] != dept:
                raise AccessDenied(f"载荷字段 {f}={p[f]} 与报送部门 {dept} 不一致")
        event["source_department"] = dept
        return

    if kind == "business":
        if et not in BUSINESS_EMIT:
            raise AccessDenied(f"企业端不能报送事件 {et}")
        _ensure_business_owns(event, actor["business_id"], proj)
        event["source_department"] = event.get("source_department", "enterprise")
        return

    if kind == "platform":
        if et not in PLATFORM_EMIT:
            raise AccessDenied(f"平台端不能报送事件 {et}")
        _ensure_platform_owns(event, actor["platform"], proj)
        event["source_department"] = event.get("source_department", "platform")
        return

    if kind == "public":
        if et not in PUBLIC_EMIT:
            raise AccessDenied("匿名公众只能提交投诉举报")
        return

    raise AccessDenied(f"未知访问者类型：{kind}")


def _ensure_business_owns(event: dict, bid: str, proj) -> None:
    et = event["event_type"]
    p = event.get("payload", {})
    if et == _D.EVT_LOT_HARVESTED:
        if p["business_id"] != bid:
            raise AccessDenied("只能为自己的主体建档批次")
    elif et in (_D.EVT_LOT_PACKED, _D.EVT_LOT_SPLIT, _D.EVT_LOT_RELABELED):
        lot = proj.lots.get(event["aggregate_id"])
        if lot is None or (lot["owner_business"] != bid
                           and bid not in lot["holdings"]):
            raise AccessDenied("只能操作本主体持有的批次")
        if et == _D.EVT_LOT_RELABELED and p.get("at_party") != bid:
            raise AccessDenied("换包登记主体必须与报送主体一致")
    elif et == _D.EVT_LOT_MERGED:
        if p.get("business_id", bid) != bid:
            raise AccessDenied("混批必须挂在本主体名下")
    elif et == _D.EVT_LOT_TRANSFERRED:
        if p["from_party"] != bid:
            raise AccessDenied("只能登记本主体交出的冷链交接")
    elif et == _D.EVT_RECALL_REPORTED:
        if p["business_id"] != bid:
            raise AccessDenied("只能上报本主体的召回数量")
    elif et == _D.EVT_CORRECTION_SUBMITTED:
        if p["business_id"] != bid:
            raise AccessDenied("只能为自己的案件提交纠正")


def _ensure_platform_owns(event: dict, platform: str, proj) -> None:
    p = event.get("payload", {})
    if event["event_type"] == _D.EVT_SHOP_LISTED:
        if p["platform"] != platform:
            raise AccessDenied("只能在本平台开店")
        return
    order = proj.orders.get(event["aggregate_id"])
    if order is not None:
        shop = proj.shops.get(order["shop_id"])
        if shop is None or shop["platform"] != platform:
            raise AccessDenied("只能更新本平台订单")
        return
    # 下单：按店铺归属校验
    shop = proj.shops.get(p.get("shop_id", ""))
    if shop is None or shop["platform"] != platform:
        raise AccessDenied("只能为本平台店铺创建订单")


# ============================================================ 读取范围

def business_scope_ids(bid: str, proj) -> set[str]:
    """企业可见的批次集合：自有、当前或历史持有/经手、已销售、
    被召回通知，并沿谱系上下游各延伸一阶关联。

    批次销毁或移交后责任链不消失，因此判断依据包含 custody 历史，
    而不只看当前持仓。
    """
    ids = set()
    for lid, lot in proj.lots.items():
        touched = (lot["owner_business"] == bid or bid in lot["holdings"]
                   or any(c.get("party") == bid or c.get("from_party") == bid
                          for c in lot["custody"])
                   or any(s["business_id"] == bid
                          for s in proj.lot_sales.get(lid, [])))
        if touched:
            ids.add(lid)
            for parent, *_ in proj.parents.get(lid, []):
                ids.add(parent)
            for child, *_ in proj.children.get(lid, []):
                ids.add(child)
    for recall in proj.recalls.values():
        if bid in {n["party_id"] for n in recall["notifications"]} \
                or any(r["business_id"] == bid for r in recall["reports"]):
            ids.add(recall["root_lot_id"])
    return ids


def can_read_aggregate(actor: dict, agg_type: str, agg_id: str, proj) -> bool:
    kind = actor.get("kind")
    if kind == "public":
        return False  # 公众只走 /alerts，不读取任何聚合明细
    if kind == "department":
        return agg_type in DEPARTMENT_READ_RIGHTS[actor["department"]]
    if kind == "platform":
        if agg_type == _D.AGG_SHOP:
            shop = proj.shops.get(agg_id)
            return bool(shop and shop["platform"] == actor["platform"])
        if agg_type == _D.AGG_ORDER:
            order = proj.orders.get(agg_id)
            if not order:
                return False
            shop = proj.shops.get(order["shop_id"])
            return bool(shop and shop["platform"] == actor["platform"])
        return False
    if kind == "business":
        bid = actor["business_id"]
        if agg_type == _D.AGG_BUSINESS:
            return agg_id == bid
        if agg_type == _D.AGG_PREMISES:
            pre = proj.premises.get(agg_id)
            return bool(pre and pre.get("business_id") == bid)
        if agg_type == _D.AGG_SHOP:
            shop = proj.shops.get(agg_id)
            return bool(shop and shop["business_id"] == bid)
        if agg_type == _D.AGG_LOT:
            return agg_id in business_scope_ids(bid, proj)
        if agg_type == _D.AGG_ORDER:
            order = proj.orders.get(agg_id)
            return bool(order and order["business_id"] == bid)
        if agg_type == _D.AGG_SAMPLE:
            sample = proj.samples.get(agg_id)
            return bool(sample and sample["lot_id"]
                        in business_scope_ids(bid, proj))
        if agg_type == _D.AGG_RECALL:
            recall = proj.recalls.get(agg_id)
            if not recall:
                return False
            if bid in {n["party_id"] for n in recall["notifications"]}:
                return True
            return any(r["business_id"] == bid for r in recall["reports"])
        if agg_type == _D.AGG_CASE:
            case = proj.cases.get(agg_id)
            return bool(case and case.get("subject_business_id") == bid)
        if agg_type == _D.AGG_COMPLAINT:
            return False
        return False
    return False


# ============================================================ 字段遮蔽

def _mask(payload: dict, keys: tuple[str, ...]) -> dict:
    out = dict(payload)
    for k in keys:
        if k in out:
            out[k] = MASK
    return out


def redact_event(event: dict, actor: dict, proj) -> dict | None:
    """返回可向该访问者展示的事件副本；无权读取返回 None。"""
    et = event["event_type"]
    agg_type = event["aggregate_type"]
    agg_id = event["aggregate_id"]
    kind = actor.get("kind")

    if kind == "public":
        return dict(event) if et in (_D.EVT_PUBLIC_ALERT_PUBLISHED,
                                     _D.EVT_PUBLIC_ALERT_RETRACTED) else None
    if not can_read_aggregate(actor, agg_type, agg_id, proj):
        return None

    out = dict(event)
    p = event.get("payload", {})
    out["payload"] = dict(p)

    if kind == "department":
        dept = actor["department"]
        if et == _D.EVT_BENEFICIAL_OWNER_LINKED and dept not in OWNER_PII_DEPARTMENTS:
            out["payload"] = _mask(p, ("owner_name", "id_ref", "id_type"))
        if et in (_D.EVT_ORDER_PLACED, _D.EVT_ORDER_DELIVERED,
                  _D.EVT_ORDER_RECALLED_FROM_CONSUMER) \
                and dept not in CONSUMER_PII_DEPARTMENTS:
            out["payload"] = _mask(out["payload"], ("consumer_ref",))
        if et == _D.EVT_COMPLAINT_FILED and dept not in REPORTER_PII_DEPARTMENTS:
            out["payload"] = _mask(out["payload"],
                                   ("reporter_ref", "reporter_contact"))
        return out

    if kind == "business":
        if et == _D.EVT_BENEFICIAL_OWNER_LINKED:
            return None  # 受益人信息不对企业开放
        if et == _D.EVT_COMPLAINT_FILED:
            out["payload"] = _mask(out["payload"],
                                   ("reporter_ref", "reporter_contact"))
        # 其他主体的未定案商业信息：控制/检测类事件仅在涉及自己批次时
        # 才通过聚合范围过滤，此处不再额外处理。
        if et in (_D.EVT_ORDER_PLACED, _D.EVT_ORDER_DELIVERED,
                  _D.EVT_ORDER_RECALLED_FROM_CONSUMER):
            out["payload"] = _mask(out["payload"], ("consumer_ref",))
        return out

    if kind == "platform":
        # 平台需要消费者信息完成配送，但样本、控制等监管内容不开放
        if agg_type not in (_D.AGG_SHOP, _D.AGG_ORDER):
            return None
        return out

    return None


def mask_party(party: str, viewer_bid: str) -> str:
    """企业追溯视图中遮蔽其他主体标识。"""
    return party if party == viewer_bid else "其他主体（已遮蔽）"
