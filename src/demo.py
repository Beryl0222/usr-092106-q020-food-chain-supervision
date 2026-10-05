"""端到端叙事演示：从田间到餐桌的一次快检阳性处置。

运行：python3 -m src.demo

覆盖：幽灵店铺识别、运输违规与批发市场换包、拆分后双向追溯、
快检阳性应急范围、风险批次冻结、违规流转登记、召回数量核对、
申诉复核、处罚反查、公众提示与访问遮蔽。
"""

import json
from datetime import datetime, timedelta, timezone

from .app import ChainSupervisionService
from .errors import RiskLotFrozenError

TZ = timezone(timedelta(hours=8))
DEPT_MR = {"kind": "department", "department": "market_regulation"}
DEPT_AG = {"kind": "department", "department": "agriculture"}
DEPT_HC = {"kind": "department", "department": "health"}
DEPT_PS = {"kind": "department", "department": "public_security"}
DEPT_JO = {"kind": "department", "department": "joint_office"}
PUBLIC = {"kind": "public"}


def main() -> None:
    svc = ChainSupervisionService()
    clock = datetime(2026, 9, 20, 6, 0, tzinfo=TZ)

    def ts():
        nonlocal clock
        clock += timedelta(minutes=17)
        return clock.isoformat()

    def put(etype: str, agg_type: str, agg_id: str, payload: dict,
            actor: dict, summary: str) -> dict:
        event = {
            "event_id": f"EVT-{len(svc) + 1:04d}",
            "event_type": etype,
            "aggregate_type": agg_type,
            "aggregate_id": agg_id,
            "occurred_at": ts(),
            "version": svc.next_version(agg_type, agg_id),
            "summary": summary,
            "payload": payload,
        }
        return svc.emit(event, actor)

    def show(title: str, data) -> None:
        print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")
        print(json.dumps(data, ensure_ascii=False, indent=2))

    # ---------------------------------------------------------- 0. 规则与方法
    put("RULE_PUBLISHED", "risk_rule", "RULE-PEST",
        {"rule_code": "RULE-PEST", "version": "v1.2026",
         "content_ref": "rules/pesticide-rapid-screen-v1.md",
         "effective_at": "2026-01-01T00:00:00+08:00"},
        DEPT_MR, "发布蔬菜农药残留快检处置规则 v1.2026")
    put("METHOD_PUBLISHED", "test_method", "M-RAPID",
        {"method_code": "M-RAPID", "version": "2026.1",
         "title": "蔬菜有机磷农残胶体金快检法"},
        DEPT_HC, "发布快检方法标准版本")

    # ---------------------------------------------------------- 1. 主体/证照/场所/店铺
    for bid, name, who in (
            ("B-COOP", "青山蔬菜合作社", DEPT_MR),
            ("B-WHOLE", "城北批发市场 12 号档口", DEPT_MR),
            ("B-REST", "真香餐饮管理有限公司", DEPT_MR)):
        put("BUSINESS_REGISTERED", "food_business", bid,
            {"name": name, "registered_by": "market_regulation"},
            who, f"登记主体 {name}")

    put("BENEFICIAL_OWNER_LINKED", "food_business", "B-REST",
        {"owner_name": "李某某", "id_type": "身份证",
         "id_ref": "3301**********1234"},
        DEPT_MR, "登记餐饮公司受益所有人")

    put("LICENSE_ISSUED", "food_business", "B-REST",
        {"license_no": "JY-REST-001", "premises_id": "PRE-1",
         "address": "登记地址：城南园区 3 栋 101",
         "scope": "热食类食品制售"},
        DEPT_MR, "核发食品经营许可证")

    # 电子证照核验：通过
    put("LICENSE_VERIFIED", "food_business", "B-REST",
        {"license_no": "JY-REST-001", "result": "pass",
         "verified_by": "market_regulation"},
        DEPT_MR, "电子证照核验通过")

    # 实际经营地点核验：查无此店（分别核验，证照通过不等于有门店）
    put("PREMISES_VERIFIED", "licensed_premises", "PRE-1",
        {"result": "no_physical_store", "verified_by": "market_regulation",
         "address": "实地核查：城南园区 3 栋 101 为虚拟地址"},
        DEPT_MR, "实地核查未发现线下门店")

    platform = {"kind": "platform", "platform": "蜂鸟外卖"}
    put("SHOP_LISTED", "platform_shop", "SHOP-1",
        {"platform": "蜂鸟外卖", "business_id": "B-REST",
         "license_no": "JY-REST-001"},
        platform, "餐饮公司在外卖平台开店（平台侧核验证照即通过）")
    put("SHOP_BINDING_REVIEWED", "platform_shop", "SHOP-1",
        {"result": "fail_no_premises", "reviewed_by": "market_regulation"},
        DEPT_MR, "联合核验判定：无线下经营场所")

    show("① 店铺画像（电子证照 vs 实际门店分别核验）",
         svc.shop_dossier("SHOP-1"))

    # ---------------------------------------------------------- 2. 田间→分装→违规运输→换包
    put("LOT_HARVESTED", "food_lot", "VEG-100",
        {"product_name": "菠菜", "quantity": 100, "unit": "kg",
         "business_id": "B-COOP"},
        {"kind": "business", "business_id": "B-COOP"},
        "合作社建档：菠菜 100kg（田间）")
    put("LOT_PACKED", "food_lot", "VEG-100",
        {"outputs": [
            {"lot_id": "VEG-100-A", "product_name": "菠菜",
             "quantity": 60, "unit": "kg"},
            {"lot_id": "VEG-100-B", "product_name": "菠菜",
             "quantity": 40, "unit": "kg"}]},
        {"kind": "business", "business_id": "B-COOP"},
        "采收后分装：A 60kg / B 40kg（守恒）")

    put("LOT_TRANSFERRED", "food_lot", "VEG-100-A",
        {"from_party": "B-COOP", "to_party": "B-WHOLE", "quantity": 60,
         "compliant": False,
         "detail": "冷链车温控记录中断，车厢检出不明浸泡液"},
        {"kind": "business", "business_id": "B-COOP"},
        "运输途中违规处理（温控失效+疑似浸泡），60kg 运抵批发市场")

    # 批发市场更换包装：同批货换标签，谱系不切断
    put("LOT_RELABELED", "food_lot", "VEG-100-A",
        {"old_label": "VEG-100-A 散装菠菜", "new_label": "精品无公害菠菜",
         "at_party": "B-WHOLE"},
        {"kind": "business", "business_id": "B-WHOLE"},
        "批发市场换包，冒充无公害精品菜")

    # 45kg 运抵餐饮档口正常交接；另 5kg 阳性时仍在冷链承运方车上
    put("LOT_TRANSFERRED", "food_lot", "VEG-100-A",
        {"from_party": "B-WHOLE", "to_party": "B-REST", "quantity": 45},
        {"kind": "business", "business_id": "B-WHOLE"},
        "45kg 运抵外卖餐饮档口（10kg 留在批发档口，5kg 另车配送中）")
    put("LOT_TRANSFERRED", "food_lot", "VEG-100-A",
        {"from_party": "B-WHOLE", "to_party": "CARRIER-LC", "quantity": 5,
         "in_transit": True},
        {"kind": "business", "business_id": "B-WHOLE"},
        "5kg 装上冷链车发往另一买家（阳性时仍在途）")

    # ---------------------------------------------------------- 3. 平台订单
    put("ORDER_PLACED", "food_order", "ORDER-1",
        {"shop_id": "SHOP-1", "consumer_ref": "张某 138****0001",
         "items": [{"name": "蒜蓉菠菜", "qty": 1}]},
        platform, "订单 1：蒜蓉菠菜")
    put("ORDER_FULFILLED", "food_order", "ORDER-1",
        {"lots": [{"lot_id": "VEG-100-A", "quantity": 2}]},
        platform, "订单 1 用 VEG-100-A 2kg 出餐")
    put("ORDER_DELIVERED", "food_order", "ORDER-1",
        {"delivered_at": ts()}, platform, "订单 1 已送达")

    put("ORDER_PLACED", "food_order", "ORDER-2",
        {"shop_id": "SHOP-1", "consumer_ref": "王某 138****0002",
         "items": [{"name": "菠菜套餐", "qty": 1}]},
        platform, "订单 2")
    put("ORDER_FULFILLED", "food_order", "ORDER-2",
        {"lots": [{"lot_id": "VEG-100-A", "quantity": 3}]},
        platform, "订单 2 用 VEG-100-A 3kg 出餐")
    put("ORDER_DELIVERED", "food_order", "ORDER-2",
        {"delivered_at": ts()}, platform, "订单 2 送达")
    # 迟到只能形成后续状态
    put("ORDER_LATE", "food_order", "ORDER-2",
        {"minutes_late": 35}, platform, "订单 2 迟到 35 分钟（后续状态）")

    put("ORDER_PLACED", "food_order", "ORDER-3",
        {"shop_id": "SHOP-1", "consumer_ref": "赵某 138****0003",
         "items": [{"name": "凉拌菠菜", "qty": 1}]},
        platform, "订单 3（阳性时已出餐待配送）")
    put("ORDER_FULFILLED", "food_order", "ORDER-3",
        {"lots": [{"lot_id": "VEG-100-A", "quantity": 1}]},
        platform, "订单 3 已出餐待配送")
    put("ORDER_PLACED", "food_order", "ORDER-4",
        {"shop_id": "SHOP-1", "consumer_ref": "钱某 138****0004",
         "items": [{"name": "菠菜蛋花汤", "qty": 1}]},
        platform, "订单 4（控制令后试图继续出餐）")

    # ---------------------------------------------------------- 4. 快检阳性 → 立即算范围
    put("SAMPLE_DRAWN", "inspection_sample", "S-1",
        {"lot_id": "VEG-100-A", "sampled_by": "health"},
        DEPT_HC, "卫健部门在档口抽检菠菜")
    pos = put("SAMPLE_TESTED", "inspection_sample", "S-1",
              {"kind": "rapid", "result": "positive",
               "tested_by": "health", "method_code": "M-RAPID",
               "indicator": "有机磷农药残留"},
              DEPT_HC, "快检阳性：有机磷农残疑似超标（只触发控制，不定性）")

    scope = svc.emergency_scope("S-1", DEPT_MR)
    show("② 阳性瞬间算出的在途/在售/待履约/已送达范围与建议措施",
         {k: scope[k] for k in
          ("affected_lot_ids", "in_transit", "on_sale",
           "pending_orders", "delivered_orders",
           "notify_parties", "recommended_decisions")})

    control = put("RISK_CONTROLLED", "inspection_sample", "S-1",
                  {"trigger_sample_id": "S-1",
                   "decisions": ["hold", "stop_sale", "seal", "recall"],
                   "authority": "market_regulation",
                   "rule_code": "RULE-PEST", "rule_version": "v1.2026"},
                  DEPT_MR, "下达控制令：封存、停售、召回（覆盖全部下游批次）")

    # ---------------------------------------------------------- 5. 冻结：违规指令一律拒绝
    frozen = []
    try:
        put("ORDER_FULFILLED", "food_order", "ORDER-4",
            {"lots": [{"lot_id": "VEG-100-A", "quantity": 1}]},
            platform, "（应被拒绝）风险批次继续出餐")
    except RiskLotFrozenError as exc:
        frozen.append(str(exc))
    try:
        put("LOT_RELABELED", "food_lot", "VEG-100-A",
            {"old_label": "精品无公害菠菜", "new_label": "农家自种菠菜",
             "at_party": "B-WHOLE"},
            {"kind": "business", "business_id": "B-WHOLE"},
            "（应被拒绝）再次换包")
    except RiskLotFrozenError as exc:
        frozen.append(str(exc))
    show("③ 风险批次冻结：平台出餐与换包指令被拒", frozen)

    # 但现场查实的违规流转必须留痕（不转移合法持仓，只登记 breach）
    viol_move = put(
        "LOT_TRANSFERRED", "food_lot", "VEG-100-A",
        {"from_party": "B-WHOLE", "to_party": "B-STALL-X", "quantity": 10,
         "observed_violation": True,
         "detail": "暗访发现 10kg 被转运至隔壁档口继续销售"},
        DEPT_MR, "现场查实：控制令期间仍向隔壁档口转运")
    viol_relabel = put(
        "LOT_RELABELED", "food_lot", "VEG-100-A",
        {"old_label": "精品无公害菠菜", "new_label": "农家自种菠菜",
         "at_party": "B-WHOLE", "observed_violation": True,
         "detail": "换包后在其他档口上架"},
        DEPT_MR, "现场查实：控制令期间换包")

    # ---------------------------------------------------------- 6. 召回：通知→上报→持续核对
    put("RECALL_OPENED", "recall_case", "RC-1",
        {"control_event_id": control["event_id"],
         "root_lot_id": "VEG-100-A", "authority": "market_regulation"},
        DEPT_MR, "立案召回（控制事件自动关联）")
    # 立案瞬间的缺口快照：下游未通知、已售 6kg 去向未交代
    balance_initial = svc.recall_status("RC-1", DEPT_MR)

    for party, channel in (("B-WHOLE", "监管督办单"), ("B-REST", "平台+短信"),
                           ("CARRIER-LC", "冷链车温控终端拦截指令")):
        put("DOWNSTREAM_NOTIFIED", "recall_case", "RC-1",
            {"party_id": party, "lot_ids": ["VEG-100-A"], "channel": channel},
            DEPT_MR, f"通知下游 {party}")

    put("RECALL_REPORTED", "recall_case", "RC-1",
        {"business_id": "B-REST", "quantities": [
            {"lot_id": "VEG-100-A", "quantity": 2, "disposition": "recovered",
             "note": "订单1追回"},
            {"lot_id": "VEG-100-A", "quantity": 3, "disposition": "consumed",
             "note": "订单2已食用无法追回"},
            {"lot_id": "VEG-100-A", "quantity": 1, "disposition": "recovered",
             "note": "订单3配送途中截回"}]},
        {"kind": "business", "business_id": "B-REST"},
        "餐饮方上报：已售 6kg 的去向全部交代")
    put("ORDER_RECALLED_FROM_CONSUMER", "food_order", "ORDER-1",
        {"recall_id": "RC-1", "quantity": 2},
        platform, "订单 1 追回确认（订单的后继状态）")

    put("CONTROL_BREACH_RECORDED", "recall_case", "RC-1",
        {"control_event_id": control["event_id"],
         "breach_type": "movement", "ref_event_id": viol_move["event_id"]},
        DEPT_MR, "登记违规转运，纳入召回督办")
    put("CONTROL_BREACH_RECORDED", "recall_case", "RC-1",
        {"control_event_id": control["event_id"],
         "breach_type": "relabel", "ref_event_id": viol_relabel["event_id"]},
        DEPT_MR, "登记违规换包，纳入召回督办")

    # 第一次核对：在库冻结 54 + 已售交代 6 = 60，物料平衡闭合
    put("RECALL_RECONCILED", "recall_case", "RC-1",
        {"reconciled_by": "market_regulation"},
        DEPT_MR, "第一次数量核对（在库54+已售6=60）")
    balance_after = svc.recall_status("RC-1", DEPT_MR)
    show("④ 召回数量与去向持续核对（每批物料平衡 + 通知覆盖）",
         {"立案瞬间": {"批次": balance_initial["lots"],
                    "未通知到的持有方": balance_initial["notifications_missing"],
                    "是否平衡": balance_initial["balanced"]},
          "首次核对后": {"批次": balance_after["lots"],
                     "未通知到的持有方": balance_after["notifications_missing"],
                     "是否平衡": balance_after["balanced"]},
          "违规记录": balance_after["breaches"]})

    # 公众提示：首次数量核对（核实）之后才能发布
    put("PUBLIC_ALERT_PUBLISHED", "recall_case", "RC-1",
        {"title": "风险提示：批次 VEG-100-A 菠菜召回中",
         "content": "经检验确认相关批次菠菜存在农药残留风险，已售出餐品请停止食用并联系平台退款。",
         "lot_ids": ["VEG-100-A"], "published_by": "market_regulation"},
        DEPT_MR, "核实后向公众发布风险提示")

    # 核实后：在库批次监督销毁（处置事件改变持仓，平衡仍闭合）
    put("LOT_DISPOSITIONED", "food_lot", "VEG-100-A",
        {"disposition": "destroyed", "quantity": 10, "party": "B-WHOLE"},
        DEPT_MR, "批发档口 10kg 监督销毁")
    put("LOT_DISPOSITIONED", "food_lot", "VEG-100-A",
        {"disposition": "destroyed", "quantity": 5, "party": "CARRIER-LC"},
        DEPT_MR, "冷链车在途 5kg 拦截后销毁")
    put("LOT_DISPOSITIONED", "food_lot", "VEG-100-A",
        {"disposition": "destroyed", "quantity": 39, "party": "B-REST"},
        DEPT_MR, "餐饮档口冻结库存 39kg 监督销毁（含截回 3kg）")
    put("RECALL_RECONCILED", "recall_case", "RC-1",
        {"reconciled_by": "market_regulation"},
        DEPT_MR, "第二次核对：在库清零，已处置54+已售交代6=60（含在途拦截5）")
    put("RECALL_CLOSED", "recall_case", "RC-1",
        {"closed_by": "market_regulation"},
        DEPT_MR, "物料平衡闭合、违规线索已立案，关闭召回")

    # ---------------------------------------------------------- 7. 复核路径（企业申诉）
    put("SAMPLE_APPEAL_DECIDED", "inspection_sample", "S-1",
        {"decision": "upheld", "decided_by": "market_regulation",
         "opinion": "实验室复核与快检一致，维持阳性结论"},
        DEPT_MR, "企业申诉经复核维持原结论（初始证据未改动）")

    # ---------------------------------------------------------- 8. 立案处罚（可反查证据/规则/交接）
    put("CASE_OPENED", "enforcement_case", "CASE-1",
        {"basis_event_ids": [viol_move["event_id"], viol_relabel["event_id"]],
         "subject_business_id": "B-WHOLE", "opened_by": "public_security"},
        DEPT_PS, "公安/市监联合立案：控制令期间换包、转移风险批次")
    for linked, who in ((viol_move["event_id"], "执法记录仪影像+温控记录"),
                        (viol_relabel["event_id"], "新旧标签物证照片")):
        put("EVIDENCE_LOCKED", "enforcement_case", "CASE-1",
            {"linked_event_id": linked, "custodian": "public_security",
             "storage_ref": f"evidence-vault://{linked}",
             "hash_alg": "sha256",
             "digest": f"hash({linked})"},
            DEPT_PS, f"证据保管：{who}")
    put("ENFORCEMENT_DECIDED", "enforcement_case", "CASE-1",
        {"penalties": ["罚款 50000 元", "停业整顿 3 日"],
         "evidence_event_ids": [viol_move["event_id"], viol_relabel["event_id"]],
         "rule_code": "RULE-PEST", "rule_version": "v1.2026",
         "decided_by": "market_regulation"},
        DEPT_MR, "作出处罚决定（引用已保管证据与规则版本）")
    put("CORRECTION_SUBMITTED", "enforcement_case", "CASE-1",
        {"business_id": "B-WHOLE",
         "description": "已完成档口整改：加装温控、标签赋码、员工培训"},
        {"kind": "business", "business_id": "B-WHOLE"},
        "企业提交纠正情况")

    audit = svc.penalty_audit("CASE-1", DEPT_PS)
    show("⑤ 处罚反查：决定 → 规则版本 → 证据保管 → 被证事件 → 每次交接",
         {"penalty": audit["penalty"],
          "evidence": [{"event_id": x["event_id"],
                        "custody": x["custody"],
                        "linked_event_type": x["linked_event"]["event_type"],
                        "交接链": x.get("custody_chain")}
                       for x in audit["evidence_chain"]]})

    # ---------------------------------------------------------- 9. 双向追溯（换包/拆分不丢链）
    show("⑥ 从母批 VEG-100 正向追溯到订单（换包历史完整保留）",
         svc.trace("VEG-100", DEPT_MR) | {"_note": "正向"})
    back = svc.trace("VEG-100-A", DEPT_MR)
    show("⑦ 从问题批 VEG-100-A 反向追溯到田间",
         {"upstream": back["upstream"], "label_history": back["label_history"],
          "orders": back["orders"], "breaches": back["breaches"]})

    # ---------------------------------------------------------- 10. 访问控制
    print(f"\n{'=' * 72}\n⑧ 访问范围与信息保护\n{'=' * 72}")
    print("公众可见：", json.dumps(svc.public_alerts(), ensure_ascii=False))
    ag_events = svc.events_for(DEPT_AG, "food_business", "B-REST")
    owner_view = [e["payload"] for e in ag_events
                  if e["event_type"] == "BENEFICIAL_OWNER_LINKED"]
    print("农业部门看受益人：", json.dumps(owner_view, ensure_ascii=False))
    mr_events = svc.events_for(DEPT_MR, "food_business", "B-REST")
    owner_full = [e["payload"] for e in mr_events
                  if e["event_type"] == "BENEFICIAL_OWNER_LINKED"]
    print("市监部门看受益人：", json.dumps(owner_full, ensure_ascii=False))
    rest_view = svc.trace("VEG-100-A",
                          {"kind": "business", "business_id": "B-REST"})
    print("餐饮企业追溯（他方主体被遮蔽）：",
          json.dumps({"holdings": rest_view["holdings"],
                      "custody": rest_view["custody_chain"][:2]},
                     ensure_ascii=False))
    print("公众事件流条数（只能看到提示）：",
          len(svc.events_for(PUBLIC)))

    print(f"\n演示完成：共 {len(svc)} 条只追加事件，初始证据零改写。")


if __name__ == "__main__":
    main()
