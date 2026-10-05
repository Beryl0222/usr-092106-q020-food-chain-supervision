"""校验领域事件信封与目录载荷。

只做静态结构校验；业务规则（权限、追溯闭合、结论前提）在各领域模块中。
"""

from datetime import datetime

from . import catalog

REQUIRED = (
    "event_id",
    "event_type",
    "aggregate_type",
    "aggregate_id",
    "occurred_at",
    "version",
    "summary",
)


def validate_event(record: dict) -> list[str]:
    """返回错误信息列表；空列表表示通过。保持对早期单条样例的兼容。"""
    errors = [f"缺少字段：{name}" for name in REQUIRED if name not in record]

    if "version" in record and (
        not isinstance(record["version"], int)
        or isinstance(record["version"], bool)
        or record["version"] < 1
    ):
        errors.append("version 必须是正整数")

    if "occurred_at" in record and isinstance(record["occurred_at"], str):
        try:
            datetime.fromisoformat(record["occurred_at"])
        except ValueError:
            errors.append("occurred_at 必须是 ISO 8601 日期时间")

    event_type = record.get("event_type")
    if event_type is not None and event_type not in catalog.EVENT_TYPES:
        errors.append(f"未知事件类型：{event_type}")
    elif event_type is not None:
        expected_agg = catalog.aggregate_of(event_type)
        if record.get("aggregate_type") != expected_agg:
            errors.append(
                f"事件 {event_type} 的聚合类型必须是 {expected_agg}"
            )
        missing = [
            key
            for key in catalog.required_payload(event_type)
            if key not in record.get("payload", {})
        ]
        if missing:
            errors.append(f"payload 缺少字段：{', '.join(missing)}")

    source = record.get("source_department")
    if source is not None and source not in catalog.DEPARTMENTS:
        errors.append(f"未知来源部门：{source}")

    return errors
