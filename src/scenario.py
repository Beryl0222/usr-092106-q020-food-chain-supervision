"""全链监督联调场景。

事件序列覆盖：产地登记 → 运输 → 批发市场换包 → 拆分/冷链配送 →
外卖幽灵店铺 → 快检阳性触发控制 → 通知下游 → 违规流动与迟到订单 →
召回数量去向核对 → 实验室复检（正式结论）→ 证据封存/交接 → 处罚与申诉 →
移送公安 → 企业纠正 → 公众风险提示。

build() 返回 (store, graph, supervision)，事件标识全部稳定可复现。
"""

from __future__ import annotations

from .store import EventStore
from .supervision import Supervision
from .trace import FoodChainGraph


def _event(
    event_id,
    event_type,
    aggregate_type,
    aggregate_id,
    occurred_at,
    version,
    summary,
    payload,
    *,
    source_department="market_regulation",
    recorded_at=None,
):
    record = {
        "event_id": event_id,
        "event_type": event_type,
        "aggregate_type": aggregate_type,
        "aggregate_id": aggregate_id,
        "occurred_at": occurred_at,
        "recorded_at": recorded_at or occurred_at,
        "version": version,
        "source_department": source_department,
        "summary": summary,
        "payload": payload,
    }
    return record


def _base_events() -> list[dict]:
    T = "2026-10-03"
    D = "2026-10-04"
    events = []
    add = events.append

    # —— 经营主体与受益人 ——
    add(_event("subj-farm-01", "SUBJECT_REGISTERED", "food_business", "bz-farm-01",
               f"{T}T08:00:00+08:00", 1, "青山蔬菜种植合作社登记",
               {"business_name": "青山蔬菜种植合作社", "business_type": "producer",
                "department": "agriculture"}, source_department="agriculture"))
    add(_event("subj-wholesale-01", "SUBJECT_REGISTERED", "food_business", "bz-wholesale-01",
               f"{T}T08:10:00+08:00", 1, "旺发蔬菜行登记",
               {"business_name": "旺发蔬菜行", "business_type": "wholesaler",
                "department": "market_regulation"}))
    add(_event("subj-distributor-01", "SUBJECT_REGISTERED", "food_business", "bz-distributor-01",
               f"{T}T08:20:00+08:00", 1, "鲜达配送公司登记",
               {"business_name": "鲜达配送有限公司", "business_type": "distributor",
                "department": "market_regulation"}))
    add(_event("subj-restaurant-01", "SUBJECT_REGISTERED", "food_business", "bz-restaurant-group-01",
               f"{T}T08:30:00+08:00", 1, "悦味餐饮管理有限公司登记",
               {"business_name": "悦味餐饮管理有限公司", "business_type": "catering",
                "department": "market_regulation"}))
    add(_event("subj-restaurant-02", "SUBJECT_REGISTERED", "food_business", "bz-restaurant-group-02",
               f"{T}T08:31:00+08:00", 1, "悦味餐饮第二分店主体登记",
               {"business_name": "悦味餐饮（城南）分店", "business_type": "catering",
                "department": "market_regulation"}))
    # 同一受益人控制两家餐饮主体
    add(_event("bo-restaurant-01", "BENEFICIAL_OWNER_DECLARED", "food_business",
               "bz-restaurant-group-01", f"{T}T08:40:00+08:00", 2,
               "受益人声明：厉强",
               {"owner_name": "厉强", "owner_id_no": "3301**********1234",
                "declared_at": f"{T}T08:40:00+08:00"}))
    add(_event("bo-restaurant-02", "BENEFICIAL_OWNER_DECLARED", "food_business",
               "bz-restaurant-group-02", f"{T}T08:41:00+08:00", 2,
               "受益人声明：厉强",
               {"owner_name": "厉强", "owner_id_no": "3301**********1234",
                "declared_at": f"{T}T08:41:00+08:00"}))

    # —— 电子证照与实地核验分别记录 ——
    add(_event("lic-pr-wholesale", "PREMISES_LICENSED", "premises", "pr-wholesale-01",
               f"{T}T09:00:00+08:00", 1, "批发市场摊位食品经营许可签发",
               {"premises_id": "pr-wholesale-01", "business_id": "bz-wholesale-01",
                "address": "城南批发市场B区17号", "license_no": "JY12026100301",
                "scope": "食用农产品销售"}))
    add(_event("ver-pr-wholesale", "PREMISES_VERIFIED", "premises", "pr-wholesale-01",
               f"{T}T10:00:00+08:00", 2, "实地核查：批发市场摊位属实",
               {"premises_id": "pr-wholesale-01", "result": "pass",
                "verifier_department": "market_regulation",
                "checked_at": f"{T}T10:00:00+08:00"}))
    add(_event("lic-pr-kitchen", "PREMISES_LICENSED", "premises", "pr-kitchen-01",
               f"{T}T09:05:00+08:00", 1, "餐饮经营许可签发",
               {"premises_id": "pr-kitchen-01", "business_id": "bz-restaurant-group-01",
                "address": "城南街道兴华路9号101室", "license_no": "JY12026200807",
                "scope": "热食类食品制售"}))
    # 平台显示核验通过，但实地没有门店
    add(_event("ver-pr-kitchen", "PREMISES_VERIFIED", "premises", "pr-kitchen-01",
               f"{T}T10:30:00+08:00", 2, "实地核查：该地址为居民住宅，无实际经营门店",
               {"premises_id": "pr-kitchen-01", "result": "no_physical_store",
                "verifier_department": "market_regulation",
                "checked_at": f"{T}T10:30:00+08:00"}))
    add(_event("shop-weipin-01", "PLATFORM_SHOP_LISTED", "platform_shop", "shop-weipin-01",
               f"{T}T11:00:00+08:00", 1, "味拼外卖店铺“悦味轻食”上线",
               {"platform": "味拼外卖", "shop_name": "悦味轻食（兴华路店）",
                "business_id": "bz-restaurant-group-01",
                "claimed_premises_id": "pr-kitchen-01"},
               source_department="platform"))
    add(_event("shop-weipin-01-verify", "DELIVERY_PLATFORM_VERIFIED", "platform_shop",
               "shop-weipin-01", f"{T}T11:05:00+08:00", 2,
               "外卖平台自检显示通过",
               {"platform": "味拼外卖", "result": "pass",
                "verified_at": f"{T}T11:05:00+08:00"},
               source_department="platform"))

    # —— 田间批次 → 运输 → 批发市场换包 ——
    add(_event("lot-veg-01-reg", "LOT_REGISTERED", "food_lot", "lot-veg-01",
               f"{T}T12:00:00+08:00", 1, "产地批次：大白菜1000公斤",
               {"product_name": "大白菜", "producer_business_id": "bz-farm-01",
                "quantity": 1000, "unit": "kg"}, source_department="agriculture"))
    add(_event("lot-veg-01-transit", "LOT_TRANSFERRED", "food_lot", "lot-veg-01",
               f"{T}T13:00:00+08:00", 2, "产地发运，在途",
               {"from_holder_id": "bz-farm-01", "to_holder_id": "bz-wholesale-01",
                "quantity": 1000, "unit": "kg", "handover_status": "in_transit"},
               source_department="agriculture"))
    add(_event("lot-veg-01-received", "LOT_TRANSFERRED", "food_lot", "lot-veg-01",
               f"{T}T16:00:00+08:00", 3, "批发市场签收",
               {"from_holder_id": "bz-farm-01", "to_holder_id": "bz-wholesale-01",
                "quantity": 1000, "unit": "kg", "handover_status": "received"},
               source_department="agriculture"))
    # 换包：旧包装消失，来源边保留
    add(_event("lot-veg-01p-relabel", "LOT_RELABELED", "food_lot", "lot-veg-01p",
               f"{T}T16:30:00+08:00", 1, "批发市场更换为“绿色基地直供”包装",
               {"source_lot_ids": ["lot-veg-01"], "new_package_label": "绿色基地直供大白菜",
                "operator_business_id": "bz-wholesale-01",
                "location": "城南批发市场B区17号", "quantity": 1000, "unit": "kg"}))
    add(_event("lot-veg-01p-a-split", "LOT_SPLIT", "food_lot", "lot-veg-01p-a",
               f"{T}T17:00:00+08:00", 1, "拆出300公斤供配送",
               {"parent_lot_id": "lot-veg-01p", "quantity": 300, "unit": "kg"}))
    add(_event("lot-veg-01p-b-split", "LOT_SPLIT", "food_lot", "lot-veg-01p-b",
               f"{T}T17:05:00+08:00", 1, "剩余700公斤留档批发",
               {"parent_lot_id": "lot-veg-01p", "quantity": 700, "unit": "kg"}))
    add(_event("lot-a-cold", "COLD_CHAIN_HANDOVER", "food_lot", "lot-veg-01p-a",
               f"{T}T17:30:00+08:00", 2, "冷链装车交接",
               {"transport_leg_id": "leg-20261003-07",
                "from_party": "bz-wholesale-01", "to_party": "bz-distributor-01",
                "carrier": "鲜达冷链车浙A·7F32", "temp_c": 4.0}))
    add(_event("lot-a-transit", "LOT_TRANSFERRED", "food_lot", "lot-veg-01p-a",
               f"{T}T18:00:00+08:00", 3, "配送发往餐饮方，至次日清晨仍在途",
               {"from_holder_id": "bz-distributor-01",
                "to_holder_id": "bz-restaurant-group-01",
                "quantity": 300, "unit": "kg", "handover_status": "in_transit"}))

    # —— 检测方法与快检 ——
    add(_event("method-rapid-01", "TEST_METHOD_REGISTERED", "test_method", "m-rapid-01",
               f"{T}T08:00:00+08:00", 1, "农药残留快速检测方法登记",
               {"method_id": "m-rapid-01", "standard_no": "KJ-2026-R01",
                "category": "rapid", "analyte": "有机磷类农药残留"},
               source_department="agriculture"))
    add(_event("method-lab-01", "TEST_METHOD_REGISTERED", "test_method", "m-lab-01",
               f"{T}T08:01:00+08:00", 1, "实验室定量检测方法登记",
               {"method_id": "m-lab-01", "standard_no": "GB 23200.121-2026",
                "category": "lab", "analyte": "敌敌畏"},
               source_department="health"))
    add(_event("samp-01-drawn", "SAMPLE_DRAWN", "inspection_sample", "samp-01",
               f"{D}T07:30:00+08:00", 1, "在配送车辆上对在途300公斤批次抽样",
               {"lot_id": "lot-veg-01p-a", "draw_department": "market_regulation",
                "drawn_at": f"{D}T07:30:00+08:00"}))
    add(_event("samp-01-rapid", "SAMPLE_TESTED", "inspection_sample", "samp-01",
               f"{D}T08:00:00+08:00", 2, "快检阳性：有机磷类农药残留",
               {"lot_id": "lot-veg-01p-a", "method_id": "m-rapid-01",
                "category": "rapid", "analyte": "有机磷类农药残留", "result": "positive"}))

    # —— 控制前已售订单（其中一笔为迟到补录） ——
    add(_event("order-1001", "ORDER_RECORDED", "order", "or-1001",
               f"{T}T20:00:00+08:00", 1, "平台订单：白菜半成品",
               {"platform": "味拼外卖", "shop_id": "shop-weipin-01",
                "business_id": "bz-restaurant-group-01",
                "lot_id": "lot-veg-01p-a", "quantity": 2, "unit": "kg",
                "placed_at": f"{T}T20:00:00+08:00", "status": "completed"},
               source_department="platform"))
    add(_event("order-1002", "ORDER_RECORDED", "order", "or-1002",
               f"{T}T21:00:00+08:00", 1, "平台订单（平台延迟至控制后才报送）",
               {"platform": "味拼外卖", "shop_id": "shop-weipin-01",
                "business_id": "bz-restaurant-group-01",
                "lot_id": "lot-veg-01p-a", "quantity": 1, "unit": "kg",
                "placed_at": f"{T}T21:00:00+08:00", "status": "completed"},
               source_department="platform",
               recorded_at=f"{D}T09:30:00+08:00"))
    return events


def _post_control_events() -> list[dict]:
    D = "2026-10-04"
    E = "2026-10-05"
    events = []
    add = events.append

    # 控制生效后仍违规外运 100 公斤；平台仍接单
    add(_event("lot-b-illegal-move", "LOT_TRANSFERRED", "food_lot", "lot-veg-01p-b",
               f"{D}T09:00:00+08:00", 2, "控制生效后商户仍向食堂发运100公斤",
               {"from_holder_id": "bz-wholesale-01", "to_holder_id": "bz-canteen-01",
                "quantity": 100, "unit": "kg", "handover_status": "in_transit"}))
    add(_event("order-1003", "ORDER_RECORDED", "order", "or-1003",
               f"{D}T09:15:00+08:00", 1, "控制后平台仍接单",
               {"platform": "味拼外卖", "shop_id": "shop-weipin-01",
                "business_id": "bz-restaurant-group-01",
                "lot_id": "lot-veg-01p-a", "quantity": 3, "unit": "kg",
                "placed_at": f"{D}T09:15:00+08:00", "status": "placed"},
               source_department="platform"))

    add(_event("recall-01-order", "RECALL_ORDERED", "recall_case", "recall-2026-1004-01",
               f"{D}T10:00:00+08:00", 1, "对在途与在售问题批次下达召回令",
               {"risk_case_id": "risk-2026-1004-01",
                "lot_ids": ["lot-veg-01p-a", "lot-veg-01p-b"]}))
    add(_event("recall-01-rec-1", "RECALL_RECONCILED", "recall_case", "recall-2026-1004-01",
               f"{D}T14:00:00+08:00", 2, "首轮核对：在途300公斤销毁，批发600公斤销毁",
               {"entries": [
                   {"lot_id": "lot-veg-01p-a", "quantity": 300, "unit": "kg",
                    "fate": "destroyed", "handler": "bz-distributor-01"},
                   {"lot_id": "lot-veg-01p-b", "quantity": 600, "unit": "kg",
                    "fate": "destroyed", "handler": "bz-wholesale-01"},
               ]}))
    add(_event("recall-01-rec-2", "RECALL_RECONCILED", "recall_case", "recall-2026-1004-01",
               f"{D}T16:00:00+08:00", 3, "续报：违规外运100公斤追回并无害化处理",
               {"entries": [
                   {"lot_id": "lot-veg-01p-b", "quantity": 100, "unit": "kg",
                    "fate": "harmless_treated", "handler": "bz-canteen-01"},
               ]}))

    # 实验室复检确认阳性：正式结论，控制不解除
    add(_event("samp-01-lab", "LAB_RETEST_RECORDED", "inspection_sample", "samp-01",
               f"{E}T10:00:00+08:00", 3, "实验室定量复检：敌敌畏阳性，正式确认",
               {"original_sample_id": "samp-01", "method_id": "m-lab-01",
                "conclusion": "positive", "value": 0.32, "unit": "mg/kg"},
               source_department="health"))

    # 规则版本、证据、投诉
    add(_event("rule-penalty-v", "RULE_VERSION_PUBLISHED", "rule_set", "rs-food-penalty",
               f"{E}T08:00:00+08:00", 1, "食品安全处罚裁量规则2026版发布",
               {"scope": "food_safety_penalty", "version": "2026.03",
                "effective_at": "2026-03-01T00:00:00+08:00"}))
    add(_event("ev-001-sealed", "EVIDENCE_SEALED", "evidence_item", "ev-sample-01",
               f"{D}T08:30:00+08:00", 1, "样本与快检原始记录封存",
               {"sealed_by": "market_regulation", "custody_location": "市监证据柜A-12",
                "related_case_id": "ec-wholesale-01", "content_hash": "sha256:9a1f...c021"}))
    add(_event("ev-002-sealed", "EVIDENCE_SEALED", "evidence_item", "ev-relabel-01",
               f"{D}T11:00:00+08:00", 1, "换包监控与交易流水封存",
               {"sealed_by": "market_regulation", "custody_location": "市监证据柜A-13",
                "related_case_id": "ec-restaurant-01", "content_hash": "sha256:4b7e...88aa"}))
    add(_event("complaint-01", "COMPLAINT_FILED", "complaint", "comp-01",
               f"{D}T12:00:00+08:00", 1, "群众举报幽灵外卖店铺（举报人要求保密）",
               {"complainant_ref": "姓名/联系方式：138****6621（保密）",
                "business_id": "bz-restaurant-group-01",
                "received_at": f"{D}T12:00:00+08:00",
                "content": "平台“悦味轻食”无实际门店"}))

    # 批发商户案：立案、处罚（引用证据、规则版本、批次）、申诉
    add(_event("ec-ws-open", "ENFORCEMENT_OPENED", "enforcement_case", "ec-wholesale-01",
               f"{E}T09:00:00+08:00", 1, "对旺发蔬菜行换包及违规流动立案",
               {"business_id": "bz-wholesale-01",
                "opened_reason": "问题批次换包且控制后违规外运"}))
    add(_event("ec-ws-decision", "ENFORCEMENT_DECISION", "enforcement_case",
               "ec-wholesale-01", f"{E}T15:00:00+08:00", 2,
               "作出罚款及没收违法所得处罚",
               {"decision_type": "fine_and_confiscation",
                "rule_set_id": "rs-food-penalty", "rule_version": "2026.03",
                "evidence_ids": ["ev-sample-01", "ev-relabel-01"],
                "lot_ids": ["lot-veg-01p-a", "lot-veg-01p-b"]}))
    add(_event("ec-ws-appeal", "APPEAL_FILED", "enforcement_case", "ec-wholesale-01",
               f"{E}T17:00:00+08:00", 3, "商户提交申诉（不停止执行，不改变原决定）",
               {"target_decision_id": "ec-ws-decision",
                "reason": "对换包责任认定有异议",
                "submitted_at": f"{E}T17:00:00+08:00"}))

    # 餐饮主体案：幽灵店铺 + 控制后接单，移送公安
    add(_event("ec-rs-open", "ENFORCEMENT_OPENED", "enforcement_case", "ec-restaurant-01",
               f"{E}T09:10:00+08:00", 1, "对悦味餐饮无证实际场所经营立案",
               {"business_id": "bz-restaurant-group-01",
                "opened_reason": "平台店铺无实际门店且控制后继续接单"}))
    add(_event("ec-rs-decision", "ENFORCEMENT_DECISION", "enforcement_case",
               "ec-restaurant-01", f"{E}T15:30:00+08:00", 2,
               "吊销许可并处罚款",
               {"decision_type": "revocation_and_fine",
                "rule_set_id": "rs-food-penalty", "rule_version": "2026.03",
                "evidence_ids": ["ev-relabel-01"],
                "lot_ids": ["lot-veg-01p-a"]}))
    add(_event("ev-002-handover", "EVIDENCE_HANDOVER", "evidence_item", "ev-relabel-01",
               f"{E}T16:00:00+08:00", 2, "证据随案移送公安，哈希核验一致",
               {"from_party": "market_regulation", "to_party": "police",
                "handler": "周某", "hash_check": "sha256:4b7e...88aa"}))
    add(_event("ec-rs-referral", "CASE_REFERRAL", "enforcement_case", "ec-restaurant-01",
               f"{E}T16:10:00+08:00", 3, "涉嫌犯罪移送公安机关",
               {"to_department": "police", "police_case_no": "A公食刑移〔2026〕042号",
                "evidence_ids": ["ev-relabel-01"]},
               source_department="police"))

    # 企业纠正
    add(_event("ca-01-submit", "CORRECTIVE_ACTION_SUBMITTED", "corrective_action", "ca-01",
               f"{E}T18:00:00+08:00", 1, "批发商户提交换包整改与召回完成报告",
               {"business_id": "bz-wholesale-01", "case_ref": "ec-wholesale-01",
                "description": "停止代客换包，落实进货查验和批次标签留存"},
               source_department="enterprise"))
    add(_event("ca-01-review", "CORRECTIVE_ACTION_REVIEWED", "corrective_action", "ca-01",
               "2026-10-06T10:00:00+08:00", 2, "整改复核通过",
               {"result": "accepted", "reviewer_department": "market_regulation"}))

    # 公众提示只能基于已核实结论
    add(_event("alert-01-pub", "PUBLIC_ALERT_PUBLISHED", "public_alert", "alert-01",
               f"{E}T18:30:00+08:00", 1, "发布正式风险提示",
               {"title": "关于“悦味轻食”问题大白菜的风险提示",
                "basis_event_ids": ["ec-rs-decision"],
                "risk_level": "medium",
                "content": "已查实相关批次农药残留超标，店铺已停止经营；10月3日订单可联系平台退款。"}))
    add(_event("alert-01-update", "PUBLIC_ALERT_UPDATED", "public_alert", "alert-01",
               "2026-10-06T11:00:00+08:00", 2, "召回与整改完成，更新提示状态",
               {"status": "resolved", "basis_event_id": "ca-01-review"}))
    return events


RISK_CASE_ID = "risk-2026-1004-01"
RECALL_CASE_ID = "recall-2026-1004-01"
CONTROLLED_AT = "2026-10-04T08:30:00+08:00"


def build() -> tuple[EventStore, FoodChainGraph, Supervision]:
    store = EventStore()
    for event in _base_events():
        store.append(event)
    graph = FoodChainGraph.from_store(store)
    sup = Supervision(store, graph)
    # 快检阳性 → 立即固化范围快照并通知全部下游
    sup.issue_control(
        RISK_CASE_ID,
        "samp-01",
        ["封存全部在途与在售同链批次", "平台店铺下架停售", "暂停批发商户出库"],
        now=CONTROLLED_AT,
    )
    for event in _post_control_events():
        sup.submit(event)
    return store, graph, sup
