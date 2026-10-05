"""主体图谱与批次双向追溯投影。

从不可变事件流折叠出当前状态：
- 经营主体、受益人、许可场所、平台店铺，证照核验与实地核验分别保存；
- 批次谱系：拆分、混批、换包、加工分装都保留来源边，支持向上溯源与向下追踪；
- 在途/在售/已售范围，供快检阳性时立即圈定影响面。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .catalog import AGGREGATES  # noqa: F401  (保留以供外部引用)


@dataclass
class Business:
    business_id: str
    business_name: str
    business_type: str
    beneficial_owners: list[dict] = field(default_factory=list)


@dataclass
class Premises:
    premises_id: str
    business_id: str
    address: str
    license_no: str
    scope: str
    # 电子证照存在（licensed）与实地核验结果相互独立
    verification: dict | None = None


@dataclass
class PlatformShop:
    shop_id: str
    platform: str
    shop_name: str
    business_id: str
    claimed_premises_id: str | None
    platform_result: str | None = None  # 平台自己的核验，不代表实地存在


@dataclass
class LotState:
    lot_id: str
    product_name: str
    quantity: float
    unit: str
    parents: list[str] = field(default_factory=list)
    edge_kinds: dict[str, str] = field(default_factory=dict)  # parent -> 边类型
    producer_business_id: str | None = None
    holder_id: str | None = None
    in_transit: bool = False
    transit_from: str | None = None
    transit_to: str | None = None
    # 当前在本批次包装下的余量；拆分/混批/换包/加工提料会扣减，
    # 使召回期望量不会把母批与子批重复计算
    balance: float | None = None


class FoodChainGraph:
    def __init__(self) -> None:
        self.businesses: dict[str, Business] = {}
        self.premises: dict[str, Premises] = {}
        self.shops: dict[str, PlatformShop] = {}
        self.lots: dict[str, LotState] = {}
        self._children: dict[str, list[str]] = {}
        # lot_id -> 订单（按时间追加，封存后补录的迟到订单也只追加）
        self.orders_by_lot: dict[str, list[dict]] = {}
        # lot_id -> 每次交接/冷链交接记录（保管链，只追加）
        self.handovers_by_lot: dict[str, list[dict]] = {}

    # —— 折叠 ——

    def apply(self, event: dict) -> None:
        etype = event["event_type"]
        agg_id = event["aggregate_id"]
        p = event.get("payload", {})

        if etype == "SUBJECT_REGISTERED":
            self.businesses[agg_id] = Business(
                business_id=agg_id,
                business_name=p["business_name"],
                business_type=p["business_type"],
            )
        elif etype == "BENEFICIAL_OWNER_DECLARED":
            business = self.businesses.get(agg_id)
            if business:
                business.beneficial_owners.append(dict(p))
        elif etype == "PREMISES_LICENSED":
            self.premises[agg_id] = Premises(
                premises_id=agg_id,
                business_id=p["business_id"],
                address=p["address"],
                license_no=p["license_no"],
                scope=p["scope"],
            )
        elif etype == "PREMISES_VERIFIED":
            premises = self.premises.get(agg_id)
            if premises:
                premises.verification = {
                    "result": p["result"],
                    "verifier_department": p["verifier_department"],
                    "checked_at": p["checked_at"],
                }
        elif etype == "PLATFORM_SHOP_LISTED":
            self.shops[agg_id] = PlatformShop(
                shop_id=agg_id,
                platform=p["platform"],
                shop_name=p["shop_name"],
                business_id=p["business_id"],
                claimed_premises_id=p.get("claimed_premises_id"),
            )
        elif etype == "DELIVERY_PLATFORM_VERIFIED":
            shop = self.shops.get(agg_id)
            if shop:
                shop.platform_result = p["result"]
        elif etype in (
            "LOT_REGISTERED",
            "LOT_SPLIT",
            "LOT_MERGED",
            "LOT_RELABELED",
            "PROCESSING_RECORDED",
        ):
            self._apply_lot_derived(etype, agg_id, p)
        elif etype == "LOT_TRANSFERRED":
            self.handovers_by_lot.setdefault(agg_id, []).append(
                {"kind": "transfer", "event_id": event["event_id"], "at": event["occurred_at"], **p}
            )
            self._apply_transfer(agg_id, p)
        elif etype == "COLD_CHAIN_HANDOVER":
            self.handovers_by_lot.setdefault(agg_id, []).append(
                {"kind": "cold_chain", "event_id": event["event_id"], "at": event["occurred_at"], **p}
            )
        elif etype == "ORDER_RECORDED":
            self.orders_by_lot.setdefault(p["lot_id"], []).append(
                {
                    "order_id": agg_id,
                    "recorded_at": event.get("recorded_at", event["occurred_at"]),
                    **p,
                }
            )

    def _apply_lot_derived(self, etype: str, lot_id: str, p: dict) -> None:
        if etype == "LOT_REGISTERED":
            parents, edge = [], None
            producer = p["producer_business_id"]
            holder = producer
        elif etype == "LOT_SPLIT":
            parents, edge = [p["parent_lot_id"]], "split"
            parent = self.lots.get(p["parent_lot_id"])
            producer = parent.producer_business_id if parent else None
            holder = parent.holder_id if parent else None
        elif etype == "LOT_MERGED":
            parents, edge = list(p["source_lot_ids"]), "merge"
            producer = None
            holder = self._common_holder(parents)
        elif etype == "LOT_RELABELED":
            parents, edge = list(p["source_lot_ids"]), "relabel"
            holder = p["operator_business_id"]
            first = self.lots.get(parents[0]) if parents else None
            producer = first.producer_business_id if first else None
        else:  # PROCESSING_RECORDED
            parents, edge = list(p["input_lot_ids"]), "process"
            holder = p["processor_business_id"]
            first = self.lots.get(parents[0]) if parents else None
            producer = first.producer_business_id if first else None

        lot = LotState(
            lot_id=lot_id,
            product_name=p.get("product_name")
            or (self.lots[parents[0]].product_name if parents and parents[0] in self.lots else ""),
            quantity=float(p["quantity"]),
            unit=p["unit"],
            parents=parents,
            producer_business_id=producer,
            holder_id=holder,
            balance=float(p["quantity"]),
        )
        for parent in parents:
            lot.edge_kinds[parent] = edge
            self._children.setdefault(parent, []).append(lot_id)
        self.lots[lot_id] = lot
        self._consume_parents(etype, parents, p)

    def _consume_parents(self, etype: str, parents: list[str], p: dict) -> None:
        """提料/换包后扣减母批余量；余额可为零（批次实体仍保留以维谱系）。"""
        if etype == "LOT_SPLIT":
            self._consume(parents[0], float(p["quantity"]))
        elif etype == "LOT_MERGED":
            quantities = p.get("quantities") or []
            for parent, qty in zip(parents, quantities):
                self._consume(parent, float(qty))
        elif etype == "LOT_RELABELED":
            for parent in parents:  # 整批换包
                source = self.lots.get(parent)
                if source and source.balance:
                    self._consume(parent, source.balance)
        elif etype == "PROCESSING_RECORDED":
            quantities = p.get("input_quantities")
            if quantities:
                for parent, qty in zip(parents, quantities):
                    self._consume(parent, float(qty))

    def _consume(self, lot_id: str, quantity: float) -> None:
        lot = self.lots.get(lot_id)
        if lot is None or lot.balance is None:
            return
        lot.balance = round(lot.balance - quantity, 6)

    def _apply_transfer(self, lot_id: str, p: dict) -> None:
        lot = self.lots.get(lot_id)
        if lot is None:
            return
        if p["handover_status"] == "in_transit":
            lot.in_transit = True
            lot.transit_from = p["from_holder_id"]
            lot.transit_to = p["to_holder_id"]
        else:  # received
            lot.in_transit = False
            lot.holder_id = p["to_holder_id"]
            lot.transit_from = None
            lot.transit_to = None

    def _common_holder(self, lot_ids: list[str]) -> str | None:
        holders = {
            self.lots[lid].holder_id
            for lid in lot_ids
            if lid in self.lots and self.lots[lid].holder_id
        }
        return next(iter(holders)) if len(holders) == 1 else None

    @classmethod
    def from_store(cls, store) -> "FoodChainGraph":
        graph = cls()
        for event in store.stream():
            graph.apply(event)
        return graph

    # —— 双向追溯 ——

    def origin_lots(self, lot_id: str) -> set[str]:
        """向上溯源：经拆分/混批/换包/加工一直追溯到的全部来源批次（含自身）。"""
        seen: set[str] = set()
        stack = [lot_id]
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            lot = self.lots.get(current)
            if lot:
                stack.extend(lot.parents)
        return seen

    def affected_lots(self, lot_id: str) -> set[str]:
        """向下追踪：含有该批次物料的全部后续批次（含自身），跨换包不失链。"""
        seen: set[str] = set()
        stack = [lot_id]
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            stack.extend(self._children.get(current, ()))
        return seen

    def genealogy_edges(self) -> list[dict]:
        edges = []
        for lot in self.lots.values():
            for parent in lot.parents:
                edges.append(
                    {"parent": parent, "child": lot.lot_id, "kind": lot.edge_kinds[parent]}
                )
        return edges

    def handovers_of(self, lot_id: str, direction: str = "down") -> list[dict]:
        """沿批次链收集每次交接：direction='up' 溯源，'down' 追踪。"""
        lot_ids = self.affected_lots(lot_id) if direction == "down" else self.origin_lots(lot_id)
        records = []
        for lid in lot_ids:
            records.extend(self.handovers_by_lot.get(lid, ()))
        return sorted(records, key=lambda r: r["at"])

    # —— 影响面 ——

    def same_source_closure(self, lot_id: str) -> set[str]:
        """同源闭合：与该批次共享任一来源批次的全部批次。

        换包后拆分出的多个子批互为兄弟，一个阳性，同批来源的各去向都应受控。
        """
        closure: set[str] = set()
        for origin in self.origin_lots(lot_id):
            closure.update(self.affected_lots(origin))
        return closure

    def impact_scope(self, lot_id: str) -> dict:
        """快检阳性时立即计算：在途、在售/在库、已售订单、须通知的下游。"""
        affected = self.same_source_closure(lot_id)
        in_transit, on_hand, sold_orders = [], [], []
        holders: set[str] = set()
        shops: set[str] = set()
        for lid in affected:
            lot = self.lots.get(lid)
            if lot is None:
                continue
            remaining = lot.balance if lot.balance is not None else lot.quantity
            if remaining <= 0:
                continue  # 已被换包/提料的母批只保留谱系，不重复计入范围
            if lot.in_transit:
                in_transit.append(
                    {
                        "lot_id": lid,
                        "from_holder_id": lot.transit_from,
                        "to_holder_id": lot.transit_to,
                        "quantity": remaining,
                        "unit": lot.unit,
                    }
                )
                holders.update(x for x in (lot.transit_from, lot.transit_to) if x)
            else:
                on_hand.append(
                    {
                        "lot_id": lid,
                        "holder_id": lot.holder_id,
                        "quantity": remaining,
                        "unit": lot.unit,
                    }
                )
                if lot.holder_id:
                    holders.add(lot.holder_id)
            for order in self.orders_by_lot.get(lid, []):
                sold_orders.append(order)
                shops.add(order["shop_id"])
                holders.add(order.get("business_id") or "")
        # 持有下游批次的平台店铺也要通知
        for shop_id, shop in self.shops.items():
            if shop.business_id in holders:
                shops.add(shop_id)
        return {
            "source_lot_id": lot_id,
            "affected_lot_ids": sorted(affected),
            "in_transit": in_transit,
            "on_hand": on_hand,
            "sold_orders": sold_orders,
            "notify_holders": sorted(h for h in holders if h),
            "notify_shops": sorted(shops),
        }

    # —— 幽灵店铺：平台核验通过但无实际线下门店 ——

    def suspicious_shops(self) -> list[dict]:
        findings = []
        bad_results = {"no_physical_store", "address_mismatch", "fail"}
        for shop in self.shops.values():
            premises = (
                self.premises.get(shop.claimed_premises_id)
                if shop.claimed_premises_id
                else None
            )
            result = premises.verification["result"] if premises and premises.verification else None
            if shop.platform_result == "pass" and (
                premises is None or result in bad_results
            ):
                findings.append(
                    {
                        "shop_id": shop.shop_id,
                        "platform": shop.platform,
                        "shop_name": shop.shop_name,
                        "business_id": shop.business_id,
                        "claimed_premises_id": shop.claimed_premises_id,
                        "platform_result": shop.platform_result,
                        "field_result": result or "premises_missing",
                    }
                )
        return findings

    def businesses_by_beneficial_owner(self, owner_name: str) -> list[str]:
        return [
            bid
            for bid, business in self.businesses.items()
            if any(owner.get("owner_name") == owner_name for owner in business.beneficial_owners)
        ]
