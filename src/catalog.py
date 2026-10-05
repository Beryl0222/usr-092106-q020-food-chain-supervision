"""全链监督事件目录。

每个事件类型固定其聚合类型与载荷必填字段。仓库与校验都以本目录为准，
contracts/domain.schema.json 是它的静态镜像，由测试保证两者一致。
"""

# 跨部门事件来源
DEPARTMENTS = (
    "agriculture",        # 农业农村部门：产地、农产品批次
    "market_regulation",  # 市场监管部门：许可、经营、抽检、召回
    "health",             # 卫生健康部门：风险监测、食源性调查
    "police",             # 公安机关：移送案件、刑事侦查
    "joint_office",       # 联合食品安全办公室
    "platform",           # 外卖/电商平台
    "enterprise",         # 经营企业自助报送
)

# 聚合类型
AGGREGATES = (
    "food_business",       # 经营主体
    "premises",            # 许可场所（电子证照）与实地核验对象
    "platform_shop",       # 平台店铺
    "food_lot",            # 农产品/食品批次（拆分、混批、换包后仍是同一聚合族）
    "inspection_sample",   # 抽检样本
    "test_method",         # 检测方法
    "order",               # 订单
    "complaint",           # 投诉举报
    "risk_case",           # 风险控制案（快检触发）
    "recall_case",         # 召回案
    "enforcement_case",    # 执法案（处罚、申诉、移送）
    "evidence_item",       # 证据（含保管链）
    "rule_set",            # 规则集（带版本）
    "corrective_action",   # 企业纠正措施
    "public_alert",        # 面向公众的风险提示
)

# (event_type, aggregate_type, payload 必填字段, 说明)
EVENTS = [
    # —— 主体与场所：电子证照与实际经营地点分别核验 ——
    ("SUBJECT_REGISTERED", "food_business",
     ("business_name", "business_type", "department"), "经营主体登记"),
    ("BENEFICIAL_OWNER_DECLARED", "food_business",
     ("owner_name", "declared_at"), "受益人（受益所有人）信息声明"),
    ("PREMISES_LICENSED", "premises",
     ("premises_id", "business_id", "address", "license_no", "scope"), "许可场所电子证照签发"),
    ("PREMISES_VERIFIED", "premises",
     ("premises_id", "result", "verifier_department", "checked_at"),
     "实地核查，结果与证照相互独立：pass/address_mismatch/no_physical_store/fail"),
    ("PLATFORM_SHOP_LISTED", "platform_shop",
     ("platform", "shop_name", "business_id", "claimed_premises_id"), "平台店铺上线及其声称的线下场所"),
    ("DELIVERY_PLATFORM_VERIFIED", "platform_shop",
     ("platform", "result", "verified_at"), "外卖平台自身核验结果"),

    # —— 批次：拆分、混批、换包、加工分装、冷链交接 ——
    ("LOT_REGISTERED", "food_lot",
     ("product_name", "producer_business_id", "quantity", "unit"), "农产品/食品批次登记（产地）"),
    ("LOT_SPLIT", "food_lot",
     ("parent_lot_id", "quantity", "unit"), "批次拆分，聚合为拆出的新批次"),
    ("LOT_MERGED", "food_lot",
     ("source_lot_ids", "quantities", "unit"), "混批，聚合为混批后的新批次"),
    ("LOT_RELABELED", "food_lot",
     ("source_lot_ids", "new_package_label", "operator_business_id", "location"),
     "更换包装（如批发市场换包），聚合为换包后的新批次，来源必须保留"),
    ("PROCESSING_RECORDED", "food_lot",
     ("input_lot_ids", "processor_business_id", "job_type", "quantity", "unit"),
     "加工/分装记录，聚合为产出批次"),
    ("LOT_TRANSFERRED", "food_lot",
     ("from_holder_id", "to_holder_id", "quantity", "unit", "handover_status"),
     "批次交接（批发、配送、上架），handover_status=in_transit/received"),
    ("COLD_CHAIN_HANDOVER", "food_lot",
     ("transport_leg_id", "from_party", "to_party", "carrier"), "冷链交接与运输条件记录"),

    # —— 抽检与检测：快检只触发控制，正式结论保留复核路径 ——
    ("SAMPLE_DRAWN", "inspection_sample",
     ("lot_id", "draw_department", "drawn_at"), "抽样"),
    ("TEST_METHOD_REGISTERED", "test_method",
     ("method_id", "standard_no", "category", "analyte"), "检测方法登记，category=rapid/lab"),
    ("SAMPLE_TESTED", "inspection_sample",
     ("lot_id", "method_id", "category", "analyte", "result"),
     "检测结果；category=rapid 且 result=positive 仅触发控制措施"),
    ("LAB_RETEST_RECORDED", "inspection_sample",
     ("original_sample_id", "method_id", "conclusion"), "实验室复检/正式结论，conclusion=positive/negative"),

    # —— 风险控制与召回：通知、在途在售范围、数量去向持续核对 ——
    ("RISK_CONTROLLED", "risk_case",
     ("source_sample_id", "lot_id", "measures"), "快检阳性触发控制措施，并固化在途/在售范围快照"),
    ("DOWNSTREAM_NOTIFIED", "risk_case",
     ("notice_targets",), "通知所有下游持有者与渠道"),
    ("CONTROL_LIFTED", "risk_case",
     ("basis_event_id", "reason"), "依据正式结论解封；快检本身不能解封"),
    ("RECALL_ORDERED", "recall_case",
     ("risk_case_id", "lot_ids"), "召回令"),
    ("RECALL_RECONCILED", "recall_case",
     ("entries",), "召回数量与去向核对（销毁/退货/无害化/责令前已售/缺失），可多次追加"),

    # —— 订单与投诉：迟到订单只能形成后续状态 ——
    ("ORDER_RECORDED", "order",
     ("platform", "shop_id", "lot_id", "quantity", "placed_at", "status"),
     "订单记录；封存后补录的订单以 recorded_at 标记为迟到追加"),
    ("COMPLAINT_FILED", "complaint",
     ("complainant_ref", "business_id", "received_at"), "投诉举报，举报人信息受保护"),

    # —— 执法、证据、规则：处罚可反查证据保管、规则版本与每次交接 ——
    ("ENFORCEMENT_OPENED", "enforcement_case",
     ("business_id", "opened_reason"), "立案"),
    ("ENFORCEMENT_DECISION", "enforcement_case",
     ("decision_type", "rule_set_id", "rule_version", "evidence_ids"),
     "处罚决定；引用规则版本与证据清单，未定案前不得对外披露"),
    ("APPEAL_FILED", "enforcement_case",
     ("target_decision_id", "reason", "submitted_at"), "申诉，只追加后续状态，不改原决定"),
    ("CASE_REFERRAL", "enforcement_case",
     ("to_department", "police_case_no", "evidence_ids"), "移送公安并联警方案号"),
    ("EVIDENCE_SEALED", "evidence_item",
     ("sealed_by", "custody_location", "related_case_id", "content_hash"), "证据封存入保管链"),
    ("EVIDENCE_HANDOVER", "evidence_item",
     ("from_party", "to_party", "handler", "hash_check"), "证据保管链上的每次交接"),
    ("RULE_VERSION_PUBLISHED", "rule_set",
     ("scope", "version", "effective_at"), "规则版本发布"),
    ("CORRECTIVE_ACTION_SUBMITTED", "corrective_action",
     ("business_id", "case_ref", "description"), "企业提交纠正措施"),
    ("CORRECTIVE_ACTION_REVIEWED", "corrective_action",
     ("result", "reviewer_department"), "纠正措施复核结果 accepted/rejected"),

    # —— 公众沟通：只发布核实后的风险提示 ——
    ("PUBLIC_ALERT_PUBLISHED", "public_alert",
     ("title", "basis_event_ids", "risk_level", "content"), "发布风险提示，依据必须是已核实事件"),
    ("PUBLIC_ALERT_UPDATED", "public_alert",
     ("status", "basis_event_id"), "风险提示后续状态（澄清/解除），不删除原提示"),

    # —— 早期资料中已存在的事件名，继续兼容 ——
    ("LICENSE_VERIFIED", "food_business", (), "兼容早期资料的证照核验记录"),
]

EVENT_TYPES = tuple(name for name, _agg, _req, _doc in EVENTS)

_BY_TYPE = {name: (agg, req, doc) for name, agg, req, doc in EVENTS}


def aggregate_of(event_type: str) -> str | None:
    entry = _BY_TYPE.get(event_type)
    return entry[0] if entry else None


def required_payload(event_type: str) -> tuple[str, ...]:
    entry = _BY_TYPE.get(event_type)
    return entry[1] if entry else ()


def describe(event_type: str) -> str:
    entry = _BY_TYPE.get(event_type)
    return entry[2] if entry else ""
