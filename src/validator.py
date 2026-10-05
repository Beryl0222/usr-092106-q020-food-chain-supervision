"""领域事件校验：信封结构 + 各事件载荷的必填字段与受控词表。

只做“写入前形态校验”，不做业务状态判定（例如快检阳性是否已通知下游、
数量是否守恒）——那是投影与策略层的职责。
"""

from datetime import datetime

from . import catalog

REQUIRED_ENVELOPE = (
    "event_id", "event_type", "aggregate_type", "aggregate_id",
    "occurred_at", "version", "summary",
)

# event_type -> (聚合类型, 必填载荷字段, 载荷枚举字段)
# 载荷中引用的其他聚合是否存在，由应用层在写入时用投影校验。
PAYLOAD_RULES: dict[str, tuple[str, tuple[str, ...], dict[str, tuple[str, ...]]]] = {
    catalog.EVT_BUSINESS_REGISTERED: (
        catalog.AGG_BUSINESS, ("name",), {"registered_by": catalog.DEPARTMENTS}),
    catalog.EVT_BENEFICIAL_OWNER_LINKED: (
        catalog.AGG_BUSINESS, ("owner_name",), {}),
    catalog.EVT_LICENSE_ISSUED: (
        catalog.AGG_BUSINESS, ("license_no", "premises_id", "scope"), {}),
    catalog.EVT_LICENSE_VERIFIED: (
        catalog.AGG_BUSINESS, ("license_no", "result", "verified_by"),
        {"result": ("pass", "fail"), "verified_by": catalog.DEPARTMENTS}),
    catalog.EVT_PREMISES_VERIFIED: (
        catalog.AGG_PREMISES, ("result", "verified_by"),
        {"result": ("pass", "address_mismatch", "no_physical_store", "closed"),
         "verified_by": catalog.DEPARTMENTS}),
    catalog.EVT_SHOP_LISTED: (
        catalog.AGG_SHOP, ("platform", "business_id", "license_no"), {}),
    catalog.EVT_SHOP_BINDING_REVIEWED: (
        catalog.AGG_SHOP, ("result", "reviewed_by"),
        {"result": catalog.SHOP_REVIEW_RESULTS, "reviewed_by": catalog.DEPARTMENTS}),

    catalog.EVT_LOT_HARVESTED: (
        catalog.AGG_LOT, ("product_name", "quantity", "unit", "business_id"), {}),
    catalog.EVT_LOT_PACKED: (
        catalog.AGG_LOT, ("outputs",), {}),
    catalog.EVT_LOT_RELABELED: (
        catalog.AGG_LOT, ("old_label", "new_label", "at_party"), {}),
    catalog.EVT_LOT_TRANSFERRED: (
        catalog.AGG_LOT, ("from_party", "to_party"), {}),
    catalog.EVT_LOT_MERGED: (
        catalog.AGG_LOT, ("inputs",), {}),
    catalog.EVT_LOT_SPLIT: (
        catalog.AGG_LOT, ("outputs",), {}),
    catalog.EVT_LOT_HELD: (
        catalog.AGG_LOT, ("custodian", "reason"), {}),
    catalog.EVT_LOT_DISPOSITIONED: (
        catalog.AGG_LOT, ("disposition", "quantity"),
        {"disposition": ("destroyed", "returned", "released")}),

    catalog.EVT_ORDER_PLACED: (
        catalog.AGG_ORDER, ("shop_id", "consumer_ref", "items"), {}),
    catalog.EVT_ORDER_FULFILLED: (
        catalog.AGG_ORDER, ("lots",), {}),
    catalog.EVT_ORDER_DELIVERED: (
        catalog.AGG_ORDER, ("delivered_at",), {}),
    catalog.EVT_ORDER_LATE: (
        catalog.AGG_ORDER, ("minutes_late",), {}),
    catalog.EVT_ORDER_RECALLED_FROM_CONSUMER: (
        catalog.AGG_ORDER, ("recall_id", "quantity"), {}),

    catalog.EVT_METHOD_PUBLISHED: (
        catalog.AGG_METHOD, ("method_code", "title", "version"), {}),
    catalog.EVT_SAMPLE_DRAWN: (
        catalog.AGG_SAMPLE, ("lot_id", "sampled_by"),
        {"sampled_by": catalog.DEPARTMENTS}),
    catalog.EVT_SAMPLE_TESTED: (
        catalog.AGG_SAMPLE, ("kind", "result", "tested_by", "method_code"),
        {"kind": catalog.TEST_KINDS, "result": catalog.TEST_RESULTS,
         "tested_by": catalog.DEPARTMENTS}),
    catalog.EVT_SAMPLE_RETESTED: (
        catalog.AGG_SAMPLE, ("result", "tested_by", "method_code"),
        {"result": catalog.TEST_RESULTS, "tested_by": catalog.DEPARTMENTS}),
    catalog.EVT_SAMPLE_APPEAL_DECIDED: (
        catalog.AGG_SAMPLE, ("decision", "decided_by"),
        {"decision": ("upheld", "overturned", "partial"),
         "decided_by": catalog.DEPARTMENTS}),

    catalog.EVT_RISK_CONTROLLED: (
        catalog.AGG_SAMPLE,
        ("trigger_sample_id", "decisions", "authority", "rule_code", "rule_version"),
        {"authority": catalog.DEPARTMENTS}),
    catalog.EVT_RISK_CLEARED: (
        catalog.AGG_SAMPLE, ("basis", "decided_by"),
        {"basis": ("retest_negative", "appeal_overturned"),
         "decided_by": catalog.DEPARTMENTS}),

    catalog.EVT_RECALL_OPENED: (
        catalog.AGG_RECALL, ("control_event_id", "root_lot_id", "authority"),
        {"authority": catalog.DEPARTMENTS}),
    catalog.EVT_DOWNSTREAM_NOTIFIED: (
        catalog.AGG_RECALL, ("party_id", "lot_ids", "channel"), {}),
    catalog.EVT_RECALL_REPORTED: (
        catalog.AGG_RECALL, ("business_id", "quantities"), {}),
    catalog.EVT_RECALL_RECONCILED: (
        catalog.AGG_RECALL, ("reconciled_by",),
        {"reconciled_by": catalog.DEPARTMENTS}),
    catalog.EVT_RECALL_CLOSED: (
        catalog.AGG_RECALL, ("closed_by",), {"closed_by": catalog.DEPARTMENTS}),
    catalog.EVT_PUBLIC_ALERT_PUBLISHED: (
        catalog.AGG_RECALL, ("title", "content", "lot_ids", "published_by"),
        {"published_by": catalog.DEPARTMENTS}),
    catalog.EVT_PUBLIC_ALERT_RETRACTED: (
        catalog.AGG_RECALL, ("reason", "retracted_by"),
        {"retracted_by": catalog.DEPARTMENTS}),

    catalog.EVT_COMPLAINT_FILED: (
        catalog.AGG_COMPLAINT, ("subject_type", "subject_id", "content"), {}),
    catalog.EVT_CASE_OPENED: (
        catalog.AGG_CASE, ("basis_event_ids", "opened_by"),
        {"opened_by": catalog.DEPARTMENTS}),
    catalog.EVT_ENFORCEMENT_DECIDED: (
        catalog.AGG_CASE, ("penalties", "evidence_event_ids", "rule_code",
                           "rule_version", "decided_by"),
        {"decided_by": catalog.DEPARTMENTS}),
    catalog.EVT_CORRECTION_SUBMITTED: (
        catalog.AGG_CASE, ("business_id", "description"), {}),
    catalog.EVT_EVIDENCE_LOCKED: (
        catalog.AGG_CASE, ("linked_event_id", "custodian", "storage_ref",
                           "hash_alg", "digest"), {}),
    catalog.EVT_RULE_PUBLISHED: (
        catalog.AGG_RULE, ("rule_code", "version", "content_ref", "effective_at"), {}),
    catalog.EVT_CONTROL_BREACH_RECORDED: (
        catalog.AGG_RECALL, ("control_event_id", "breach_type", "ref_event_id"),
        {"breach_type": ("movement", "sale", "delivery", "relabel")}),
}


def parse_ts(value: str) -> datetime:
    """解析事件时间，允许 ``Z`` 结尾；失败抛 ValueError。"""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def validate_event(record: dict) -> list[str]:
    """返回错误信息列表；空列表表示通过。保持对旧调用方的函数签名。"""
    errors: list[str] = []

    missing = [f"缺少字段：{name}" for name in REQUIRED_ENVELOPE if name not in record]
    errors.extend(missing)
    if missing:
        return errors

    if not isinstance(record["event_id"], str) or not record["event_id"].strip():
        errors.append("event_id 必须是非空字符串")
    if record["event_type"] not in catalog.EVENT_TYPES:
        errors.append(f"未知 event_type：{record['event_type']}")
    if record["aggregate_type"] not in catalog.AGGREGATE_TYPES:
        errors.append(f"未知 aggregate_type：{record['aggregate_type']}")
    if not isinstance(record["aggregate_id"], str) or not record["aggregate_id"].strip():
        errors.append("aggregate_id 必须是非空字符串")
    if not isinstance(record["version"], int) or isinstance(record["version"], bool) \
            or record["version"] < 1:
        errors.append("version 必须是正整数")
    if not isinstance(record.get("summary"), str) or not record["summary"].strip():
        errors.append("summary 必须是非空字符串")
    try:
        parse_ts(record["occurred_at"])
    except (ValueError, AttributeError):
        errors.append("occurred_at 必须是 ISO 8601 日期时间")

    if record.get("source_department") is not None \
            and record["source_department"] not in catalog.DEPARTMENTS + ("enterprise", "platform"):
        errors.append("source_department 取值非法")

    rule = PAYLOAD_RULES.get(record["event_type"])
    if rule is None:
        return errors

    expected_agg, required_payload, enum_fields = rule
    if record["aggregate_type"] != expected_agg:
        errors.append(
            f"{record['event_type']} 的 aggregate_type 必须是 {expected_agg}")

    payload = record.get("payload")
    if not isinstance(payload, dict):
        errors.append("payload 必须是对象")
        return errors

    for key in required_payload:
        if key not in payload:
            errors.append(f"{record['event_type']} 缺少载荷字段：{key}")

    for key, allowed in enum_fields.items():
        if key in payload and payload[key] not in allowed:
            errors.append(
                f"{record['event_type']}.{key} 取值非法：{payload[key]}，"
                f"允许 {list(allowed)}")

    if record["event_type"] == catalog.EVT_RISK_CONTROLLED:
        decisions = payload.get("decisions", [])
        if not isinstance(decisions, list) or not decisions:
            errors.append("RISK_CONTROLLED.decisions 必须是非空数组")
        elif bad := [d for d in decisions if d not in catalog.CONTROL_DECISIONS]:
            errors.append(f"控制措施非法：{bad}")

    return errors
