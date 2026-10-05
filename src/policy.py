"""快检阳性应急策略与召回数量核对（纯函数，不改状态）。

快检结论只触发**控制措施**，不是最终定性：策略层输出“应当下达什么
控制、覆盖哪些批次与订单、通知谁”，由应用层落为 RISK_CONTROLLED /
RECALL_OPENED / DOWNSTREAM_NOTIFIED 事件；正式结论必须走复检或申诉
（SAMPLE_RETESTED / SAMPLE_APPEAL_DECIDED → RISK_CLEARED）。
"""

from .errors import ConflictError


def _rapid_positive(proj, sample_id: str) -> dict:
    sample = proj.samples.get(sample_id)
    if sample is None:
        raise ConflictError(f"样本不存在：{sample_id}")
    rapid_positive = any(t["kind"] == "rapid" and t["result"] == "positive"
                         for t in sample["tests"])
    lab_positive = any(t["kind"] == "laboratory" and t["result"] == "positive"
                       for t in sample["tests"])
    if not (rapid_positive or lab_positive):
        raise ConflictError("样本尚无阳性检测结论，不能启动应急控制")
    return sample


def affected_scope(proj, sample_id: str) -> dict:
    """阳性后立即计算在途、在售、待履约、已送达四类范围。

    覆盖样本批次及其全部下游（拆分/混批/换包后仍闭合），并沿订单链
    延伸到消费者侧。
    """
    sample = _rapid_positive(proj, sample_id)
    root = sample["lot_id"]
    closure = {root} | proj._descendants(root)

    in_transit, on_sale, holders = [], [], set()

    def party_in_transit(lot: dict, party: str) -> bool:
        """该持有方最近一次取得占有的交接是否仍处在途状态。"""
        for c in reversed(lot["custody"]):
            if c.get("party") == party and c["op"] in (
                    "transfer", "transfer_in_transit",
                    "transfer_violation_observed"):
                return c["op"] == "transfer_in_transit"
        return False

    for lid in sorted(closure):
        lot = proj.lots[lid]
        for party, qty in lot["holdings"].items():
            holders.add(party)
            entry = {"lot_id": lid, "party_id": party, "quantity": qty,
                     "unit": lot["unit"]}
            if party_in_transit(lot, party):
                in_transit.append(entry)
            else:
                on_sale.append(entry)

    pending_orders, delivered_orders = [], []
    for lid in closure:
        for sale in proj.lot_sales.get(lid, []):
            order = proj.orders[sale["order_id"]]
            row = {"order_id": order["id"], "shop_id": order["shop_id"],
                   "business_id": order["business_id"],
                   "lot_id": lid, "quantity": sale["qty"],
                   "status": order["status"]}
            if order["status"] in ("placed", "fulfilled"):
                pending_orders.append(row)
            elif order["status"] == "delivered":
                delivered_orders.append(row)

    # 下游通知对象：当前持有人 + 有待履约订单的店铺/主体 + 批发出入方
    notify_parties = set(holders)
    for row in pending_orders:
        notify_parties.add(row["business_id"])

    return {
        "sample_id": sample_id,
        "root_lot_id": root,
        "affected_lot_ids": sorted(closure),
        "in_transit": in_transit,
        "on_sale": on_sale,
        "pending_orders": pending_orders,
        "delivered_orders": delivered_orders,
        "notify_parties": sorted(notify_parties),
        "already_controlled": sample.get("control") is not None,
    }


def recommended_decisions(scope: dict) -> list[str]:
    """按范围给出控制措施建议：有在途→扣押，有在售→停售，
    已送达消费者→召回。"""
    decisions = ["hold"]
    if scope["on_sale"]:
        decisions.append("stop_sale")
    if scope["in_transit"]:
        decisions.append("seal")
    if scope["delivered_orders"]:
        decisions.append("recall")
    return decisions


def recall_balance(proj, recall_id: str) -> dict:
    """持续核对召回数量与去向：按批次做物料平衡。

    每批应交代数量 = 建档总量；已知去向 = 已处置 + 已售（订单行）+
    当前持仓；企业上报的追回/销毁/无法追回逐批累加。缺口持续暴露，
    任何一项都不能修改原始事件，只能新增上报。
    """
    recall = proj.recalls.get(recall_id)
    if recall is None:
        raise ConflictError(f"召回案件不存在：{recall_id}")
    control = proj.controls[recall["control_event_id"]]

    lots_report = []
    for lid in control["affected_lots"]:
        lot = proj.lots[lid]
        produced = lot["quantity"]
        sold = sum(s["qty"] for s in proj.lot_sales.get(lid, []))
        remaining = sum(lot["holdings"].values())
        disposed = lot["disposed"]
        # 母批经拆分/混批交予子批的数量（谱系守恒：母批余量由此交代）
        issued_children = sum(qty for _, _, qty, _ in proj.children.get(lid, []))
        reported = {"recovered": 0.0, "destroyed": 0.0, "consumed": 0.0,
                    "unaccounted": 0.0}
        for rep in recall["reports"]:
            for q in rep["quantities"]:
                if q["lot_id"] == lid:
                    key = q.get("disposition", "recovered")
                    reported[key] = reported.get(key, 0.0) + float(q["quantity"])
        reported_total = sum(reported.values())
        # 可交代去向：在库冻结 + 已处置 + 已交予子批 + 企业上报的最终去向
        explained = remaining + disposed + issued_children + reported_total
        gap = round(produced - explained, 6)
        # 已售给消费者的数量必须由 已食用/无法追回/追回/销毁 上报覆盖
        sold_covered = round(
            reported["consumed"] + reported["unaccounted"]
            + reported["recovered"] + reported["destroyed"], 6)
        lots_report.append({
            "lot_id": lid, "produced": produced, "unit": lot["unit"],
            "remaining": round(remaining, 6), "sold": sold,
            "disposed": disposed, "issued_to_children": issued_children,
            "reported": {k: round(v, 6) for k, v in reported.items()},
            "sold_followup_gap": round(sold - sold_covered, 6),
            "unaccounted_gap": gap,
        })

    notified = {n["party_id"] for n in recall["notifications"]}
    missing_notify = sorted(set(
        scope_parties(proj, recall["root_lot_id"]) - notified) &
        _current_holder_set(proj, control["affected_lots"]))

    return {
        "recall_id": recall_id,
        "status": recall["status"],
        "control_event_id": recall["control_event_id"],
        "lots": lots_report,
        "notifications_sent": len(recall["notifications"]),
        "notifications_missing": missing_notify,
        "reports_count": len(recall["reports"]),
        "reconciliations": len(recall["reconciliations"]),
        "breaches": list(recall["breaches"]),
        "balanced": all(x["unaccounted_gap"] == 0
                        and x["sold_followup_gap"] <= 0
                        for x in lots_report)
                    and not missing_notify,
    }


def scope_parties(proj, root_lot_id: str) -> set[str]:
    closure = {root_lot_id} | proj._descendants(root_lot_id)
    parties: set[str] = set()
    for lid in closure:
        parties.update(proj.lots[lid]["holdings"].keys())
        for s in proj.lot_sales.get(lid, []):
            parties.add(s["business_id"])
    return parties


def _current_holder_set(proj, lot_ids: list[str]) -> set[str]:
    out: set[str] = set()
    for lid in lot_ids:
        out.update(proj.lots[lid]["holdings"].keys())
    return out
