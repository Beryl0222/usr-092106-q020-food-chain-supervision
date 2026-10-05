# 食品全链监督图谱 / 全链监督后端

面向联合食品安全办公室的事件溯源后端：把农业农村、市场监管、卫生健康、公安以及外卖平台
各自掌握的一段数据，接入同一个**只追加、不可改写**的事件仓库，还原从田间到餐桌的责任链。

## 领域规则（代码即约束）

- **电子证照与实地核验分离**：`PREMISES_LICENSED` 与 `PREMISES_VERIFIED` 各自独立；
  外卖平台自检通过（`DELIVERY_PLATFORM_VERIFIED=pass`）但实地为 `no_physical_store` /
  `address_mismatch` 的店铺由 `FoodChainGraph.suspicious_shops()` 标记为幽灵店铺。
- **拆分、混批、换包后双向追溯**：`LOT_SPLIT / LOT_MERGED / LOT_RELABELED /
  PROCESSING_RECORDED` 全部保留来源边。`origin_lots()` 向上溯源，`affected_lots()`
  向下追踪，`same_source_closure()` 覆盖同源兄弟批次（换包后分别流向在途与在售）。
- **快检只触发控制**：`SAMPLE_TESTED(category=rapid,result=positive)` 只能经
  `Supervision.issue_control()` 生成 `RISK_CONTROLLED`，同时固化在途/在售范围快照并
  `DOWNSTREAM_NOTIFIED` 通知全部下游持有者与平台店铺。
- **后续状态不改最初证据**：复检（`LAB_RETEST_RECORDED`）、申诉（`APPEAL_FILED`）、
  迟到订单（信封 `recorded_at` 晚于控制时间）都只追加。解封 `CONTROL_LIFTED` 必须引用
  **同一受控样本**的实验室复检**阴性**结论；阳性正式结论不能解封，快检更不能解封。
- **召回持续核对数量与去向**：`RECALL_RECONCILED` 可多次追加，去向限定为
  销毁/退货/无害化/召回令前已售/缺失；`recall_status()` 按批次对期望量与实报量，
  超报、欠报都视为未闭合。控制后解封前的交接、接单由 `movement_violations()` 检出。
- **处罚可反查**：`ENFORCEMENT_DECISION` 必须引用已封存证据与已发布规则版本；
  `decision_audit()` 返回规则版本事件、证据保管链（每次交接哈希一致才 `chain_intact`）
  与批次的每一次交接（含换包前后、冷链交接）。
- **访问服从法定职责**：见下节。

## 访问角色（`src/access.py`）

| 角色 | 可见范围 |
| --- | --- |
| `agriculture` | 主体登记、批次链、抽样检测、控制与召回、规则与已发布提示 |
| `market_regulation` / `joint_office` | 全量（联合办为跨部门统筹视角） |
| `health` | 抽样检测、控制与召回、规则与已发布提示 |
| `police` | 仅已 `CASE_REFERRAL` 移送的案件、随案证据与涉案主体/受益人/批次 |
| `enterprise(business_id)` | 本主体档案、自有批次、影响本主体的控制/召回、针对本主体的决定、本主体纠正；**不含**举报件、受益人信息、他案信息 |
| `public` | 仅已发布的风险提示及其后续状态 |

字段级遮蔽：举报人身份仅市场监管与联合办可见；受益人信息仅市场监管、联合办、公安（涉案）
可见。未定案执法信息不对企业与其他部门开放；`PUBLIC_ALERT_PUBLISHED` 的依据只能是
已核实事件（实验室复检结论或处罚决定），快检记录永远不会直达公众。

## 模块

- `src/catalog.py`：事件类型—聚合类型—必填载荷目录；`contracts/domain.schema.json` 是其静态镜像。
- `src/validator.py`：信封与目录载荷的静态校验。
- `src/store.py`：不可变事件仓库。`event_id` 跨部门幂等去重；同标识内容不一致即拒绝；
  聚合 `version` 单调递增；支持 JSONL 落盘/回放。
- `src/trace.py`：主体图谱与批次谱系投影、影响面计算、幽灵店铺、受益人关联。
- `src/supervision.py`：业务规则网关（`submit()` 先校验后入库）、控制命令、
  召回核对、违规流动发现、迟到订单、处罚反查。
- `src/access.py`：按法定职责的聚合级可见性与字段级遮蔽、企业看板、公众视图。
- `src/scenario.py`：全链联调场景（田间→运输→批发市场换包→拆分冷链→幽灵店铺→
  快检阳性→通知→违规流动/迟到订单→召回核对→实验室复检→处罚申诉→移送公安→
  企业纠正→公众提示）。

## 数据

- `data/sample.json`：早期最小样例（继续兼容）。
- `data/scenario_chain.json`：由 `src/scenario.build()` 生成的 50 条全链事件，
  可直接用于跨部门联调与回放。

## 本地检查

```bash
python3 -m unittest discover -s tests
```

覆盖：目录与 schema 一致性、事件去重/冲突/版本、换包拆分双向追溯、幽灵店铺、
受益人关联、影响面快照、召回数量去向闭合、控制后违规、迟到订单不可改证、
解封前提、提示依据前提、处罚引用前提、证据链断裂可见、各角色访问边界。
