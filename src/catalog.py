"""领域事件目录：聚合类型、事件名称与受控词表。

事件一旦写入仓库即为事实，不可原地修改；业务更正（复检、申诉、纠正、
迟到订单等）只能产生后继事件，前驱事件的标识与载荷始终保留。

跨部门对接时同一仓库事件可能由不同部门重复上报，统一使用
``event_id``（仓库事件标识）去重；各部门本地系统编号放入
``payload.source_record_id``，不参与幂等判定。
"""

# ---------------------------------------------------------------- 聚合类型

AGG_BUSINESS = "food_business"          # 经营主体（含受益人关联）
AGG_PREMISES = "licensed_premises"      # 许可场所（实际经营地点）
AGG_SHOP = "platform_shop"              # 平台店铺
AGG_LOT = "food_lot"                    # 农产品/食品批次
AGG_HANDOFF = "cold_chain_handoff"      # 冷链/物流交接单
AGG_SAMPLE = "inspection_sample"        # 抽检样本
AGG_METHOD = "test_method"              # 检测方法（标准）
AGG_ORDER = "food_order"                # 订单
AGG_COMPLAINT = "complaint"             # 投诉举报
AGG_CASE = "enforcement_case"           # 执法案件/处罚决定
AGG_RECALL = "recall_case"              # 召回/控制案件
AGG_RULE = "risk_rule"                  # 风险规则（带版本）
AGG_METHOD_VER = "method_version"       # 检测方法版本登记（独立聚合）

AGGREGATE_TYPES = frozenset({
    AGG_BUSINESS, AGG_PREMISES, AGG_SHOP, AGG_LOT, AGG_HANDOFF,
    AGG_SAMPLE, AGG_METHOD, AGG_ORDER, AGG_COMPLAINT, AGG_CASE,
    AGG_RECALL, AGG_RULE, AGG_METHOD_VER,
})

# ---------------------------------------------------------------- 事件名称

# 主体与证照
EVT_BUSINESS_REGISTERED = "BUSINESS_REGISTERED"
EVT_BENEFICIAL_OWNER_LINKED = "BENEFICIAL_OWNER_LINKED"
EVT_LICENSE_ISSUED = "LICENSE_ISSUED"
EVT_LICENSE_VERIFIED = "LICENSE_VERIFIED"              # 电子证照核验
EVT_PREMISES_VERIFIED = "PREMISES_VERIFIED"            # 实际经营地点核验
EVT_SHOP_LISTED = "SHOP_LISTED"                        # 平台开店（必须挂接主体）
EVT_SHOP_BINDING_REVIEWED = "SHOP_BINDING_REVIEWED"    # 店铺-主体/场所核验结论

# 批次与交接
EVT_LOT_HARVESTED = "LOT_HARVESTED"                    # 田间/生产批次建档
EVT_LOT_PACKED = "LOT_PACKED"                          # 加工分装（可拆分）
EVT_LOT_RELABELED = "LOT_RELABELED"                    # 换包/重新包装（不切断谱系）
EVT_LOT_TRANSFERRED = "LOT_TRANSFERRED"                # 冷链/物流交接（含运输违规）
EVT_LOT_MERGED = "LOT_MERGED"                          # 混批
EVT_LOT_SPLIT = "LOT_SPLIT"
EVT_LOT_HELD = "LOT_HELD"                              # 先行登记保存/扣押（证据保管）
EVT_LOT_DISPOSITIONED = "LOT_DISPOSITIONED"            # 无害化处理/退回/解封

# 平台与订单
EVT_ORDER_PLACED = "ORDER_PLACED"
EVT_ORDER_FULFILLED = "ORDER_FULFILLED"
EVT_ORDER_DELIVERED = "ORDER_DELIVERED"
EVT_ORDER_LATE = "ORDER_LATE"                          # 迟到订单：后续状态
EVT_ORDER_RECALLED_FROM_CONSUMER = "ORDER_RECALLED_FROM_CONSUMER"

# 检验检测
EVT_METHOD_PUBLISHED = "METHOD_PUBLISHED"
EVT_SAMPLE_DRAWN = "SAMPLE_DRAWN"
EVT_SAMPLE_TESTED = "SAMPLE_TESTED"                    # 快检/实验室结论
EVT_SAMPLE_RETESTED = "SAMPLE_RETESTED"                # 复检
EVT_SAMPLE_APPEAL_DECIDED = "SAMPLE_APPEAL_DECIDED"    # 申诉复核决定

# 风险控制与召回
EVT_RISK_CONTROLLED = "RISK_CONTROLLED"                # 快检阳性 → 控制措施
EVT_RISK_CLEARED = "RISK_CLEARED"                      # 正式结论：解除（保留复核路径）
EVT_RECALL_OPENED = "RECALL_OPENED"
EVT_DOWNSTREAM_NOTIFIED = "DOWNSTREAM_NOTIFIED"
EVT_RECALL_REPORTED = "RECALL_REPORTED"                # 企业提交纠正/召回进展
EVT_RECALL_RECONCILED = "RECALL_RECONCILED"            # 核对数量与去向
EVT_RECALL_CLOSED = "RECALL_CLOSED"
EVT_PUBLIC_ALERT_PUBLISHED = "PUBLIC_ALERT_PUBLISHED"  # 仅核实后
EVT_PUBLIC_ALERT_RETRACTED = "PUBLIC_ALERT_RETRACTED"

# 投诉与执法
EVT_COMPLAINT_FILED = "COMPLAINT_FILED"
EVT_CASE_OPENED = "CASE_OPENED"
EVT_ENFORCEMENT_DECIDED = "ENFORCEMENT_DECIDED"        # 处罚决定
EVT_CORRECTION_SUBMITTED = "CORRECTION_SUBMITTED"      # 企业纠正
EVT_EVIDENCE_LOCKED = "EVIDENCE_LOCKED"                # 证据保管登记
EVT_RULE_PUBLISHED = "RULE_PUBLISHED"                  # 规则版本
EVT_CONTROL_BREACH_RECORDED = "CONTROL_BREACH_RECORDED"  # 控制令期间违规

EVENT_TYPES = frozenset({
    EVT_BUSINESS_REGISTERED, EVT_BENEFICIAL_OWNER_LINKED,
    EVT_LICENSE_ISSUED, EVT_LICENSE_VERIFIED, EVT_PREMISES_VERIFIED,
    EVT_SHOP_LISTED, EVT_SHOP_BINDING_REVIEWED,
    EVT_LOT_HARVESTED, EVT_LOT_PACKED, EVT_LOT_RELABELED,
    EVT_LOT_TRANSFERRED, EVT_LOT_MERGED, EVT_LOT_SPLIT,
    EVT_LOT_HELD, EVT_LOT_DISPOSITIONED,
    EVT_ORDER_PLACED, EVT_ORDER_FULFILLED, EVT_ORDER_DELIVERED,
    EVT_ORDER_LATE, EVT_ORDER_RECALLED_FROM_CONSUMER,
    EVT_METHOD_PUBLISHED, EVT_SAMPLE_DRAWN, EVT_SAMPLE_TESTED,
    EVT_SAMPLE_RETESTED, EVT_SAMPLE_APPEAL_DECIDED,
    EVT_RISK_CONTROLLED, EVT_RISK_CLEARED,
    EVT_RECALL_OPENED, EVT_DOWNSTREAM_NOTIFIED,
    EVT_RECALL_REPORTED, EVT_RECALL_RECONCILED, EVT_RECALL_CLOSED,
    EVT_PUBLIC_ALERT_PUBLISHED, EVT_PUBLIC_ALERT_RETRACTED,
    EVT_COMPLAINT_FILED, EVT_CASE_OPENED, EVT_ENFORCEMENT_DECIDED,
    EVT_CORRECTION_SUBMITTED, EVT_EVIDENCE_LOCKED, EVT_RULE_PUBLISHED,
    EVT_CONTROL_BREACH_RECORDED,
})

# 向后兼容：旧信封校验曾使用的 5 个名称仍在目录内。
_LEGACY_EVENTS = frozenset({
    EVT_LICENSE_VERIFIED, EVT_LOT_TRANSFERRED, EVT_SAMPLE_TESTED,
    EVT_RISK_CONTROLLED, EVT_RECALL_RECONCILED,
})
assert _LEGACY_EVENTS <= EVENT_TYPES

# ---------------------------------------------------------------- 受控词表

DEPARTMENTS = ("agriculture", "market_regulation", "health", "public_security", "joint_office")
"""农业农村、市场监管、卫生健康、公安、联合食品安全办公室。"""

CONTROL_DECISIONS = ("hold", "stop_sale", "recall", "seal", "no_action")
"""快检阳性可触发的控制措施；``no_action`` 仅允许出现在阴性记录中。"""

TEST_KINDS = ("rapid", "laboratory", "retest")
TEST_RESULTS = ("positive", "negative", "inconclusive")

SAMPLE_STATUSES = ("drawn", "screened", "retested", "appeal_decided", "finalized")

LOT_STATUSES = ("active", "controlled", "recalled", "released", "disposed")

SHOP_REVIEW_RESULTS = ("pass", "fail_no_premises", "fail_license_mismatch", "recheck")

ORDER_STATUSES = ("placed", "fulfilled", "delivered", "recalled_from_consumer")

CASE_STATUSES = ("opened", "penalized", "closed")

ALERT_STATUSES = ("published", "retracted")

# 哪些事件天然携带“需要遮蔽的个人信息”
PII_BEARING_EVENTS = frozenset({
    EVT_BENEFICIAL_OWNER_LINKED, EVT_COMPLAINT_FILED,
    EVT_ORDER_PLACED, EVT_ORDER_DELIVERED, EVT_ORDER_RECALLED_FROM_CONSUMER,
})

# 未定案前属于商业敏感、不得对公众/其他企业开放的事件
PRE_DECISION_COMMERCIAL_EVENTS = frozenset({
    EVT_SAMPLE_TESTED, EVT_SAMPLE_RETESTED, EVT_RISK_CONTROLLED,
    EVT_CASE_OPENED, EVT_RECALL_OPENED, EVT_LOT_HELD,
})
