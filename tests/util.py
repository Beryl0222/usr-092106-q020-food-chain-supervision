"""测试辅助：在内存服务上按顺序构造事件。"""

from datetime import datetime, timedelta, timezone

from src.app import ChainSupervisionService

_TZ = timezone(timedelta(hours=8))
_BASE = datetime(2026, 9, 20, 8, 0, tzinfo=_TZ)

MR = {"kind": "department", "department": "market_regulation"}
AG = {"kind": "department", "department": "agriculture"}
HC = {"kind": "department", "department": "health"}
PS = {"kind": "department", "department": "public_security"}
JO = {"kind": "department", "department": "joint_office"}
PUBLIC = {"kind": "public"}


def biz(bid: str) -> dict:
    return {"kind": "business", "business_id": bid}


def plat(name: str) -> dict:
    return {"kind": "platform", "platform": name}


class Builder:
    def __init__(self, svc: ChainSupervisionService | None = None):
        # 注意：服务实现了 __len__，空服务为假值，不能用 `svc or ...`
        self.svc = svc if svc is not None else ChainSupervisionService()
        self.n = 0

    def emit(self, etype: str, agg_type: str, agg_id: str, payload: dict,
             actor: dict, eid: str | None = None) -> dict:
        self.n += 1
        event = {
            "event_id": eid or f"T-{self.n:04d}",
            "event_type": etype,
            "aggregate_type": agg_type,
            "aggregate_id": agg_id,
            "occurred_at": (_BASE + timedelta(minutes=self.n)).isoformat(),
            "version": self.svc.next_version(agg_type, agg_id),
            "summary": etype,
            "payload": payload,
        }
        return self.svc.emit(event, actor)

    # 常用场景积木 ----------------------------------------------------

    def rule(self, code: str = "R1", version: str = "v1") -> None:
        self.emit("RULE_PUBLISHED", "risk_rule", code,
                  {"rule_code": code, "version": version,
                   "content_ref": f"rules/{code}.md",
                   "effective_at": "2026-01-01T00:00:00+08:00"}, MR)

    def method(self, code: str = "M1") -> None:
        self.emit("METHOD_PUBLISHED", "test_method", code,
                  {"method_code": code, "version": "2026.1",
                   "title": "快检法"}, HC)

    def registered_business(self, bid: str, name: str | None = None) -> None:
        self.emit("BUSINESS_REGISTERED", "food_business", bid,
                  {"name": name or bid, "registered_by": "market_regulation"}, MR)

    def harvest(self, bid: str, lot: str, qty: float, product: str = "菠菜") -> None:
        self.emit("LOT_HARVESTED", "food_lot", lot,
                  {"product_name": product, "quantity": qty, "unit": "kg",
                   "business_id": bid}, biz(bid))
