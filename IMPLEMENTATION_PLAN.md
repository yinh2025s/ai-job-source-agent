# AI Job Source Agent Implementation Plan

本文档记录当前项目进度、已经实现的能力、仍然不完整的部分，以及后续补齐计划。它的目的不是包装结果，而是让后续开发和汇报都能围绕清晰的工程路线推进。

项目变更记录、稳定架构边界和决策依据分别由 `CHANGELOG.md`、`docs/ARCHITECTURE.md`、`DEVELOPMENT_GOVERNANCE.md` 和 `docs/adr/` 管理。路线图发生变化时必须同步这些文档，不能只修改代码或只更新计划。

## 目标

从 LinkedIn job search 或预处理后的公司列表出发，自动发现：

1. 招聘公司
2. 公司官网或母公司招聘体系
3. 官方 career/job list 页面
4. 尽可能匹配 LinkedIn job title 的具体开放岗位 URL

系统应优先保证：

- 不乱编 URL
- 不把 404/error page 当作成功
- 对失败有结构化错误和 trace
- 能逐步扩展到不同 ATS/hiring platform 的专用 adapter
- 任意一家公司失败时，都能明确知道失败关卡、失败原因和是否可重试
- 已完成的关卡可以复用，修复后不必每次从头运行
- 用固定 benchmark 和失败分布决定开发优先级，而不是按遇到公司的先后顺序打补丁

## 核心工程原则

本项目不是以“每家公司永远 100% 抓取成功”为可控目标，而是分三层衡量：

1. **覆盖率**：目标公司使用的主要 ATS/provider 有多大比例已被支持。
2. **可靠性**：已声明支持的 provider 在固定样本上的成功率是否稳定。
3. **失败可治理性**：所有失败是否都能定位、归类，并进入重试、适配或明确不支持的处理路径。

因此，每家公司每次运行都必须得到一种明确结果：

- 成功：得到经过验证的 career page、job board 或具体 opening。
- 部分成功：前置关卡成功，但目标 opening 未找到；保留可信的 job board fallback。
- 临时失败：网络超时、限流或上游暂时异常，可以重试。
- 能力缺失：识别出尚未支持的 provider 或页面变体，进入 adapter backlog。
- 外部阻塞：登录墙、验证码、robots/权限限制等当前无法自动完成。
- 正常空结果：公司没有公开职位、目标职位已下线，不能当成系统 Bug。

## 当前稳定化阶段（2026-07-14）

### 阶段结论

从本节生效起，暂停“看到一个失败样本就继续增加 heuristic、provider 或公司规则”的开发方式。当前主线进入 correctness-first stabilization；在本节验收标准全部满足前：

- 不新增 provider。
- 不增加 company-specific branch 或单公司例外。
- 不以提高当前 cohort 的表面 exact rate 为目标。
- 不扩大 `pipeline.py` 的职责；跨 stage 行为必须先冻结 contract。
- 不把 provider benchmark、focused replay、已观察样本或预填 website/career root 的结果称为 blind success。

触发本阶段的事实基线：

- 最新动态 LinkedIn 查询请求 30 家，实际只冻结到 24 家；原始漏斗为 22 website、16 career page、13 job list、7 exact opening。
- 17 个 non-exact 不能统一视为系统缺陷，必须区分正常关闭、无公开岗位、招聘客户未披露、外部阻塞、临时失败和真实 system gap。
- `/private/tmp/.89-budget2-results.json` 中 Fresh Ventures 被错误匹配到 Notion Ashby tenant 的 `Software Engineer, New Grad` opening，S5-S7 却全部成功。这证明当前 exact success 缺少连续 identity evidence chain，历史单一 success-rate 指标不能证明正确性。
- 25/25 provider fixture benchmark 主要证明 adapter 回归稳定，不证明陌生公司的 website/career/tenant discovery 泛化能力。
- `pipeline.py` 仍集中承担 S4-S6 多项职责，legacy `JobSourceAgent.discover()` 与 production `PipelineApplication` 尚未完全收口；本轮只做必要的 contract extraction，不全面重写。

### 稳定化执行状态

本轮 contract implementation 与 replay 发布闭环均已完成，相关集成提交包括 `b810282`、`81bd207`、`76f2de8`、`a99181d` 和 `1679779`。当前状态不是继续扩展 provider，而是等待独立标注后再决定下一轮：

| 范围 | 状态 | 已验证事实 / 剩余动作 |
| --- | --- | --- |
| Opening identity continuity | 已实现 | S3-S6 传递 typed hiring/provider/opening identity；S7 独立 fail-closed，并抑制被拒绝的 exact URL |
| Availability 与 typed error | 已实现 | complete/incomplete/retryable 语义收口；fetch reason、retryability、status 与 request provenance 不再降级为普通 not-found |
| Evaluation contract | 已闭环 | 六类 disposition 与显式 eligibility 已落地；30/30 冻结记录已完成独立于 runtime gate 的 artifact/official-evidence review，并由 SHA-256 绑定工具安全合并 |
| Replay identity outcome gate | 已闭环 | Scoped bundle v7 比较 terminal semantic、identity verdict、failure code 和规范化 identity chain；同 stage rerun 只消费最终 lineage sequence bounds，保留 v6 scoped evidence 读取兼容 |
| 离线门禁 | 已通过 | 1405 tests、provider 25/25、resolver 6/6、architecture 26 adapters / 0 issues |
| 冻结 live gate | 已执行一次 | observed 30-company cohort 已完成；不重跑、不调参后包装为同一轮结果，现有 artifact 用于离线 replay 闭环 |

本轮没有新增 provider，也没有加入公司特例。历史 `.89` 入口改动已在 identity gate 下集成，不再属于“待提交能力”。最终完整 30 条 bundle 达到 30/30 reproduced，均为 record integrity passed、0 fixture gap、0 mismatch。独立标注也已完成，稳定化实现与可信评测阶段均已封板。

## 下一阶段：Blind Holdout 产品基线（2026-07-15）

稳定化反馈要求的 correctness contract 已经封板。下一步不再根据已知失败继续增加
heuristic、provider 或公司特例，而是建立第一批真正陌生、不可回看的产品基线。
本阶段用于回答“系统面对从未用于开发的真实 LinkedIn 输入时有多可靠”，并取代
provider/replay 通过率作为对外成功率依据。

### 阶段硬约束

1. 通过 S1-only LinkedIn public search 收集候选；收集阶段不得调用 S2-S7，不得预填
   website、career、board、opening 或 external apply URL。
2. 从 30-50 家唯一公司中冻结 cohort。候选公司名和 LinkedIn job ID 必须在当前仓库、
   指定历史 artifact root 和完整 Git patch history 中均未出现；缺失历史 root、脏的
   tracked worktree 或不可验证的代码身份一律 fail closed。
3. 冻结 manifest 必须绑定候选池、规范化 cohort、身份表、运行配置、Git commit、
   source tree 和历史审计摘要。冻结后不得修改 production code、测试、run config 或
   cohort；任何变化必须废弃该 cohort，并重新开始一个新的 blind 版本。
4. 只允许一次完整 live execution。one-shot ledger 在发起请求前原子消费；即使进程失败，
   cohort 也从 `blind_unseen` 变为 `blind_observed`，不得重跑后仍称同一次 blind evaluation。
5. Result、trace、summary 和 execution manifest 必须形成摘要链；result/trace 的 company、
   source job、website、career、board、provider、opening、status 和 stages 发生语义漂移时
   整轮报告 fail closed。
6. Codex artifact review 与 human evaluation review 独立生成。人类 review 是唯一指标标签
   authority，必须使用 reviewer 自有 SSH key 对原始 manifest 做 detached signature；可编辑的
   reviewer name、Boolean attestation 或 Codex 生成的 hash 不能证明人工身份。
7. 所有 exact URL 必须人工核验正确 company/hiring entity relationship、provider tenant、
   canonical board、title、location 和公开可访问性；opening、board 和招聘主体证据必须分别
   记录，且 opening/board evidence URL 与被评分 URL 一致。
8. 本阶段不新增 provider、不加公司规则、不调参恢复数字。exact rate 下降可以接受；跨公司
   URL 或未经验证的 exact success 不可接受。

### 执行清单

| Gate | 状态 | 产物 / 验收标准 |
| --- | --- | --- |
| B0 审计并冻结 blind contract | 已完成 | one-shot runner、历史审计、execution chain、独立 review schema 和攻击性 tests |
| B1 离线门禁与 prep commit | 门禁已通过，待提交 | 1413 tests、provider 25/25、resolver 6/6、architecture 26/0；提交后验证 tracked tree clean |
| B2 S1-only 候选与 unseen audit | 待开始 | 至少 30、最多 50 家；0 historical company/job overlap；0 discovery-answer prefill |
| B3 冻结 cohort | 待开始 | cohort、holdout manifest、run config 和 source identity digest 全部锁定 |
| B4 one-shot live execution | 待开始 | 串行运行一次；ledger、execution manifest 和 results/trace/summary digest chain 完整 |
| B5 双轨独立审查 | 待开始 | Codex review 与 SSH-signed human review 分离；每个 exact URL 完成人工多维核验 |
| B6 基线报告 | 待开始 | 同时报 raw exact、exact precision、conditional exact recall、system defect 和六类 disposition |
| B7 阶段停止点 | 待开始 | 发布剩余 failure clusters 和最多三个按覆盖样本数 x 风险 x 收益排序的候选任务；不自动修复 |

### Blind 基线报告规则

- `exact_precision` 的分母只能是系统输出的 exact opening，分子必须是人工验证通过完整 identity
  chain 且当前公开可访问的 URL；目标仍为至少 98%，错误公司 URL 必须为 0。
- `conditional_exact_recall` 只计算人工确认存在 eligible public official opening 的记录；
  unknown eligibility 不得强行进入分母。
- `raw_exact_rate` 使用全部冻结输入，同时必须展示 `exact_public`、`verified_closed`、
  `no_public_opening`、`recruiter_client_undisclosed`、`external_blocked`、`system_gap` 分布。
- `system_defect_rate` 单独统计错误 company/tenant/URL、parser bug、错误失败分类和可恢复 transport
  failure。provider fixture、focused replay、observed cohort 和预填 discovery benchmark 不得混入。

完成 B6 后先停下来审查结果。任何后续修复都属于新迭代，并使用新的 blind holdout；本次已经
观察的 cohort 只能作为 regression cohort，不能再次用于产品泛化声明。

### 冻结 Observed Cohort 结果

本次只运行了一次冻结的、开发者已观察过的 30-company cohort，因此只能标记为 `frozen_observed`，不能称为 blind、unfamiliar holdout 或独立精度验收。

| 指标 | 结果 | 可报告性 |
| --- | --- | --- |
| Exact URL / raw exact rate | 19/30 | 可报告，但必须与失败分布同时出现 |
| Pipeline status | 19 success / 5 partial / 6 failed | 可报告 |
| Identity verdict | 19 verified / 6 not_applicable / 5 rejected | 可报告；这是 runtime gate 结果，不等于人工 URL precision |
| Exact precision | 19/19（100.0%） | 由 runtime gate 外的 frozen artifact/official evidence review 得出；只适用于 observed cohort |
| Conditional exact recall | 19/24（79.2%） | 24 条 confirmed eligible；Deloitte、Akkodis 的 eligibility 保持 unknown |
| System defect rate | 7/30（23.3%） | 5 个 identity-evidence false negative，加 2 个 retryable transport gap |

当前 failure clusters：

- Provider relationship unverified（4）：Dematic / KION Workday、Quest Global / Phenom、ReturnPro / Paycom、Adobe / Phenom。
- Opening identity missing（1）：Awesome Motive 的 generic first-party 到 Workable handoff。
- Opening not found（3）：Viking、GPTZero、Percepta；在 inventory/eligibility 外部复核前不自动视为系统缺陷。
- Ambiguous hiring identity（1）：Aventis；不得猜测未披露招聘客户。
- Retryable network timeout（2）：Deloitte、Akkodis；保留 retryable 语义，不转成 not-found。

自动 replay 在读取完整 live artifact 时发现 same-stage rerun 产生重复 ordinal。主线现已只用最终 lineage 的 sequence bounds 排除同 scope 的 orphan record，并从既有 trace/snapshot 离线重建 bundle；完整 30 条与 11 条失败子集分别 30/30、11/11 reproduced。外部返回的非标准 response status `999` 作为 typed transport evidence 原样保留。整个修复没有重新运行 live cohort。

### 阶段入口工作区

进入本阶段时的未提交 `.89` 工作必须完整保留并逐项审查，不得 reset、checkout 或用格式化覆盖：

| 未完成工作 | 文件范围 | 稳定化处理 |
| --- | --- | --- |
| listing/hydration 与 semantic-card extraction | `listing_extraction.py`、`card_listing_extraction.py` 及测试 | 只在 identity gate 后保留；不得仅凭 title/URL pair 产生跨 tenant exact success |
| Career 首页和单数 `Career` evidence | `pipeline.py`、`test_career_surface_detection.py` | 作为 S4 evidence 审查，不得隐式授权任意下游 provider tenant |
| opening incomplete reason contract | `reasons.py`、`opening_availability.py`、`stages/discovery.py`、`evaluation.py` 及测试 | 与 replay/outcome contract 一起冻结，不能只改旧断言求绿 |
| deadline publication reserve | `live_batch_eval.py` 及测试 | 保留为 runner reliability 变更，独立验证，不参与 exact identity 判定 |
| `.89` adapter version | `checkpoint.py` | 最终 contract 确定后统一失效；当前不是发布完成标志 |

上述入口变化已经过 P0 identity contract 集成；最终发布状态仍取决于 replay sequence-bound 修复和修复后的统一离线门禁，而不是 live exact 数量。

### P0-A：Opening Identity Continuity Contract

exact success 必须存在以下连续、可验证且版本化的 identity chain：

```text
source company identity
  -> resolved hiring entity / verified relationship
  -> official career source
  -> provider + tenant + canonical board
  -> opening provider + tenant + canonical board
  -> validated opening URL
```

冻结规则：

1. S3 必须输出招聘主体，或明确记录继续使用 source company；母公司、收购品牌和统一招聘体系必须有显式 verified relationship evidence。
2. S4 只证明 career source 的官方性，不自动信任页面中出现的任意 ATS tenant。
3. S5 成功必须输出结构化 `provider_identity`：provider、tenant、canonical board URL、evidence URL、verification method 和 hiring-entity relationship provenance。
4. Native adapter 成功不得只返回字符串 URL；其 provider、tenant 和 canonical board identity 必须可重新验证。
5. S6 opening 必须绑定 S5 已验证的 provider/tenant/canonical board；标题相同、ATS 相同但 tenant 不同仍必须拒绝。
6. S7 必须独立检查 S3-S6 identity continuity。identity 缺失、冲突或无法证明时 fail closed，不得输出 exact success。
7. acquired-brand/parent-company 路径只在 S3/S4 已冻结的显式关系下通过，不能用“一律同域”或“一律同 provider”代替关系证据。
8. Identity failure 必须进入结构化 reason/evidence，并可由 snapshot/replay 原样复现。

P0-A 必须先写以下通用 contract tests：

- Fresh Ventures 页面链接到 Notion tenant 时拒绝 opening，且代码中不存在公司名特例。
- 正确 company/hiring entity、board tenant 和 opening tenant 连续时继续通过。
- 有显式 verified acquisition/parent relationship 时可受约束通过。
- 标题完全相同但 tenant 不同仍拒绝。
- S5 board 与 S6 opening tenant 不一致时由 S7 拒绝。
- 缺失 provider/tenant identity 的 native success 不能成为 exact success。
- 同一 identity failure 可通过 scoped replay 复现，且 outcome gate 不把它当作普通 opening miss。

### P0-B：Opening Availability And Replay Contract

三个 availability reason 的唯一语义：

| Reason | 使用条件 | 禁止条件 |
| --- | --- | --- |
| `OPENING_NOT_FOUND` | eligible boards 已全部尝试，相关 inventory 已验证完整且非空，但没有可信 title/location match | 未完成 board、分页、provider fetch 或 identity 验证 |
| `NO_PUBLIC_OPENINGS` | 官方完整 inventory 被明确验证为空，且 scope 是 company-wide public inventory | 仅 title-filtered empty、页面解析不到岗位、网络失败 |
| `OPENING_DISCOVERY_INCOMPLETE` | 无 exact opening，且 inventory/eligible boards/identity 证据不足但没有更具体 retryable typed error | 已有完整 no-match/no-public 证据 |

附加规则：

- provider/network/timeout/403/budget error 优先保留具体 typed reason 和 retryable 字段，不得降级成以上三个业务结果。
- portfolio 中任一 eligible board 不完整时，company-wide 结论保持 incomplete。
- production result、stage result、evaluation terminal semantic 和 replay outcome gate 必须使用同一 availability contract。
- 修改旧测试期望前，先用 contract test 证明 fixture 属于 complete、empty、incomplete 或 retryable 的哪一种。

### P1-A：Typed Error Fidelity

- Stage 首先消费 `FetchError.reason_code`、`retryable`、HTTP status 和 typed metadata；`classify_fetch_error(str(exc))` 只允许作为 legacy/untyped fallback。
- 候选回退可以继续尝试，但每次 `FetchError` 必须进入结构化 evidence；不得吞掉异常后返回 `None`。
- 最终 reason 按 evidence tier 和 typed priority 聚合，不能让后续弱 not-found 覆盖前面的 timeout、403、预算耗尽或 provider failure。
- 增加 timeout、403、budget exhausted、fixture missing 和真实 candidate absence 的跨 stage contract tests。

### P1-B：可信评测口径

后续 summary/report 必须同时提供：

1. `exact_precision`：所有 exact 输出中，company/hiring entity/provider/tenant/opening identity 全部正确的比例；目标至少 98%，cross-company URL 必须为 0。
2. `conditional_exact_recall`：仅在人工或独立证据确认存在公开、可访问 official opening 的 eligible 样本中计算 exact recall。
3. `raw_exact_rate`：全部输入中的 exact rate，同时展示各 failure disposition，不能单独报告。
4. `system_defect_rate`：错误公司、错误 URL、parser bug、错误失败分类和可恢复超时的比例。

每个 blind/live record 必须且只能标注为：

- `exact_public`
- `verified_closed`
- `no_public_opening`
- `recruiter_client_undisclosed`
- `external_blocked`
- `system_gap`

评测报告还必须记录 cohort provenance：dynamic requested count、actual frozen count、是否预填 website/career root、是否已被开发者观察、是否 focused，以及 eligible-label 的证据来源。focused、replay、provider fixture 和预填输入不得标记为 blind。

### 并行 Workstream 与 Ownership

本阶段 contract 由主线先冻结，之后才允许以下互斥工作线进入独立 worktree：

| Workstream | 目标 | 唯一 ownership | 独立临时根 | 局部验收 |
| --- | --- | --- | --- | --- |
| Main / Contract | identity dataclass/schema、S7 continuity、composition 与最终集成 | 公共 contract、composition root、S7、ADR、中央文档 | `/private/tmp/stabilize-main-*` | identity contract suite |
| A / Stage identity | S3-S6 只通过 contract 传递 identity，不读取别的 stage 私有 trace | stage-owned modules 和新 stage tests；不修改中央 schema | `/private/tmp/stabilize-identity-*` | stage continuity tests |
| B / Typed errors | typed FetchError propagation 和 final reason priority | error/fetch/stage error tests；不修改 identity contract | `/private/tmp/stabilize-errors-*` | typed-error tests |
| C / Evaluation | precision/recall/raw/defect 与 disposition schema/report | evaluation/reporting 模块和 fixtures | `/private/tmp/stabilize-eval-*` | metric contract tests |
| D / Replay audit | identity failure capture/outcome comparison，只读调查后再分配写集 | replay tests 或只读 artifact audit；不修改 production stage | `/private/tmp/stabilize-replay-*` | scoped replay identity tests |

中央共享文件、`pipeline.py`、schema/version、registry、ADR、`IMPLEMENTATION_PLAN.md` 和最终 Git 操作由主线统一修改。完整 live benchmark、登录态 Chrome 和共享网络出口始终串行。

### 执行顺序与门禁

1. 审计 dirty diff、Fresh→Notion trace、现有 identity objects、typed error 丢失点和 replay comparison；不先实现。
2. 新增 ADR 冻结 identity continuity、availability/replay 和兼容策略；若 schema 变化，先冻结 schema/version contract test。
3. 先写 P0 失败测试，再实现最小 contract extraction 和 fail-closed validation。
4. P0 局部全绿后并行推进 typed error、evaluation 和 replay 适配；不得新增 provider/heuristic。
5. 主线集成后统一运行：全量 unittest、25-case provider regression、6-case resolver regression、architecture validator。
6. 上述离线门禁全绿后，冻结一个未继续调参的 cohort，并且只运行一次；不得边看结果边修改后继续称为同一次 blind evaluation。
7. 统一报告修复前后 `exact_precision`、`conditional_exact_recall`、`raw_exact_rate`、`system_defect_rate` 和六类 disposition。正确性修复导致 exact rate 下降时如实保留。
8. 完成本轮文档、分组 commit 和 push 后停止，不自动进入下一 provider/company 修复轮。

### 本轮验收标准

- Fresh Ventures→Notion 跨 tenant opening 被稳定拒绝，无公司特例。
- 正确 provider/tenant/opening 链路和受约束 acquired-brand 链路无回归。
- every exact success 都携带并通过完整 identity chain。
- availability、evaluation 和 replay 使用同一 complete/incomplete/retryable contract。
- typed provider/network failure 不再退化为普通 not-found。
- 全量离线门禁通过后只执行一次冻结 cohort。
- 报告四个可信指标和六类 record disposition，不再只报告单一 success rate。
- 本轮结束时列出修改文件、contract 变化、测试结果、未解决 failure clusters，并按“覆盖样本数 × 风险 × 预计收益”只给出最多三个下一轮候选任务。

### Provider 扩展暂停条件

Provider/heuristic churn 继续暂停。Replay release blocker、独立 disposition/eligibility 审阅与稳定化门禁均已闭环；只有用户明确进入下一开发轮后，才重新按 failure cluster 评估通用能力。历史 Phase 3 provider backlog 保留为参考，但当前不执行；observed cohort 的 100% exact precision 不能外推为 blind 产品精度。

### 下一轮候选（最多三项）

候选按“覆盖样本数 × 正确性风险 × 预计收益”排序；它们是稳定化候选，不授权新增 provider 或公司规则。

1. **New unfamiliar holdout**：在任何继续调参前冻结真正未观察样本，独立测量 precision、conditional recall 与 cross-company error；当前 observed cohort 的 100% precision 不足以证明泛化。
2. **Provider-neutral relationship promotion contract**：为 Dematic、Quest Global、ReturnPro、Awesome Motive、Adobe 这 5 个 false negative 冻结 first-party career handoff 到 typed provider relationship 的通用正负 contract；不得加入公司或 tenant 特例。
3. **Retryable transport isolation**：对 Deloitte、Akkodis 的 timeout 做串行 transport reliability 验证；完整证据前 eligibility 继续为 unknown，不得降级成 not-found。

## 标准七关 Pipeline

所有代码、trace、benchmark 和汇报统一使用下面七个关卡，避免不同模块对“成功”有不同理解。

| Stage | 名称 | 输入 | 成功标准 | 失败后后续状态 |
| --- | --- | --- | --- | --- |
| S1 | LinkedIn discovery | 搜索条件或 LinkedIn 数据 | 得到公司名、职位标题和来源 URL | S2-S7 `not_run` |
| S2 | Website resolution | 公司身份线索 | 找到经过证据验证的官方域名 | S3-S7 `not_run` |
| S3 | Hiring identity resolution | 品牌、官网、母公司线索 | 确定实际招聘主体或明确沿用原公司 | S4-S7 `not_run` |
| S4 | Career discovery | 官网或已知招聘主体 | 找到经过 404/error guard 验证的官方 career URL | S5-S7 `not_run` |
| S5 | Provider / job board discovery | career URL | 识别 provider，并找到可访问的 job list/board | S6-S7 `not_run` |
| S6 | Opening match | job board、title、location | 找到可信具体岗位，或明确返回 `not_found` | S7 根据产品要求执行或降级 |
| S7 | Result validation and output | 前六关结果 | 输出符合 schema 的结果、stage trace 和质量状态 | 记录输出失败 |

每一关只能使用以下状态：

- `success`：达到该关验收标准。
- `partial`：得到可用但不完整的可信结果。
- `failed`：本次执行失败，并有标准错误码。
- `not_run`：因前置硬依赖失败而没有执行。
- `not_applicable`：该公司或该路径不需要此关。
- `unsupported`：识别出当前尚未支持的类型或变体。

不是所有关卡都必须硬阻塞。例如 S6 未找到具体 opening 时，如果 S5 已得到可信 job board，系统应输出部分成功，而不是抹掉前五关成果。

### 统一结果语义

顶层 `pipeline_status` 由各关卡推导，不再仅凭是否存在 `job_list_url` 判断：

- `success`：达到本次任务要求；若任务要求具体 opening，则 S6 必须成功。
- `partial`：有可信 career/job board，但没有具体 opening 或缺少非关键字段。
- `failed`：核心任务没有得到可用输出。
- `unsupported`：已定位到当前能力边界，而不是未知失败。

`LINKEDIN_NATIVE_ONLY` 是受约束的 `partial` 终态：只有认证详情 DOM 明确显示 active native Apply、source URL 与当前 record 匹配，并且官网/ATS 路径确定性耗尽且没有 retryable/incomplete error 时成立。它保留来源岗位可用性证据，但不产生或暗示 official career/job-list/opening URL。公开 search card 缺少 External Apply 仍是 `listed + unknown`。

同时保留独立字段，避免混淆：

- `career_page_status`
- `job_board_status`
- `opening_match_status`
- `output_validation_status`

## 当前已完成

### 1. LinkedIn Public Job Discovery

已实现：

- 从 LinkedIn public job search 抓取 job cards
- 解析 job title、company name、LinkedIn job URL、LinkedIn company URL、location
- 对公司去重后进入 downstream pipeline

相关模块：

- `job_source_agent/linkedin_discovery.py`
- `job_source_agent/cli.py`

当前限制：

- 使用的是 LinkedIn public guest endpoint，不处理登录态
- LinkedIn 结果会波动，批量测试每次 unique company 数不固定
- 没有接第三方 LinkedIn crawler/API

### 2. Official Website Resolver

已实现：

- 从 LinkedIn company page / search / domain guess 中解析公司官网
- 支持 LinkedIn company slug TLD hint，例如 `tesseralabsai` 倾向 `tesseralabs.ai`
- 过滤 LinkedIn 静态资源、社交网站、聚合站和明显非官网域名
- 保存 HTML/public payload 只采信明确 Website label 或带 company identity context 的官网字段；公开 job/company URL 会规范化 locale host 并移除 tracking query
- 多词公司 canonical/domain/homepage 必须确认完整 identity，不能凭父品牌中的一个 token 通过
- 单字符品牌可在精确 LinkedIn slug 和主页证据同时成立时保守确认
- 允许 future override map

相关模块：

- `job_source_agent/website_resolver.py`

当前限制：

- 同名公司仍需要更多外部组织证据区分
- 对 bot-protected 官网仍依赖 timeout/fallback
- 没有用组织数据库或 Clearbit/Crunchbase 类外部信号

### 3. Company Identity Resolver

已实现：

- 将品牌映射到母公司或专用招聘体系
- 已覆盖部分高频规则：
  - Instagram / WhatsApp / Threads / Meta -> Meta Careers
  - Google / YouTube -> Google Careers
  - Notion, Netflix, Hudl, Snap, Roku, Home Depot
  - Stripe, Nuro, Morgan Stanley, Lemonade, Podium, ParetoHealth
- 修复了 `NOX METALS` 被误匹配到 Meta 的假阳性

相关模块：

- `job_source_agent/company_identity.py`

当前限制：

- 目前还是 curated rules
- 覆盖率依赖高频公司积累
- 后续应将规则拆成可维护配置或数据库

### 4. Career Page Discovery

已实现：

- 首页链接抽取
- 常见路径探测，例如 `/careers`, `/jobs`, `/join-us`
- 品牌化路径探测，例如 `/join-{brand}`
- career/jobs 子域探测，例如 `careers.example.com`, `jobs.example.com`
- sitemap / robots sitemap discovery
- search fallback：`{company} careers jobs`
- ATS 域名识别
- 404/error page guard，避免把错误页误判为 career page

相关模块：

- `job_source_agent/pipeline.py`
- `job_source_agent/career_search.py`
- `job_source_agent/scoring.py`

当前限制：

- 对 JS-heavy career pages 仍不稳定
- 对防爬网站、慢网站、企业招聘系统跳转链仍容易失败
- fast batch mode 为了速度会牺牲召回

### 5. Job List / Opening Matcher

已实现：

- 根据 career/job list URL 识别 provider
- 根据 LinkedIn title 做 title token matching
- 防止不相关岗位被误选为具体 opening
- 找不到具体岗位时保留稳定 job board fallback
- 对部分 provider 生成 provider-aware search URL

相关模块：

- `job_source_agent/opening_matcher.py`
- `job_source_agent/scoring.py`

### 6. ATS / Hiring Platform Adapter Progress

当前 provider 支持状态：

| Provider | 当前状态 | 说明 |
| --- | --- | --- |
| Google Careers | Native SSR adapter | 自动发现；使用公开 title search 页面并验证 canonical detail URL 与数字 job ID |
| Meta Careers | Native positive-evidence adapter | 自动发现；只消费 visible-page 具体岗位证据，固定 `inventory_complete=false`，不支持 no-match |
| Lever | Native API adapter | 自动发现；使用 `api.lever.co/v0/postings/{company}` |
| Greenhouse | Native API/page adapter | 自动发现 hosted board；使用 Boards API；first-party frontend 可从 `__NEXT_DATA__` 完整 Greenhouse schema 识别并读取同源岗位 |
| SmartRecruiters | Native API adapter | 自动发现；使用 `api.smartrecruiters.com/v1/companies/{company}/postings` |
| Ashby | Native API adapter | 自动发现；使用 `api.ashbyhq.com/posting-api/job-board/{board}` |
| Workable | Native structured-page adapter | 自动发现；解析 embedded JSON 并还原 `apply.workable.com/{company}/j/{shortcode}/` |
| iCIMS | Native structured-page/API adapter | 自动发现 hosted iCIMS URL 或 Jibe 页面指纹；解析 JSON-LD / embedded JSON，customer-owned domain 使用同源 `/api/jobs` |
| Workday | Native CXS API adapter | 自动发现；构造 `/wday/cxs/{tenant}/{site}/jobs` 并用 title payload 搜索 |
| SuccessFactors | Native structured-page/API adapter | 支持 legacy hosted page；支持 `*.jobs.hr.cloud.sap` CSRF/locale discovery 和同源 recruiting v1 API |
| Phenom | Native page-aware SSR adapter | 通过 customer-owned 页面 `phApp`/`refNum` 指纹识别；读取 eager refine state，支持 keyword pagination 和同源 detail URL 重建 |
| Rippling | Structured HTML adapter | 支持公开 board anchors、Next.js job state、metadata enrichment 和 title matching |
| BambooHR | Native listing API adapter | 自动发现；使用公开 `/careers/list` JSON 返回职位并还原详情 URL，支持 `atsLocation` fallback |
| Sitecore/Next JobSearch | Native page-aware API adapter | 从 first-party `__NEXT_DATA__` 绑定 tenant；同源 POST 分页并重建安全 `jobId` URL |
| CEIPAL | Native page-aware inventory adapter | 强 widget/tenant 绑定；读取公开 multipart inventory，完整分页后才允许 empty/no-match，重建 first-party detail URL |
| Talemetry Career Sites | Detection-only page-aware adapter | 强 Career Sites 指纹与 tenant 绑定；403/Cloudflare/未知 schema 保持 typed incomplete |

已验证的离线 fixtures：

- Workday CXS API response and job detail
- iCIMS job detail
- iCIMS JSON-LD and embedded JSON job records
- SmartRecruiters job detail
- SuccessFactors job detail
- SuccessFactors embedded JSON job records
- Greenhouse structured API
- Lever structured API
- SmartRecruiters structured API
- Ashby structured API
- Workable embedded JSON job records

### 7. Batch Evaluation

已实现：

- `scripts/live_batch_eval.py`
- `scripts/benchmark_eval.py`
- `scripts/export_replay_input.py`
- `scripts/validate_replay_input.py`
- `scripts/render_summary_report.py`
- 固定离线 benchmark：`samples/benchmark_companies.json`
- 固定 live benchmark：`samples/live_benchmark_companies.json`
- 每家公司处理后 checkpoint 写结果
- 输出 `summary.json`，包含 funnel rates、provider distribution、failure stages
- 可将 `summary.json` 渲染成 Markdown 报告，包含 overview rates、S1-S7 stage funnel、provider/reason 分布、expectation checks 和公司 × 七关矩阵
- 支持从 prior results/trace 导出可复跑 input，并按 stage、stage status、reason code、provider 过滤
- 支持验证 replay input 的 checkpoint metadata 是否仍兼容当前 schema / adapter / input fingerprint
- 支持 `--snapshot-dir` 将 live fetch 的页面保存为脱敏、fixture-compatible snapshots，并写入 `snapshots.jsonl` metadata
- 支持 `--fetch-retries` / `--retry-base-delay`，只重试标准 reason code 中 `retryable=true` 的 fetch failures，并在 trace 中记录 retry events
- 支持 `--workers` 进行公司级 bounded concurrency；每家公司仍保留 process-level hard budget，完成一家公司就 checkpoint 一次
- 支持 fast mode：
  - `--skip-sitemap`
  - `--fetch-timeout`
  - `--career-search-timeout`
  - `--max-career-candidates`
  - `--max-career-fetches`
  - `--max-career-search-queries`
  - `--max-ats-board-fetches`
  - `--max-job-pages`
  - `--company-time-budget` (live batch; each company has a wall-clock deadline)
  - `--website-time-budget` (S2/S3 gets its own checkpoint budget before S4-S6)

已知结果：

- 2026-07-11 Product Manager live batch：8 unique companies, 8/8 websites, 6/8 official job-list pages, 1/8 exact opening。
- 2026-07-11 Data Analyst live batch after fast-domain + ATS-root routing：9 unique companies, 9/9 websites, 8/9 official job-list pages, 1/9 exact opening。唯一失败是咨询/外包发布方 YO HR，在官网解析后耗尽 career discovery budget。
- 2026-07-11 fixed live benchmark：6 named companies, 6/6 websites, 6/6 official job-list pages, 1/6 exact opening, 6/6 expectation checks passed。覆盖 Greenhouse、Lever、Ashby、PostHog first-party careers 和 Brex first-party careers。
- 2026-07-13 exploratory LinkedIn batch：19 unique companies, 14/19 official job-list pages, 6/19 exact openings。下游 timeout 现在会保留已完成 S1-S3，因此 5 个原始失败中 Hadrian、Multifactor、Paramount 和 Docusign 的官网解析证据不再丢失；下一主 failure cluster 是 hidden ATS/list-root discovery、structured listing 和 parent-card link semantics。
- 2026-07-13 focused replay：S5 从 iframe/data attributes/form action/escaped state/redirect 提取 hidden ATS root；Oracle login/profile 被拒绝为公开 listing，Snowflake 进入 Phenom `search-results`。S6 用 parent-card paragraph 和 multi-assignment JSON state 命中 Plaid/Snowflake；后续 provenance-aware root validation、Greenhouse config promotion 和 verified ATS fallback 分别命中 Glean、Reddit、Zillow、Twitch。Zillow 在整组运行中仍有 search/network 波动，Uber Seattle 与 Starbucks Nashville 当前官方 inventory 未确认。
- 2026-07-13 opening availability diagnostics：S6 现在区分 `verified_inventory_no_match`、`verified_inventory_empty`、`discovery_incomplete` 和带明确来源证据的 `source_posting_closed`。官方 provider trace 保存库存状态、候选数量和最佳标题分数；单纯搜索未命中不会被误报为岗位过期。Summary/Markdown report 可直接聚合这些 disposition。
- 2026-07-13 fresh `AI Engineer` exploratory batch：LinkedIn 30 条去重为 26 家；10 秒旧示例参数下为 13/26 job list、6/26 exact。对 9 个 S2 timeout 使用当前默认 20 秒预算后，官网从 0/9 恢复到 8/9，4/9 到 job list、2/9 exact，证明 README 的旧 10 秒命令需要校正。该 replay 同时暴露 generic false positive：Morgan Stanley `/people` 被误记为 list、Clera dashboard 被误认官网。S5 现要求 provider/detail/listing-route evidence，focused live 将 Morgan Stanley 修正到 `/careers/career-opportunities-search`，Nuro 无 listing evidence 时诚实返回 `JOB_BOARD_NOT_FOUND`；S2 ambiguous non-`.com` 候选在首页缺品牌身份时不再仅凭 slug 入选。
- 2026-07-13 S4 timeout cluster：generic search 对每个 query 重复访问 RSS/HTML 源、随后串行验证推测 ATS tenant，导致 45 秒 outer budget 先于结构化结果触发。当前普通 career search 限为 3 条 query，ATS-only sweep 保留 5 条；RSS 已返回结果但无有效候选时跳过同 query 的重复 HTML fallback，推测 tenant 先走 native adapter。Fetch 层逐请求压缩 timeout 到 soft deadline 剩余时间，runner 预留最多 1 秒发布 checkpoint。两组 8-company focused live 中 7 个原 hard timeout 降为 0，Mercor job list 成功保持，Clera 假官网被拒绝，其余 6 家稳定输出 `CAREER_PAGE_NOT_FOUND`，4-company 慢组在 44.6 秒内完成并保存全部 stage checkpoint。
- 2026-07-13 S5 traversal cluster：按当前 evidence gate 重放 7 个旧 opening miss 后，7/7 被正确归类为 `JOB_BOARD_NOT_FOUND`。允许 known-ATS embed 交给 adapter 验证、同分 route 优先保留 locale、redirect duplicate 不占有效 page budget 后，focused live 提升到 3/7 job list、1/7 exact opening；Epistemix 通过 Ashby embed 命中，Quest Global/Viking 到达 Phenom search-results。ReturnPro 暴露 Paycom board；Quest/Viking 的静态列表仍只有 Phenom shell，Phenom structured inventory 和 Paycom provider 分别进入后续 workstream。
- Product Manager / Data Analyst 这类品牌和成熟公司样本成功率明显高于随机 long-tail AI Engineer 样本。
- 2026-07-11 focused live checks: Cricut reached `https://cricut.com/careers`; Carv's public Rippling board matched `Growth Product Manager` to its exact job-detail URL. The full Carv homepage-to-board run remains sensitive to transient website timeouts.
- Follow-up live verification: ReachMobi now maps `Product Manager` through BambooHR to `/careers/270`; MatrixSpace reaches its localized careers page and Ashby board; ONEOK retains its legitimate Workday board instead of a false `/assets/logo` URL.
- Ardent Health 的 Jibe customer-owned iCIMS 页面已通过 page evidence 识别；`Registered Nurse` 经带品牌/地区隔离的 `/api/jobs` 返回具体 canonical opening，已加入固定 live benchmark。
- Brex first-party careers 的 `__NEXT_DATA__` 保留完整 Greenhouse job schema；page-evidence adapter 已精确匹配 `Data Analyst II`，不再作为 generic board 处理。
- DeLaval 的 `*.jobs.hr.cloud.sap` 新 SuccessFactors Career Site 已通过同源 recruiting v1 API 精确匹配 `Process Engineer`，无需 browser rendering。

当前限制：

- live batch 已支持 bounded company concurrency，但 live 网站吞吐仍受网络质量、上游限流和每家公司 hard deadline 影响。
- Python 3.14 在被强杀的 worker socket 清理时偶尔输出 harmless cleanup warning；业务结果和 JSON checkpoint 不受影响。
- 后续需要固定 live benchmark set，不能只依赖 LinkedIn 当天随机结果。

### 8. Tests

当前测试覆盖：

- LinkedIn adapter
- website resolver
- company identity resolver
- career page discovery
- sitemap discovery
- search fallback
- ATS/provider detection
- provider-specific opening matcher
- static resource / false positive filters

当前测试数量：

- 817 unit tests passing

## 当前主要短板

### 1. Core Modules Still Violate SOLID Boundaries

当前已完成第一轮 SOLID 拆分，但仍有兼容层需要逐步迁移：

- S2-S7 已有独立 stage，通用 `ApplicationRunner` 支持顺序执行、范围重跑和上游结果复用；`JobSourceAgent` 仍保留 discovery helper 和兼容 facade。
- 26 个 provider module 已自动发现；其中 25 个提供原生 inventory 或受约束 positive evidence，仅 Talemetry 提供 detection-only typed incomplete semantics；generic fallback 继续作为兼容路径。
- `live_batch_eval.py` 保留公司级并发、process budget 和输出职责，七关业务执行已委托统一 `PipelineApplication`。
- Fetcher 已有显式 protocol 和跨实现 contract suite；filesystem stage store 已接入 production CLI 和 live batch。
- 原生 adapter 已支持包内自动发现；新 provider 不再需要修改中央 registry。

Phase 2.5 并行门槛已经达到。后续可以让 Provider、Pipeline、Resolver、Fetch 和 Evaluation 工作线并行，同时继续收缩 legacy compatibility path。

### 2. Provider Adapter Still Incomplete

虽然已经开始做 provider-specific adapters，但仍有一部分 ATS 只是结构化页面抽取，还没有完成稳定 live search/list API。

仍需系统补齐或 live hardening：

- iCIMS 更多真实 hosted-search theme/API 变体 live hardening
- SuccessFactors 更多真实 tenant/theme 变体 live hardening
- Workable public jobs cursor API 已完成 5 个真实 tenant smoke；后续继续扩展固定 live 样本和未知 payload 变体
- Ashby embedded fallback 已完成，后续做真实 board live hardening
- Rippling Next.js structured state 已完成 5 个真实 tenant 覆盖；后续继续跟踪未知 locale/state 变体
- CEIPAL public multipart inventory contract 已冻结并通过 live/replay；后续扩大未知 tenant/theme 样本。Talemetry 仍需在冻结并验证成功 public inventory schema 后才能从 detection-only 升级；bot block、Cloudflare challenge 或未知 JSON 不能解释为空库存

### 3. Browser Rendering Needs Live Hardening

已有 `RenderedFetcher` 和 `SmartRenderedFetcher`，并已接入 CLI / live batch runner：

- 静态 fetch 优先
- 静态 fetch 失败时可自动 render
- 页面明显是 JS shell 时可自动 render
- 支持 per-run / per-company render budget
- trace 中可看到 browser source
- live batch trace 中会记录 render events
- `--render-screenshot` 可为 Playwright-rendered page 保存截图 artifact；配合 `--snapshot-dir` 写入 `snapshots.jsonl` metadata
- Playwright-managed Chromium 不可用时，可 fallback 到本机 Google Chrome channel
- 2026-07-12 smoke: local Chrome fallback rendered a real URL and wrote screenshot artifact metadata; PostHog fixed live batch with render/screenshot/snapshot flags passed 1/1

仍需补：

- 继续用真实 JS-heavy provider 变体校准 render trigger
- live batch 中验证 render budget 不会拖垮吞吐

2026-07-12 更新：5 个真实 Workable shell 已证明 locale/self-link 和静态资源会错误抑制 render；trigger 已修正为 `static_no_usable_job_links`。浏览器导航正常时以 DOMContentLoaded 为边界，networkidle 只使用剩余 timeout budget并允许软超时；若 DOMContentLoaded timeout，系统无额外等待地检查当前 DOM，仅保留可用 job/career link 或至少 120 字符且包含招聘语义的页面，空 shell 继续失败。当前系统 Python 未安装可选 Playwright 时会记录失败 event 并安全回退静态页，不影响默认 pipeline。

### 4. Search Fallback Needs Better Sources

当前 search fallback 使用 Bing RSS、Bing HTML 和 DuckDuckGo HTML。

当前状态（2026-07-12）：三种免费源按 query 逐源 fallback，单个 source 失败不会提前终止；总 source fetch 有硬预算。结果只接受官方域的 career/job path，或包含完整 company identity 的 ATS URL；Bing/DDG redirect、credentials、非法端口、resource URL 和 ATS filter-query duplication 已覆盖。真实 Mistral AI smoke 从 DuckDuckGo 找到 `mistral.ai/careers` 和 `jobs.lever.co/mistral`。

问题：

- 容易 timeout
- 结果质量不稳定
- 对小公司 career page 不一定靠前

后续可考虑：

- 多 search provider fallback
- SerpAPI / Brave Search API / Google CSE
- 查询模板扩展：
  - `{company} jobs`
  - `{company} careers`
  - `{company} greenhouse`
  - `{company} lever`
  - `{company} workday`
  - `{company} smartrecruiters`
- 在 Bing 失败后，已增加 bounded deterministic ATS board probes（Lever、Greenhouse、Ashby、SmartRecruiters、Workable、BambooHR、Rippling）；它们仍不能覆盖自建招聘系统或未知 slug。

### 5. Official Website Resolver Needs Stronger Evidence

当前官网解析已用 15 个真实公司矩阵校准短名称、非 `.com`、redirect、canonical 和母子品牌；缺官网的 Mistral AI 端到端 smoke 自动解析到 `https://mistral.ai/`、官方 careers 和 Lever board。

后续需要：

- LinkedIn company page website field 更强解析（第一版已完成 explicit field/context extraction）
- search result title/snippet scoring
- homepage content verification 和 canonical URL 归一
- company alias / parent-company relationship table

### 6. Live Success Rate Is Improving But Not Yet Product-Grade

当前系统适合展示架构和工程思路，但还不是稳定产品。

主要原因：

- real websites dirty and slow
- ATS provider coverage incomplete
- JS-heavy pages not fully supported
- batch run lacks robust parallel execution and budgets

### 7. Stage 状态已标准化第一版

当前状态：已完成第一版标准化。`DiscoveryResult` 现在包含 `pipeline_status`、`error_code` 和版本化 `stages` 数组。每家公司都会输出 S1-S7 的 status、reason_code、retryable、owner、provider、duration、input/output counts 和 evidence。

它已经可以直接回答：

- 具体失败在七关中的哪一关
- 后续关卡是失败了，还是根本没有运行
- job board 成功但 opening 未找到时，整体应算成功还是部分成功
- 同一错误在不同模块中是否属于同一类问题

当前 `StageResult` schema：

```json
{
  "stage": "job_board_discovery",
  "status": "failed",
  "reason_code": "RATE_LIMITED",
  "retryable": true,
  "provider": "workday",
  "started_at": "...",
  "duration_ms": 1234,
  "input_count": 1,
  "output_count": 0,
  "evidence": [],
  "detail": "..."
}
```

### 8. 错误码与失败归属已完成第一版

当前已有集中维护的 `reason_code` 目录，覆盖网络、HTTP、防护、身份、页面发现、provider、解析、匹配、业务和预算类错误。旧版 lowercase `error` 字段保留给 CLI 兼容；新版分析统一使用 uppercase `error_code / reason_code`。

第一版错误码表包括：

| 分类 | 标准错误码示例 | 默认是否可重试 |
| --- | --- | --- |
| 网络 | `NETWORK_TIMEOUT`, `DNS_FAILED`, `CONNECTION_FAILED` | 是 |
| HTTP | `HTTP_FORBIDDEN`, `RATE_LIMITED`, `SERVER_ERROR` | 视状态码 |
| 防护 | `BOT_PROTECTION`, `LOGIN_REQUIRED`, `CAPTCHA_REQUIRED` | 否/人工处理 |
| 渲染 | `JS_RENDER_REQUIRED`, `RENDER_FAILED`, `RENDER_BUDGET_EXHAUSTED` | 是 |
| 身份 | `WEBSITE_NOT_RESOLVED`, `COMPANY_IDENTITY_AMBIGUOUS` | 否/需补证据 |
| 页面发现 | `CAREER_PAGE_NOT_FOUND`, `JOB_BOARD_NOT_FOUND` | 否/需扩展发现能力 |
| Provider | `PROVIDER_UNKNOWN`, `PROVIDER_UNSUPPORTED`, `PROVIDER_VARIANT_UNSUPPORTED` | 否/进入 adapter backlog |
| 解析 | `PARSING_FAILED`, `INVALID_STRUCTURED_DATA`, `EMPTY_PROVIDER_RESPONSE` | 视情况 |
| 匹配 | `OPENING_NOT_FOUND`, `TITLE_MISMATCH`, `LOCATION_MISMATCH` | 否，通常是正常结果 |
| 业务 | `NO_PUBLIC_OPENINGS`, `OPENING_CLOSED` | 否，不算系统 Bug |
| 预算 | `COMPANY_TIME_BUDGET_EXHAUSTED`, `FETCH_BUDGET_EXHAUSTED` | 是 |

每个 stage-level 错误必须附带：

- `stage`
- `reason_code`
- `retryable`
- `owner`：`network / resolver / provider / parser / matcher / external`
- 简短、可脱敏的 detail

### 9. Checkpoint、Replay 与 Snapshot 已进入严格版本化阶段

Production CLI、live batch 和 failure replay 均通过 `PipelineApplication` 执行。Batch completion、stage checkpoint、attempt/stage evidence lineage、durable publication、typed retry、full-outcome bundle-v6 replay 和 30-company identity gate 已完成；S1-S3 与 S4-S7 的 process hard budget 仍保留。`scripts/export_replay_input.py` 可按 stage/status/reason/provider 输出稳定 replay input，snapshot v3 精确绑定 producer attempt、execution fingerprint 和 stage scope；missing、extra、mismatch、unconsumed 或损坏 outcome 全部 fail closed。

Legacy v1/v2 snapshot materialization 仅用于显式兼容。任何 v3-only 或 mixed scoped index 都会在写入前非零失败并要求 bundle-v6 replay，不能再把现代 capture 过滤成空 fixture success。当前剩余 checkpoint 工作不是“补第一版”，而是统一普通 CLI 与 live batch 的完整 prefix preflight：请求从 S5/S6 恢复时，缺失、损坏、incompatible 或语义无效的 authoritative upstream chain 必须回退到最早缺口或明确失败，不能带 `not_run` prefix 继续执行下游。

当前关键中间产物均以 typed/versioned contract 保存：

- LinkedIn/company 原始输入
- resolved website 及证据
- hiring entity / parent company 决策
- verified career URL
- detected provider 和 board identifier
- provider response 或脱敏 HTML/JSON snapshot
- job candidates 和 title-match 分数
- 最终选择及验证证据

Checkpoint 带 `schema_version`、execution/input fingerprint、run configuration 和 adapter 版本；只有完整兼容且能按顺序应用到 fresh context 的 prefix 才能复用。下一项治理重点是公司级 URL/provider/tenant/opening identity regression matrix，避免总成功率不变却悄悄换成错误 URL。

阶段级重跑至少支持：

- 从指定失败关卡继续
- 强制重跑某一关及其下游
- 只重试 `retryable=true` 的失败
- 在修复 parser 后，用保存的 snapshot 离线重放，不依赖真实网站仍然在线

### 10. Provider 级可靠性指标已完成第一版

当前 summary 已经输出以下统计，用于判断修复哪一项收益最大：

- `provider × stage × status`
- `provider × reason_code`
- 每个 provider 的公司数、job board 成功率、opening 成功率
- 昨天成功今天失败的 regression 数量
- 未知 provider / 自建站占比
- 每关输入数、输出数、耗时 P50/P95

已输出“公司 × 七关”的矩阵，例如：

| Company | Provider | S1 | S2 | S3 | S4 | S5 | S6 | S7 | Reason |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A | Greenhouse | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — |
| B | Workday | ✓ | ✓ | ✓ | ✓ | ✗ | — | — | `RATE_LIMITED` |
| C | Unknown | ✓ | ✓ | ✓ | ✓ | unsupported | — | ✓ | `PROVIDER_UNSUPPORTED` |

### 11. 开发优先级尚未完全由数据驱动

当前 adapter 路线方向正确，但不能仅按 Workday、iCIMS、SuccessFactors 的固定名单顺序开发。每轮应先读取固定 benchmark 的失败分布，再选择预期收益最大的工作项。

推荐排序公式：

```text
priority = affected_companies × user_impact × recurrence × confidence / estimated_effort
```

同时设置可靠性优先规则：

1. 已支持 provider 的回归和假阳性，高于新增 provider。
2. 能一次修复多个公司的共同原因，高于单公司特殊规则。
3. 前置关卡问题通常高于后置关卡问题，因为它会阻断更多下游。
4. 数据正确性和“不乱编 URL”高于表面成功率。
5. 公司级 hardcode 只作为最后手段，并必须记录原因和退出条件。

## 架构与治理原则

后续实现统一遵循 SOLID 和明确 ownership boundary：

- **Single Responsibility**：每个 stage、resolver、provider adapter、fetch wrapper 和 reporter 只有一个主要变化原因。
- **Open/Closed**：新增 ATS 通过新增导出 `ADAPTER` 的 provider module 完成，不修改中央 registry 或扩大条件分支。
- **Liskov Substitution**：所有 fetcher、stage 和 provider implementation 遵循相同成功、空结果和异常语义。
- **Interface Segregation**：stage、provider、fetch、checkpoint 和 reporting 使用独立的小型 contract。
- **Dependency Inversion**：业务 stage 依赖 protocol/contract；HTTP、Playwright、filesystem 和具体 adapter 在 composition root 注入。

治理要求：

- 每个可交付开发任务必须更新 `CHANGELOG.md` 的 `Unreleased`。
- 计划状态或优先级改变时更新本文档。
- 模块边界改变时更新 `docs/ARCHITECTURE.md`。
- 核心 abstraction、schema、持久化或并发模型改变时增加 ADR。
- 行为、接口和架构变化必须有单一目的 commit；Git commit 是详细历史，changelog 是版本级摘要。

## 下一阶段计划

下面的 Phase 按顺序执行。Phase 0 和 Phase 1 已建立可靠性与评测基线；Phase 2 是继续并行扩展 adapter 之前的架构门槛。完成 Phase 2 后，具体 adapter 的先后顺序由 benchmark 数据动态决定。

### Phase 0: Reliability Contract And Stage Model

这是下一步最高优先级。在继续扩大 provider 覆盖之前，先让整条 pipeline 的状态和失败可被统一管理。

当前进度（2026-07-11）：已完成。`DiscoveryResult` 现在输出版本化的 `stages` 数组，覆盖 S1-S7；每关包含 status、标准 reason code、retryable、owner、provider、duration、counts 和 evidence。旧版 `status/error` 保留给既有 CLI/JSON 使用，新版 `pipeline_status/error_code` 负责产品语义。离线测试已覆盖 exact opening、job board fallback、career discovery failure 和网络错误分类。

#### 0.1 定义 StageResult 和 PipelineResult

目标：

- 为标准七关建立固定枚举和 schema
- 每关记录 status、reason code、retryable、耗时、输入输出数量和证据
- 顶层 `pipeline_status` 由各关结果统一推导
- 保留现有字段的兼容读取或提供一次性迁移脚本

验收标准：

- 任意结果无需阅读自由文本 trace，就能确定最后成功关卡和首个失败关卡
- 前置失败时，所有未执行的下游关卡明确为 `not_run`
- “job board 找到、opening 未找到”稳定表示为 `partial`，且不会丢失 job board
- schema 有版本号并通过序列化/反序列化测试

#### 0.2 建立标准错误目录

目标：

- 用枚举或集中注册表维护 `reason_code`
- 建立 HTTP/exception 到标准错误码的映射
- 区分 Bug、临时故障、能力缺失、外部阻塞和正常空结果
- 每类错误定义默认 `retryable` 和 `owner`

验收标准：

- benchmark 中不存在未归类的裸 exception 字符串
- 每个 `failed` 或 `unsupported` stage 都有标准 `reason_code`
- `OPENING_NOT_FOUND`、`NO_PUBLIC_OPENINGS` 不计入系统异常率
- 新增错误码必须有单测和文档说明

#### 0.3 明确关卡依赖和降级规则

目标：

- 配置每一关是 hard dependency 还是 optional enrichment
- 明确 S6 未匹配具体岗位时，何时返回 job board fallback
- 明确不同调用目标的成功标准，例如“找到 job board”和“找到 exact opening”

验收标准：

- 同一输入、同一任务目标的顶层状态是确定性的
- 可选字段缺失不会错误地阻断可用结果
- 所有降级结果都有明确 `partial_reason`

### Phase 1: Baseline, Matrix And Data-Driven Prioritization

当前进度（2026-07-13）：1.1 的固定离线 benchmark 已有 15 个 provider/path fixture，覆盖 Rippling、traditional iCIMS、page-aware Phenom 和 Paycom exact opening，并由 `samples/benchmark_expectations.json` 声明 provider、最低成功关卡和 exact-opening 要求；回归不满足预期会以非零退出。固定 live benchmark 已扩展到 51 家。1.2 的 JSON 漏斗、公司七关矩阵、`provider × stage × status`、`provider × reason_code`、阶段耗时 P50/P95、跨运行 regression delta 和 Markdown summary report 已实现。

#### 1.1 固定 benchmark 分层

建立并版本化三套样本：

- 离线 fixture benchmark：确定性回归，覆盖所有已支持 provider 和常见失败
- 固定 live benchmark：每个主要 provider 至少 5 家，长期比较成功率
- 探索样本：来自 LinkedIn 当天结果，用于发现未知 provider 和新失败类型

样本必须标记：

- expected provider
- expected 最低成功关卡
- 是否期望 exact opening
- 是否允许 job board fallback
- 已知外部限制

验收标准：

- 每次改动可以与上一基线比较，而不是只看一次随机 live run
- fixture benchmark 必须完全确定且不可访问公网
- live benchmark 的结果和运行时间带时间戳保存

#### 1.2 输出漏斗与公司关卡矩阵

目标：

- summary 输出 S1-S7 的输入、成功、部分成功、失败、未运行和不支持数量
- 输出 `provider × stage × status` 与 `provider × reason_code`
- 生成公司 × 七关矩阵，可用 JSON 和人类可读表格查看
- 记录每关耗时 P50/P95 和从成功变失败的 regression

验收标准：

- 能在一分钟内回答“哪一关损失最多公司”
- 能回答“修复哪个 provider/reason code 预期覆盖最多公司”
- 报告同时显示成功率和样本数，避免小样本误导

#### 1.3 建立每轮开发决策门槛

每轮开始前：

1. 运行固定 benchmark，保存 baseline。
2. 按失败关卡、provider、reason code 聚类。
3. 用影响公司数、用户影响、复现频率和成本排序。
4. 选一个明确的 failure cluster 作为本轮目标。
5. 写出期望提升，例如“Workday S5 成功率从 40% 提升到 80%，覆盖 6/10 个失败样本”。

每轮结束后：

1. 运行对应 adapter 单测和离线 fixtures。
2. 运行全量固定 benchmark，检查其他 provider 回归。
3. 比较 baseline 与新结果。
4. 达到验收指标才关闭任务；否则保留失败样本和原因。

### Phase 2: SOLID Architecture Decomposition

当前状态（2026-07-13）：Phase 2.5 并行门槛已达到并完成多轮并行验证。版本化 contracts、S1-S7 独立 stage classes、通用 `ApplicationRunner`、并发安全 filesystem stage checkpoint store、provider registry、18 个原生 adapter、adapter 自动发现、composition root、architecture validator 和跨 fetcher contract suite 已实现；20/20 provider benchmark、6/6 resolver benchmark 和 51-company fixed live job-list gate 均通过。Production CLI 与 live batch 均已完成接线。

这一阶段不追求提高 live 命中率，目标是降低新增 provider、stage replay 和多人并行开发的修改成本。重构期间必须保持现有 CLI、result schema 和 benchmark 行为兼容。

#### 2.1 Freeze Small Contracts

当前状态：已完成第一版。`FetchClient`、`PipelineContext`、`StageExecution`、`Stage`、`CheckpointStore`、`ProviderAdapter`、`JobBoard`、`JobQuery` 和 `AdapterResult` 已有显式 contract tests。

目标：

- 定义 `FetchClient` protocol，统一 HTTP、browser、retry、snapshot 和 fixture fetcher 的行为。
- 定义版本化 `PipelineContext`、`StageExecution` 和 `Stage` contract。
- 定义 `ProviderAdapter`、`JobBoard`、`JobQuery` 和 `AdapterResult` contract。
- 定义 checkpoint store contract，不让 stage 直接依赖 filesystem。
- 为每类 contract 建立 implementation-independent contract tests。

验收标准：

- 业务模块依赖 contract，而不是具体 HTTP、Playwright 或 filesystem class。
- 所有 fetch implementation 对成功、`FetchError`、timeout 和空 body 语义一致。
- Contract schema 有版本和兼容性测试。
- Contract 不暴露自由格式内部 trace 作为下游必需输入。

#### 2.2 Extract Independent Stages

当前状态：S2 website、S3 hiring identity、S4 career、S5 job-board、S6 opening 和 S7 validation 都已有独立 stage class。`ApplicationRunner` 可按标准顺序执行 stage、限制 `start_at`/`stop_after`、复用上游结果并确定性标记下游 `not_run`；`JobSourceAgent.discover()` 保留兼容 facade。Production CLI 和 live flow 已委托统一 `PipelineApplication`。

目标：

- 将 S2-S7 拆成单一职责 stage；S1 保持独立 discovery source。
- Runner 只负责依赖顺序、预算、retry policy、checkpoint 和取消。
- 保留 `JobSourceAgent.discover()` 作为兼容 facade，内部委托新 runner。
- 每个 stage 只读自己的声明输入，只写自己的声明输出。

验收标准：

- S4、S5、S6 可以用固定 `PipelineContext` 独立运行和测试。
- 一个 stage 的 parser/strategy 变化不要求修改其他 stage。
- 重构后 440 个测试、13/13 provider benchmark 和 6/6 resolver benchmark 结果一致。
- Stage failure 会确定性地生成下游 `not_run` 或允许的降级状态。

#### 2.3 Introduce Provider Adapter Registry

当前状态：已完成可并行扩展的第一版。26 个 provider module 通过导出 `ADAPTER` 自动注册；25 个提供 native inventory 或受约束 positive evidence，仅 Talemetry 使用 page-evidence extension 提供 detection-only typed incomplete contract。Generic fallback 暂时保留 compatibility path。

已完成目标：

- 新建 `providers/base.py`、`providers/registry.py` 和 provider-specific modules。
- 将 detection、board identifier、request construction、response parsing 和 URL normalization 收进对应 adapter。
- 将 title/location ranking 保留为共享 matcher service，不复制到每个 adapter。
- 逐个迁移现有 provider，每次只迁移一个并运行全量回归。

验收标准：

- 新增 provider 通过新增导出 `ADAPTER` 的 module、fixture 和测试完成，不修改中央 registry。
- Stage runner 和 opening matcher 不再包含不断增长的 `if provider == ...` 分支。
- Provider 空结果、unsupported variant、retryable failure 和 parsing failure 使用统一结果语义。
- 每个 adapter 可以完全离线测试。

#### 2.4 Establish A Composition Root

当前状态：已完成第一版。`composition.py` 集中构造 static/browser/retry/snapshot fetcher、provider registry 和 agent；CLI 与 live batch runner 已改用统一 composition functions。

目标：

- 将具体 fetcher、resolver、registry、stage、store 和 policy 的构造集中到一个 composition root。
- CLI 和 batch scripts 只解析参数、调用 application service 和输出结果。
- 将并发、process budget 和 checkpoint writing 从页面解析逻辑中移出。

验收标准：

- `live_batch_eval.py` 不再直接实现 resolver/provider 业务规则。
- 单测可以注入 fake stage、fake adapter、fake store 和 fixture fetcher。
- Browser、retry 和 snapshot 可以通过配置组合，不改变 stage 代码。

#### 2.5 Parallel Development Gate

当前状态（2026-07-13）：已通过并完成真实并行验证。多轮独立工作线在不修改中央 registry 的前提下交付 stage/provider/fetch/resolver/reporting 变化；最近主线交付 provenance-aware career-root validation、ATS-only search、provider-config priority、strict speculative-tenant title gate、bounded traversal、regional root recovery，以及 Phenom、Paycom、RippleHire、Taleo、Eightfold、JazzHR、Avature adapter。主线 architecture validator、20/20 provider benchmark、6/6 resolver benchmark、51/51 fixed live job-list gate 和 5/5 strict browser live gate 全部通过；缺官网的 Mistral AI S2-S5 live smoke 也通过。

完成以下条件后，才开启多个 provider 分支并行开发：

- Stage/provider/fetch/checkpoint contracts 已合并到 `main`。
- Registry 和至少一个代表性 provider 已完成迁移。
- Contract tests、全量测试和离线 benchmark 全部通过。
- `docs/ARCHITECTURE.md` 与代码目录一致。
- 每条并行工作线已有明确 ownership，且不需要共同修改中央文件。

推荐并行线：

| Workstream | Ownership | 可并行内容 |
| --- | --- | --- |
| Pipeline | stages、runner、checkpoint contracts | stage resume/rerun |
| Provider | 单个 provider module、fixture、tests | Workday/iCIMS/SuccessFactors 等 |
| Resolver | website、identity、career discovery | evidence 和 search hardening |
| Fetch | browser、retry、snapshot、budget | network reliability |
| Evaluation | benchmark、summary、reports | regression governance |

### Phase 3: Complete Provider Adapters（当前暂停）

本 Phase 仅保留历史 backlog。根据 2026-07-14 correctness-first stabilization 决策，在 opening identity continuity、typed error、可信评测和冻结 cohort gate 完成前，不启动任何新 provider 或单公司 heuristic 工作。

以下是已知 adapter backlog，不代表固定执行顺序。进入本 Phase 后，应由 Phase 1 的失败分布选择优先项。

#### 3.1 Workday Adapter

当前状态：

- 已实现 Workday CXS API URL 构造
- 已实现 POST JSON payload，使用 LinkedIn title 作为 `searchText`
- 已解析 `jobPostings.title` 和 `jobPostings.externalPath`
- 已用离线 fixture 验证 API result -> concrete job URL
- 固定 live 覆盖 ONEOK、NVIDIA、Adobe、Salesforce 和 Autodesk，包含 wd1/wd5/wd12、大小写 site slug 和 570-2000 opening 的 board

目标：

- 增强更多 Workday tenant/path 变体
- 尝试解析页面 embedded JSON
- 继续增强 job detail URL 识别
- 如果具体 opening 找不到，稳定返回 Workday job board

验收标准：

- 离线 fixture 覆盖 Workday CXS response + detail
- live smoke 至少能稳定返回 Workday board URL
- title mismatch 不产生假阳性 opening

#### 3.2 iCIMS Adapter

当前状态：

- 已支持 `careers-*.icims.com/jobs/search`
- 已支持 `searchKeyword`
- 已支持 JSON-LD `JobPosting`
- 已支持 embedded JSON job record
- 可从 `id + title` 保守还原 `/jobs/{id}/{slug}/job`
- 已识别 `/jobs/{id}/{slug}/job` 详情页
- 已支持 iCIMS Jibe customer-owned domain 页面指纹识别
- 已解析页面 `searchOverride`，通过同源 `/api/jobs` 保留品牌、地区和 internal/external 隔离
- API candidate 只接受 `ats_code=icims` 和同源 canonical URL
- 已支持同 tenant `ss=1&searchKeyword=...&in_iframe=1` hosted search
- 已解析传统 iCIMS HTML anchor record，并校验数字 job ID、详情路径和 tenant 隔离
- 固定 live 覆盖 Ardent Health Jibe、Prime Healthcare、Peraton、Chenega 和 GovCIO Jibe，5/5 均命中 exact opening
- Jibe canonical URL 必须保持同 origin、符合 `/jobs/{id}` 且 URL job ID 与 payload slug 一致

目标：

- hosted search pagination 已完成第一版，继续 harden 更多 theme
- 覆盖更多 iCIMS script payload 变体
- 持续扩展真实 tenant/theme，而不是只验证 job list

验收标准：

- 离线 fixture 覆盖 traditional search page + detail page + JSON-LD + embedded JSON
- live smoke 对 5 个 iCIMS tenant 返回 job list，其中 5/5 可命中 exact opening

#### 3.3 SuccessFactors Adapter

当前状态：

- 已支持 keyword query URL
- 已识别 `career_job_req_id` / `jobReqId`
- 已支持 embedded JSON job record
- 可从 `jobReqId` 保守还原 detail URL
- 已支持 `*.jobs.hr.cloud.sap` 页面 CSRF/locale discovery 和同源 recruiting v1 API
- 页面 locale 优先于陈旧 query locale，并支持 record-level `supportedLocales`
- exact-title match 可提前停止分页，canonical detail URL 保持 tenant/locale 隔离
- 固定 live 覆盖 DeLaval、W. L. Gore、Colas、Telstra Broadcast Services 和 Nova，5/5 命中 exact opening

目标：

- 继续调查 legacy `successfactors.com` / `sapsf.com` 尚未迁移到 Cloud SAP/RMK 的真实变体
- 增强 list/search API 或 AJAX payload extraction
- 覆盖更多 legacy/RMK URL 和 theme 变体
- 解析 list/detail page

验收标准：

- 离线 fixture 覆盖 list + query detail + Cloud SAP live contracts
- error page guard 保持有效
- 5 个独立 Cloud SAP live tenant 返回 job list 和 exact opening

#### 3.4 Ashby Adapter

当前状态：

- 已支持从 Ashby board URL 构造 `api.ashbyhq.com/posting-api/job-board/{board}`
- 已解析 `jobs.title` 和 `jobs.jobUrl`
- 已用离线 fixture 验证 API result -> concrete job URL

目标：

- Ashby embedded JSON fallback 已完成
- 从 board page 抽取 title / location / URL 已完成；department 仍作为可选 enrichment
- title match 后返回具体 opening 已完成，并由 5 个官方 live board 持续验证

验收标准：

- 离线 fixture 覆盖 Ashby structured response
- live smoke 固定覆盖 Notion、Linear、Cursor、Harvey 和 Perplexity 5 个 Ashby board

#### 3.5 Workable Adapter

当前状态：

- 已支持 Workable query URL pattern
- 已支持官方 `POST /api/v3/accounts/{tenant}/jobs`、opaque cursor 有界分页、title query 和 exact-title early stop
- 已支持 embedded JSON job record
- 可从 `shortcode` 保守还原 `apply.workable.com/{company}/j/{shortcode}/`

已完成目标：

- 支持 `apply.workable.com/{company}`
- 支持 query URL、embedded posting JSON 和官方 public jobs API
- 识别并校验 detail URL

验收标准：

- 离线 fixture 覆盖 board + detail
- fixed live 覆盖 5 个 Workable board

### Phase 4: Browser Fallback

当前状态：

- 已实现 `SmartRenderedFetcher`
- CLI 支持 `--render-js` smart fallback、`--render-budget`、`--render-js-always` 和 `--render-screenshot`
- live batch runner 支持 `--render-js`、per-company `--render-budget`、render events trace 和截图 artifact snapshot
- Playwright-managed Chromium 缺失时可 fallback 到本机 Google Chrome channel
- 单测覆盖静态优先、JS shell fallback、静态失败 fallback、budget guard、local Chrome fallback、artifact source trace 和 snapshot artifact metadata
- 非空 job-context JS shell 在没有可用 job link 时会触发 browser；结构化 jobs payload 和已有可用链接的静态页面不会浪费 render budget
- render budget 耗尽会在 trace 中记录 `skipped_budget`
- Workable `#app` shell、locale/self-link 和静态资源过滤已用 5 个真实静态页面回放验证
- Stripe、Microsoft、Uber 等长轮询页面证明 networkidle 不能作为硬成功条件；已改为剩余预算内软等待
- 固定 5-company cohort 为 Plum、Meta、Apple Jobs、Spotify 和 IIC Lakshya，共 5 个 provider、5 类技术栈
- fixture 使用真实 HTTP static shell 和 Playwright Chrome 12 秒 browser DOM 的脱敏最小 capture，明确 `complete=false`
- saved/live 共用严格 evidence gate：成功 render event、结构化 selector 文本、可选 expected URL、最小正文、无 loading/final error；saved replay 与 15 秒 live 均为 5/5，Meta 验证 static HTTP 400 -> browser，Meta/Apple/IIC 验证 exact job URL

目标：

- 继续优化 render 触发条件
- 用 live benchmark 验证 render budget 不会拖垮吞吐

验收标准：

- 对一个 JS-heavy career page，static fails but browser succeeds
- batch run 不会无限卡住
- 不触发本地 Python crash reporter

### Phase 5: Stage Checkpoint, Retry And Safer Batch Runner

#### 5.1 阶段级 Checkpoint 和离线重放

当前状态（2026-07-12）：已完成 replay-level metadata、并发安全 filesystem stage checkpoint store、production CLI 和 live batch 接线。Store 对每个 stage 原子保存 `StageExecution`，通过 schema version、adapter version 和 input fingerprint 校验兼容性，使用 fingerprint 级进程锁协调 load/save/invalidate，损坏文件按安全 cache miss 处理。CLI 已暴露 `--checkpoint-dir`、`--resume-from-stage`、`--rerun-stage` 和 `--stop-after-stage`；live batch 以 S1-S3/S4-S7 两段 process budget 运行同一 application service。Snapshot 正文和 artifact 已使用跨进程锁与内容寻址 blob 发布，重复 URL/query/POST 分页不会再使旧 manifest hash 失效；replay 会选择最后一个完整版本并保留 duplicate/superseded 统计。Snapshot index 还能跳过进程中断产生的唯一 EOF 截断尾行并报告 corrupt-tail 统计，但中间损坏或完整非法记录仍严格失败。`scripts/replay_snapshots.py` 可生成安全 fixture tree，`scripts/replay_failure_bundle.py` 可进一步筛选失败样本并离线执行完整 pipeline。Live batch 已通过 `--failure-bundle-dir` / `--failure-bundle-limit` 把 partial/failed/unsupported 样本自动纳入常规 regression artifact；无可重放失败时写 skipped manifest。Company completion store 已完成 batch restart 闭环；later-stage resume 会验证完整 checkpoint chain，S5 恢复 S1-S4、S6 恢复 S1-S5，链不完整时安全回退或失败。真实子进程已在 S4/S5 checkpoint durable marker 后分别接受 `SIGTERM`/`SIGKILL`，第二进程成功恢复且所有 checkpoint 可重新加载、无临时文件残留。32-company/6-worker 主进程 `SIGKILL` stress 已验证只重跑缺失公司、稳定顺序、失败隔离和无遗留临时文件；宿主机断电和磁盘故障仍属于外部耐久性场景。

目标：

- 保存 S1-S7 各阶段结果和关键中间产物
- 使用 input fingerprint、schema version、adapter version 判断缓存是否可复用
- 支持 `--resume-from-stage`、`--rerun-stage` 和 snapshot replay
- 对 snapshot 中的敏感信息、token、cookie 做脱敏或禁止保存

验收标准：

- S5 失败后可复用 S1-S4 的有效结果继续执行
- parser 修复后可对已保存 provider snapshot 离线重放
- 上游输入或 adapter 版本变化时，相关 stage checkpoint 自动失效
- checkpoint 损坏不会导致错误成功，系统会安全地重新执行

#### 5.2 Retry Policy

当前状态（2026-07-13）：已完成有界 fetch-level retry policy。`RetryingFetcher` 会根据 `classify_fetch_error -> reason_spec.retryable` 判断是否重试；timeout、DNS、429、5xx 等使用 exponential backoff + jitter，HTTP 403、login/bot protection、parser/title mismatch 等不会重试。sleep 受 deadline/预算约束，clock、RNG 和 sleeper 可注入；每次 delay、reason 和 outcome 进入 trace，耗尽后保留原始 `FetchError`。即使 `--fetch-retries 0`，存在 caller deadline 时 composition root 也会安装 wrapper；每次初始或重试 fetch 前检查 deadline，并把底层单次 timeout 临时压缩到剩余时间。Live runner 的 inner deadline 比 outer process budget 最多提前 1 秒，使 stage 能发布结构化结果和 checkpoint，hard kill 只处理不遵守 Python timeout 的底层卡死。

目标：

- 只对 `retryable=true` 的错误自动重试
- timeout、429、5xx 使用有限次数和 exponential backoff + jitter
- 解析失败、title mismatch、unsupported 不盲目重试
- 每个 provider 和每家公司都有 fetch、render、时间和重试预算

验收标准：

- 重试次数、原因和最终结果进入 stage trace
- 达到预算后返回标准 budget error，不会无限卡住
- 自动重试不会绕过登录、验证码或网站访问限制

#### 5.3 Safer Batch Runner

当前状态（2026-07-12）：已完成 bounded company concurrency 和 crash-safe company resume。`live_batch_eval.py --workers N` 使用 thread pool 调度公司级任务，每个公司内部仍通过 process-level hard budget 防止 DNS/socket/native code 卡死；每家公司完成后以版本化 input fingerprint、adapter version、进程锁和 atomic replace 发布独立 envelope。重启默认扫描兼容 envelope，只提交未完成公司，并按原始输入顺序重建 results / trace / summary；`--no-resume` 和任何 `--rerun-stage` 会绕过整公司复用。Process budget 已覆盖无结果崩溃、SIGTERM-ignore 后强制 kill、PID 回收和并发隔离。32-company/6-worker 离线 stress test 会在部分完成后真实 `SIGKILL` 主进程，重启仅执行缺失公司，并验证稳定顺序、无重复、单 worker 失败隔离、原子 JSON 和无残留临时文件；atomic artifact writer 会清理上一次崩溃遗留的同目标 tmp，压力测试连续五轮通过；51-company/4-worker live 为 51/51 expectations。

目标：

- 每家公司独立 hard budget
- 每家公司实时 checkpoint
- S2/S3 与 S4-S6 分阶段 checkpoint，保留已验证中间结果
- 汇总成功率、失败类型、provider 分布

建议实现：

- process-level killable worker for operations that may block in DNS/socket/native code
- completed-company restart 的 32-company 真实中断注入已完成；后续只需随规模和平台变化做回归
- per-company timeout by internal budget checks plus outer process deadline
- output:
  - `results.json`
  - `trace.json`
  - `summary.json`

验收标准：

- 30 companies batch 不会因为单家公司卡住而丢失结果
- 能输出 provider-level failure distribution

### Phase 6: Evaluation And Reporting Hardening

当前状态（2026-07-13）：

- 已建立固定离线 benchmark set
- 已建立固定 live benchmark 第一版
- 覆盖 Greenhouse、Lever、SmartRecruiters、Workday、Ashby、iCIMS、SuccessFactors、Workable、Rippling、BambooHR、Google Careers
- `scripts/benchmark_eval.py` 可输出 results / trace / summary
- `scripts/live_batch_eval.py` 已支持持续写入 summary checkpoint
- live summary 显式聚合独立 trace records，在不改变 `results.json` schema 的情况下保留 checkpoint save/restore/miss/invalidate 统计；51-company live trace、32-company crash restart、37/38 company restart 和 S5/S6 精确恢复已验证
- Markdown report 已包含 rates、S1-S7 funnel、stage duration、provider/reason 分布、regression 和公司 stage matrix
- Markdown report 已增加 `provider x stage x status` 和 `provider x reason_code` 交叉表，便于定位 ATS 级可靠性问题
- `scripts/archive_evaluation.py` 已提供原子、内容寻址、带 commit/adapter/command metadata 的时间戳 history，并自动比较 latest baseline
- `scripts/resolver_benchmark.py` 已提供 6-case 缺官网固定离线 benchmark
- `live_batch_eval.py` 的下游 failure result 会复用完整 S1-S3 evidence；51-company rerun 已真实验证 30 个 completion restore + 21 个 pending execution，仍为 51/51 expectations、51/51 job list、50/51 exact opening
- S6 availability diagnostics 已将“官方库存已读取但无标题匹配”“官方空库存”“抓取证据不足”和“来源明确关闭”拆开；保持 `OPENING_NOT_FOUND` 的保守默认语义，仅在显式来源状态下使用 `OPENING_CLOSED`
- Generic S5 success 现在要求已知 ATS、具体岗位证据或实际抵达的 first-party listing/search route；career landing page 不再自动计作 job list
- S2/S5 evidence gate 收紧后 clean fixed live 仍为 51/51 expectations、51/51 job list、50/51 exact opening，4 workers 用时 90.6 秒；451 个测试、13/13 provider、6/6 resolver 和 architecture gate 同步通过
- S4 bounded search、native-adapter-first tenant verification 和 fetch soft deadline 完成后，8-company focused live 将 7 个 hard timeout 降为 0 并保留 Mercor 成功；clean fixed live 继续达到 51/51 expectations、51/51 job list、50/51 exact opening，4 workers 在本轮网络条件下用时 121.3 秒。458 个测试、13/13 provider、6/6 resolver 和 architecture gate 同步通过
- S5 known-ATS embed、locale-preserving traversal 和 redirect-budget 修复使 7-company focused replay 从 0/7 提升到 3/7 job list、1/7 exact opening；clean fixed live 保持 51/51 expectations、51/51 job list、50/51 exact opening，4 workers 用时 119.7 秒。462 个测试、13/13 provider、6/6 resolver 和 architecture gate 同步通过
- Phenom structured inventory 已作为第 12 个原生 adapter 接入，provider benchmark 扩展到 14/14 exact；Quest Global focused live 命中 exact opening，Viking 以 title-filtered verified no-match 返回 `OPENING_NOT_FOUND`。
- Paycom 已作为第 13 个原生 adapter 接入：稳定 canonical portal、页面临时 session config、公开 title-filtered POST inventory、有界分页、tenant/redirect/detail URL 校验和 token-free trace 已完成。S5 跨站 traversal 现在接受 registry-backed board，同时继续拒绝未知域、credentials 和异常端口。ReturnPro focused live 从 `JOB_BOARD_NOT_FOUND` 提升为 exact `AI/ML Engineer` opening；473 个测试、15/15 provider、6/6 resolver、13-adapter architecture gate 和 checkpointed 51/51 fixed live expectations 同步通过。下一 provider 工作项继续由 live failure cluster 排序决定。
- Lever embed config discovery 已加入 bounded link extraction：仅在同时存在 embed 指纹和合法 `leverJobsOptions.accountName` 时派生 board，并优先进入现有 Lever adapter 的 tenant/title 验证。Influur focused live 从 `JOB_BOARD_NOT_FOUND` 提升为 exact `AI Engineer` opening。
- First-party career taxonomy traversal 现在有界优先 staff/business-services/professional audience，并只接受官网明确背书、同 registrable domain 的 jobs/careers 子域 portal。Kirkland focused live 从 `JOB_BOARD_NOT_FOUND` 提升到官方 U.S. staff jobs portal，但 `AI Engineer II` 因 Cloudflare challenge 和无公开搜索证据继续保持 `OPENING_NOT_FOUND`。477 个测试、15/15 provider、6/6 resolver、13-adapter architecture gate 和 clean `.26` 51/51 fixed live expectations 同步通过。旧 7-case cluster 的准确状态是 4 exact、Viking verified no-match、Kirkland official portal/opening unconfirmed、Nashville 无公开列表。
- `.26` 新一轮 LinkedIn `AI Engineer` live batch 得到 25 家去重公司：21 website、17 career、11 verified job list、9 exact opening。6 个 S5 miss 不再属于单一通用 traversal cluster：Mphasis 暴露 RippleHire、Kforce 暴露 Taleo、Netflix 指向自有 `explore.jobs.netflix.net` portal，Nuro/Melotech 属于 hidden first-party data，Nashville 只有通用申请页。下一 provider contract 优先级改为先比较 Taleo 与 RippleHire 的可复用覆盖和公开接口，再决定实现顺序；Netflix 与两个 hidden-data 样本分别调查，不用单一样本规则污染通用 BFS。
- RippleHire 已作为第 14 个原生 adapter 接入。Fetcher 现在为每个 worker thread 保持仅驻内存的匿名 cookie session；adapter 规范化稳定 board、校验同 tenant redirect/API、解析公开 portal routing token、使用单关键词 translation 和 50 条有界分页，并且不在 trace 中记录 token。Mphasis focused live 从 `JOB_BOARD_NOT_FOUND` 提升到官方 RippleHire job list；公开库存返回 91 个过滤候选但不含当前 LinkedIn 标题，因此诚实保持 `OPENING_NOT_FOUND`。Snapshot hidden-input 脱敏和 redirect request alias 已补齐，12-record live capture 可在 0.2 秒离线重放同一结果。`ADAPTER_VERSION` 提升到 `2026-07-13.27`；487 个测试、16/16 provider、6/6 resolver、14-adapter architecture gate 和 clean 51/51 fixed live expectations 通过，fixed live 为 51/51 job list、50/51 exact opening，4 workers 用时 97.5 秒。下一 provider workstream 是 Taleo/Kforce contract。
- Taleo 已作为第 15 个原生 adapter 接入：支持 custom-domain `/careersection/{code}` board、FacetedSearch shell 指纹、`portalNo/urlCode/lang/src` 配置校验、匿名 REST keyword/location inventory、响应 pageSize 驱动的最多 5 页分页、exact-title early stop 和同 tenant numeric detail URL。Kforce 从 first-party Careers at Kforce 页面自动导航到 Taleo；当前 `AI Engineer` 过滤库存为 0，因此从 `JOB_BOARD_NOT_FOUND` 提升到 verified job list 并诚实保持 `OPENING_NOT_FOUND`。`sessionCSRFToken` 已加入 snapshot 脱敏，8-record capture 的未脱敏字段为 0，并可在 0.3 秒离线重放。Ashby API 失败时不再用空 embedded container 误报 `NO_PUBLIC_OPENINGS`，而是返回 retryable incomplete。`ADAPTER_VERSION` 提升到 `2026-07-13.28`；495 tests、17/17 provider、6/6 resolver、15-adapter architecture gate 通过。两次 clean no-resume fixed live 均为 51/51 job list；轮换 timeout 造成 49/51、48/51 exact，Peraton 及 Harvey/Datadog 随后分别 focused 1/1、2/2 exact recovery，严格 expectations 不下调。下一 failure-cluster workstream 转向 Netflix-owned portal 与 Nuro/Melotech hidden first-party data 的收益比较。
- Eightfold 已作为第 16 个原生 adapter 接入：hosted 与 customer-owned `/careers` 由 URL 或 `smartApplyData` 强页面证据识别，支持 title/location-filtered SSR 首屏、公开 jobs v2 API 有界分页、exact-title early stop、hosted slug 到已验证 customer domain 的映射及 tenant/redirect/detail URL 校验。S5 明确 job-list command 可跨 registrable domain 做有界探测，但只有原生 provider evidence 能确认跨站页面，普通外站 `/careers` 继续拒绝。Netflix focused live 从 `JOB_BOARD_NOT_FOUND` 提升为 exact AI Platform opening；meta `_csrf` snapshot 脱敏已补齐，旧不完整 capture 被 validator 拒绝，新 8-record capture 可在 0.2 秒离线重放。`ADAPTER_VERSION` 提升到 `2026-07-13.29`；506 tests、18/18 provider、6/6 resolver 和 16-adapter architecture gate 通过。Clean 51-company live 为 51/51 job list、48/51 exact、50/51 expectations；Ardent Health 的唯一严格失败是 iCIMS timeout，focused rerun 立即 exact recovery。下一 failure-cluster workstream 是 Nuro/Melotech hidden first-party data。
- Nuro workstream 完成：registry 通过可选 `PageProbeProviderAdapter` 支持 adapter 自己执行有界公开 payload 验证，中央 discovery 不依赖 Nuxt/Greenhouse 细节。Greenhouse Nuxt variant 只接受同源 `/careers/payload.js` preload、Greenhouse-shaped record 和 numeric `gh_jid`，岗位详情仅允许裸域/`www` 等价规范化。Focused live 从 `JOB_BOARD_NOT_FOUND` 提升为 exact opening，91 条 inventory 的 10-record capture 转成 3 fixtures 后可在 0.3 秒离线重放。`ADAPTER_VERSION` 提升到 `2026-07-13.30`；508 tests、18/18 provider、6/6 resolver 和 16-adapter architecture gate 通过。51-company live 经 crash-safe resume 完成为 51/51 job list、50/51 exact、51/51 expectations；Python 3.14.2 multi-worker native interruption 暴露为运行时风险，但 49 个 completion envelope 均成功恢复。Melotech 当前 careers URL 返回 Webflow 404，暂按外部状态处理，不添加无证据 provider 规则。下一轮重新按 live failure cluster 和可复用收益排序。
- Cisco workstream 证明现有 Phenom adapter 已覆盖 provider，主要缺口在 discovery/replay 基础设施。Sitemap index 现在按 scheduled + seen 总量限制为 10 个文件，避免 Cisco 多地区 index 展开几十次 fetch；trace 显式记录 fan-out limit 与未调度数。Snapshot path 使用脱敏 query fingerprint，fixture fetcher 在已有 query variants 时拒绝用 legacy 页面替代缺失分页；redirect alias 从当次 immutable blob 回放，不受后来 canonical recapture 覆盖。Cisco focused live/replay 均读取五页、50 条 title-filtered candidates 和 628 total hits，当前 `Machine Learning Engineer` 无 exact evidence，诚实保持 `OPENING_NOT_FOUND`。`ADAPTER_VERSION` 提升到 `2026-07-13.31`；513 tests、18/18 provider、6/6 resolver、16-adapter architecture gate 通过。长 live gate 被 Homebrew Python 3.14.2 多次 native termination 分割，3 个 strict miss 随后 clean focused 3/3 exact recovery；下一最高优先级从新增 parser 调整为稳定 Python release runtime、启动前版本 gate 和可复现的 native-crash isolation 验收。
- Runtime workstream 完成第一版：项目支持范围收紧为 CPython `>=3.10,<3.14`，release baseline 固定 3.12，并通过 `.python-version`、`RuntimeStatus` policy、`scripts/check_runtime.py --release` 和 Makefile 将离线/实时 release gate 绑定同一解释器。3.14.2 gate 按预期非零，3.12.6 下 517 tests、18/18 provider、6/6 resolver 和 16-adapter architecture 全绿。首次 3.12 clean `--no-resume` live 在 Codex PTY 输出提前结束后仍完成并原子发布全部 51 个 company envelopes；重连为 `restored: 51, pending: 0`，最终 51/51 job list、50/51 exact、51/51 expectations，且无新 macOS Python crash report。下一步是在独立 CI/终端加入同一 `make offline-gates` 与 `make live-gate`，将 Codex PTY 生命周期和 Python native crash 作为不同 failure class 监控。
- GitHub Actions runtime 闭环已实现：push/PR 用 3.10-3.13 matrix 验证支持范围，3.12 release job 执行 `make offline-gates`；网络型 `make live-gate` 只允许手动 workflow，使用独立 concurrency group、20 分钟 timeout，并以 `always()` 上传 14 天 results/trace/summary。首次 Linux CI run `29240521415` 全部成功，未发现跨平台/版本差异。下一步回到 live failure-cluster 排序，继续以可复用收益选择 provider 或 resolver 工作项。
- Dynamic LinkedIn cohort resume 已补齐：`LinkedInDiscoveryManifestStore` 使用独立 discovery version、精确 keywords/location/limit/pages request、稳定 company hash、进程锁、临时文件 + fsync + atomic replace，在下游 company scheduling 前冻结 S1 输出。普通重连 restore manifest；`--no-resume` refresh；`--rerun-stage` 仍复用同一 S1 cohort。真实 smoke 从首次 3 家保存到第二次 `restored: 3, pending: 0`，证明未重新搜索。新的 30-company manifest 在多次 Codex PTY 重连中保持固定 membership/order，完整基线为 27 website、17 career、14 job list、11 exact；521 tests、18/18 provider、6/6 resolver 和 16-adapter architecture gate 通过。下一 failure cluster 优先分析 3 个已有 career 但无 job list 的 Finch、Deloitte、Waltonen，再区分 7 个 `CAREER_PAGE_NOT_FOUND` 中的真实无招聘页与 resolver 缺口。
- Waltonen failure-cluster workstream 完成：其 first-party careers 页已有明确 `waltonen.applytojob.com/apply/jobs/` 证据，缺口是未知 JazzHR provider，而非 generic traversal。第 17 个原生 adapter 只接受 HTTPS 单租户 `*.applytojob.com`，规范化 `/apply/jobs/` board，用 JazzHR/Resumator 公开页面组合指纹读取完整 inventory，并只产生同租户 `/apply/jobs/details/{id}` URL；跨租户 redirect、credentials、非标准端口和伪 detail link 均拒绝。Focused live 在 8.0 秒内 exact 命中 `AI Programmer`，4-record capture 物化成 3 fixtures 后 0.2 秒离线重放同一结果。`ADAPTER_VERSION` 提升到 `2026-07-13.32`；527 tests、19/19 provider、6/6 resolver 和 17-adapter architecture gate 通过。稳定 30-company cohort 的下一项转向 Deloitte regional hiring resolver，再处理 Finch 品牌歧义。
- Deloitte regional workstream 完成：S2 `WebsiteResolutionService` contract 增加可选 job location，U.S. posting 遇到已验证的外区 redirect 时拒绝误选，并在同一 brand host 上有界探测 3 个 U.S. root；搜索慢路径也携带区域意图。S4 对同域 career/sitemap candidate 继承 verified homepage locale，同区加分、跨区重罚，并处理 homepage self-link、明确 career landing root 和 executive/student audience mismatch。S5 允许同 registrable brand 的 `apply` 子域在明确 `Job search` 命令下作为官方 portal。Avature 作为第 18 个原生 page-aware adapter 接入：用 `avature.portal.id/lang/urlPath` 与同 host SearchJobs route 组合证据识别 customer domain，执行 title-filtered search，并只接受同 host/lang/portal 的 numeric JobDetail。Deloitte 从错误 Southeast Asia website 和 Australia sitemap 修正为 U.S. root，最终 exact 命中 `Agentic AI Engineer — Healthcare AI` job `355577`；skip-sitemap live 21.0 秒，完整全球 sitemap live 44.1 秒，8-record capture 0.3 秒离线 exact replay。`ADAPTER_VERSION` 提升到 `2026-07-13.33`；538 tests、20/20 provider、6/6 resolver 和 18-adapter architecture gate 通过。稳定 cohort 下一项转向 Finch 品牌歧义。
- Finch ambiguous-brand workstream 完成：S2 对短而歧义的 company name 引入受限的 LinkedIn identity anchor。只有 company slug 与候选域主标签完全一致、slug 含品牌之外的消歧文本、候选主页已验证时才获得强分；普通 exact brand、任意包含关系及未验证页面不会受益。Finch 从错误 `finch.com` 改为 `finchlegal.com`，随后复用已有 Ashby adapter exact 命中 `Machine Learning Engineer`。8-record live capture 物化为 7 fixtures，0.2 秒离线 exact replay。`ADAPTER_VERSION` 提升到 `2026-07-13.34`；539 tests、20/20 provider、6/6 resolver 和 18-adapter architecture gate 通过。原 30-company 的 3 个 `career -> no job list` failure cluster 已逐项闭环，下一轮转向 7 个 `CAREER_PAGE_NOT_FOUND` 样本并先区分 S2 假官网、S4 discovery 缺口和真实无公开招聘页。
- Standard Template Labs workstream 完成：S2 把 `my.site.com`、`l.ink`、`bit.ly` 定义为可承载证据但不可成为公司 homepage 的 host，并在 redirect 后再次核验；多词品牌的 initials-plus-final-token 缩写候选必须同时匹配域名与已验证 homepage title，LinkedIn slug 的产品/TLD 后缀只用于候选恢复。S5 发现 official first-party payload 中无文本的 native ATS detail 时，在 generic detail 分支前返回 adapter canonical board；可见岗位链接和当前 provider 页面保持原有处理。STLabs 从假 `l.ink` 修正到 `stlabs.com/careers`、Ashby `st-labs` board 和 exact `AI Engineer`。5-record live capture 物化为 3 fixtures，0.3 秒离线 exact replay。`ADAPTER_VERSION` 提升到 `2026-07-13.35`；543 tests、20/20 provider、6/6 resolver 和 18-adapter architecture gate 通过。下一项处理 ModMed：真实 `modmed.com` 静态请求持续 403，需要设计强身份证据下的 browser/search career fallback，不能放宽普通未验证官网门槛。
- ModMed workstream 完成第一版 External Apply 产品契约：公开 guest LinkedIn HTML 不稳定暴露站外 Apply 目标，因此不声称 CLI guest search 可以自动提取；认证浏览器插件、saved authenticated page 或 trusted direct input 可提供 `external_apply_url`。该字段贯穿 `CompanyInput`、result/trace、checkpoint fingerprint、batch/replay export；S2 失败不会再让两阶段 runner 提前结束，S4 可保持 `not_run`，S5 只允许 registry 支持且可识别 canonical board 的 native provider URL，S6 仍读取真实公开库存并进行 title/location match，未知 provider 返回 `PROVIDER_UNSUPPORTED`。ModMed 在官网未解析的情况下由 Workday `ModMed12` board exact 命中 Machine Learning Engineer `R4352`；live 11.4 秒，5-record snapshot 在 0.2 秒离线重放。`ADAPTER_VERSION` 提升到 `2026-07-13.36`；550 tests、20/20 provider、6/6 resolver 和 18-adapter architecture gate 通过。下一步是实现最小浏览器扩展采集层，并回到固定 cohort 的下一个失败簇；扩展只负责从登录态页面提取证据，不复制 provider discovery 业务逻辑。
- Replay website revalidation workstream 完成：S2 只对 `replay_input` 历史官网做 bounded revalidation，旧 URL 以 `preferred_input` 身份获得强制验证槽但不再绕过 resolver；当前用户直接提供的官网仍保持快速路径。停放页检测覆盖 Sedo 模板和 Squarespace `parking-page` 静态资产，并避免用普通营销文案单独判定。Suffolk Construction 依次拒绝 `suffolk-construction.com` 与 `suffolkconstruction.com`，通过搜索证据迁移到 `suffolk.com`，进入现有 iCIMS adapter 并 exact 命中 Site AI Engineer `11054`。冻结的 11-company failure cluster 从 0/11 提升到 1/11 exact，其余 10 家保持结构化失败，无假阳性。`ADAPTER_VERSION` 提升到 `2026-07-13.37`；560 tests、20/20 provider、6/6 resolver 和 18-adapter architecture gate 通过。下一项继续按该簇拆分：先处理 Direct Supply/GPTZero 等已验证官网但无 career route 的 S4 发现缺口，再单独研究 VELOX/Nevis/Mirage 的 S2 身份不足。
- Startup team/Magnolia content workstream 完成：S4 只提升官网实际导航出的 `/team`，页面仍必须有强就业或 ATS 证据，避免对所有公司增加 path probe 或误收普通 leadership 页面。React/Vite shell 可通过同站 module bundle 声明的公开 Magnolia Delivery endpoint补充内容；CMS host 必须 HTTPS 标准端口、包含官网品牌标签，payload response 不得跨 host。S4 和 S5 复用相同 probe，但 Workday/Ashby URL 仍由原生 adapter 验证。GPTZero 到达官方 Ashby board，3 个当前岗位不含旧 Artificial Intelligence Engineer，保持 `OPENING_NOT_FOUND`；Direct Supply 到达 Workday 并 exact 命中 `AI Engineer` `REQ-2026-2441`。15/9-fixture captures 在 0.3/0.2 秒离线回放；冻结 11-company cluster 从 `.36` 的 0/11 提升到 3/11 job list、2/11 exact，其余 8 家无假阳性。`ADAPTER_VERSION` 提升到 `2026-07-13.38`；563 tests、20/20 provider、6/6 resolver 和 18-adapter architecture gate 通过。下一轮转向剩余 5 个 S4 failure，先区分 Eightpoint/Stage 2/Centraprise/Aventis/M|R Walls 的真实无公开入口、隐藏 CMS 与外部 ATS。
- SmartRecruiters tenant-identity workstream 完成：adapter 从公开 inventory 提取 company identifier/name，并仅在非空库存全部与 board tenant 一致时输出 `tenant_identity_verified`；中央 discovery 只消费这一强身份 contract，不解析 SmartRecruiters payload。Centraprise 的 derived board 因此可在旧 `AI/ML Engineer` 标题已下线时仍确认官方 job list，并保持 `OPENING_NOT_FOUND`。官网同时存在 CEIPAL widget，但公开接口明确返回 bot access block，暂归入 browser/provider 后续项，不声称支持。Focused live capture 已离线回放；`ADAPTER_VERSION` 提升到 `2026-07-13.39`；565 tests、20/20 provider、6/6 resolver 和 18-adapter architecture gate 通过。下一优先级是 Eightpoint 与 M|R Walls 的 S2 官网迁移；Stage 2 Capital 与 Aventis Solutions 先验证 portfolio/recruiter 场景中的真实招聘主体，避免把第三方职位误归到公司官网。
- LinkedIn official-website migration workstream 完成：仅在历史官网复核时提前读取公开公司页 JSON-LD，并要求 `Organization` 名称匹配后才把 `sameAs` 作为强候选；所有候选继续通过统一 homepage/redirect/parking/region 校验，普通外链和直接输入快速路径不变。Eightpoint 从旧同名 `.com` 迁到 `eightpoint.io/careers`，当前无公开 job list；M|R Walls 从 `.com` 迁到 `mrwalls.io`，当前无公开 careers 入口。27-record capture 物化为 24 fixtures，离线 0.2 秒复现相同状态。`ADAPTER_VERSION` 提升到 `2026-07-13.40`；568 tests、20/20 provider、6/6 resolver 和 18-adapter architecture gate 通过。下一轮处理 Stage 2 Capital 与 Aventis Solutions 的招聘主体归属，先区分 portfolio board、招聘代理和实际雇主，再决定 S3 hiring-identity contract 是否需要扩展。
- Posting-identity/S3 workstream 完成：新增独立 probe，只对名称呈现投资/招聘中介特征的发布者读取公开 `JobPosting` JSON-LD；alternate employer 必须重复出现至少 3 次、覆盖至少 2 类雇主上下文，并包含 benefits 或 anti-fraud 自有语义，单次技术名词不会触发。Stage 2 Capital 以 12 次 ModMed 提及和 4 类上下文切换到 ModMed，复用官方 Workday exact 命中 `R4352`；Aventis Solutions 明确为代理但客户未披露，只记录 `publisher_role=recruiting_agency` 并保持失败。29-record capture 物化为 28 fixtures，0.3 秒离线复现。`ADAPTER_VERSION` 提升到 `2026-07-13.41`；576 tests、20/20 provider、6/6 resolver 和 18-adapter architecture gate 通过。原 5 个 S4 failure 已完成 Centraprise、Eightpoint、Stage 2、Aventis、M|R Walls 的逐项分类/修复；下一轮回到冻结 11-company cluster 的 3 个 S2 identity failure（VELOX、Nevis、Mirage），先调查真实公司身份与官网，不用搜索命中率替代身份准确率。
- Startup identity/provider-assets/Breezy workstream 完成：S2 将营销前缀 LinkedIn slug 视为 TLD 歧义信号，在 fast selection 前优先读取名称匹配的官方 `Organization.sameAs`，并对薄公开页做一次 trailing-slash 重试；bare domain 同样可规范化但普通外链不升级。S5 可按当前 route 优先读取最多 3 个同源 JavaScript module assets，只有 registry 识别出 provider URL 时才合并为页面证据。Breezy 作为第 19 个原生 adapter 接入，并进入默认五次 derived ATS 探测预算。Focused live 3/3 exact：VELOX -> Breezy Artificial (AI) Engineer，Nevis -> Ashby Applied AI Engineer，Mirage -> 正确 `mirage.app` + Ashby Software Engineer, Agents。`ADAPTER_VERSION` 提升到 `2026-07-13.42`；587 tests、21/21 provider、6/6 resolver 和 19-adapter architecture gate 通过。下一轮应重新运行冻结 cohort，按新的最大 `stage × provider × reason_code` failure cluster 决定目标。
- ADR-0003 LinkedIn official-website evidence cache workstream 完成：cache identity 绑定规范化 company name + LinkedIn company URL，默认 TTL 30 天，只接受严格名称匹配的公开 `Organization` JSON-LD 官网字段。Resolver 保持 live-first、cache-fallback，并以 trace `live`/`cache` 标记 provenance；cached candidate 不绕过 redirect、parking、region 或 brand identity 验证。Schema mismatch、corrupt/malformed/nonfinite/future/expired 数据统一安全 miss，进程锁保护读取与 read-modify-write，同目录临时文件经 `fsync` + atomic replace 发布。CLI 显式 `--linkedin-evidence-cache`、checkpoint 默认文件和 extension output 稳定文件均已接线，`ADAPTER_VERSION` 提升到 `2026-07-13.43`。Content script 增加隐藏 DOM/祖先过滤和 selector harness，loopback bridge 增加真实 CORS/HTTP error contract tests；最终门禁为 616 tests、21/21 provider、6/6 resolver 和 architecture validation 19 adapters / 0 issues。相同 hash 的冻结 30-company clean run 从 v32 的 27/17/14/11 提升为 29 官网、23 career、23 verified job list、18 exact opening；clean cache 仅写入 4 条 live evidence、未使用 seeded fallback。Chrome `Load unpacked` 与一次登录态 LinkedIn DOM scan 仍待人工验收。下一轮最大可泛化 cluster 是 source posting 仍 open、LinkedIn onsite apply、`external_apply_url=null` 且无可跟随 ATS URL；应增加 source-posting availability / `LINKEDIN_NATIVE_ONLY` 终态，并把 Quest Global、Starbridge 等本轮 career regression 与稳定能力缺口分开处理。
- ADR-0004 source-posting terminal workstream 完成：public search card 只输出 `listed + unknown + public_search_card`；登录态 content script 仅从 visible/enabled native/external Apply 或明确 closed banner 产生结构化 evidence，hidden/disabled/missing 保持 unknown。S5 只有在 career/job-board 路径完整且确定性失败时消费匹配当前 job URL 的 `active + linkedin_native + authenticated_detail_dom`，返回 `partial / LINKEDIN_NATIVE_ONLY`；verified board、supported external provider 和任何 incomplete/retryable trace 保持优先，S6 不运行且不制造 official URL。Pipeline status policy 已抽成单一纯函数，legacy/top-level partial 和 unsupported error propagation 一致；checkpoint fingerprint 纳入稳定 source 字段并忽略 observation time，evaluation 独立统计 source disposition。`ADAPTER_VERSION` 提升到 `2026-07-13.44`；640 tests、21/21 provider、6/6 resolver、19-adapter architecture gate 全绿。Chrome unpacked extension 已安装，真实 Scan/Run 验收暂缓；下一主线是冻结 VELOX、Centraprise、Quest Global、Starbridge、Deloitte 五家公司，按历史成功 artifact 修复当前 budget/ranking/traversal regression，不为 Eightpoint、Aventis 或 M|R Walls 添加公司特例。
- 冻结 5-company regression cluster 完成：S2 复用同域 LinkedIn official fast-path 验证结果，S4 在 sitemap 前验证 homepage/common-path evidence，过滤 resource/cross-site sitemap 噪声，并按职位地区优先调度、评分和提前停止 regional fanout；不存在的猜测 ATS 以不可重试 `HTTP_NOT_FOUND` 退出，SmartRecruiters 作为稳定 structured probe 优先验证。Avature 搜索参数统一 Unicode dash 后可确定性复用旧 snapshot。`ADAPTER_VERSION` 提升到 `2026-07-13.45`；647 tests、21/21 provider、6/6 resolver、19-adapter architecture gate 全绿。冻结 focused live 达到 5/5 website、5/5 career、5/5 verified job list、4/5 exact；Centraprise 当前 4 条 verified inventory 无旧标题，Deloitte region-aware live 恢复 US job list 且 v33 snapshot exact replay 成功。完整 30-company cohort 与真实登录态插件验收继续串行保留到最终 gate。
- 冻结 30-company `.45` gate 完成：同一 manifest 从 `.43` 的 29/23/23/18 提升到 30 website、27 career、26 verified job list、21 exact opening，VELOX、Quest Global、Starbridge 恢复 exact，Centraprise 恢复 verified no-match；Eightpoint、Aventis Solutions、M|R Walls 仍按真实无公开入口或未披露客户分类。`.46` failure-cluster 以通用 contract 修复 Akkodis：sitemap cap 改按实际读取计数并优先 job/目标地区 index，语言 locale 与地区分离，同站 HTTP evidence 升级为 HTTPS，`job-results` 作为 first-party listing route 且 page-aware provider 优先，结构化岗位卡片与普通 careers taxonomy 分离。Render capability 缺失只探测一次并静态降级，availability 聚合所有 provider/generic error provenance。Akkodis focused live 稳定输出美国 `en-us/careers/job-results`，S5 不再跨区覆盖，S6 网络不足保持 `discovery_incomplete`。`ADAPTER_VERSION` 提升到 `2026-07-13.46`；661 tests、21/21 provider、6/6 resolver、19-adapter architecture gate 全绿。下一轮继续按冻结 cohort 的 remaining `CAREER_PAGE_NOT_FOUND`/`OPENING_NOT_FOUND` cluster 排序；真实登录态插件验收继续 deferred，不阻塞 provider/pipeline 工作。
- `.47` S4/S6/replay/evaluation reliability workstream 完成：generic matcher 复用已抓 landing page，识别同主机 HTTPS GET 搜索表单并在猜测 query 前提交，拒绝跨站、POST、credentials、非标准端口和敏感 action；native `PROVIDER_VARIANT_UNSUPPORTED` 保留 typed adapter/inventory trace。S4 verification order 优先明确 homepage navigation，generated paths 不再耗尽强证据预算；跨站 redirect 只有原生 URL/page provider evidence 才能入选，Quest Global 从 TimesJobs 假阳性恢复 Phenom exact，GPTZero 恢复 Ashby verified no-match。Failure replay 使用 source-posting allowlist 合并 `linkedin_posting` 与显式 closed evidence，checkpoint fingerprint 在合并后计算，认证 payload 不进入 bundle。Live summary、fixed benchmark、history 和直接 baseline 全部绑定实际 company/expectations cohort，`no_compatible_baseline` 可安全输出。冻结 30-company S4+ 回归为 30/27/26/20；整批超时的 Deloitte 与 Direct Supply 随后最终代码串行 2/2 exact recovery，Kirkland portal HTTP 403 保持可解释 partial。`ADAPTER_VERSION` 提升到 `2026-07-14.47`；680 tests、21/21 provider、6/6 resolver、19-adapter architecture gate 全绿。插件真实登录态验收继续 deferred；下一轮按冻结陌生 cohort 的 remaining failure cluster 排序，并优先处理可复用的 generic/Meta/CEIPAL 能力，不增加公司特例。
- `.48` inventory-completeness/Meta/resolver-fetch reliability workstream 完成：S6 provider contract 新增 `inventory_scope`/`inventory_complete`，并将 `target_location` 传给 adapter；不完整库存的 miss 不再升级为权威 no-match。10 个有界分页 adapter 对中途错误、未覆盖 total、cap、重复 cursor 与 exact 提前停止显式返回 incomplete；location 只用于同标题 tie-break。Meta Careers 作为第 20 个原生 adapter，仅以 visible-page positive evidence 验证具体岗位并固定 `inventory_complete=false`；离线 fixture/provider benchmark 可 exact，匿名 live hydration 仍不稳定，不声称 live stable。Rendered fetcher 为 DOM settle 保留预算，`networkidle` timeout 后继续等待强 detail links，并识别 Lever/Ashby/Workable/SmartRecruiters detail path；generic career search 在 Bing RSS 原始结果全部无效时继续 DDG；S2 在多个同品牌 fast domain 均已验证，或公司名包含域名会丢失的 identity separator 时，优先 LinkedIn authoritative `sameAs`/cache。`ADAPTER_VERSION` 提升到 `2026-07-14.48`；CPython 3.12 全量 702 tests、22/22 provider、6/6 resolver、20-adapter architecture gate / 0 issues。冻结 30-company live 为 30/30 website、29/30 career、27/30 verified job list、22/30 exact，较上一 frozen run 为 +0/+2/+1/+2，failed -2、success +2，8 个 failure bundle 全部成功。真实登录态 LinkedIn Scan/Run 继续 deferred；CEIPAL bot blocked，保持无 adapter。
- `.49` provider-detection/Sitecore/typed-failure workstream 完成：新增 Sitecore/Next native inventory adapter，以及 CEIPAL/Talemetry detection-only adapters；HTML comment 不再激活 retired integration，`OFFLINE_FIXTURE_MISSING` 归 replay 且不可重试，provider/opening diagnostics 保留最具体 typed failure。Sitecore/Next 严格绑定 first-party tenant/config、同源 endpoint、分页 total/range、重复 ID 与 record identity，并规范化公开字段的无害空白；原生 provider 最终标题门槛拒绝 `Engineer` 对具体 AI title 的假阳性。Composition root 增加只缓存成功无 header/body GET 的 per-company bounded LRU。`ADAPTER_VERSION` 提升到 `2026-07-14.49`；CPython 3.12 为 749 tests、23/23 provider、6/6 resolver、23-adapter architecture gate / 0 issues。相同 frozen-30 live 为 30 website、28 career、26 job list、20 exact，10 个 failure bundle成功；Direct Supply 旧入口当前不可达，Finch 旧 exact 岗位已从当前 inventory 下线。Akkodis 可读 official job list 和全部 85 条 filtered inventory并正确 no-match；真实登录态 extension Scan/Run 继续 deferred。
- `.50` typed-board/replay-evaluation/SPA-navigation reliability workstream 完成：ADR-0005 将 S5 page-derived `JobBoard` 以类型化、版本化 contract 传到 S6；provider 默认 runtime-only，只有明确 public locator 可写 checkpoint，CEIPAL credential-like identifier 不落盘。S6 resume 可直接按 provider-owned locator 读取库存，不读取 trace 或重复识别 landing page。Failure bundle outcome gate 现在输出 reproduced、显式 expected transition、fixture gap 和 mismatch，结构变化默认非零；summary/Markdown report 按规模排名 `stage x provider x reason_code` cluster。S4 在最多 3 个同站 asset 中只接受强标签绑定的同源 career route，Direct Supply 在 skip-sitemap snapshot 与当前 live 均恢复 Workday exact；Akkodis current live 到 official Sitecore board，但变化中的 pagination total 保持 `INVALID_STRUCTURED_DATA / discovery_incomplete`。Contract/checkpoint 为 `1.1`，adapter 为 `2026-07-14.50`；765 tests、23/23 provider、6/6 resolver、23-adapter architecture gate / 0 issues。旧 `.49` partial replay 为 4 reproduced、2 fixture gaps、2 mismatches，纠正“bundle 运行完成=失败已复现”的错误指标。下一项是同一 frozen-30 cohort 的最终 live/replay gate与按新 cluster 排序的 checkpoint 精细失效；真实登录态插件验收继续 deferred。
- `.51` locator-policy/final-gate workstream 完成：checkpoint schema 提升到 `1.2`，七个 replay-safe provider 通过注册式 policy 绑定 public URL、origin、path 与 identifier；未知 provider、跨 origin evidence、敏感 query、控制字符、HTML/密钥/JWT/auth 形态内容和越界 locator 在 decode 前拒绝，CEIPAL 等 runtime-only identity 继续不落盘。Fixture gap 与 mismatch 都使 failure replay CLI 非零，跨阶段 expected transition 以声明阶段比较；复用 bundle output 时清理受管 fixture/checkpoint。最终门禁为 774 tests、23/23 provider、6/6 resolver、23-adapter architecture gate / 0 issues；相同 frozen-30 serialized live 为 30 website、28 career、26 verified job list、20 exact，rate delta 全为 0。10 个非成功样本 replay 为 6 reproduced、2 fixture gaps、2 mismatches，留下的是明确的 capture/behavior debt 而不是被“脚本跑完”掩盖的成功。插件真实登录态 Scan/Run 是当前唯一 deferred release gate。
- `.52` request-aware outcome replay workstream 完成：ADR-0006 统一敏感键、sanitized request identity、POST JSON/form body digest、semantic header allowlist 和 terminal fetch failure contract；snapshot/failure-bundle schema 升至 v2，legacy v1 success record 保持兼容。Failure bundle 从首个 non-success stage 复用 authoritative typed handoff，修复 Aventis 同名实体漂移；Sitecore/Next 同 endpoint POST 分页按 body identity 隔离，修复 Akkodis replay 坍缩；Kirkland 的 success-then-403 可结构化复现；CEIPAL 安全接受服务端省略已知空参数，`apikey` 不再通过 metadata/manifest 泄漏。4-company focused live 为 4/4 website、4/4 career、3/4 verified job list，45 page + 1 terminal failure 物化为 46 fixtures，4/4 outcome signature 原样复现。最终门禁为 791 tests、23/23 provider、6/6 resolver、23-adapter architecture gate / 0 issues；同一 frozen-30 cohort 为 30 website、29 career、27 verified job list、21 exact opening，相对 `.51` 为 +0/+1/+1/+1，pipeline 从 20 success / 8 partial / 2 failed 改善为 21 / 8 / 1。9 个 non-success outcome 全部 reproduced，0 fixture gap / 0 mismatch。插件真实登录态 Scan/Run 继续 deferred，不阻塞 provider/pipeline 主线。
- `.53` CEIPAL public-inventory/failure-semantics workstream 完成：CEIPAL 从 detection-only 提升为 page-aware inventory adapter，严格验证 first-party widget、单一 tenant iframe、Origin/Referer、公开 text-only multipart inventory、稳定 count/limit/pages、连续 pagination、重复 ID、最多 50 页和 first-party detail URL。Request identity 对 endpoint path/query/body 统一脱敏，snapshot 中的精确 `[REDACTED]` pagination path 可离线重放但不放宽 host/method/page contract。Centraprise focused live exact，29 fixtures 在 0.3 秒复现同一 opening；固定 provider benchmark 增加完全脱敏的 CEIPAL exact case。S3 未披露代理客户以 `COMPANY_IDENTITY_AMBIGUOUS` 终止；visible explicit-empty 与 candidate fetch-budget exhaustion 分别输出 `NO_PUBLIC_OPENINGS` 和 retryable `FETCH_BUDGET_EXHAUSTED`。Eightpoint/Aventis/M|R Walls focused live/replay 3/3 reproduced。最终门禁为 817 tests、24/24 provider、6/6 resolver、23-adapter architecture gate / 0 issues；同一 serialized frozen-30 为 30 website、28 career、27 verified job list、22 exact，299 page + 85 terminal failures 物化为 291 fixtures，8/8 non-success reproduced、0 gap、0 mismatch。插件真实登录态 Scan/Run 继续 deferred。
- `.54` S4 evidence/diversity scheduler workstream 完成：新增独立 `career_candidate_scheduler.py`，`LinkCandidate` 从 `RawLink` 保留结构化 origin，按 authoritative handoff、明确 first-party career navigation、sitemap/search discovered evidence、speculative probe 和低相关普通导航分层；score 只在同层排序。Speculative 候选先覆盖 canonical host + locale-free route family，再延后裸域/`www` 与 locale alias；五次生产预算保留四类 route coverage 和一次最强 family concrete-host fallback，`subdomain_probe`/blind ATS 不会伪装成 evidence，两字母产品路径不会误作 locale。Trace 保存每次 selection phase，而不是被后续空 search 覆盖，并记录 tier/score/origin/host/locale/family role/truncation/remaining。M|R Walls focused live 的前五次从 `.53` 的近重复 localized URL 修正为 `/careers`、`/careers/jobs`、`careers.` 子域、brand join、`www /careers`，真实结果均为 404/DNS，继续诚实返回 retryable `FETCH_BUDGET_EXHAUSTED`，未增加公司特例。最终离线门禁为 826 tests、24/24 provider、6/6 resolver、23-adapter architecture gate / 0 issues；插件真实登录态验收继续 deferred。
- `.55` deterministic execution/replay workstream 完成：ADR-0007 将全部行为型 `AgentConfig` 冻结为 schema `1.0`，result `2.1`、trace、summary、bundle manifest 与 baseline identity 保存同一 canonical payload/digest；stage checkpoint `1.3` 按 pipeline execution fingerprint 隔离 discovery policy。Batch execution schema `1.0` 另绑定 company/website budget、fetch timeout/retry、render、verify 和 offline policy，batch completion `1.1` 与 baseline 同时使用两个 digest，短 timeout 不会污染后续长预算运行。Bundle schema `3` 从 source record 重建原始 agent 配置，混合/损坏/隐式 legacy provenance fail closed，旧 artifact 仅可显式标记 `legacy_defaulted`。成功或已验证 job-list replay 新增官网、招聘主体、career、job-list、exact-opening 与 provider 的 canonical identity gate；自动 failure/full replay 的 mismatch 或 fixture gap 均使 live gate 非零。`.54` clean frozen-30 为 30 website、27 career、26 verified job list、22 exact；相对 `.53` 的 career/job-list 各 -1 全部来自 Akkodis：上游证据可达但 fresh 运行撞到 45 秒 company deadline，下一轮作为通用完成结果发布/cancellation cluster 处理，不增加公司特例。60 秒 Akkodis focused live 恢复官方 Sitecore/Next job list；schema-3 replay 使用 source config 并对完整 URL/provider identity 达到 1/1 reproduced、0 gap、0 mismatch。最终门禁为 846 tests、24/24 provider、6/6 resolver、23-adapter architecture gate / 0 issues；真实登录态插件验收继续 deferred。
- `.56` process deadline/durable-publication workstream 完成：ADR-0008 将 hard budget 从“大对象 pipe 是否及时送达”改为“结果 envelope 是否在 deadline 前完整发布”。Worker 先成为独立 POSIX process-group leader，大结果通过受约束 `AttemptArtifactTransaction` 写入 destination-local temp、file fsync、atomic replace、directory fsync，pipe 只通知 readiness；共享 monotonic publication time 解决截止前完成但 IPC 延迟的假 timeout，序列化越界仍按 timeout，父进程在 timeout 和 final cleanup 终止整个后代树。Snapshot 的 blob、canonical view、artifact 和 sequence 全部在 JSONL index 前持久化；completion save 失败不会暴露 derived results。完整 stage checkpoint 继续按 execution identity 保留，避免下游失败破坏可复用上游证据。最终门禁为 859 tests、24/24 provider、6/6 resolver、23-adapter architecture gate / 0 issues。Akkodis 在原 45 秒条件下 34.5 秒完成 website/career/Sitecore job list/current exact opening；completion restore 为 1/1，8 fixture 的 schema-3 replay 为 1/1 reproduced、0 gap、0 mismatch。插件真实登录态 Scan/Run 继续 deferred。
- `.57` frozen-30 failure-cluster workstream 完成：S2 拒绝 Spaceship 停放模板，并让无直接身份来源的 non-apex fast candidate 等待 LinkedIn official evidence；S4 只在 tier 0-2 强证据未尝试时报告预算耗尽，纯 speculative truncation 返回确定性 not-found；S5 对官网实际可见、registry 可识别且 URL 本身等于 canonical listing root 的 ATS 链接执行 direct handoff，避免 Taleo 链接被 generic cap 截断，同时保留 Paycom redirect 和 Lever detail 的原验证路径；S6 修正 nested anchor 单词边界，并严格接受同源 career child `/job?jid=<stable-id>`。完整 native inventory 已验证 no-match 后停止 HTML query fallback。最终门禁为 871 tests、24/24 provider、6/6 resolver、23-adapter architecture gate / 0 issues；同一 frozen-30 从 `.56` 的 30/27/24/19 提升到 30/28/26/23，General Motors、Adobe、Zello、Awesome Motive exact，Percepta 恢复 Taleo job list。Akkodis 当前完整 Sitecore inventory 无目标标题，移除冗余 fallback 后 36.7 秒内输出 verified `OPENING_NOT_FOUND`，不再撞 45 秒 hard budget。自动 failure replay 为 5 reproduced / 2 mismatch：Percepta HTTP 500 与离线 invalid payload 的结果选择、Akkodis hard-timeout 与 checkpoint recovery 的 transition 分类成为下一 replay reliability cluster；插件真实登录态验收继续 deferred。
- `.58` replay-integrity workstream 完成：ADR-0009 与 failure bundle schema `4` 将 outer company budget interruption 的离线继续执行单独分类为 `budget_recovery`。只有原始 reason、完整成功前缀、严格后移的 failure stage、无 fixture gap 和已建立 identity 全部满足时 gate 才通过；source identity prefix 与 replay full identity 进入 manifest，普通改善和 identity drift 仍失败，显式 expected transition 同样受 identity gate 约束。Snapshot sanitizer 的 unquoted key 只匹配独立 JavaScript identifier，修复 `code` 误伤 Taleo `urlCode`，同时保留 standalone code、CSRF、token、cookie 和 API-key 脱敏。旧 `.57` failure artifacts 为 5 reproduced、1 budget recovery、1 不可逆旧 blob mismatch；新 Percepta 9-fixture capture 对 live Taleo `SERVER_ERROR` 达到 1/1 reproduced、0 gap、0 mismatch。最终门禁为 876 tests、24/24 provider、6/6 resolver、23-adapter architecture gate / 0 issues。Smart Bricks 已确认 first-party 链到 `app.whitecarrot.io/careers/smart-bricks`，但 capture 没有 WhiteCarrot page/API/detail evidence；下一轮先取得匿名公开 contract，再决定通用 adapter，不添加公司特例。插件真实登录态验收继续 deferred。
- `.59` WhiteCarrot workstream 完成：匿名公开证据冻结了 app tenant API、`/share/careers`、custom `*.whitecarrot.ai/jobs` SSR、detail UUID、API 400 missing tenant 与 404 closed detail 语义。原生 adapter 对 app tenant 使用一次性完整 `roles` inventory，对 custom host 使用强 Next/WhiteCarrot SSR item；两种模式均严格验证 origin/path/tenant/UUID/redirect，排除 Talent Pool、draft、跨站和 malformed records，不读取申请表或登录态。Smart Bricks 与 Whitecarrot 两种变体 focused live 2/2 exact，2-record sanitized capture 离线 2/2 exact replay。固定 provider benchmark 扩为 25/25，最终门禁为 891 tests、25/25 provider、6/6 resolver、24-adapter architecture gate / 0 issues。冻结 30-company 仍保留 `.58` 的 30/28/26/23，待下一次统一整批 live 再比较；插件验收按用户决定暂缓，主线转回剩余 failure cluster。
- `.60` canonical provider-board handoff workstream 完成：同一冻结 30-company `.59` 串行 live 为 30 website、28 career、27 verified job list、23 exact，较 `.58` 多 1 个 job list，但 Smart Bricks 被通用 opaque-path heuristic 误判成 detail，停留在 first-party generic listing。S5 现在以 adapter canonical equality 作为可见 provider board 的决定性边界；WhiteCarrot tenant board 可直接 handoff，而 Paycom/Lever detail 或 legacy URL 因 canonical identity 不等继续执行原有 fetch/redirect contract。Smart Bricks focused live 在 9.7 秒内 exact 命中 `AI Engineer`；6-record sanitized capture 物化为 5 fixtures、0 privacy exclusion，0.5 秒离线 exact replay。`ADAPTER_VERSION` 提升到 `2026-07-14.60`；最终门禁为 892 tests、25/25 provider、6/6 resolver、24-adapter architecture gate / 0 issues。下一轮先批量调查 `.59` 剩余 failure cluster，再统一重跑完整 cohort；插件验收继续暂缓。
- `.61` failure-replay resume workstream 完成：并行审计确认 Eightpoint、GPTZero、Viking、Aventis 均为正确产品终态，Percepta 为可确定性 replay 的真实 Taleo HTTP 500；不修改 matcher、provider contract 或招聘主体边界。Viking 的 fixture gap 来自 S6 replay 丢失 page-derived typed locator。Bundle 现在对 `page_evidence`/`page_probe` 从 S5 重跑并由 snapshot 重新验证 locator，URL-native handoff 仍从 S6 开始，不从 trace 重建运行时身份。`.59` failure replay 从 5 reproduced / 2 gaps 提升为 6 reproduced / 1 gap；唯一 Akkodis gap 保持 fail closed。最终门禁为 894 tests、25/25 provider、6/6 resolver、24 adapters / 0 issues；下一轮进入 hard-timeout durable-stage recovery。
- `.62` hard-timeout durable-stage recovery 完成：并行调查证明 `.59` Akkodis 实际已原子保存 S4/S5，真正超时点是 S6 Sitecore 第 8/9 页附近，但旧 parent fallback 固定重建成 S4 failure。Live runner 现在对 timeout/remote error 按 execution fingerprint 恢复连续、兼容的 success/not-applicable checkpoint prefix，遇到损坏、版本不兼容、语义非法或中间缺口立即停止；gap 后 URL/trace 不得泄漏，S7 failure 的 pipeline 语义保持 failed。Akkodis focused live 在 43.6 秒内完整读取 9 页、83 条 inventory，输出 `OPENING_NOT_FOUND`；16 fixtures、0 privacy exclusion，离线 outcome 1/1 reproduced、0 gap。`ADAPTER_VERSION` 提升到 `2026-07-14.62`；最终门禁为 900 tests、25/25 provider、6/6 resolver、24 adapters / 0 issues。下一轮在统一 30-company 回归前先评估 provider pagination soft-stop 是否需要独立 contract，避免只依赖 45 秒 hard kill。
- `.63` S4 transient-evidence classification 完成：同一冻结 30-company `.62` 统一 live 为 30 website、28 career、27 verified job list、23 exact，Smart Bricks 的 WhiteCarrot exact 增益被 Dematic 的一次官网 career fetch timeout 抵消；failure bundle 为 6 reproduced / 1 Akkodis mismatch。Dematic 上一轮完整命中 Workday exact，本轮首页仍明确给出 `/about/careers/`，但该 tier-1 page link 超时后旧逻辑错误输出 `CAREER_PAGE_NOT_FOUND`。Candidate failure 现携带 reason/retryable/origin/evidence tier，tier 0-2 的可重试失败会成为 S4 真实终态；纯 speculative probe 仍不能制造临时失败。Focused live 已验证 Dematic 改为 retryable `NETWORK_TIMEOUT`。`ADAPTER_VERSION` 提升到 `2026-07-14.63`；最终门禁为 901 tests、25/25 provider、6/6 resolver、24 adapters / 0 issues。Akkodis mismatch 进一步证明 S6 paginated provider 需要在 hard deadline 前 cooperative soft stop；该通用 contract 进入下一轮。插件真实登录态验收继续 deferred。
- `.64` cooperative pagination budget workstream 完成：ADR-0010 冻结可选 `FetchBudget` capability、timeout + publication reserve、Python 3.12 wrapper delegation、nonfinite fail-closed、partial inventory 与负向结论边界；`FetchClient`、result schema 和 checkpoint schema 均不改变。Sitecore/Next JobSearch 在后续页前执行 guard，reserve 不足时不发请求，保留正向 candidates 并输出 retryable `FETCH_BUDGET_EXHAUSTED`、`inventory_complete=false`。Guard 将被拒绝请求按 ADR-0006 保存为脱敏 terminal outcome，离线 replay 不使用 wall-clock 或 trace 重建。Akkodis 同配置 focused live 在 44.7 秒读取 8 页/80 of 83 records 后 cooperative stop，保留官方 job list、无伪造 opening；failure bundle 1/1 reproduced、0 gap、0 mismatch。最终门禁为 912 tests、25/25 provider、6/6 resolver、24 adapters / 0 issues。同一冻结 30-company 统一回归为 30/29/28/24，pipeline 24 success / 5 partial / 1 failed；相对 `.62` 的 website/career/job-list/exact 为 +0/+1/+1/+1，6 个 non-success 全部 reproduced、0 gap、0 mismatch。剩余结果由官方 empty、完整 no-match、未披露招聘主体、Taleo server error 和 Sitecore budget partial 构成，没有新的未知 provider cluster。CEIPAL、Workday 和 iCIMS 只在同类 failure cluster 或 provider fixture 证明收益后迁移，避免无证据的大范围行为变化。
- `.65` Taleo location-filter recovery 完成：公开 shell/JavaScript 证明 `searchjobs` 的 endpoint/method/header 与 adapter 一致，同时证明 `LOCATION` 是 OLF structured field，不保证接受 LinkedIn 自由文本。只有非空 location 请求返回 5xx 时，adapter 才向同一 tenant/portal 单次降级为 title-only；成功 inventory 继续由客户端 location 评分，其他错误保持 incomplete。分页 metadata 必须完整，page number、page size 与 total 在整段 inventory 中保持一致，否则 fail closed。Percepta 从两次稳定 HTTP 500 恢复为 `total=0` 的完整 title-filtered official inventory，15.9 秒输出 verified `OPENING_NOT_FOUND`；request-aware snapshot 通过 body fingerprint 区分失败 location 请求和成功 title-only 请求，离线 1/1 reproduced、0 gap、0 mismatch。该修复不增加 exact opening，也不解析 Taleo shell 的静态 no-results 占位文案。最终门禁为 919 tests、25/25 provider、6/6 resolver、24 adapters / 0 issues。
- `.71` redirect-aware same-run cache workstream 完成：代码审计确认 normal S4-S7 在同一 downstream child 中共享 fetcher，Adobe 重抓不是缺少跨进程 HTML contract，而是 S4 请求 `careers.adobe.com` 后跳转到 `/us/en`，旧 cache 只保存 request URL，S5 按 final URL 查询时 miss。Page cache 现在把 request/page/final URL 绑定到同一 bounded LRU entry，别名共同淘汰且只存在于当前公司进程；checkpoint resume、旧 snapshot 和 trace 均不能命中。并行审计发现并修复 resume preflight 的语义缺口：完整 checkpoint chain 必须能顺序应用到 fresh context，unsupported/malformed updates 或 success stage 缺少 required output 时从 S4 重建。Adobe focused live 为 1/1 website、career、job list、exact，44.8 秒得到 Workday `R168316-1`；capture 对 `careers.adobe.com` 仅 1 次 GET，无 final URL 重抓。全量门禁为 1004 tests、25/25 provider、6/6 resolver、24 adapters / 0 issues；`ADAPTER_VERSION` 为 `2026-07-14.71`。下一轮继续按 frozen cohort 的非插件 failure matrix 聚类，真实登录态插件验收保持 deferred。
- `.72` typed-board discovery completion workstream 完成：Akkodis 的 first-party landing 已通过 `page_evidence` 绑定 Sitecore typed board，但旧 S5 又用 URL-only detector 检查 customer-owned URL，误判 generic 后执行五次 blind ATS search，挤占 S6 pagination 预算。现在 listing-capable typed board 直接结束 S5；detection-only evidence 仍执行 fallback，Talemetry 也明确恢复 `supports_listing=false`。Architecture gate 分别约束两类 capability。复用 `.69` 已脱敏 landing fixture、其余 Sitecore API live 的 focused run 在 27.3 秒读取 9 页、83/83 records，无 `ats_search_fallback`，得到 verified `OPENING_NOT_FOUND`；同 snapshot 从 S4 离线 0.3 秒复现。两次完整网络尝试均在 S4/S5 landing fetch 超时，未冒充完整 live success。最终门禁为 1006 tests、25/25 provider、6/6 resolver、24 adapters / 0 issues；`ADAPTER_VERSION` 为 `2026-07-14.72`。插件验收继续 deferred，下一轮仍由非插件 failure matrix 决定。
- `.73` listing-first/cache/replay reliability workstream 完成：S5 对同站强 listing route 执行 asset/page-probe 之前的有界验证，visible canonical provider link 与纯页面 typed evidence 保持优先，失败后恢复原 fallback 且不重复 trace；Akkodis 不再为已声明的 job-results route 下载三个无关 Next bundle。Page cache 仅在内部 key 中统一 HTTP(S) origin 的空 path 与 `/`，使 redirect final-root alias 在同一 process 命中；跨 S1-S3/S4-S7 process 的第二次首页请求继续保留，不引入 durable HTML cache。Failure bundle 对缺少 diagnostic trace 的 `results.json` 使用 registry capability 推断是否必须从 S5 重验 page-derived board，不从结果或 trace 拼装 locator；`.72` 与新 `.73` frozen-30 的 results/trace replay 均为 30/30 reproduced、0 gap、0 mismatch。最终门禁为 1020 tests、25/25 provider、6/6 resolver、24 adapters / 0 issues。新串行 frozen-30 为 30/29/28/24，24 success / 5 partial / 1 failed，与 `.72` 持平；Akkodis S5 为 55ms，但 S4 16.3 秒网络波动后 S6 仅完成 70/83，按 reserve 保持 `FETCH_BUDGET_EXHAUSTED` partial。`ADAPTER_VERSION` 为 `2026-07-14.73`；插件真实验收继续 deferred，下一轮优先分析 S4 候选调度与跨 phase 证据成本，不猜 Sitecore location/page-size 参数。
- `.74` S4 explicit-list scheduling workstream 完成：frozen trace 证明 Akkodis 的同站 `job-results` embedded route 得分更高却因 tier/boost 不一致排在普通 careers link 后，先消耗一次 6 秒 timeout。Scheduler v2 只把 HTTPS 来源页声明的、HTTPS 同 concrete host、现有 scorer 已标记为 `explicit job-list route` 的 embedded candidate 提升到 first-party tier，并给予与 homepage career navigation 相同 boost；identity tier-0、跨 host、HTTP、普通 embedded reference 和 eligibility threshold 不变。Fresh focused live 第一候选变为 `/en-us/careers/job-results`，S4 10.1 秒、S5 57ms，S6 完整读取 83/83 后 verified no-match；focused replay 1/1，旧 frozen-30 replay 30/30，均 0 gap / 0 mismatch。对 provider-result reuse 的并行审计只找到 VELOX/Breezy 与 Kobie/Lever 两次 parse，底层 transport 已缓存且 location query contract 不同，因此不增加高风险低收益 cache。最终门禁为 1027 tests、25/25 provider、6/6 resolver、24 adapters / 0 issues；`ADAPTER_VERSION` 为 `2026-07-14.74`。下一轮继续评估 S4 stage-wide 总 fetch budget 与跨进程首页 evidence handoff，先冻结 contract 再实现；插件验收继续 deferred。
- `.75` S4 transport-budget workstream 完成：ADR-0011 将总网络上界从 candidate loop 提升为 `find_career_page` 整个 stage 的真实 delegate dispatch budget。Run-configuration schema 升至 `1.1`，CLI/live 默认 32、library 默认 unbounded；PageCache/Snapshot/Retrying/transport-counter 的顺序保证 cache hit=0、retry attempt=1、pre-dispatch reject=0，typed exhaustion 和 privacy-safe phase trace 不改变原 evidence/failure contract。Legacy `1.0` payload/digest 保持完全一致；replay composition 复用原 versioned config，修复 checkpoint seed 与 pipeline fingerprint 因静默 schema 升级而分离的问题。旧 frozen-30 replay 30/30；新 live 的 S4 最大 22/32、平均 3.96、0 rejection。整批原始结果 30/28/27/23，Dematic 唯一一次 tier-1 `NETWORK_TIMEOUT` 随后同配置 focused 16.1 秒 exact 恢复，证明不是 cap 回归；full replay 30/30、failure replay 7/7、focused replay 1/1，全部 0 gap / 0 mismatch。最终门禁为 1045 tests、25/25 provider、6/6 resolver、24 adapters / 0 issues；`ADAPTER_VERSION` 为 `2026-07-14.75`。跨进程 S2→S4 homepage evidence handoff 需要新的持久化/隐私 contract，留到独立 ADR；插件真实验收继续 deferred。
- `.76` checkpoint homepage-navigation handoff workstream 完成：ADR-0012 将跨 S1-S3/S4-S7 process 的复用限制为 execution-bound、URL-only typed evidence，不引入 durable HTML cache。S2 只从最终 verified `Page` 生成 exact homepage 与最多 8 个 query-free public HTTPS career/ATS candidates；context/checkpoint schema 分别升至 `1.2`/`1.4`，S4 exact-match 后仍逐 URL fetch/verify，缺失、损坏、不匹配或候选失败均恢复 legacy 路径。Frozen-30 中 21/27 个 S4 run 使用 handoff，20 个只 dispatch 1 次；总量从 107 降至 70（-34.6%），平均 2.59，最大仍为无 evidence 的 VELOX 22/32，0 rejection。首次完整 live 为 30/29/28/22：Suffolk 因可见 iCIMS board 的 presentation query 未通过 canonical equality 产生真实回归，Adobe 为 Phenom transient timeout，Akkodis 为既有 cooperative partial；不降低 expectations。通用 canonical path 修复后 Suffolk/Adobe focused 2/2 exact、2/2 replay；根 URL `""`/`"/"` replay alias 修复后完整 snapshot 30/30 reproduced、0 gap、0 mismatch，其他 path/query/request identity 继续严格。最终门禁为 1067 tests、25/25 provider、6/6 resolver、24 adapters / 0 issues；`ADAPTER_VERSION` 为 `2026-07-14.76`。完整 cohort 不用 focused 结果伪装重算，最后发布的 full baseline 仍为 30/29/28/24；插件真实验收继续 deferred。
- `.77` typed completion resume 与 snapshot terminal-outcome workstream 完成：ADR-0013 只自动重提完整 stage chain 中首个明确 `retryable=true` 的 non-success，明确 non-retryable 与不完整/歧义 metadata 均 fail-closed restore；失败 stage 及下游 checkpoint 定向失效，上游 checkpoint 和旧 completion 保留到新 completion 原子发布。Completion trace/summary 区分 restore、non-retryable restore、unclassified restore 与 retryable resubmit，crash stale tmp 只在同 fingerprint lock 内清理。Career search 对单次 invocation 内明确 non-retryable 的来源熔断；frozen live 中 Kobie search dispatch 从 6 降到 4，但整批 S4 dispatch 受 blind-ATS 变化影响仍为 70。Snapshot page/failure 共享 sequence 现在按完整 request identity 只物化最新终态，Kobie 和 Akkodis replay mismatch 被通用修复。Frozen-30 首轮 30/28/27/22 后，第一次 resume 为 restored 27 / retryable 3，Dematic 恢复 exact；第二次为 restored 28 / retryable 2，Adobe 恢复 exact，Akkodis 保留 retryable partial。最终 full baseline 为 30/29/28/24，6/6 failure replay、30/30 full replay、0 gap/0 mismatch；门禁为 1081 tests、25/25 provider、6/6 resolver、24 adapters / 0 issues，`ADAPTER_VERSION=2026-07-14.77`。下一持久化工作项是 attempt/stage-scoped snapshot evidence lineage：为 capture outcome、stage checkpoint 和 completion 冻结 scope，避免新 attempt 在某 stage 未产生 outcome 时混用旧 attempt；在此之前 replay mismatch 继续 fail closed。真实插件验收按用户要求 deferred。
- `.78` attempt/stage-scoped replay workstream 完成：ADR-0014 将一个 company invocation 的 opaque attempt ID 贯穿 S1-S3 与 S4-S7 process phase，每个执行 stage 发布 execution-bound `StageEvidenceLineage`；恢复 checkpoint 保留旧 producer，重算后缀使用新 producer，completion 因此可冻结合法 mixed-attempt chain。Snapshot v3 为每个 terminal page/failure 保存 exact scope membership 和 stage-local ordinal，scope 以 count、sequence bounds 与 privacy-safe descriptor digest finalize；cache hit 作为消费 stage outcome 记录，zero-request stage 不借旧证据。Strict outcome tape 按 ordinal 消费并拒绝 missing/early/extra/unconsumed/divergent request；bundle v6 为每个 source occurrence 隔离 record ID、checkpoint root、application、runtime cache 和 cursor，outcome gate 按 record ID join。Legacy v1/v2 snapshot 只允许显式 bundle v5 `legacy_global_latest`，不得与 scoped selection 混用。Context/checkpoint/completion schema 分别为 `1.3`/`1.5`/`1.2`，`ADAPTER_VERSION=2026-07-14.78`。真实 `SIGKILL` acceptance 已验证 S1-S4 从旧 attempt 恢复、S5-S7 新 attempt 重算、未 finalize S5 evidence 保持 orphan 且 bundle v6 只消费 durable references。最终门禁为 1138 tests、25/25 provider、6/6 resolver、24 adapters / 0 issues；产品 frozen-30 baseline 仍为 30/29/28/24，本轮不虚报 live 增益。下一轮回到陌生样本 failure cluster 与 provider hardening；插件真实登录态验收继续 deferred，不阻塞后端主线。
- `.79` regional board/provider hardening workstream 完成：S5 contract 传递 target location，leading language-country locale 产生 typed region；匹配 region 优先、中立 URL 可 fallback、明确冲突 URL 在 direct provider、page evidence、visible provider、portal、traversal 和 ATS search 统一排除，匹配板的 retryable failure 不会被错误地区掩盖。Sitecore Next 允许 record brand 匹配页面主品牌或 page-bound dictionary brand，language 只比较 primary subtag，country/tenant/ID/duplicate/mixed-page 继续严格。Akkodis 90 秒 focused live 在 45.2 秒完成：选择 U.S. board，排除 Belgian board，读取九页 83/83 official records，目标 LinkedIn title 不存在，因此 verified `OPENING_NOT_FOUND`；fresh bundle v6 为 1/1 reproduced、0 gap、0 mismatch。首次 fresh capture 还暴露 scoped replay 用 `read_text()` 归一 CRLF 导致假 digest corruption；现改为复用 byte-validation 已解码的 exact UTF-8 text，并加入 CRLF 回归。`ADAPTER_VERSION=2026-07-14.79`；最终门禁为 1148 tests、25/25 provider、6/6 resolver、24 adapters / 0 issues。Frozen-30 baseline 仍为 30/29/28/24；下一轮继续按其陌生样本 failure cluster 排序，插件验收继续 deferred。
- `.80` regional-hub/replay-determinism workstream 完成：S5 只允许 S4 已验证且非 provider/detail 的初始 career root 作为跨地区导航 hub，所有下游 board、listing、detail 和 candidate 继续执行 target-region gate；Dematic 可从官方 Australia landing 找到 U.S. Workday board，但 Australia board 不会被提升。Bundle v6 从 authoritative S1-S4 records 重建 original source、website 和 career-root 输入语义，避免 exported output 倒灌成 trusted input；执行结束后再附加 replay provenance。Scoped outcome tape 以完整 request identity 和原子 consumed state 支持独立并发请求换序，同时保持重复 identity 的 capture order，任何 missing/extra/mismatch/unconsumed 继续 fail closed；trailing-dot fixture path 在 Python 3.10-3.14 间保持一致。Fresh frozen-30 为 30 website、29 career、27 verified job list、23 exact；full scoped replay 30/30 reproduced、0 gap、0 mismatch。Dematic 同配置 retry 及 focused replay 1/1 exact，首轮 timeout 仍诚实保留。最终离线门禁为 1159 tests、25/25 provider、6/6 resolver、24 adapters / 0 issues；真实登录态插件验收按用户要求继续 deferred。
- `.81` production-gate/evaluation/snapshot reliability workstream 完成：固定 25-case provider benchmark 不再直接调用 legacy agent，而是通过 production `PipelineApplication`、S1-S7、fetch wrapper、execution fingerprint、run configuration 和 evidence lineage；25/25 exact/expectations 保持通过。Evaluation 为每家公司和 failure-cluster company 生成唯一 durable terminal semantic，只读 typed stage result/evidence，不让中间 trace error 覆盖最终证据；summary/report/baseline delta 可区分 exact、verified no-match、no-public-openings、identity ambiguity、retryable、external block、unsupported、replay infrastructure 和 unresolved discovery。Legacy snapshot CLI 对 v3-only/mixed scoped index 在写入前输出 `SCOPED_SNAPSHOT_REQUIRES_BUNDLE_V6` 并非零退出。Fresh frozen-30 为 30/29/27/24；typed completion resume 恢复 Akkodis 后为 30/29/28/24，Percepta 仍保持明确 Taleo/network retryable。Final semantics 为 24 exact、3 verified no-match、1 no-public、1 identity-ambiguous、1 retryable；full scoped replay 30/30 reproduced、0 gap、0 mismatch。`ADAPTER_VERSION=2026-07-14.81`；最终门禁为 1168 tests、25/25 production provider、6/6 resolver、24 adapters / 0 issues。下一优先级是 URL/provider/tenant/opening identity regression gate、CLI checkpoint-prefix parity 和新陌生 holdout cohort；插件真实验收继续 deferred。
- `.82` result-identity/checkpoint-prefix/unfamiliar-holdout workstream 完成：ADR-0015 允许 expectation 冻结非空 identity 子集，并对声明的 website、career、provider、public URL tenant、board URL 和 opening URL 严格比较；company identity matrix、field drift 和 bounded report 让 aggregate rate 无法掩盖 URL/company swap。CLI、live resume、typed completion retry、显式 rerun和 parent-timeout recovery 统一使用连续 authoritative checkpoint prefix；replay input 不再绕过 gap，rerun 在写入前失败，完整恢复显式覆盖 S1-S7。新 15-company title-only holdout 在运行前冻结 official opening URL；最终为 13/15 website、12/15 career、8/15 verified job list、7/15 title-matched opening，但 strict exact identity 只有 6/15。首轮暴露的 P-1 AI→P&G 与 CyberArk→Palo Alto 两个 cross-company false positive 通过 body-only incomplete identity veto 和 Workday auxiliary-route rejection 降为诚实未发现，错误公司 success 为 0；ATS second-source fallback、受约束 acronym/TLD candidate 和 explicit first-party jobs portal priority 均无公司分支。`ADAPTER_VERSION=2026-07-14.82`；最终门禁为 1217 tests、25/25 provider、6/6 resolver、24 adapters / 0 issues。下一轮优先按剩余 S2 acronym verification、S5 provider recall 和 multi-board scope cluster 继续提高 strict unfamiliar success；插件验收作为独立手工 gate。
- `.83` direct-evidence/S2/S5 command workstream 完成：ADR-0016 将 candidate provenance 从 trace 标签提升为执行顺序 contract，先验证 preferred/LinkedIn-official wave；恰好一个候选通过完整 homepage/identity/redirect/parking gate 后不再等待 speculative guesses，直接证据失败或冲突时仍恢复原有有界 fan-out。短品牌标题只新增受限 legal-entity suffix identity；机构 fallback 只接受 4 字符以上、全词首字母、精确 `.edu` 且主机返回 401/403，DNS/timeout/404/其他 TLD/短缩写和更强冲突证据继续拒绝。S5 portal promotion 与 link scoring 共用 job-list command taxonomy，补齐 `find jobs/roles` 和 `explore jobs/roles`，仍要求 HTTPS、同 registrable site 和 jobs/careers/apply 子域。Focused live 中 RIVR 与 Atira 均恢复 exact，Bosch 恢复官方 job list，SNHU 恢复官网但因全站 403 和搜索无有效候选诚实停在 S4；没有公司特例。`ADAPTER_VERSION=2026-07-14.83`；最终门禁为 1226 tests、25/25 provider、6/6 resolver、24 adapters / 0 issues。下一轮先冻结 Visa multi-board portfolio 的 board-local completeness/attempt cap/checkpoint contract，再单独冻结 Hostinger first-party literal same-origin GET inventory 的 SSRF、payload、snapshot 和 replay contract；插件手工验收保持独立。
- `.84` multi-board/fail-closed workstream 完成：ADR-0017 冻结 `JobBoardPortfolio` 的 typed identity、eligible-set completeness、1-8 board 上限、versioned attempt cap 和 all-or-nothing checkpoint。S5 只在主板与目标 title 受众冲突时搜索替代板，每个候选必须由 listing-capable native adapter 验证；搜索错误、熔断、provider failure 或 fetch cap 都使集合 incomplete。S6 只有在全部 eligible board 完整检查后才能输出 company-wide empty/no-match，未尝试板输出 `JOB_BOARD_PORTFOLIO_INCOMPLETE`；单个 complete board 保留旧 trace/evidence。Workday/SmartRecruiters 新增 public URL 与 identifier 严格绑定的 replay-safe policy。Run config/context/checkpoint schema 为 `1.2`/`1.4`/`1.6`，`ADAPTER_VERSION=2026-07-14.84`。Visa focused capture 到达官方 general Workday 与 exact `Sr Data Scientist`，同时发现 S2 拒绝 website-only 输入后 context 仍可残留该 URL；现对无 career-root/external-apply 的 S2 failure 清空 handoff，禁止下游绕过。最终门禁为 1249 tests、25/25 provider、6/6 resolver、24 adapters / 0 issues。下一后端项是 Hostinger first-party dynamic inventory；插件真实验收继续独立手工执行。
- `.85` first-party dynamic inventory/title-identity workstream 完成：ADR-0018 冻结 literal GET、exact-origin HTTPS `api`/`api-proxy`、3 page assets + 1 static dependency、5 MB / 5,000 rows、all-or-nothing same-native-board、typed fetch failure、explicit empty、privacy-safe trace 和 request-aware replay contract。Opaque Nuxt chunks 按声明尾部优先，避免部署 hash 的字母顺序决定 recall；`api-proxy` 只合成 `Bearer <page hostname>`，从不读取或转发 bundle Authorization，因此清洗后的 scoped bundle 仍可消费同一请求。Hostinger live 从 `JOB_BOARD_NOT_FOUND` 改为 verified Ashby board，完整读取 77 条当前岗位；首次 S6 将 `AI Engineer` 错配为 `Full Stack Engineer (Automation & AI Agents)`，现增加 ordered title identity / equal normalized multiset gate，在保留 `AI Algorithm Engineer Intern` 与 `Sr`/`Senior` 合法变体的同时拒绝该 false positive。最终 live 为 website/career/job-list 成功、`OPENING_NOT_FOUND` verified no-match；fresh scoped replay 同结果、0 fixture gap / 0 divergence。`ADAPTER_VERSION=2026-07-14.85`；最终门禁为 1266 tests、25/25 provider、6/6 resolver、24 adapters / 0 issues。冻结 15-company strict exact baseline 仍为 6/15，必须整批重跑后才能更新；插件真实登录态验收继续作为独立手工 gate。
- `.86` upstream-terminal replay/search fallback workstream 完成：ADR-0019 允许 allowlisted original source + scoped lineage 的 company-only terminal result 进入 bundle-v6，并把 lineage 最后 stage 作为 replay `stop_after`；完整性门禁仍要求 source/filter/selection/export/result/trace/comparison 全等，未知 source、缺失或非连续 scope 均拒绝。Replay summary 分开报告 matched/selected/exported/replayed。冻结 15-company clean rerun 为 13 website、12 career、11 verified job list、7 current opening、7 pre-frozen exact；完整 capture 15/15 reproduced、0 gap、0 mismatch。S4 对 RSS 有 raw 结果但零有效 candidate 的情况在预算内继续 Bing HTML，已有有效 RSS candidate 仍停止。最终门禁为 1271 tests、25/25 provider、6/6 resolver、24 adapters / 0 issues。下一轮优先冻结 Bosch e-Spirit CaaS inventory 和 CyberArk acquired-brand portal handoff contract；P-1/Visa 的输入证据与 regional identity 作为独立 S2 workstream，三个 Ashby no-match 不放宽标题门禁。`ADAPTER_VERSION=2026-07-14.86`；插件真实登录态验收继续手工执行。
- `.87` runtime-provider/acquired-brand workstream 完成：ADR-0020 冻结 e-Spirit CaaS page-bound public credential、origin-pinned request、HAL pagination 和持久化隐私边界；ADR-0021 将 acquired-brand handoff 限制为已验证 career root 中同一可见容器的明确关系与 `Search All Jobs` 命令，只在 S5 probe 一个 parent portal，不改变 S3 identity；ADR-0022 冻结 TalentBrew tenant/site/CDN 指纹、localized SSR search、分页完整性和 replay-safe locator。Bosch focused live 通过 e-Spirit CaaS 精确命中 `Data Scientist` opening 并完成 replay。CyberArk 进入 Palo Alto Networks TalentBrew job list，完整读取 148 条过滤记录后确认没有 exact normalized `Data Scientist`，拒绝错误的相近 parent-company role，输出可信 `OPENING_NOT_FOUND`；scoped replay 1/1。`ADAPTER_VERSION=2026-07-14.87`；最终门禁为 1318 tests、25/25 provider、6/6 resolver、26 adapters / 0 issues。下一轮回到冻结陌生样本的最大 failure cluster；插件真实登录态验收继续作为独立手工 gate。
- `.88` replay-preflight/S2/S4 reliability workstream 完成：ADR-0023 要求 scoped replay 在写 input、checkpoint 或启动 pipeline 前验证 effective start 后存在连续 capture boundary；active stage 超时但尚未 finalize scope 时输出原子 `replay_plan_integrity_failed` manifest 和 bounded record diagnostics，live runner 保留完整 summary 后正常 gate failure，不再 traceback。S2 仅把 title 读取范围扩展到最多 64 KiB 的 `head`，普通 identity token 仍限制在前 5 KiB；Visa 因此稳定验证 regional homepage，不降低短品牌门槛。S4 search candidate 与 homepage candidate 共用 target-title audience mismatch；ATS portfolio 先执行一次 provider-neutral careers 查询，再执行 provider-specific bounded queries。Fresh frozen-15 为 14/12/12/8，较 `.86` 为 +1/+0/+1/+1；full outcome replay 15/15。P-1 继续 evidence-insufficient，SNHU/Visa 在当前公共证据下停于 S4，CyberArk verified no-match，Bosch exact；Hostinger 本轮 provider fetch failure 保持 retryable。`ADAPTER_VERSION=2026-07-14.88`；最终门禁为 1322 tests、25/25 provider、6/6 resolver、26 adapters / 0 issues。下一后端轮先以新的陌生样本或冻结 30-company failure cluster 判断是否实现 Talemetry inventory，不能因 provider coverage 指标单独立项；插件真实登录态验收继续独立手工执行。
- `.70` verified-career-root classification workstream 完成：`.69` 统一 frozen-30 live 为 30/29/27/23，Akkodis 38.3 秒 cooperative partial、无 hard timeout；Adobe 在队尾因 S5 重抓已验证 career root 超时而错误输出确定性 `JOB_BOARD_NOT_FOUND`。S5 现在把该 root 作为 tier-0 `verified_career_page` evidence，保留 typed retryable reason；同一 snapshot 定向 replay 稳定输出 `NETWORK_TIMEOUT`、0 fixture gap，并由 gate 显式报告 source outcome transition。后续页面复用必须保持 same-run ephemeral 或另行冻结 durable contract，不能用 trace 或旧 snapshot 偷渡页面。`ADAPTER_VERSION` 提升到 `2026-07-14.70`；离线门禁为 1000 tests、25/25 provider、6/6 resolver、24 adapters / 0 issues。真实登录态插件验收继续 deferred。
- `.69` frozen-cohort reliability workstream 完成：schema-5 full replay 对 source/filter/selection/export/result/trace/comparison 数量做 30/30 完整性门禁；baseline identity 绑定 observed total，partial summary 不能冒充完整 cohort。Adobe 暴露的通用簇被拆为 S5 retryable evidence classification、Phenom landing typed handoff 与 deadline-aware search；Akkodis 暴露的通用簇被拆为 Sitecore 首次/分页统一 reserve、native budget interruption 禁止 generic fallback，以及 parent timeout durable-stage provenance。`.68` frozen live 是 30/29/27/23；同 snapshot `.69` replay 为 30/29/28/23，Adobe 已恢复 official Phenom job list。新的 Viking/Akkodis/Adobe focused live 为 3/3 official job list、0 hard timeout；Viking 是 verified no-match，Akkodis 是 cooperative budget partial，Adobe 是 retryable network partial；完整新 capture replay 为 3/3 reproduced、0 gap、0 mismatch。`ADAPTER_VERSION` 提升到 `2026-07-14.69`；离线门禁为 999 tests、25/25 provider、6/6 resolver、24 adapters / 0 issues。真实登录态插件验收继续 deferred。
- 最小浏览器扩展采集层完成：`extension/` 提供 Manifest V3 content script 和 popup，从当前 LinkedIn Jobs 详情/列表读取最多 30 条可见 evidence，展示 Apply URL 数量并异步报告 job-list/exact-opening rate；不读取 cookie、不模拟点击、不猜 redirect。`ExtensionRunManager` 和 loopback-only HTTP bridge 复用统一 input normalization 与 `PipelineApplication`，使用 bearer token、Chrome-extension Origin gate、256 KiB request limit 和本地原子 artifacts；长任务在 popup 关闭后继续。真实 HTTP smoke 为 health 200、submit 202、最终 1/1 job list + 1/1 exact opening，results/trace/summary 均落盘。ADR-0002 固化 evidence-adapter 边界；556 tests、20/20 provider、6/6 resolver 和 18-adapter architecture gate 通过。Chrome `Load unpacked` 与一次登录态 LinkedIn DOM scan 保留为需用户明确执行的手工验收；完成后回到固定 cohort 下一 failure cluster。

目标：

- 将固定 live benchmark 扩展到每个主要 provider 至少 5 家
- 保存带时间戳的历史 baseline 和 regression artifact（机制已完成；是否将 live artifact 长期提交到仓库需数据保留决策）
- 不只依赖 LinkedIn 当天随机结果

建议 benchmark：

- 5 known Lever（已完成：Ekimetrics、Palantir、Highspot、Spotify、Wishpond；Veeva 迁离 Lever 后已替换）
- 5 known Greenhouse（已完成：Anthropic、Lyft、Brex、Datadog、Airbnb）
- 5 known Ashby（已完成：Notion、Linear、Cursor、Harvey、Perplexity）
- 5 known Workday（已完成：ONEOK、NVIDIA、Adobe、Salesforce、Autodesk）
- 5 known iCIMS（已完成：Ardent Health、Prime Healthcare、Peraton、Chenega、GovCIO）
- 5 known SuccessFactors（已完成：DeLaval、W. L. Gore、Colas、Telstra Broadcast Services、Nova）
- 5 known SmartRecruiters（已完成：SanDisk、Bosch、Ubisoft、Delivery Hero、SGS）
- 5 known Workable（已完成：Plum、Workable、Town Web、ClassWallet、Huzzle）
- 5 known Rippling（已完成：Carv、AllVoices、Terradot、RevOptimal、Spangle AI）
- 5 known BambooHR（已完成：ReachMobi、Soundstripe、beehiiv、Signal 1、SAI360）
- 5 known JS-heavy company career pages（已完成：Plum、Meta、Apple Jobs、Spotify、IIC Lakshya；5 provider/5 technology saved + live 严格 gate 5/5）

验收标准：

- 每次改动后能比较前后成功率
- 报告区分：
  - website resolved
  - career page found
  - job board found
  - concrete opening found
  - false positive prevented

## 推荐开发顺序

Phase 0、Phase 1 和治理基线已经完成。接下来统一按以下顺序执行：

1. Freeze fetch、stage、provider 和 checkpoint 小型 contracts。
2. 先抽取 S4-S6，并保留当前 `JobSourceAgent` 兼容 facade。
3. 建立 provider registry，迁移一个代表性 structured API adapter 验证设计。
4. 逐个迁移其余 provider；每次迁移都运行全量测试和固定 benchmark。
5. 建立 composition root，缩减 CLI/live runner 的业务职责。
6. 达到 Phase 2.5 gate 后，开启多个 provider/resolver/fetch/evaluation 分支并行开发。
7. 每轮由最大 `stage × provider × reason_code` failure cluster 决定功能目标。
8. 完成任意 stage checkpoint、`--rerun-stage` 和 snapshot offline replay。
9. 扩大固定 live benchmark，持续验证 browser budget、并发吞吐和跨运行 regression。

Workday、iCIMS、SuccessFactors、Ashby、Workable 等 adapter 都保留在 backlog 中，但每一轮具体选谁必须由 benchmark 结果决定。

## 每个开发任务的完成标准（Definition of Done）

任何 resolver、provider adapter、parser 或 pipeline 改动，只有同时满足以下条件才算完成：

- 问题能够用固定输入或保存的 snapshot 稳定复现。
- 已明确所属 stage、provider、reason code 和影响样本数。
- 修改位于明确 ownership boundary；跨边界变化先更新 contract 和 contract tests。
- 新增 provider 不增加 stage runner 或 matcher 中的中央 provider 条件分支。
- 业务模块依赖 protocol/contract，不直接构造 HTTP、browser 或 filesystem implementation。
- 正常路径有测试。
- 常见异常路径、空结果和 title mismatch 有测试。
- 不把 404、error page 或推测 URL 当作成功。
- stage trace 包含足够定位信息，但不泄露 cookie、token 或个人数据。
- 对应 fixture/benchmark 已加入，修复前失败、修复后通过。
- 全量回归没有降低其他 provider 的正确率。
- benchmark 前后指标已记录。
- `CHANGELOG.md` 的 `Unreleased` 已更新。
- 计划、架构边界或重大决策变化时，对应 `IMPLEMENTATION_PLAN.md`、`docs/ARCHITECTURE.md` 或 ADR 已更新。
- 变更形成单一目的、可独立回滚的 commit。

## 日常执行循环

后续开发统一按以下循环进行，避免看到一家公司失败就立即加入特殊规则：

```text
运行固定 benchmark
  -> 查看七关漏斗和失败矩阵
  -> 按 stage/provider/reason 聚类
  -> 选择覆盖面最大的 failure cluster
  -> 保存最小失败样本
  -> 写验收标准和预期指标提升
  -> 实现通用修复
  -> 跑局部测试
  -> 跑全量 benchmark
  -> 比较 baseline、检查回归
  -> 更新 changelog/计划/ADR
  -> 创建单一目的 commit
  -> 进入下一轮
```

遇到单个特殊公司时，先按以下顺序判断：

1. 是否是已有 provider 的新 URL 或 payload 变体。
2. 是否与其他失败共享同一 stage 和 reason code。
3. 是否能通过 provider 级通用规则解决。
4. 是否属于临时网络问题，只需重试。
5. 是否属于外部限制或正常空结果。
6. 只有无法通用化且业务价值足够高时，才增加 company-specific override。

## 当前可汇报说法

当前项目已经不是一个简单脚本，而是一个可扩展的 job-source discovery pipeline：

- LinkedIn discovery 已经接入
- 官网解析和品牌/母公司招聘体系映射已实现
- career page discovery 有 homepage/common path/sitemap/search fallback
- provider-specific ATS 能力已迁移到自动发现的独立 registry/adapter modules，共 26 个 module；25 个提供 inventory/positive evidence，仅 Talemetry 为 detection-only
- Greenhouse、Lever、SmartRecruiters、Workday、Ashby、BambooHR 已接 structured API
- iCIMS、SuccessFactors、Workable、Rippling 已加入原生 structured page / embedded JSON / verified-link extraction，但还需要更多真实站点 live hardening
- browser fallback 已经从全量渲染升级为 smart fallback + render budget
- batch evaluator 已经能输出 results / trace / summary，固定离线 benchmark 可作为回归测试

最诚实的当前状态：

> 七关状态模型、统一错误码、benchmark 矩阵和 SOLID 并行开发架构已完成第一版。Correctness stabilization 已实现 S3-S7 typed opening identity continuity、S7 fail-closed validation、complete-inventory availability、typed fetch failure fidelity、六类 evaluation disposition、安全 annotation merge 和 identity-aware replay outcome gate，没有新增 provider 或公司特例。一次冻结但已观察的 30-company live 为 19 exact、19 success / 5 partial / 6 failed；独立 artifact review 得到 exact precision 19/19、conditional exact recall 19/24（另 2 条 eligibility unknown）、system defect 7/30。完整 scoped replay 为 30/30 reproduced、0 gap、0 mismatch。该结果不能称为 blind，下一轮应先冻结陌生 holdout；provider/heuristic 扩张继续暂停。
