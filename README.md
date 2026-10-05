# 食品全链监督图谱 · 全链监督后端

面向联合食品安全办公室的**事件溯源（event-sourcing）后端**：农业农村、市场监管、
卫生健康、公安四个部门的数据统一为只追加领域事件流，还原“田间 → 分装/混批/换包 →
冷链交接 → 批发市场 → 平台店铺 → 订单 → 餐桌”的完整责任链。

纯 Python 3.11 标准库实现，无第三方依赖；事件持久化为 JSONL，重启自动重放。

## 设计要点如何对应业务要求

| 业务要求 | 落地方式 |
| --- | --- |
| 跨部门数据各持一段、重复上报 | 统一仓库事件标识 `event_id` 幂等去重（内容不同的同号事件直接拒绝）；部门本地编号放 `source_record_id` |
| 初证不可改，复检/申诉/迟到订单只能形成后续状态 | 只追加存储，无更新/删除接口；聚合版本严格递增；`SAMPLE_RETESTED`、`SAMPLE_APPEAL_DECIDED`、`ORDER_LATE` 均为后继事件 |
| 电子证照与实际门店分别核验 | `LICENSE_VERIFIED` 与 `PREMISES_VERIFIED` 独立；`/shops/{id}/dossier` 给出 `ghost_kitchen_suspected` 画像 |
| 拆分、混批、换包后双向追溯 | 谱系有向图（packed/split/merge），换包只追加标签历史不换批次；`/lots/{id}/trace` 同时给 upstream/downstream |
| 数量守恒 | 分装/混批输出合计不得超过母批余量；交接/履约校验持仓 |
| 快检阳性立即算范围 | `policy.affected_scope`：在途（冷链承运方持仓）、在售、待履约、已送达、应通知下游、建议控制措施 |
| 快检只触发控制，正式结论保留复核 | `RISK_CONTROLLED` 只接受阳性触发且引用已发布规则版本；解除只能凭复检阴性或申诉成立（`RISK_CLEARED`） |
| 风险批次不得重新流通 | 控制令覆盖批次（含全部下游）冻结，出餐/发运/换包指令返回 **423 Locked**；现场查实的违规流转以 `observed_violation` 登记 breach，但不转移合法持仓 |
| 通知所有下游并持续核对召回数量去向 | `DOWNSTREAM_NOTIFIED` 去重；`/recalls/{id}` 按批物料平衡（建档量 = 在库 + 已处置 + 已交子批 + 企业上报去向），另核已售订单的追回/食用/无法追回覆盖 |
| 企业提交纠正、跟踪召回 | `CORRECTION_SUBMITTED`、`RECALL_REPORTED`；关闭前必须有 `RECALL_RECONCILED` |
| 公众只看核实后的风险提示 | 公众仅能读 `PUBLIC_ALERT_PUBLISHED`，且发布前必须已有数量核对；`/alerts` |
| 举报人/受益人/消费者保护 | `access.redact_event` 按部门法定职责遮蔽 PII；受益人信息不向企业开放 |
| 未定案商业信息保护 | 企业只能读自有/持有批次及其一阶谱系；样本、控制、案件等不跨企业可见 |
| 处罚反查证据、规则版本、每次交接 | 处罚必须引用已 `EVIDENCE_LOCKED` 的事件和已发布规则版本；`/cases/{id}/audit` 串联 决定→规则→保管链→被证事件→批次交接链 |

## 模块

- `src/catalog.py`：聚合、事件、受控词表（五部门、控制措施、检测类型…）
- `src/validator.py`：信封 + 各事件载荷的写入前形态校验
- `src/store.py`：只追加 JSONL 仓库（幂等去重、聚合版本流）
- `src/projection.py`：重放投影——主体/证照/店铺、谱系与守恒、持仓、冻结、
  样本复核、召回、投诉、案件、证据、规则索引
- `src/policy.py`：阳性应急范围、控制建议、召回物料平衡
- `src/access.py`：法定职责读写矩阵与 PII/商业信息遮蔽
- `src/app.py`：事务门面（授权 → 业务校验 → 落库 → 投影）
- `src/server.py`：薄 HTTP 接口
- `src/demo.py`：端到端叙事场景（幽灵店铺、运输违规、换包、阳性应急、
  冻结拦截、在途拦截、召回平衡、申诉、处罚反查、访问遮蔽）

## 事件契约

`contracts/domain.schema.json` 与 `src/catalog.py` 保持同步；每个事件的载荷
必填字段与枚举见 `src/validator.py:PAYLOAD_RULES`。测试会校验两者枚举一致。

## 运行

```bash
# 端到端演示
python3 -m src.demo

# HTTP 服务
python3 -m src.server --port 8080 --db data/eventlog.jsonl

# 身份通过请求头传递（生产环境应由网关注入）
curl -s localhost:8080/alerts
curl -s -H 'X-Actor-Kind: department' -H 'X-Actor-Id: market_regulation' \
  localhost:8080/samples/S-1/scope

# 测试
python3 -m unittest discover -s tests -v
```

## 访问者与部门

- 部门：`agriculture`（农业农村）、`market_regulation`（市场监管）、
  `health`（卫生健康）、`public_security`（公安）、`joint_office`（联合食安办）
- `business`（经营主体）、`platform`（外卖/电商平台）、`public`（公众，仅提示与举报）

## 边界与取舍

- 投影为内存态、单进程写锁；事件库是 JSONL，适合作为领域内核与联调参考，
  规模化部署时替换 `EventStore` 实现即可（接口只有 append/stream/next_version）。
- 身份头是演示级机制；生产需接入统一身份认证与部门职责数据源。
- “在途”以冷链承运方持仓表达（交接给承运方即产生其持仓，运抵后归接收方），
  从而在途拦截、通知承运方、销毁数量都能进入同一物料平衡。
