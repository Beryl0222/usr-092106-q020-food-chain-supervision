"""读模型投影：从只追加事件流重放出全链监督所需的全部查询状态。

投影在事件写入**前**做业务不变量校验（引用完整、数量守恒、状态合法、
风险批次冻结），写入后再把同一条事件应用到内存状态。任何重新启动都能
从事件流完整重放，因此投影本身不持久化。

关键不变量：
- 拆分/分装/混批数量守恒：子批之和不得超过母批当前余量；
- 换包（RELABELED）不产生新批次、不切断谱系，标签历史逐次留痕；
- 被控制令覆盖的批次冻结：平台/企业的销售、发运、换包指令一律拒绝
  （RiskLotFrozenError）；监管部门现场查实的违规流转以
  ``observed_violation=true`` 登记，只记录 breach，不转移合法持仓——
  风险批次不会因为违规操作而“变成干净货”重新流通；
- 处罚决定引用的证据必须已保管、规则版本必须已发布。
"""

from collections import defaultdict

from . import catalog
from .errors import ConflictError, RiskLotFrozenError


class Projection:
    def __init__(self) -> None:
        self.event_ids: set[str] = set()
        self.businesses: dict[str, dict] = {}
        self.premises: dict[str, dict] = {}
        self.shops: dict[str, dict] = {}
        self.lots: dict[str, dict] = {}
        self.orders: dict[str, dict] = {}
        self.samples: dict[str, dict] = {}
        self.methods: dict[str, dict] = {}
        self.recalls: dict[str, dict] = {}
        self.controls: dict[str, dict] = {}      # control_event_id -> 控制记录
        self.cases: dict[str, dict] = {}
        self.complaints: dict[str, dict] = {}
        self.rules: dict[str, dict] = {}
        # 谱系：child -> [(parent, op, qty, event_id)]；反向索引同步维护
        self.parents: dict[str, list[tuple[str, str, float, str]]] = defaultdict(list)
        self.children: dict[str, list[tuple[str, str, float, str]]] = defaultdict(list)
        # lot_id -> 使用该批次的订单行
        self.lot_sales: dict[str, list[dict]] = defaultdict(list)
        # linked_event_id -> [evidence locks]
        self.evidence_by_event: dict[str, list[dict]] = defaultdict(list)

    # ========================================================== 对外校验/应用

    def validate(self, event: dict) -> None:
        self._apply(event, mutate=False)

    def apply(self, event: dict) -> None:
        self._apply(event, mutate=True)

    def _apply(self, e: dict, *, mutate: bool) -> None:
        et = e["event_type"]
        handler = getattr(self, f"_h_{et.lower()}", None)
        if handler is None:
            return
        handler(e, mutate)
        if mutate:
            self.event_ids.add(e["event_id"])

    # ---------------------------------------------------------- 工具

    def _require_event(self, event_id: str | None, ctx: str) -> None:
        if not event_id or event_id not in self.event_ids:
            raise ConflictError(f"{ctx} 引用的事件不存在：{event_id}")

    def _lot(self, lot_id: str) -> dict:
        lot = self.lots.get(lot_id)
        if lot is None:
            raise ConflictError(f"批次不存在：{lot_id}")
        return lot

    def _total_holding(self, lot: dict) -> float:
        return sum(lot["holdings"].values())

    def _primary_holder(self, lot: dict) -> str:
        return max(lot["holdings"].items(), key=lambda kv: kv[1])[0]

    def _consume_holding(self, lot: dict, qty: float, party: str | None) -> None:
        party = party or self._primary_holder(lot)
        if lot["holdings"].get(party, 0) < qty - 1e-9:
            raise ConflictError(
                f"批次 {lot['id']} 在 {party} 持仓 "
                f"{lot['holdings'].get(party, 0)}，不足以动用 {qty}")
        lot["holdings"][party] -= qty
        if abs(lot["holdings"][party]) < 1e-9:
            lot["holdings"].pop(party, None)

    def _freeze_guard(self, lot: dict, e: dict, *, relabel: bool = False) -> None:
        """控制令期间的常规流转指令一律拒绝。"""
        if lot["status"] == "controlled" and not e.get("payload", {}).get("observed_violation"):
            raise RiskLotFrozenError(
                f"批次 {lot['id']} 处于控制令（{lot['control_refs']}）期间，"
                f"禁止{e['event_type']}；复检/申诉应产生后继事件，不得让风险批次重新流通")

    def _descendants(self, lot_id: str) -> set[str]:
        seen: set[str] = set()
        stack = [lot_id]
        while stack:
            cur = stack.pop()
            for child, *_ in self.children.get(cur, []):
                if child not in seen:
                    seen.add(child)
                    stack.append(child)
        return seen

    def _ancestors(self, lot_id: str) -> set[str]:
        seen: set[str] = set()
        stack = [lot_id]
        while stack:
            cur = stack.pop()
            for parent, *_ in self.parents.get(cur, []):
                if parent not in seen:
                    seen.add(parent)
                    stack.append(parent)
        return seen

    # ========================================================== 主体 / 证照 / 店铺

    def _h_business_registered(self, e: dict, m: bool) -> None:
        bid = e["aggregate_id"]
        if bid in self.businesses:
            raise ConflictError(f"经营主体已存在：{bid}")
        if m:
            self.businesses[bid] = {
                "id": bid, "name": e["payload"]["name"],
                "registered_by": e["payload"].get("registered_by"),
                "owners": [], "licenses": {}, "created_event": e["event_id"],
                "created_at": e["occurred_at"],
            }

    def _h_beneficial_owner_linked(self, e: dict, m: bool) -> None:
        bid = e["aggregate_id"]
        if bid not in self.businesses:
            raise ConflictError(f"经营主体不存在：{bid}")
        if m:
            self.businesses[bid]["owners"].append({
                "name": e["payload"]["owner_name"],
                "id_type": e["payload"].get("id_type"),
                "id_ref": e["payload"].get("id_ref"),
                "event_id": e["event_id"], "at": e["occurred_at"],
            })

    def _h_license_issued(self, e: dict, m: bool) -> None:
        bid = e["aggregate_id"]
        biz = self.businesses.get(bid)
        if biz is None:
            raise ConflictError(f"经营主体不存在：{bid}")
        lic = e["payload"]["license_no"]
        if lic in biz["licenses"]:
            raise ConflictError(f"许可证编号已存在：{lic}")
        pid = e["payload"]["premises_id"]
        if m:
            biz["licenses"][lic] = {
                "license_no": lic, "premises_id": pid,
                "scope": e["payload"]["scope"],
                "issued_at": e["occurred_at"], "verified": None,
            }
            self.premises.setdefault(pid, {
                "id": pid, "address": e["payload"].get("address"),
                "verifications": [], "license_no": lic, "business_id": bid,
            })

    def _h_license_verified(self, e: dict, m: bool) -> None:
        biz = self.businesses.get(e["aggregate_id"])
        if biz is None:
            raise ConflictError(f"经营主体不存在：{e['aggregate_id']}")
        lic_no = e["payload"]["license_no"]
        if lic_no not in biz["licenses"]:
            raise ConflictError(f"许可证未签发，无法核验：{lic_no}")
        if m:
            biz["licenses"][lic_no]["verified"] = {
                "result": e["payload"]["result"],
                "by": e["payload"]["verified_by"],
                "at": e["occurred_at"], "event_id": e["event_id"],
            }

    def _h_premises_verified(self, e: dict, m: bool) -> None:
        pid = e["aggregate_id"]
        if pid not in self.premises:
            # 允许场所核查先于许可签发建档
            if m:
                self.premises[pid] = {"id": pid, "address": None,
                                      "verifications": [], "license_no": None,
                                      "business_id": None}
        if m:
            self.premises[pid]["address"] = self.premises[pid].get("address") \
                or e["payload"].get("address")
            self.premises[pid]["verifications"].append({
                "result": e["payload"]["result"],
                "by": e["payload"]["verified_by"],
                "at": e["occurred_at"], "event_id": e["event_id"],
            })

    def _h_shop_listed(self, e: dict, m: bool) -> None:
        sid = e["aggregate_id"]
        if sid in self.shops:
            raise ConflictError(f"平台店铺已存在：{sid}")
        p = e["payload"]
        biz = self.businesses.get(p["business_id"])
        if biz is None:
            raise ConflictError(f"店铺挂接的经营主体不存在：{p['business_id']}")
        if p["license_no"] not in biz["licenses"]:
            raise ConflictError(f"店铺申报的许可证不属于该主体：{p['license_no']}")
        if m:
            self.shops[sid] = {
                "id": sid, "platform": p["platform"],
                "business_id": p["business_id"], "license_no": p["license_no"],
                "listed_at": e["occurred_at"], "reviews": [],
            }

    def _h_shop_binding_reviewed(self, e: dict, m: bool) -> None:
        shop = self.shops.get(e["aggregate_id"])
        if shop is None:
            raise ConflictError(f"平台店铺不存在：{e['aggregate_id']}")
        if m:
            shop["reviews"].append({
                "result": e["payload"]["result"],
                "by": e["payload"]["reviewed_by"],
                "at": e["occurred_at"], "event_id": e["event_id"],
            })

    # ========================================================== 批次谱系

    def _new_lot(self, e: dict, lot_id: str, product: str, qty: float,
                 unit: str, holder: str) -> dict:
        return {
            "id": lot_id, "product_name": product, "quantity": qty,
            "unit": unit, "status": "active", "holdings": {holder: qty},
            "owner_business": e["payload"].get("business_id", holder),
            "labels": [], "custody": [{"party": holder, "at": e["occurred_at"],
                                       "event_id": e["event_id"], "op": "harvest"}],
            "control_refs": [], "breaches": [], "created_event": e["event_id"],
            "created_at": e["occurred_at"], "disposed": 0.0,
        }

    def _h_lot_harvested(self, e: dict, m: bool) -> None:
        lid = e["aggregate_id"]
        p = e["payload"]
        if lid in self.lots:
            raise ConflictError(f"批次已存在：{lid}")
        if not isinstance(p["quantity"], (int, float)) or p["quantity"] <= 0:
            raise ConflictError("批次数量必须为正数")
        if m:
            self.lots[lid] = self._new_lot(e, lid, p["product_name"],
                                           float(p["quantity"]), p["unit"],
                                           p["business_id"])

    def _edge(self, parent: str, child: str, op: str, qty: float, eid: str) -> None:
        self.parents[child].append((parent, op, qty, eid))
        self.children[parent].append((child, op, qty, eid))

    def _h_lot_packed(self, e: dict, m: bool) -> None:
        self._split_like(e, m, op="packed")

    def _h_lot_split(self, e: dict, m: bool) -> None:
        self._split_like(e, m, op="split")

    def _split_like(self, e: dict, m: bool, *, op: str) -> None:
        parent = self._lot(e["aggregate_id"])
        self._freeze_guard(parent, e)
        outputs = e["payload"]["outputs"]
        if not isinstance(outputs, list) or not outputs:
            raise ConflictError("outputs 必须是非空数组")
        total = 0.0
        holder = self._primary_holder(parent)
        for o in outputs:
            if o.get("lot_id") in self.lots or o["lot_id"] == parent["id"]:
                raise ConflictError(f"输出批次标识冲突：{o.get('lot_id')}")
            if not isinstance(o.get("quantity"), (int, float)) or o["quantity"] <= 0:
                raise ConflictError("输出批次数量必须为正数")
            total += float(o["quantity"])
        if total > self._total_holding(parent) + 1e-9:
            raise ConflictError(
                f"拆分守恒冲突：输出合计 {total} 超过母批余量 "
                f"{self._total_holding(parent)}")
        if m:
            self._consume_holding(parent, total, holder)
            for o in outputs:
                child = self._new_lot(
                    e, o["lot_id"],
                    o.get("product_name", parent["product_name"]),
                    float(o["quantity"]), o.get("unit", parent["unit"]), holder)
                child["custody"][0] = {"party": holder, "at": e["occurred_at"],
                                       "event_id": e["event_id"], "op": op}
                self.lots[o["lot_id"]] = child
                child["owner_business"] = parent["owner_business"]
                self._edge(parent["id"], o["lot_id"], op, float(o["quantity"]),
                           e["event_id"])

    def _h_lot_merged(self, e: dict, m: bool) -> None:
        lid = e["aggregate_id"]
        p = e["payload"]
        if lid in self.lots:
            raise ConflictError(f"混批输出批次已存在：{lid}")
        inputs = p["inputs"]
        if not isinstance(inputs, list) or not inputs:
            raise ConflictError("inputs 必须是非空数组")
        total = 0.0
        target_party = p.get("to_party")
        for i in inputs:
            src = self._lot(i["lot_id"])
            self._freeze_guard(src, e)
            if not isinstance(i.get("quantity"), (int, float)) or i["quantity"] <= 0:
                raise ConflictError("投入批次数量必须为正数")
            if i["quantity"] > self._total_holding(src) + 1e-9:
                raise ConflictError(
                    f"混批守恒冲突：{src['id']} 余量不足")
            total += float(i["quantity"])
            target_party = target_party or self._primary_holder(src)
        if m:
            merged = self._new_lot(e, lid, p.get("product_name", "混批产品"),
                                   total, p.get("unit", "kg"), target_party)
            merged["custody"][0] = {"party": target_party, "at": e["occurred_at"],
                                    "event_id": e["event_id"], "op": "merged"}
            for i in inputs:
                src = self.lots[i["lot_id"]]
                self._consume_holding(src, float(i["quantity"]), None)
                self._edge(src["id"], lid, "merged", float(i["quantity"]),
                           e["event_id"])
            merged["owner_business"] = p.get("business_id") \
                or self.lots[inputs[0]["lot_id"]]["owner_business"]
            self.lots[lid] = merged

    def _h_lot_transferred(self, e: dict, m: bool) -> None:
        lot = self._lot(e["aggregate_id"])
        p = e["payload"]
        qty = float(p.get("quantity", self._total_holding(lot)))
        if qty <= 0:
            raise ConflictError("交接数量必须为正数")
        if p.get("observed_violation"):
            if lot["status"] != "controlled":
                raise ConflictError("observed_violation 只能针对受控批次登记")
            if m:
                lot["breaches"].append({
                    "control_refs": list(lot["control_refs"]),
                    "breach_type": "movement", "event_id": e["event_id"],
                    "from_party": p["from_party"], "to_party": p["to_party"],
                    "at": e["occurred_at"], "detail": p.get("detail"),
                })
                lot["custody"].append({
                    "party": p["to_party"], "at": e["occurred_at"],
                    "event_id": e["event_id"], "op": "transfer_violation_observed",
                    "qty": qty,
                })
            return
        self._freeze_guard(lot, e)
        if lot["holdings"].get(p["from_party"], 0) + 1e-9 < qty:
            raise ConflictError(
                f"交接冲突：{p['from_party']} 持有 {lot['id']} "
                f"{lot['holdings'].get(p['from_party'], 0)}，不足 {qty}")
        if m:
            lot["holdings"][p["from_party"]] -= qty
            if abs(lot["holdings"][p["from_party"]]) < 1e-9:
                lot["holdings"].pop(p["from_party"], None)
            lot["holdings"][p["to_party"]] = lot["holdings"].get(p["to_party"], 0) + qty
            lot["custody"].append({
                "party": p["to_party"], "at": e["occurred_at"],
                "event_id": e["event_id"],
                "op": "transfer_in_transit" if p.get("in_transit") else "transfer",
                "qty": qty, "from_party": p["from_party"],
                "compliant": p.get("compliant", True),
            })

    def _h_lot_relabeled(self, e: dict, m: bool) -> None:
        lot = self._lot(e["aggregate_id"])
        p = e["payload"]
        if p.get("observed_violation"):
            if lot["status"] != "controlled":
                raise ConflictError("observed_violation 只能针对受控批次登记")
            if m:
                lot["labels"].append({"old": p["old_label"], "new": p["new_label"],
                                      "at_party": p["at_party"], "at": e["occurred_at"],
                                      "event_id": e["event_id"], "violation": True})
                lot["breaches"].append({
                    "control_refs": list(lot["control_refs"]),
                    "breach_type": "relabel", "event_id": e["event_id"],
                    "at_party": p["at_party"], "at": e["occurred_at"],
                    "detail": p.get("detail"),
                })
            return
        self._freeze_guard(lot, e)
        if m:
            lot["labels"].append({"old": p["old_label"], "new": p["new_label"],
                                  "at_party": p["at_party"], "at": e["occurred_at"],
                                  "event_id": e["event_id"], "violation": False})
            lot["custody"].append({"party": p["at_party"], "at": e["occurred_at"],
                                   "event_id": e["event_id"], "op": "relabel"})

    def _h_lot_held(self, e: dict, m: bool) -> None:
        lot = self._lot(e["aggregate_id"])
        if m:
            lot["custody"].append({"party": e["payload"]["custodian"],
                                   "at": e["occurred_at"], "event_id": e["event_id"],
                                   "op": "held", "reason": e["payload"]["reason"]})

    def _h_lot_dispositioned(self, e: dict, m: bool) -> None:
        lot = self._lot(e["aggregate_id"])
        p = e["payload"]
        qty = float(p["quantity"])
        if p["disposition"] in ("destroyed", "returned"):
            if qty > self._total_holding(lot) + 1e-9:
                raise ConflictError(
                    f"处置冲突：数量 {qty} 超过余量 {self._total_holding(lot)}")
            if m:
                self._consume_holding(lot, qty, p.get("party"))
                lot["disposed"] += qty
                if self._total_holding(lot) <= 1e-9 and lot["status"] != "controlled":
                    lot["status"] = "disposed"
        elif m:  # released：解封归还，数量不动
            lot["custody"].append({"party": p.get("party", self._primary_holder(lot)),
                                   "at": e["occurred_at"], "event_id": e["event_id"],
                                   "op": "released"})

    # ========================================================== 订单

    def _h_order_placed(self, e: dict, m: bool) -> None:
        oid = e["aggregate_id"]
        if oid in self.orders:
            raise ConflictError(f"订单已存在：{oid}")
        p = e["payload"]
        if p["shop_id"] not in self.shops:
            raise ConflictError(f"订单的店铺不存在：{p['shop_id']}")
        if m:
            self.orders[oid] = {
                "id": oid, "shop_id": p["shop_id"],
                "business_id": self.shops[p["shop_id"]]["business_id"],
                "consumer_ref": p["consumer_ref"], "items": p["items"],
                "status": "placed", "lots": [], "placed_at": e["occurred_at"],
                "late": None, "events": [e["event_id"]],
            }

    def _h_order_fulfilled(self, e: dict, m: bool) -> None:
        order = self.orders.get(e["aggregate_id"])
        if order is None:
            raise ConflictError(f"订单不存在：{e['aggregate_id']}")
        if order["status"] != "placed":
            raise ConflictError(f"订单已履约，不能重复履约：{order['id']}")
        rows = e["payload"]["lots"]
        if not rows:
            raise ConflictError("履约必须绑定批次")
        for r in rows:
            lot = self._lot(r["lot_id"])
            self._freeze_guard(lot, e)
            if not isinstance(r.get("quantity"), (int, float)) or r["quantity"] <= 0:
                raise ConflictError("订单行数量必须为正数")
            if lot["holdings"].get(order["business_id"], 0) + 1e-9 < r["quantity"]:
                raise ConflictError(
                    f"店铺 {order['business_id']} 对批次 {lot['id']} 持仓不足，"
                    "无法履约")
        if m:
            for r in rows:
                lot = self.lots[r["lot_id"]]
                self._consume_holding(lot, float(r["quantity"]), order["business_id"])
                order["lots"].append({"lot_id": r["lot_id"],
                                      "quantity": float(r["quantity"])})
                self.lot_sales[r["lot_id"]].append({
                    "order_id": order["id"], "qty": float(r["quantity"]),
                    "business_id": order["business_id"],
                    "at": e["occurred_at"], "status": "fulfilled",
                    "event_id": e["event_id"],
                })
            order["status"] = "fulfilled"
            order["events"].append(e["event_id"])

    def _h_order_delivered(self, e: dict, m: bool) -> None:
        order = self.orders.get(e["aggregate_id"])
        if order is None:
            raise ConflictError(f"订单不存在：{e['aggregate_id']}")
        if order["status"] != "fulfilled":
            raise ConflictError("订单未履约，不能送达")
        if m:
            order["status"] = "delivered"
            order["delivered_at"] = e["payload"]["delivered_at"]
            order["events"].append(e["event_id"])
            for r in order["lots"]:
                for row in self.lot_sales[r["lot_id"]]:
                    if row["order_id"] == order["id"]:
                        row["status"] = "delivered"

    def _h_order_late(self, e: dict, m: bool) -> None:
        order = self.orders.get(e["aggregate_id"])
        if order is None:
            raise ConflictError(f"订单不存在：{e['aggregate_id']}")
        if order["status"] not in ("fulfilled", "delivered"):
            raise ConflictError("迟到状态只能挂在已履约/已送达订单之后")
        if m:
            order["late"] = {"minutes_late": e["payload"]["minutes_late"],
                             "at": e["occurred_at"], "event_id": e["event_id"]}
            order["events"].append(e["event_id"])

    def _h_order_recalled_from_consumer(self, e: dict, m: bool) -> None:
        order = self.orders.get(e["aggregate_id"])
        if order is None:
            raise ConflictError(f"订单不存在：{e['aggregate_id']}")
        rid = e["payload"]["recall_id"]
        if rid not in self.recalls:
            raise ConflictError(f"召回案件不存在：{rid}")
        if m:
            order["status"] = "recalled_from_consumer"
            order["events"].append(e["event_id"])

    # ========================================================== 方法 / 样本 / 控制

    def _h_method_published(self, e: dict, m: bool) -> None:
        code = e["payload"]["method_code"]
        ver = str(e["payload"]["version"])
        known = self.methods.setdefault(code, {"code": code, "versions": {}})
        if ver in known["versions"]:
            raise ConflictError(f"检测方法版本已发布：{code}@{ver}")
        if m:
            known["versions"][ver] = {
                "title": e["payload"]["title"], "at": e["occurred_at"],
                "event_id": e["event_id"],
            }

    def _h_sample_drawn(self, e: dict, m: bool) -> None:
        sid = e["aggregate_id"]
        if sid in self.samples:
            raise ConflictError(f"样本已存在：{sid}")
        self._lot(e["payload"]["lot_id"])
        if m:
            self.samples[sid] = {
                "id": sid, "lot_id": e["payload"]["lot_id"],
                "drawn_by": e["payload"]["sampled_by"],
                "drawn_at": e["occurred_at"], "status": "drawn",
                "tests": [], "retests": [], "appeal": None,
                "control": None, "clear": None,
            }

    def _positive_exists(self, sample: dict) -> bool:
        return any(t["result"] == "positive" for t in sample["tests"])

    def _h_sample_tested(self, e: dict, m: bool) -> None:
        sample = self.samples.get(e["aggregate_id"])
        if sample is None:
            raise ConflictError(f"样本不存在：{e['aggregate_id']}")
        p = e["payload"]
        method = self.methods.get(p["method_code"])
        if method is None or not method["versions"]:
            raise ConflictError(f"检测方法未登记：{p['method_code']}")
        if m:
            sample["tests"].append({
                "kind": p["kind"], "result": p["result"],
                "method_code": p["method_code"], "by": p["tested_by"],
                "at": e["occurred_at"], "event_id": e["event_id"],
            })
            sample["status"] = "screened"

    def _h_sample_retested(self, e: dict, m: bool) -> None:
        sample = self.samples.get(e["aggregate_id"])
        if sample is None:
            raise ConflictError(f"样本不存在：{e['aggregate_id']}")
        if not self._positive_exists(sample):
            raise ConflictError("复检只能针对出现过阳性结论的样本")
        if m:
            sample["retests"].append({
                "result": e["payload"]["result"],
                "method_code": e["payload"]["method_code"],
                "by": e["payload"]["tested_by"], "at": e["occurred_at"],
                "event_id": e["event_id"],
            })
            sample["status"] = "retested"

    def _h_sample_appeal_decided(self, e: dict, m: bool) -> None:
        sample = self.samples.get(e["aggregate_id"])
        if sample is None:
            raise ConflictError(f"样本不存在：{e['aggregate_id']}")
        if not self._positive_exists(sample):
            raise ConflictError("没有阳性初检，无从申诉")
        if m:
            sample["appeal"] = {
                "decision": e["payload"]["decision"],
                "by": e["payload"]["decided_by"], "at": e["occurred_at"],
                "event_id": e["event_id"],
            }
            sample["status"] = "appeal_decided"

    def _h_risk_controlled(self, e: dict, m: bool) -> None:
        sample = self.samples.get(e["aggregate_id"])
        if sample is None:
            raise ConflictError(f"样本不存在：{e['aggregate_id']}")
        if not self._positive_exists(sample):
            raise ConflictError("控制措施必须由阳性检测结论触发")
        rule = self.rules.get(e["payload"]["rule_code"])
        if rule is None or str(e["payload"]["rule_version"]) not in rule["versions"]:
            raise ConflictError(
                f"控制令引用的规则版本未发布："
                f"{e['payload']['rule_code']}@{e['payload']['rule_version']}")
        if sample["control"] is not None:
            raise ConflictError("样本已下达控制令；后续状态请用解除/复核事件表达")
        if m:
            root = sample["lot_id"]
            affected = {root} | self._descendants(root)
            record = {
                "control_event_id": e["event_id"], "sample_id": sample["id"],
                "root_lot_id": root, "affected_lots": sorted(affected),
                "decisions": list(e["payload"]["decisions"]),
                "authority": e["payload"]["authority"],
                "rule_code": e["payload"]["rule_code"],
                "rule_version": str(e["payload"]["rule_version"]),
                "at": e["occurred_at"], "status": "active", "clear": None,
            }
            self.controls[e["event_id"]] = record
            sample["control"] = e["event_id"]
            for lid in affected:
                self.lots[lid]["status"] = "controlled"
                self.lots[lid]["control_refs"].append(e["event_id"])

    def _h_risk_cleared(self, e: dict, m: bool) -> None:
        sample = self.samples.get(e["aggregate_id"])
        if sample is None:
            raise ConflictError(f"样本不存在：{e['aggregate_id']}")
        cid = sample.get("control")
        if cid is None:
            raise ConflictError("样本没有生效中的控制令")
        basis = e["payload"]["basis"]
        if basis == "retest_negative":
            ok = any(r["result"] == "negative" for r in sample["retests"])
        else:
            ok = sample["appeal"] is not None and sample["appeal"]["decision"] != "upheld"
        if not ok:
            raise ConflictError(f"解除依据不成立：{basis}（缺少对应后继记录）")
        if m:
            control = self.controls[cid]
            control["status"] = "cleared"
            control["clear"] = {"basis": basis, "by": e["payload"]["decided_by"],
                                "at": e["occurred_at"], "event_id": e["event_id"]}
            sample["clear"] = e["event_id"]
            for lid in control["affected_lots"]:
                lot = self.lots[lid]
                lot["control_refs"] = [r for r in lot["control_refs"] if r != cid]
                if not lot["control_refs"] and self._total_holding(lot) > 1e-9:
                    lot["status"] = "active"

    # ========================================================== 召回 / 提示

    def _h_recall_opened(self, e: dict, m: bool) -> None:
        rid = e["aggregate_id"]
        if rid in self.recalls:
            raise ConflictError(f"召回案件已存在：{rid}")
        cid = e["payload"]["control_event_id"]
        if cid not in self.controls:
            raise ConflictError(f"控制令不存在：{cid}")
        root = e["payload"]["root_lot_id"]
        if root != self.controls[cid]["root_lot_id"]:
            raise ConflictError("召回根批次必须与控制令一致")
        if m:
            self.recalls[rid] = {
                "id": rid, "control_event_id": cid, "root_lot_id": root,
                "authority": e["payload"]["authority"], "opened_at": e["occurred_at"],
                "notifications": [], "reports": [], "reconciliations": [],
                "status": "opened", "alert": None, "breaches": [],
                "opened_event": e["event_id"],
            }

    def _h_downstream_notified(self, e: dict, m: bool) -> None:
        recall = self.recalls.get(e["aggregate_id"])
        if recall is None:
            raise ConflictError(f"召回案件不存在：{e['aggregate_id']}")
        if recall["status"] == "closed":
            raise ConflictError("召回案件已关闭")
        p = e["payload"]
        key = (p["party_id"], tuple(sorted(p["lot_ids"])), p["channel"])
        existing = {(n["party_id"], tuple(sorted(n["lot_ids"])), n["channel"])
                    for n in recall["notifications"]}
        if key in existing:
            raise ConflictError("相同下游通知已存在（按  party+批次+渠道 去重）")
        if m:
            recall["notifications"].append({
                "party_id": p["party_id"], "lot_ids": list(p["lot_ids"]),
                "channel": p["channel"], "at": e["occurred_at"],
                "event_id": e["event_id"],
            })

    def _h_recall_reported(self, e: dict, m: bool) -> None:
        recall = self.recalls.get(e["aggregate_id"])
        if recall is None:
            raise ConflictError(f"召回案件不存在：{e['aggregate_id']}")
        if recall["status"] == "closed":
            raise ConflictError("召回案件已关闭")
        qs = e["payload"]["quantities"]
        if not isinstance(qs, list) or not qs:
            raise ConflictError("quantities 必须是非空数组")
        affected = set(self.controls[recall["control_event_id"]]["affected_lots"])
        for q in qs:
            if q["lot_id"] not in affected:
                raise ConflictError(f"上报批次不在控制范围：{q['lot_id']}")
            if not isinstance(q.get("quantity"), (int, float)) or q["quantity"] < 0:
                raise ConflictError("召回数量不能为负")
        if m:
            recall["reports"].append({
                "business_id": e["payload"]["business_id"],
                "quantities": qs, "at": e["occurred_at"],
                "event_id": e["event_id"],
            })

    def _h_recall_reconciled(self, e: dict, m: bool) -> None:
        recall = self.recalls.get(e["aggregate_id"])
        if recall is None:
            raise ConflictError(f"召回案件不存在：{e['aggregate_id']}")
        if m:
            recall["reconciliations"].append({
                "by": e["payload"]["reconciled_by"], "at": e["occurred_at"],
                "event_id": e["event_id"],
            })

    def _h_recall_closed(self, e: dict, m: bool) -> None:
        recall = self.recalls.get(e["aggregate_id"])
        if recall is None:
            raise ConflictError(f"召回案件不存在：{e['aggregate_id']}")
        if recall["status"] == "closed":
            raise ConflictError("召回案件已关闭")
        if not recall["reconciliations"]:
            raise ConflictError("关闭召回前必须至少完成一次数量核对")
        if m:
            recall["status"] = "closed"
            recall["closed_at"] = e["occurred_at"]
            recall["closed_event"] = e["event_id"]

    def _h_public_alert_published(self, e: dict, m: bool) -> None:
        recall = self.recalls.get(e["aggregate_id"])
        if recall is None:
            raise ConflictError(f"召回案件不存在：{e['aggregate_id']}")
        if not recall["reconciliations"]:
            raise ConflictError("公众风险提示只能在数量核对（核实）之后发布")
        p = e["payload"]
        for lid in p["lot_ids"]:
            self._lot(lid)
        if m:
            recall["alert"] = {
                "status": "published", "title": p["title"], "content": p["content"],
                "lot_ids": list(p["lot_ids"]), "by": p["published_by"],
                "at": e["occurred_at"], "event_id": e["event_id"],
            }

    def _h_public_alert_retracted(self, e: dict, m: bool) -> None:
        recall = self.recalls.get(e["aggregate_id"])
        if recall is None or recall["alert"] is None:
            raise ConflictError("没有可撤回的公众提示")
        if m:
            recall["alert"]["status"] = "retracted"
            recall["alert"]["retract"] = {
                "reason": e["payload"]["reason"],
                "by": e["payload"]["retracted_by"], "at": e["occurred_at"],
                "event_id": e["event_id"],
            }

    def _h_control_breach_recorded(self, e: dict, m: bool) -> None:
        recall = self.recalls.get(e["aggregate_id"])
        if recall is None:
            raise ConflictError(f"召回案件不存在：{e['aggregate_id']}")
        p = e["payload"]
        self._require_event(p["control_event_id"], "CONTROL_BREACH")
        self._require_event(p.get("ref_event_id"), "CONTROL_BREACH")
        if m:
            recall["breaches"].append({
                "control_event_id": p["control_event_id"],
                "breach_type": p["breach_type"], "ref_event_id": p["ref_event_id"],
                "at": e["occurred_at"], "event_id": e["event_id"],
            })

    # ========================================================== 投诉 / 执法

    def _h_complaint_filed(self, e: dict, m: bool) -> None:
        cid = e["aggregate_id"]
        if cid in self.complaints:
            raise ConflictError(f"投诉已存在：{cid}")
        if m:
            self.complaints[cid] = {
                "id": cid, "subject_type": e["payload"]["subject_type"],
                "subject_id": e["payload"]["subject_id"],
                "content": e["payload"]["content"],
                "reporter_ref": e["payload"].get("reporter_ref"),
                "reporter_contact": e["payload"].get("reporter_contact"),
                "confidential": e["payload"].get("confidential", True),
                "at": e["occurred_at"], "event_id": e["event_id"],
            }

    def _h_case_opened(self, e: dict, m: bool) -> None:
        cid = e["aggregate_id"]
        if cid in self.cases:
            raise ConflictError(f"案件已存在：{cid}")
        for evid in e["payload"]["basis_event_ids"]:
            self._require_event(evid, "立案依据")
        if m:
            self.cases[cid] = {
                "id": cid, "basis": list(e["payload"]["basis_event_ids"]),
                "subject_business_id": e["payload"].get("subject_business_id"),
                "opened_by": e["payload"]["opened_by"], "opened_at": e["occurred_at"],
                "evidence": [], "penalty": None, "corrections": [],
                "status": "opened",
            }

    def _h_evidence_locked(self, e: dict, m: bool) -> None:
        case = self.cases.get(e["aggregate_id"])
        if case is None:
            raise ConflictError(f"案件不存在：{e['aggregate_id']}")
        p = e["payload"]
        self._require_event(p["linked_event_id"], "证据保管")
        if any(v["linked_event_id"] == p["linked_event_id"] for v in case["evidence"]):
            raise ConflictError("同一事件已在本案登记保管")
        if m:
            rec = {"linked_event_id": p["linked_event_id"],
                   "custodian": p["custodian"], "storage_ref": p["storage_ref"],
                   "hash_alg": p["hash_alg"], "digest": p["digest"],
                   "at": e["occurred_at"], "event_id": e["event_id"]}
            case["evidence"].append(rec)
            self.evidence_by_event[p["linked_event_id"]].append(
                {"case_id": case["id"], **rec})

    def _h_enforcement_decided(self, e: dict, m: bool) -> None:
        case = self.cases.get(e["aggregate_id"])
        if case is None:
            raise ConflictError(f"案件不存在：{e['aggregate_id']}")
        if case["status"] != "opened":
            raise ConflictError("案件已作出处罚决定")
        p = e["payload"]
        rule = self.rules.get(p["rule_code"])
        if rule is None or str(p["rule_version"]) not in rule["versions"]:
            raise ConflictError(
                f"处罚依据的规则版本未发布：{p['rule_code']}@{p['rule_version']}")
        locked = {v["linked_event_id"] for v in case["evidence"]}
        missing = [x for x in p["evidence_event_ids"] if x not in locked]
        if missing:
            raise ConflictError(f"处罚引用的证据未完成保管登记：{missing}")
        if m:
            case["penalty"] = {
                "penalties": p["penalties"],
                "evidence_event_ids": list(p["evidence_event_ids"]),
                "rule_code": p["rule_code"], "rule_version": str(p["rule_version"]),
                "decided_by": p["decided_by"], "at": e["occurred_at"],
                "event_id": e["event_id"],
            }
            case["status"] = "penalized"

    def _h_correction_submitted(self, e: dict, m: bool) -> None:
        case = self.cases.get(e["aggregate_id"])
        if case is None:
            raise ConflictError(f"案件不存在：{e['aggregate_id']}")
        if m:
            case["corrections"].append({
                "business_id": e["payload"]["business_id"],
                "description": e["payload"]["description"],
                "at": e["occurred_at"], "event_id": e["event_id"],
            })

    def _h_rule_published(self, e: dict, m: bool) -> None:
        code = e["payload"]["rule_code"]
        ver = str(e["payload"]["version"])
        known = self.rules.setdefault(code, {"code": code, "versions": {}})
        if ver in known["versions"]:
            raise ConflictError(f"规则版本已存在：{code}@{ver}")
        if m:
            known["versions"][ver] = {
                "content_ref": e["payload"]["content_ref"],
                "effective_at": e["payload"]["effective_at"],
                "at": e["occurred_at"], "event_id": e["event_id"],
            }

    # ========================================================== 追溯查询

    def trace(self, lot_id: str) -> dict:
        """双向追溯：田间 → 分装/混批/换包/交接 → 订单；以及反向回溯。"""
        self._lot(lot_id)
        upstream, downstream = [], []

        def walk(node: str, edges: dict, out: list, seen: set) -> None:
            for other, op, qty, eid in edges.get(node, []):
                out.append({"lot_id": other, "op": op, "quantity": qty,
                            "event_id": eid})
                if other not in seen:
                    seen.add(other)
                    walk(other, edges, out, seen)

        walk(lot_id, self.parents, upstream, {lot_id})
        walk(lot_id, self.children, downstream, {lot_id})
        lot = self.lots[lot_id]
        return {
            "lot_id": lot_id,
            "product_name": lot["product_name"],
            "status": lot["status"],
            "upstream": list(reversed(upstream)),     # 田间在前
            "downstream": downstream,
            "label_history": lot["labels"],
            "custody_chain": lot["custody"],
            "holdings": dict(lot["holdings"]),
            "remaining_quantity": self._total_holding(lot),
            "control_refs": list(lot["control_refs"]),
            "breaches": list(lot["breaches"]),
            "orders": [dict(s) for s in self.lot_sales.get(lot_id, [])],
        }

    def lineage_closure(self, lot_id: str) -> dict:
        return {"ancestors": sorted(self._ancestors(lot_id) | {lot_id}),
                "descendants": sorted(self._descendants(lot_id))}

    def penalty_audit(self, case_id: str) -> dict:
        """处罚反查：决定 → 规则版本 → 证据保管 → 被证事件 → 每次交接链。"""
        case = self.cases.get(case_id)
        if case is None or case["penalty"] is None:
            raise ConflictError(f"案件或处罚决定不存在：{case_id}")
        chain = []
        for linked_id in case["penalty"]["evidence_event_ids"]:
            locks = [v for v in case["evidence"] if v["linked_event_id"] == linked_id]
            chain.append({"event_id": linked_id, "custody": locks})
        return {"case_id": case_id, "penalty": case["penalty"],
                "basis_events": list(case["basis"]), "evidence_chain": chain}
