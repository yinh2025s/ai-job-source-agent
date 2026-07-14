# Architecture

## Purpose

AI Job Source Agent 将 LinkedIn 或固定公司输入转换成经过验证的官网、招聘页、job board 和具体 opening。系统优先保证证据可追踪、失败可分类和结果不编造。

## Dependency Direction

依赖只能由外向内流动：

```text
CLI / batch scripts / loopback extension bridge
        |
        v
orchestration / stage runner
        |
        v
stage contracts and application services
        |
        +------------------+
        v                  v
provider adapters      resolver services
        |                  |
        +---------+--------+
                  v
             fetch contract
                  |
                  v
      HTTP / browser / retry / snapshot

models, reason codes and schemas are shared contracts at the center.
evaluation/reporting consume versioned outputs and do not control execution.
```

内层模块不得 import CLI 或 batch runner。Provider adapter 不得依赖某个具体 browser/fetcher。Reporting 不得读取 runner 的进程内对象。

## Input And Execution Identity

Company/posting identity and execution identity are separate contracts. `input_fingerprint`
contains only stable domain input. `DeterministicRunConfig` contains the bounded behavior
settings that alter candidate scheduling, fetch/search limits, sitemap policy, and search
timeout. `execution_fingerprint` combines their digests and is the key for stage checkpoints
records. `BatchExecutionConfig` separately captures company/website wall-clock budgets,
fetch/retry policy, render policy, verification limit, and offline mode. Batch completion keys
combine both configuration digests; stage checkpoints remain reusable across transport-budget
changes because they contain only already-published compatible stage executions.

The composition root constructs one canonical run configuration and injects it into the
pipeline. Result, trace, summary, and replay bundle use that same agent payload; live summary
also records the batch execution payload. Replay reconstructs
the source `AgentConfig`; it never reads behavior settings from trace fragments or machine-local
CLI state. Per-company replay input remains configuration-free. See ADR-0007.

Successful replay is a data-contract check as well as a control-flow check: canonical website,
hiring entity, career page, job list, opening, and provider identity must remain equal. Focused
failure replay may compare the first non-success signature, while full-outcome replay applies
the stronger identity gate to successful records. Listing-capable adapters define canonical
provider-board identity for career/job-list route comparison; unknown routes, provider or tenant
changes, and exact-opening changes remain strict mismatches. A typed fixture gap in failed,
partial, or identity-drifting replay remains incomplete, while an unused probe may be ignored only
when both source and replay have identical complete success identity.

ADR-0014 extends execution identity to the captured evidence itself. One company
invocation owns an opaque attempt ID shared across its process phases; every executed
stage publishes typed lineage containing its execution fingerprint, producer attempt,
and an optional exact snapshot scope. Restored checkpoints retain the producer that
created them, so selective retry may intentionally produce a mixed-attempt completion
without making evidence ambiguous. Snapshot v3 scope membership is defined by stage-local
request ordinals plus a terminal-descriptor digest, not by timestamps or global sequence
bounds. Scoped replay bundle v6 consumes one strict ordered tape per referenced scope and
isolates every source occurrence by record ID, checkpoint root, application, cache, and
tape cursor. Legacy v1/v2 snapshots use explicit bundle v5 materialization; scoped and
legacy records cannot be mixed. Missing, extra, corrupt, cross-stage, or unconsumed
outcomes fail closed, and unreferenced records from interrupted stages remain orphans.

## Standard Pipeline

| Stage | Owner | Input | Output |
| --- | --- | --- | --- |
| S1 LinkedIn discovery | discovery | search request | company job inputs |
| S2 Website resolution | resolver | company identity hints | verified official website |
| S3 Hiring identity | resolver | company and website evidence | hiring entity and career root |
| S4 Career discovery | stage | verified company context | verified career page |
| S5 Job board discovery | stage/provider registry | career page evidence | provider and verified board |
| S6 Opening match | provider/matcher | board, title, location | ranked opening or normal no-match |
| S7 Result validation | validation | accumulated stage outputs | versioned final result |

同一家公司的 hard dependencies 顺序执行；不同公司可以 bounded parallel。S4 内的独立 probe 可以受预算约束并发，但第一个结果仍需通过统一验证。

S6 的职责进一步拆成三层：provider/matcher 记录官方库存读取状态、候选数量和最佳标题分数；`opening_availability` 只根据这些证据和显式来源状态生成保守诊断；stage 将诊断写入标准 reason/evidence。搜索未命中或 provider 读取失败不能单独证明岗位已关闭，只有明确的来源状态才能产生 `OPENING_CLOSED`。

S5 不把 generic career landing page 自动视作 job list。成功必须来自已知 provider、页面中的具体 job-detail evidence，或经过有界 traversal 后抵达的明确 first-party listing/search route；仅链接到下游搜索页的导航页面不能继承该搜索页的证据。

JavaScript-only career page 可以通过 ADR-0018 的 first-party dynamic inventory probe 产生 S5 provider evidence。Probe 只扫描最多三个同源 page asset 和一个静态 import dependency；opaque hash chunk 按页面声明尾部优先，命名 route chunk 保持最高优先。接口必须是字面量 job-list GET 和 exact-origin HTTPS `api`/`api-proxy` base。Payload 上限为 5 MB / 5,000 rows，每条 URL 都必须由 listing-capable native adapter 绑定到同一个 canonical board；坏行、mixed tenant、redirect、跨源、超限和不完整 fetch 全部 fail closed。`api-proxy` 只发送由 page hostname 合成的公开 marker，不读取 bundle Authorization。Trace 不保存 opening rows，snapshot 按普通 request-aware GET 记录并可在清洗后 replay。

S6 的 title score 只负责排序，不能单独建立岗位身份。候选还必须保留 target token 顺序，或在 level alias 规范化后具有相同 token multiset；单 token target 只接受规范化同一标题。这样允许 `AI Algorithm Engineer Intern` 对 `AI Engineer` 的有序细化，但拒绝先出现 `Engineer`、后出现 `AI` 的另一角色描述。完整 provider inventory 中没有通过 identity gate 的候选时，输出 verified no-match，不选择最高模糊分作为 opening。

S5 的 board eligibility 还受 target location 约束。只有 URL 的 leading path segment 明确采用 language-country locale 时才提取 country region；无 region 的 URL 保持中立。目标 region 已知时，匹配 URL 优先，中立 URL 可作为 fallback，明确冲突 URL 在 URL-native provider、page-derived provider、visible canonical link、first-party portal、traversal 和 ATS search 各入口统一排除。匹配 board 的 retryable failure 不能被冲突 board 覆盖；没有兼容 fallback 时继续发布原 typed retryable failure。Location 缺失时保持旧候选顺序和行为。

## S4 Candidate Scheduling

S4 将候选验证与候选调度分开。纯调度策略位于 `job_source_agent/career_candidate_scheduler.py`；`pipeline.py` 只负责编排 fetch 与验证。验证仍由页面、redirect、CMS 和 provider evidence gate 决定；调度只决定在固定 fetch budget 内先检查哪些候选，不能把猜测 URL 提升为成功。

`LinkCandidate.origin` 是调度的结构化输入。证据层级固定为：identity/provider handoff、明确 first-party navigation、sitemap/search 等 discovered evidence、common path/subdomain/blind ATS 等 speculative probe；不含 career 语义的普通官网导航位于这些候选之后。Score 只在同一证据层内排序，不能让高分猜测压过明确官网导航。Legacy 手工 candidate 可以暂时从 reason 兼容推导，但生产 scorer 必须保留 `RawLink.origin`。

同一 speculative 层先按 canonical host 与 locale-free route family 选择代表，再延后裸域/`www`、locale 和同族 alias。代表优先官网当前 concrete host、无 locale 的较短路径；两字母产品路径不能自动当作 locale。生产的五次 S4 fetch budget 在有足够 speculative 候选时覆盖四个 route family，并为最强 family 保留一次 concrete-host fallback。Fallback 只延后、不删除，且不能挤掉更高证据层的候选。

Trace 记录 schedule policy/version、origin、evidence tier、score、canonical/concrete host、locale、route family、family role、eligible/bounded/truncated 数量以及预算耗尽后仍未尝试的候选数。Checkpoint adapter version 在调度 contract 改变时失效，避免旧 S4 成功或失败掩盖新顺序。

## Browser Extension Boundary

`extension/` 是 S1 evidence adapter，不是第二套 pipeline。Content script 只读取当前 LinkedIn Jobs DOM 中可见的 company/title/location/job URL、company URL、可选 External Apply URL，以及详情页明确可见的 apply/closed 状态。它不读取 cookie、不实现 ATS detection、不猜测 Apply redirect，也不验证官网岗位。只有 visible + enabled 的详情页 native Apply 控件能产生 `active + linkedin_native + authenticated_detail_dom`；隐藏、disabled 或缺失控件保持 unknown。

`scripts/extension_bridge.py` 只绑定 loopback，使用 bearer token 和 Chrome-extension Origin gate 接收最多 30 条记录，并通过后台 run manager 调用统一 `PipelineApplication`。Bridge 可以持久化 results/trace/summary，但不包含 resolver/provider 规则。S5 必须通过 provider registry 识别 External Apply board，S6 继续负责真实库存验证。

## Source Posting Availability

ADR-0004 定义 `source_trace.linkedin_posting` 的生产和消费边界。公开 LinkedIn search card 只产生 `listed + unknown + public_search_card`，缺少站外链接不能推导 native apply。认证详情 DOM 只有在 Apply 控件明确、可见且 enabled 时才产生 active apply-mode；source job URL 必须规范化并与当前 record 匹配。

S5 只在官网/ATS 路径确定性失败且 trace 不含 fetch/provider/parser error 时消费 `active + linkedin_native`，返回 `partial / LINKEDIN_NATIVE_ONLY` 和 typed evidence。已验证 board、受支持 External Apply、retryable failure 与 incomplete discovery 均优先；该终态不写 career/job-list/opening URL，S6 保持 `not_run`，S7 与 legacy/top-level 状态统一为 partial。Evaluation 将 source disposition 与 S6 official-inventory availability 分开统计。

## LinkedIn Website Evidence Cache

ADR-0003 定义 S2 的持久化 LinkedIn official-website evidence boundary。Store key 是规范化 company name 与规范化 LinkedIn company URL 组合的 SHA-256；单独的公司名或 slug 不足以标识记录。Value 只包含该组合身份、严格名称匹配的公开 `Organization` JSON-LD 官网 URL 和观测时间，默认 TTL 30 天。

Resolver 始终先请求 live 公司页，仅在当前页面没有匹配官网时加载 cache，并在 trace 中把 provenance 标为 `live` 或 `cache`。Cached evidence 只是候选，不是 identity override 或成功结果；它仍经过 redirect、parking、region 和 brand-identity verification，也不能单独证明 career page、job list 或 opening。LinkedIn 公司页保留 bounded trailing-slash retry。

Filesystem store 将缺失文件、schema mismatch、corrupt JSON、malformed roots/records/URLs、future/nonfinite timestamp 和过期记录视为安全 miss。读取与 read-modify-write 保存持有进程锁；写入使用同目录临时文件、file `fsync`、atomic replace 和 directory `fsync`，replace 失败时保留上一完整文件。CLI 可通过 `--linkedin-evidence-cache` 指定路径；否则 checkpoint 使用 `<checkpoint_dir>/linkedin-website-evidence.json`，extension manager 在其 output directory 复用同名稳定文件。并行 worktree/benchmark 不共享 cache root。

Cache 只允许公开公司级 URL evidence，不得保存求职者身份、个人 profile、职位页 HTML、cookies、tokens、request headers、browser storage 或 authenticated LinkedIn payload，也不得把 company-specific cache records 提交到仓库。自动化 extension smoke 不覆盖真实登录态 DOM 或本地安装；Chrome `Load unpacked` 与一次登录态 LinkedIn scan 仍是独立人工验收。Release 以陌生冻结样本的 exact-opening 与 verified-job-list 结果为最终指标，不以 cache hit count 代替。

## Target Contracts

Stage 只接收和返回版本化数据，不互相调用内部方法：

```python
class Stage:
    name: str

    def run(self, context: PipelineContext) -> StageExecution:
        ...
```

Provider adapter 封装自己的识别、请求和解析变化：

```python
class ProviderAdapter:
    name: str

    def recognize(self, url: str, page: Page | None = None) -> bool:
        ...

    def list_jobs(self, board: JobBoard, query: JobQuery) -> AdapterResult:
        ...
```

Registry 负责选择 adapter。原生 provider module 导出一个 `ADAPTER` 实例后会被自动发现；新增 provider 不需要修改中央 registry 或 stage 条件分支。

对 customer-owned career domain，adapter 可以选择实现 `PageAwareProviderAdapter.identify_board_from_page(Page)`。Registry 只负责依次询问实现该扩展的 adapter；ATS 指纹、tenant isolation 和 board 构造仍封装在 provider module 内。URL host 识别优先，只有 URL 不透明时才使用已抓取页面证据。

Page-aware adapter 分为 inventory/positive-evidence 与 detection-only 两类。Detection-only adapter 可以识别并绑定 tenant，但在成功 inventory schema 被冻结和验证前必须返回空 candidates、`inventory_scope=unknown`、`inventory_complete=false`；bot、403 和未知 JSON 不能解释为空库存或 no-match。当前 Talemetry 遵守该边界；CEIPAL 已在 `.53` 通过冻结的公开 widget/iframe/inventory contract 提升为 inventory adapter。

e-Spirit CaaS 的公开 browser credential 只允许在 page-aware adapter 的当前进程 handoff 中存在。它不进入 board identifier、trace、checkpoint、snapshot 或 request identity；请求使用不会随 redirect 转发的 `Authorization` header，并将 API origin、tenant、project、collection、分页和详情 URL 绑定到同一份已验证页面配置。脱敏 snapshot 中的精确 `[REDACTED]` 只作为 replay sentinel，不恢复真实 secret。

Acquired-brand portal 是 S5 的单次 probe，不改变 S3 招聘主体。只有已验证 career root 中同一可见语义容器同时给出明确收购关系和 `Search All Jobs` 命令时，才允许探测一个 parent portal；目标页必须通过 parent metadata identity 和 listing-capable native provider 双重验证。该 handoff 的 S6 opening 需要 exact normalized title，不能用母公司相近职位冒充被收购品牌职位。

TalentBrew page-aware adapter 通过 tenant/site metadata、localized same-origin GET search form、hidden organization ID 和精确 CDN tenant asset 共同识别 customer-owned board。它只请求受约束的 SSR `/search-jobs` 分页，严格验证计数、页码、job ID、locale、tenant 和详情 URL；只有完整读取过滤库存后才可发布 no-match。

ADR-0005 定义 page-derived board 的跨关卡 contract。S5 可输出 `DiscoveredJobBoard(JobBoard, detection_method, evidence_url)`；S6 按 board provider 选择 adapter，并让 adapter 继续负责 locator、origin、tenant、response 和 detail URL 验证。`JobBoard` 默认 runtime-only，只有 provider 明确标记的 public locator 才写入 checkpoint；CEIPAL 的 API-key-shaped identifier 仍只在当前进程内使用，raw page、cookie、credential header/body 和认证内容不持久化。其公开 multipart request 只以脱敏 URL、结构化 body digest 和 allowlisted semantic headers进入 snapshot identity。旧 checkpoint 或 runtime-only handoff 缺失时继续使用 URL/page detection fallback，trace 不作为运行时输入。

Provider adapter 也可以在统一 trace contract 中输出强租户身份证据，例如 SmartRecruiters 的 `tenant_identity_verified`。该布尔值必须由 adapter 根据 provider 自己的结构化 inventory 和 tenant 规则计算；中央 discovery 只组合“非空库存 + 强身份”结论，不读取或解释 provider-specific payload。这样 derived board 的验证可以复用，同时保持 provider 高内聚和 pipeline 低耦合。

Taleo FacetedSearch 的 `LOCATION` 是页面 OLF selector，而不是任意文本字段。Adapter
可以先尝试来源 location，但只有该非空 filter 导致 5xx 时，才向同一已验证
tenant/portal 单次降级为 title-only inventory，并把 location 留给客户端 ranking。
其他错误不触发降级；静态 shell 的空 tbody、no-results 占位与 localization resource
不能作为 empty inventory。REST 响应的 page number、page size 和 total 必须
完整且跨页稳定，不一致时 fail closed。所有 Taleo failure 都保持
`inventory_complete=false`。

S6 adapter result 通过 `inventory_scope` 和 `inventory_complete` 区分“读取到正向岗位证据”与“完整检查了可用于 no-match 的库存”，并由 stage 把 `target_location` 传入 provider query。只有与目标查询相关且完整的库存才能支持权威 no-match；`inventory_complete=false` 的未命中必须保持 incomplete。CEIPAL 对 first-party wrapper、单一 tenant iframe、公开 inventory endpoint、稳定 count/limit/pages、连续 next/previous、重复 ID 与最多 50 页执行整体校验；具体岗位 URL 只由 verified record ID 构造回 first-party board。中途错误、cap、循环和矛盾 metadata 都保守为 incomplete。Location 只对同标题候选加 tie-break 分数，缺失地点不会拒绝岗位。Meta Careers 原生 adapter 只接受 visible-page positive evidence，可确认明确出现的具体岗位，但固定返回不完整库存。其离线 fixture/provider benchmark 可保证 exact parsing，匿名 live hydration 仍不稳定，不能据此声明 live stable 或 no-match。

Sitecore/Next adapter 从 first-party `__NEXT_DATA__` 绑定 site、brand、language、country 和 search configuration，仅调用同源固定 jobs endpoint；分页 total/range、重复 job ID 和 record tenant identity 任一矛盾都会保持 incomplete。公开 record 的无害空白可以规范化，但 job ID、endpoint 和租户边界不放宽。原生 provider 的最终 opening 选择使用比通用页面探索更严格的标题门槛，单个泛化角色词不能验证具体职位。

S2 对历史输入执行官网复核时，可把 LinkedIn 公司页中公司名匹配的 JSON-LD `Organization.sameAs` 作为强 identity evidence。多个同品牌 fast domain 都已通过基础验证，或公司名包含域名会丢失的 identity separator 时，LinkedIn authoritative `sameAs` 或同一证据 contract 的 cache candidate 优先；解析器只负责结构化证据提取，候选仍必须经过统一 homepage、redirect、parking、region 和 company-identity 验证。普通页面外链与当前用户直接提供的官网不改变快速路径。

S3 的 posting-identity 扩展由独立 probe 负责，且只在发布者名称呈现投资或招聘中介特征时读取公开 LinkedIn detail，避免给普通公司批量增加网络请求。Probe 输出 `alternate_employer`、`agency_unresolved` 或不改变身份的状态；S3 组合现有品牌/官网 resolver，只有重复雇主自述和 employer-owned 上下文同时成立才允许切换。未披露客户的代理职位以 `COMPANY_IDENTITY_AMBIGUOUS` 终止，S4 因依赖未满足而不运行，不能搜索代理官网或猜测招聘主体。

Fetcher contract 保持最小：

```python
class FetchClient(Protocol):
    def fetch(
        self,
        url: str,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> Page:
        ...
```

ADR-0010 另定义可选 `FetchBudget` capability，只由具有 cooperative deadline 的
wrapper 暴露 `remaining_fetch_seconds()`；无 deadline 返回 `None`，有 deadline
返回非负剩余秒数。它不改变 `FetchClient`，也不暴露绝对 monotonic deadline。
分页 provider 在下一次请求前可通过共享 reserve helper 留出当前 request timeout
和 publication reserve；reserve 不足必须返回 retryable
`FETCH_BUDGET_EXHAUSTED`、保留正向候选并标记 incomplete，不能形成 empty/no-match
负向结论。

HTTP、browser、retry 和 snapshot 通过组合实现相同 contract。Rendered fetcher 为 DOM settle 保留独立预算；`networkidle` timeout 后不会立即放弃当前页面，而会在剩余 settle budget 内继续等待强 job-detail links，包括 Lever、Ashby、Workable、SmartRecruiters 和 Meta 的原生详情路径。Generic career search 的 source fallback 以有效候选而非原始结果数量为准，Bing RSS 返回结果但全部验证失败时仍继续 DuckDuckGo。

Composition root 最外层使用每公司进程内的 bounded LRU page cache，只缓存成功、无 body、无 headers 的 GET response，使 S4/S5/S6 可以复用同一 landing page。Request URL、`Page.url` 与 redirect `final_url` 作为同一 entry 的别名一起命中和淘汰，因此下游按最终 URL 访问不会重新 transport；内部 alias key 仅把 HTTP(S) origin 的空 path 与 `/` 视为同一根路径，真实 transport URL、query/fragment、非根 trailing slash 和 snapshot identity 均不改变。POST、带 headers 请求、失败响应和跨进程状态不进入 cache；cache 内容不写入 checkpoint、trace 或 snapshot，snapshot/retry 仍位于 cache 内层并保持各自 contract。

ADR-0011 为 S4 `find_career_page` 定义独立的 transport-call budget。`AgentConfig.max_career_discovery_transport_calls` 在 schema 1.1 是 `int | None`：CLI/live 默认 `32`，library 默认 `None`；schema 1.0 继续无界并保持完全相同的 payload/digest。S4 composition 顺序为 `PageCache -> Snapshot -> RetryingFetcher -> S4 transport counter -> delegate`，故 cache hit 为 0、每次 retry attempt 为 1、delegate dispatch 前被拒绝为 0。有限预算耗尽返回 typed `FETCH_BUDGET_EXHAUSTED`，不改写 career evidence 或 failure taxonomy。每次 `find_career_page` 单独计数；trace 仅记录 privacy-safe 的 `policy`、`limit`、`dispatched`、`remaining`、`exhausted`、`rejected`、`by_phase` 与 `cache_hits`，phase 限于 `homepage`、`bundle_navigation`、`sitemap_discovery`、`search_discovery` 和 `{schedule_source}_candidates`。计数器和这些 trace 聚合属于 S4 composition；PageCache、Snapshot、Retrying 和 scheduler 保持各自职责。跨进程 S2 website evidence 到 S4 homepage 的 typed handoff 由 ADR-0012 单独约束。

ADR-0012 将该跨进程优化限制为 execution-checkpoint 内的 typed URL evidence，而不是 durable page cache。S2 只有在最终选中的公司首页已经完成身份验证时，才可发布 exact homepage URL 和最多 8 个 URL 本身具有 career/registered-ATS 语义的 query-free public HTTPS candidates。Payload 不保存 HTML、link text、title、timestamp、request identity、headers、cookies、tokens、browser state 或 trace；credentials、query、fragment、local/private host、secret/HTML-shaped content、重复、越界和 unknown fields 均 fail closed。S4 只在 homepage exact match 时把这些 candidates 作为 first-party scheduling input，每个 URL 仍须真实 fetch 和验证；typed candidates 不能成功时恢复原 homepage fetch/extract/bundle/search 路径。Context contract `1.2` 与 stage checkpoint `1.4` 让旧/损坏记录安全失效，不做隐式迁移。

`.76` frozen-30 验证中，23 个 S2 checkpoint 发布该值，21/27 个实际执行 S4 的公司消费成功；20 个 S4 只需一次 dispatch，总 transport 从 107 降至 70，平均从 3.96 降至 2.59。Replay 只把 HTTP(S) 根 URL 的空 path 与 `/` 视为等价，其他 path、query 和 request identity 仍严格；最终 full replay 为 30/30 reproduced、0 gap、0 mismatch。可见 provider URL 若由 adapter 规范化为同 scheme/host/path 的 query-free board，可忽略候选上的 presentation query，但 detail path 仍不能提升为 board；该规则由 adapter identity 约束，不包含 provider/company 分支。

Fixture fetch 缺失使用 `OFFLINE_FIXTURE_MISSING`，这是 non-retryable、owner `replay` 的基础设施结果；Fetcher 在 exception 边界直接携带 typed reason 和脱敏 request identity，S4/S5 aggregation 与 availability diagnostics 必须保留它，不能改写为网络失败、官网不存在或岗位不存在。Replay manifest 将 request identity 与 `Page.url`、跨域 `final_url`、body hash/length 绑定；现代 manifest 缺项、歧义或损坏 fail closed，legacy GET 和 failure-only capture 保持兼容。Embedded URL 与 provider-config 扫描在 escape decoding 前剥离 HTML comments，避免 retired integration 进入活动证据集。

## SOLID Rules

### Single Responsibility

- Stage 只负责一个 pipeline 关卡。
- Adapter 只负责一个 provider family。
- Runner 只负责调度、预算和 checkpoint，不做页面解析。
- Evaluation 只消费结果，不发网络请求。
- Opening availability diagnosis 只解释既有证据，不抓取页面或选择 provider。

### Open/Closed

- 新 provider 通过新增导出 `ADAPTER` 的 module 自动扩展。
- 新 fetch behavior 通过 wrapper 扩展。
- 新报告通过消费 schema 扩展，不修改 pipeline。

### Liskov Substitution

- 所有 fetcher 对相同请求返回 `Page` 或抛出 `FetchError`。
- 所有 adapter 对空结果、unsupported 和 retryable failure 使用统一语义。

### Interface Segregation

- Stage、provider、fetch 和 checkpoint contract 分开定义。
- Adapter 不必实现 browser、snapshot 或 reporting 方法。

### Dependency Inversion

- Stage 依赖 `FetchClient`、adapter registry 和 store contract。
- 具体 HTTP/Playwright/filesystem 实现在 composition root 注入。
- 测试使用 fixture-backed implementations，不改业务逻辑。

## Current Technical Debt

- S2-S7 均有独立 stage，通用 `ApplicationRunner` 已支持顺序执行、范围重跑和上游结果复用；`JobSourceAgent` 仍保留 discovery helper 和兼容 facade。
- 24 个 provider module 已自动发现；其中 23 个提供原生 inventory 或受约束 positive evidence，仅 Talemetry 提供 detection-only typed incomplete semantics。Meta 仍只接受 visible-page positive evidence；CEIPAL 与 WhiteCarrot 已冻结并验证 public inventory schema，Talemetry 仍需完成同等 contract 才能升级为可支持 candidate/no-match 的 adapter。
- S2 根据输入 provenance 区分用户当前声明与历史回放证据：普通 direct input 可直接采用显式官网；`replay_input` 官网只能作为优先候选，必须通过 bounded verification，停放、托管或身份不符时继续进入 LinkedIn/search/guess resolver。验证槽分配保证历史候选确实被请求，同时保留严格的 verified-homepage 选择门槛。
- S2 success 表示“可访问性 + 肯定公司身份”，不是 HTTP 成功。域名 token、TLD、历史 LinkedIn slug 与搜索 snippet 只用于排序；非 authoritative 候选还必须由 title/body、结构化 Organization/legalName 或 canonical identity确认。有限 client-side redirect-only shell 同源最多跟随一跳并重新验证，跨域目标只作为 migration hint，不能直接成为官网。
- 独立 `content_probe.py` 可供 S4/S5 从官网自己声明的同站 module bundle 读取公开 Magnolia Delivery payload，但只在 endpoint、app base、品牌一致 CMS host、HTTPS 标准端口和同 host response 全部验证后合并内容；该 probe 只补充页面证据，provider URL 仍进入原生 adapter 做 board/inventory 验证。
- `live_batch_eval.py` 只负责公司级并发、两段 process hard budget 和输出；实际 S1-S7 执行委托 `PipelineApplication`，S1-S3 与 S4-S7 通过 filesystem stage checkpoint 衔接。每段先向 fetch wrapper 注入略早于 outer budget 的 soft deadline，逐请求压缩 socket timeout，并为结构化收尾和 checkpoint 发布预留最多 1 秒；process kill 只作不合作底层调用的最后保险。
- ADR-0008 将 process hard budget 定义为 durable-publication deadline：worker 在独立 POSIX process group 中运行，大结果先写入 attempt-local、destination-atomic envelope，pipe 只发送 readiness；父进程只接受 deadline 前已 fsync/replace 完成的 envelope，并在 timeout/final cleanup 终止整个进程组。完整 stage checkpoint 保持可复用，不因下游 timeout 回滚；snapshot 按 blob/view/artifact/sequence 在前、durable JSONL index 在后的顺序发布，company completion 继续作为最后的 authoritative commit marker。`.56` 离线门禁为 859 tests、24/24 provider、6/6 resolver、23 adapters / 0 issues；Akkodis 在 45 秒 focused live 内 34.5 秒 exact，并由 8 fixtures 对完整 URL/provider identity 做 1/1 replay。
- `.62` 将 ADR-0008 的“stage checkpoint 不回滚”落实到 parent timeout result：只恢复同一 execution fingerprint 下连续、兼容且已完成的 stage prefix，首个 gap 标记 `COMPANY_TIME_BUDGET_EXHAUSTED`，不读取 gap 后 checkpoint。Akkodis 即使在 S6 分页撞 hard deadline也不再丢失已完成的 S4/S5；本轮网络下 43.6 秒完整读取 9 页/83 条 inventory 并得到 verified no-match，16-fixture replay 1/1 reproduced。
- ADR-0010 / `.64` 在 hard process deadline 内增加 provider cooperative stop：`FetchBudget` 与最小 `FetchClient` 分离，分页 guard 以 request timeout + publication reserve 决定是否允许下一请求；PageCache/Snapshot 显式透传 capability，未知/nonfinite timeout fail closed。被 guard 拒绝的 request 不发网络，但按 ADR-0006 保存脱敏 request identity 和 `FETCH_BUDGET_EXHAUSTED` terminal outcome，使离线 replay 复现同一 partial boundary。Sitecore 首个迁移；Akkodis 45 秒 focused live 保留 8 页/80 条正向 inventory 和 verified job list，bundle 1/1 reproduced。同一冻结 30-company 统一回归为 30/29/28/24，6 个 non-success 全部 reproduced、0 fixture gap、0 mismatch。
- `.66` 将 replay response identity、fixture-gap propagation 和 provider board canonicalization 收口到同一 contract：Airbnb 跨域 redirect 不再在 fixture 中退化为请求 URL；Netflix 未采用 homepage probe 不再污染完整成功；Google 的 provider 入口和 listing route 由 adapter 统一映射为同一 board identity。新的 17-company Product Manager 样本为 17/10/10/7，回放达到 16 reproduced、1 个真实 Adobe capture gap、0 mismatch；最终门禁为 936 tests、25/25 provider、6/6 resolver、24 adapters / 0 issues。登录态 extension gate 继续独立 deferred。
- `.57` 将 evidence strength 与执行预算绑定：只有 scheduler tier 0-2 的未尝试候选构成 retryable career fetch exhaustion，tier 3 speculative truncation 是确定性 miss。S5 direct provider handoff 接受官网可见链接和 registry listing-capable adapter，并要求输入 URL 已是 provider canonical board root；`.60` 移除会误伤 opaque tenant path 的通用 detail heuristic，因为 canonical equality 已经使 detail 和 legacy URL 继续走原有 fetch/redirect 验证。S6 对 native adapter 的完整 verified inventory 使用 terminal no-match，不再进入 generic HTML fallback；incomplete/unsupported provider 仍允许 fallback。该边界使 Percepta 越过 generic cap 到 Taleo、Smart Bricks 进入 WhiteCarrot API，同时不绕过 Paycom/Lever 既有 contract。
- ADR-0009 / bundle schema `4` 将 outer live budget 与 domain outcome 分开：只有 `COMPANY_TIME_BUDGET_EXHAUSTED`、完整 authoritative upstream chain、replay 越过超时 stage、无 fixture gap 且 source identity prefix 不漂移时才输出 passing `budget_recovery`。Manifest 保存该 prefix 和 replay full identity；expected transition 也不能绕过 URL/provider 检查。Snapshot body 的 unquoted sensitive key 使用 JavaScript identifier 左边界，standalone `code` 仍脱敏而 `urlCode` 不再损坏。旧过度脱敏 blob 不能猜测修复，必须重新 capture；Percepta 新 capture 的 9 fixtures 可 1/1 复现 Taleo HTTP 500。`.58` 门禁为 876 tests、24/24 provider、6/6 resolver、23 adapters / 0 issues。
- WhiteCarrot adapter 具有两个互斥 locator mode。App mode 将 `/careers/{tenant}` 与 `/share/careers/{tenant}` 规范化为稳定 tenant board，只读取匿名 `GET /api/careers/{tenant}` 的一次性完整 `roles` inventory；custom mode 将单标签 `*.whitecarrot.ai/jobs` 规范化为 same-origin board，只接受 Next SSR 中带 `career-job-item-name-*` 强标记的同源 UUID detail。两者都拒绝 credentials、异常端口、query/fragment redirect、cross-origin/mismatched detail、profile-builder Talent Pool 和 malformed record；只有 schema-valid API `roles=[]` 可形成 complete empty。Trace 仅保存 URL、计数及 job id/status，不保存 description、申请表、cookie、token 或登录态。`.60` 门禁为 892 tests、25/25 provider、6/6 resolver、24 adapters / 0 issues；Smart Bricks canonical handoff focused live/replay 均为 exact。
- Failure replay 不把 trace 当作 typed locator persistence。首个失败为 S6 时，若 S5 detection method 是 `page_evidence`/`page_probe`，bundle 从 S5 开始用 snapshot 重建受 adapter 验证的 handoff；URL-native/linked handoff 保持从 S6 恢复。`.59` capture 因此将 Viking 从 fixture gap 恢复为 Phenom verified no-match，Akkodis 未完整捕获的分页仍保持唯一 gap。
- Fetch wrappers 已满足显式 `FetchClient` protocol 和跨实现 contract suite；deadline wrapper 在零重试时仍生效，并在每次初始/重试请求前执行预算门禁；browser live variants 仍需持续验证。
- S5 first-party traversal 使用有界 BFS；同分 listing route 优先保留 source locale prefix，redirect 到已访问 canonical page 不消耗有效 page budget。Known-ATS embed 和 registry-backed board 只负责进入 adapter boundary，最终 board root 仍由 adapter 识别和规范化。
- S4 的 first-party bundle navigation 只扫描最多 3 个已验证同站 HTTPS asset、每个最多 5MB；active labeled anchor 可指向同 registrable-site 子域，comments、裸 URL、资源 URL、unsafe URL 和外域保持拒绝。Generic official career redirect 是独立于 provider detection 的 evidence：请求必须来自已验证官网的安全 career route，终点须同时自证公司 identity、same-origin canonical/OG、同源可操作 job route，并回链 exact official origin 或 company-token 绑定的 corporate sibling。该证据只建立 career root，S5/S6 仍独立验证 job list、inventory 与 opening。
- `.67` focused live 将 Stash、Airbnb、Peloton Interactive、Solomon Page 从 0/4 career 提升到 4/4 career，Airbnb 另得到 verified positions list；4/4 outcome replay、0 fixture gap、0 mismatch。最终门禁为 967 tests、25/25 provider、6/6 resolver、24 adapters / 0 issues；真实登录态 extension gate 继续 deferred。
- `.68` 删除 S2 stage 对非 replay supplied website 的直接信任旁路，所有来源统一通过 resolver 的 preferred-candidate、正向 identity 和 migration contract。S3 的 website semantics 只决定是否执行 bounded public posting probe，不能单独改变招聘主体；S5 队列保存 candidate provenance，使 first-party asset-backed typed board 在回跳 career root 时仍能交给 adapter；显式 same-site `all-jobs` 与严格 numeric child detail 分别建立 list/detail evidence。Stash focused live 通过 Greenhouse 到 exact opening；Peloton 从旧 supplied domain 自动迁移后达到 exact opening，full-outcome replay 1/1 reproduced、0 gap、0 mismatch；Solomon Page 保持 publisher-unconfirmed 和无 company-owned job list。最终门禁为 983 tests、25/25 provider、6/6 resolver、24 adapters / 0 issues；真实登录态 extension gate 继续 deferred。
- `.69` 把预算与 replay 可靠性收紧为跨模块一致语义：full-outcome bundle 在完整回放模式下要求 source 到 comparison 全链记录数一致，evaluation baseline 绑定 observed total；S5 仅提升带强 evidence tier 的 retryable candidate failure，caller deadline 归属 company budget。Phenom landing handoff 由 adapter 自己校验 tenant、官方 CDN、同源 base path 与显式 search route，不在中央 pipeline 增加 provider 分支。`FetchBudget` 在 career search 和 Sitecore 首次/分页请求前执行 cooperative stop；native inventory 预算中断后 matcher 不再进入 generic fallback。Parent timeout recovery 从 durable checkpoint 恢复最长成功前缀，并为每个 stage 发布 `parent_timeout_restore` event。离线门禁为 999 tests、25/25 provider、6/6 resolver、24 adapters / 0 issues；`.68` frozen snapshot replay 的 record integrity 为 30/30，但两个 fixture gap 与一个 timeout-transition mismatch 继续 fail closed。
- `.70` 规定 stage output 本身也是下游 evidence：S4 成功发布的 career root 在 S5 重新 fetch 时拥有 tier-0 `verified_career_page` provenance；其 retryable fetch failure 必须保留 network/budget taxonomy，不能因 BFS root 没有 incoming candidate 而变成确定性 `JOB_BOARD_NOT_FOUND`。该变更只传播证据和错误分类，不把历史 HTML、trace 或 snapshot 当作当前页面缓存；跨进程页面复用仍需独立 durable handoff contract。
- `.71` 明确 normal downstream run 的 S4-S6 共享同一个 ephemeral fetch composition：redirect response 可由 request/page/final URL 任一别名命中同一 LRU entry，但 resume 到新进程仍会重新验证网络或离线 fixture，不能从历史 snapshot/trace 恢复 runtime page。Resume preflight 在启动 child 前顺序应用完整 checkpoint prefix；不能应用的 update、错误字段类型和 success stage 缺失 required output 都视为 chain miss，并从 S4 安全重建。
- `.72` 明确 S5 discovery completion 由 typed capability 决定，而不是由 URL host 再猜一次：first-party page evidence 只要绑定到 registry 中 `supports_listing=true` 的 adapter，就直接交给 S6 inventory；`supports_listing=false` 的 detection-only evidence 继续允许 bounded ATS fallback。Architecture validator 分开报告 listing 与 detection-only adapter，并要求后者实现 page-aware 或 page-probe 证据入口，避免不可达空壳。
- `.73` 将 S5 内部探索顺序收紧为纯页面 typed evidence / visible canonical provider link、强同站 listing route、provider asset/page probe；优先 route 必须安全 fetch 并重新验证，失败后恢复原 fallback，不把 URL 文本本身升级成 board。Failure replay 对 `results.json` 缺失 diagnostic trace 的情况，仅在 registry adapter 具备 page-aware/page-probe 能力且 URL 不能原生重建 board 时从 S5 重验 snapshot；它仍禁止从 trace/result 拼装 typed locator。Root cache alias 只优化同 process 请求拼写，不跨 live runner 的上下游 process。`.73` 离线门禁为 1020 tests、25/25 provider、6/6 resolver、24 adapters / 0 issues；串行 frozen-30 为 30/29/28/24，full replay 30/30、0 gap、0 mismatch。Akkodis 因 S4 波动只读取 70/83，保持 cooperative-budget partial。
- `.74` 将 S4 的 listing-first 原则限制在调度层：HTTPS 来源页中发现的 HTTPS 同 concrete-host embedded URL，只有经过既有 scorer 标记为 `explicit job-list route` 才能与可见 homepage career navigation 同属 tier 1、使用相同 evidence boost，再按 score/family 排序。该规则不建立 career/job-list success，不改变跨站 redirect、provider、页面内容或 opening 的验证门禁；identity-supplied tier 0 仍优先。Akkodis focused live 因此先验证 job-results，S6 完整读取 83/83 后 verified no-match；旧 frozen-30 outcome replay 仍为 30/30。对 S4/S6 provider result 的审计只发现两处重复解析且无重复 transport，location query 又不同，所以没有引入 query-agnostic runtime cache。
- Bounded BFS 可穿过 career root 下最多两层的 staff、business-services、professional、student/lateral audience taxonomy；只有官网明确使用 job-opportunity 语义并指向同 registrable domain 的 jobs/careers 子域时，才允许在 portal 被 challenge 阻挡时保留官方 job-list root。
- First-party provider configuration 在 bounded link extraction 内派生 board；当前 Greenhouse template API 和带 embed 指纹的 Lever `accountName` 配置会优先于普通页面链接，最终仍由原生 adapter 验证 tenant 和目标标题。
- Filesystem stage checkpoint store 已支持原子保存、兼容性校验、安全 cache miss 和从指定 stage 向下失效。
- LinkedIn website evidence store 已按 ADR-0003 支持 30 天 TTL、schema/corruption/nonfinite 安全 miss、进程锁和 atomic replace；composition root 为 CLI checkpoint 与 extension output 注入稳定路径。ADR-0004 进一步把 authenticated detail DOM 的 explicit apply mode 与 public search 的 unknown evidence 分离，S5 可输出不伪造官网 URL 的 `LINKEDIN_NATIVE_ONLY` partial terminal。S4 在昂贵 sitemap 前验证 primary evidence，按实际读取文件执行 10-file cap，优先展开 job/目标地区 index，并将 language locale 与 region 分离。S4/S5 只把官网明确提供且已 fetch 的 first-party `job-results` 当 listing，page-aware provider 仍先于 generic route，结构化 parent-card title 与普通 careers taxonomy 的详情判定分离。Render capability unavailable 被缓存并静态降级，opening availability 聚合 generic/provider/adapter/detection error provenance；S6 进一步以 inventory completeness 阻止 incomplete miss 被提升为 no-match。`.48` 门禁为 CPython 3.12 702 tests、22/22 provider、6/6 resolver 和 architecture validation 20 adapters / 0 issues；冻结 30-company live 为 30/30 官网、29/30 career、27/30 verified job list、22/30 exact opening，较上一 frozen run 为 +0/+2/+1/+2，failed -2、success +2，8 个 failure bundle 全部成功。Content script 通过 DOM visibility contract 排除隐藏记录但保留 offscreen cards，loopback handler 有真实 HTTP contract tests；unpacked 扩展已安装，真实登录态 LinkedIn Scan/Run 核验继续暂缓。
- `.51` 当前离线门禁为 CPython 3.12 774 tests、23/23 provider、6/6 resolver 和 architecture validation 23 adapters / 0 issues。Checkpoint schema 1.2 通过注册式 provider policy 约束 replay-safe locator，并拒绝未知 provider、跨 origin evidence、敏感 query、credential/HTML-shaped 内容和越界值。S4 只从 bounded same-site JS assets 接受明确招聘标签绑定的同源 route，之后仍执行页面/CMS/ATS verification；serialized frozen-30 live 为 30/28/26/20，Direct Supply 恢复 Workday exact，Akkodis 通过 typed Sitecore handoff 读取 official inventory。10 个非成功样本 replay 为 6 reproduced、2 fixture gaps、2 mismatches，fixture gap 与 mismatch 均使 CLI gate 非零；复用 bundle output 会清理受管 fixture/checkpoint，summary/report 按规模输出 actionable 三维 failure cluster。真实登录态 LinkedIn Scan/Run 继续 deferred。
- ADR-0006 将 snapshot 从 URL-only 成功页面集合升级为 request-aware outcome log。`request_identity.py` 是 URL/query/body/header 脱敏和指纹的单一实现：JSON/form body 先结构化脱敏再摘要，opaque body 只输出不可回放分类；成功页面和 terminal fetch failure 共享全局 sequence，但失败保存在独立 JSONL 中而不伪装成 HTML。Replay v2 为 POST/body/header identity 物化独立 fixture，并为 failure-focused Fetcher 发布结构化 failure manifest；legacy v1 success records 兼容读取，未知 major、unsanitized identity、重复 sequence、非有限时间和损坏 manifest fail closed。
- Failure bundle v2 从首个 non-success stage 恢复，成功上游 stage 以 typed checkpoint handoff 直接复用，不把历史输出重新送回 resolver。CEIPAL 只允许同 HTTPS endpoint 在保留 tenant 参数时省略已知空 presentation query；snapshot/query/body 对 `apikey` 拼写使用统一敏感键政策。Focused Centraprise/Kirkland/Aventis/Akkodis capture 含 45 个 page records、1 个 terminal failure 和 46 个 replay fixtures，4/4 outcome signature 原样复现；插件真实登录态 Scan/Run 仍是独立 deferred gate。
- `.52` 最终门禁为 791 tests、23/23 provider、6/6 resolver 和 architecture validation 23 adapters / 0 issues。同一 frozen-30 cohort 为 30/29/27/21，相对 `.51` 的 website/career/job-list/exact 为 +0/+1/+1/+1；pipeline status 为 21 success、8 partial、1 failed。292 个 page records 与 88 个 terminal failure records 物化为 283 fixtures，9 个 non-success outcome 全部 reproduced，0 fixture gap、0 mismatch。
- `.53` 将 CEIPAL public inventory 纳入同一 provider boundary：runtime-only tenant identity 驱动 wrapper、iframe 与 multipart inventory，请求落盘仅保留 sanitized path/body digest/semantic headers；snapshot body 的精确脱敏 pagination URL 仍执行同 host/method/page 验证。S3 未披露客户是 S4 的 terminal dependency，generic first-party path 区分 visible official empty、确定性 miss 与 budget exhaustion。最终门禁为 817 tests、24/24 provider、6/6 resolver、23 adapters / 0 issues；serialized frozen-30 为 30/28/27/22，8 个 non-success 由 291 fixtures 全部 reproduced，0 gap、0 mismatch。Aventis 的 career -1 是拒绝错误招聘主体，Centraprise exact +1 是 CEIPAL 新增能力。
- Production CLI 和 live batch 均由 `PipelineApplication` 和通用 runner/store 执行；live batch 保留两段 process hard budget。
- Stage store 通过 fingerprint 级进程锁和原子替换保证并发安全；checkpoint trace 明确记录 save、restore、miss 和 invalidate。
- Sanitized live snapshots 使用跨进程发布锁、全局 sequence 和内容寻址的不可变 page/artifact blobs；canonical fixture view 保持 Fetcher 兼容。`scripts/replay_snapshots.py` 验证 blob、request identity、failure taxonomy 与 canonical view，并按完整 request identity 生成 deterministic fixture tree。
- `.77` 将 batch completion 与 typed stage retryability 接通。兼容 success 和明确 non-retryable outcome 继续恢复；只有完整 stage chain 的首个 non-success 明确 `retryable=true` 才自动重提，并只 invalidate 该 stage 及下游 checkpoint。旧 completion 保留到新结果原子发布，trace/summary 只记录 privacy-safe action、stage 和 reason code。Snapshot materialization 同时按完整 request identity 在 page/failure 共享 sequence 中选择唯一最新终态，修复旧 failure 压过后来 success 的 replay 漂移。跨 live invocation、跨 stage 的 attempt evidence lineage 仍需独立 versioned scope contract；缺少该 contract 的 outcome mismatch 继续由 replay gate fail closed。
- `.79` 将 target location 贯穿 S5 board discovery，并在所有 board-selection bypass 前应用同一 regional eligibility；Sitecore Next 接受 page-bound dictionary brand 和 primary-language match，同时继续严格校验 country/tenant/record identity。Akkodis focused live 只选择 U.S. board，完整读取 83/83 records 后 verified no-match；bundle v6 1/1 reproduced。Scoped page replay 复用 byte-validation 阶段解码的原始 UTF-8 text，不再由 `read_text()` 的 universal-newline 转换破坏 CRLF scope digest。最终门禁为 1148 tests、25/25 provider、6/6 resolver、24 adapters / 0 issues；frozen-30 baseline 仍为 30/29/28/24，插件验收继续 deferred。
- `.80` 区分“可用于导航的初始 career hub”和“可作为结果提升的 regional board”：只有 S4 已验证、非 provider、非 detail 的初始 career root 可以在 fetch 前暂缓地区拒绝，页面内后续 provider board、listing、detail 和 candidate 仍执行统一 target-region gate。Dematic 因而可以从官方 Australia landing 找到 U.S. Workday board，而不会把 Australia board 当成结果。Bundle v6 在执行前根据 authoritative S1-S4 trace 重建原始 source/website/career-root 输入语义，执行后才重新附加 replay provenance；导出的 discovery output 不再倒灌成 trusted input。Scoped outcome tape 在锁内按完整 request identity 消费，独立并发请求允许调度换序，同 identity 重复项保持 capture order，任何 missing/extra/mismatch/unconsumed 仍 fail closed。Fresh frozen-30 为 30/29/27/23，full scoped replay 30/30、0 gap、0 mismatch；Dematic 同配置重试及 replay 1/1 exact。最终离线门禁为 1159 tests、25/25 provider、6/6 resolver、24 adapters / 0 issues；插件验收明确 deferred。
- `.81` 将 offline provider gate 从 legacy `JobSourceAgent.discover()` 迁到 production `PipelineApplication`，因此 25 个 fixture case 都覆盖完整 S1-S7、production fetch wrappers、execution fingerprint、run-configuration identity 和 evidence lineage。Evaluation 只从 typed stage result/evidence 计算每家公司唯一 terminal semantic，不读取中间 trace error；exact、verified no-match、no-public-openings、identity ambiguity、retryable、external block、unsupported、replay infrastructure 和 unresolved discovery 与 URL hit rate 分开报告并支持 baseline delta。Legacy snapshot materializer 在任何写入前拒绝 v3-only/mixed scoped index，要求 bundle-v6 replay，避免现代 capture 被过滤成空成功。Fresh frozen-30 为 30/29/27/24；typed resume 恢复 Akkodis 后为 30/29/28/24，Percepta 保持 Taleo/network retryable。Final semantics 为 24 exact、3 verified no-match、1 no-public、1 identity-ambiguous、1 retryable；full scoped replay 30/30、0 gap、0 mismatch。最终门禁为 1168 tests、25/25 production provider、6/6 resolver、24 adapters / 0 issues；插件验收继续 deferred。
- ADR-0015 / `.82` 将聚合命中率之后的 company-level identity 变成 release gate：expectation 可声明任意非空 website/career/job-board/opening 子集，声明字段必须以严格 public URL、provider 和 `url:<canonical board>` tenant 比对；只允许显式有限 alias，legacy baseline 明确显示 identity unavailable。`checkpoint_prefix.py` 是 CLI、live resume、completion retry、rerun 和 timeout recovery 的单一连续权威前缀规则；gap 后记录不恢复，resume 从首 gap 重算，显式 rerun 在 mutation 前失败。陌生 15-company strict holdout 为 13 website、12 career、8 verified job list、7 title-matched opening，但只有 6 条等于运行前冻结 URL；S2 body-only incomplete identity 与 Workday auxiliary route 的两条跨公司 false positive 已归零。最终门禁为 1217 tests、25/25 provider、6/6 resolver、24 adapters / 0 issues。
- ADR-0016 / `.83` 将 S2 direct evidence 与 speculative guesses 拆成顺序 wave：preferred/LinkedIn-official 候选仍执行完整 fetch、identity、redirect 和 parking validation，恰好一个 direct candidate 可选时停止 speculative dispatch；direct miss 或 conflict 才进入原有 bounded concurrency。短品牌 title identity 只接受完整 token 加有限 legal-entity suffix。没有 homepage positive body 时，唯一新增的 host-level fallback 是 4 字符以上全词机构缩写的精确 `<acronym>.edu` 收到 401/403；DNS、timeout、404、其他 TLD 和更强冲突身份 fail closed。S5 的 first-party portal promotion 与 `score_job_link` 共用一个 command taxonomy，URL 仍须是 HTTPS、同 registrable site 的 jobs/careers/apply 子域。Focused live 为 Atira/RIVR exact、Bosch official job list、SNHU website-only；最终门禁为 1226 tests、25/25 provider、6/6 resolver、24 adapters / 0 issues。Multi-board portfolio 和 first-party dynamic inventory 继续作为独立 contract，避免把单 board complete 错当 company-wide complete 或从任意 JS 发起请求。
- ADR-0017 / `.84` 将 S5→S6 从单 board handoff 扩展为可选 `JobBoardPortfolio`。Portfolio 保存 1-8 个有序 typed board 和 eligible-set completeness；只在主板与目标 title 受众冲突时执行 bounded ATS 搜索，每个替代板必须通过 listing-capable adapter 的 positive 或 complete inventory 验证。S6 attempt cap 属于 deterministic run configuration，只有 complete eligible set 全部检查后才允许 company-wide empty/no-match；单 complete board 继续使用旧 S6 结果和 trace。Workday/SmartRecruiters 只在 canonical public HTTPS host/path 与 identifier 一致时允许 replay-safe checkpoint，任一 runtime-only member 使整个 portfolio 不落盘。S2 failure 对仅官网输入清空未验证 handoff，显式 career-root/external-apply 保留独立 evidence。Schema 为 run config `1.2`、contract `1.4`、checkpoint `1.6`，adapter `.84`；门禁为 1249 tests、25/25 provider、6/6 resolver、24 adapters / 0 issues。
- ADR-0018 / `.85` 将 JavaScript-only career inventory 纳入 bounded S5 evidence：最多三个 exact-origin page asset、一个 static import dependency、literal GET、5 MB / 5,000-row all-or-nothing payload，并要求所有 detail URL 绑定同一个 listing-capable native board。Opaque chunk 使用声明尾部优先而不是 hash 字母序；`api-proxy` 只合成 page-host public marker，bundle Authorization 永不读取。S6 在 score 外增加 ordered title identity / equal normalized multiset gate。Hostinger live 验证 77 条岗位和 canonical Ashby board 后，对已下线的 frozen `AI Engineer` 输出 verified no-match，拒绝错误 Full Stack role；fresh scoped replay 同结果且无 divergence/gap。Adapter `.85`；门禁为 1266 tests、25/25 provider、6/6 resolver、24 adapters / 0 issues，strict unfamiliar baseline 未整批重跑前保持 6/15 exact。
- ADR-0019 / `.86` 将 scoped replay 的完整性边界从固定 S1-S7 改为 source capture 的连续 terminal stage。没有最终 website 的记录只有在 allowlisted original source 和合法 lineage 同时存在时才能导出；执行前恢复原 preferred input，按 lineage 最后 stage 设置 `stop_after`，缺失或非连续 scope 继续 fail closed。Frozen unfamiliar live 为 13/12/11/7，严格预冻结 identity 为 7/15；同一 capture 15/15 reproduced、0 gap、0 mismatch。S4 search 在 RSS raw result 全部无效且预算尚存时继续 Bing HTML，不放宽 candidate identity gate。最终门禁为 1271 tests、25/25 provider、6/6 resolver、24 adapters / 0 issues。
- ADR-0020 至 ADR-0022 / `.87` 冻结 public runtime credential、acquired-brand S5 probe 和 TalentBrew SSR inventory contract。Bosch focused live 通过 e-Spirit CaaS 精确命中 `Data Scientist`；CyberArk 通过受约束 handoff 到 Palo Alto Networks TalentBrew board，完整读取 148 条过滤记录后正确输出 verified no-match，并拒绝相近的 parent-company Data Scientist role。两条 focused capture 均完成 scoped replay。最终门禁为 1318 tests、25/25 provider、6/6 resolver、26 native adapters / 0 issues；登录态插件验收继续手工执行。
- ADR-0014 / `.78` 完成 attempt/stage-scoped evidence lineage。Context contract `1.3`、stage checkpoint `1.5` 和 batch completion `1.2` 保存每个 stage 的 producer 与 scope；snapshot v3 记录 exact membership，zero-request stage 也发布空 scope。Bundle v6 使用严格 outcome tape 和 per-record runtime isolation，legacy bundle v5 保持显式兼容。真实 `SIGKILL` 验收确认 S1-S4 可从旧 attempt 恢复、S5-S7 由新 attempt 重算，被杀前未 finalize 的 S5 记录不会进入 replay。最终门禁为 1138 tests、25/25 provider、6/6 resolver、24 adapters / 0 issues；frozen-30 live baseline 仍为 30/29/28/24，本轮不声称产品命中率变化，登录态插件验收继续 deferred。
- `scripts/replay_failure_bundle.py` 将结果筛选、snapshot replay、authoritative upstream checkpoint seed 和离线 `PipelineApplication` 串成自包含失败复现 bundle；outcome gate 比较原始与 replay 的 pipeline/failure-stage signature，未声明变化非零退出。Live batch 可在运行结束后自动调用该边界，reporting 汇总 checkpoint activity、bundle 状态和按规模排序的 `stage x provider x reason_code` failure cluster。
- S4 career candidate verification 对明确 homepage navigation evidence 增加执行优先级，但仍保留原 score 作为同层排序；generated path 不再先耗尽强证据预算。Candidate 发生跨站 redirect 时，只有 registry URL adapter 或无额外网络探测的 page-aware provider evidence 才能确认，普通内容/媒体站被拒绝。S6 generic matcher 在一次调用内复用已抓取 landing page，并只把同主机 HTTPS GET form 的白名单关键词字段加入 bounded search plan；页面声明 action 先于推测 query，跨站/POST/敏感 query 不进入 fetch。Native adapter 的 unsupported variant 保留 adapter trace 和 incomplete inventory，不再退化成无类型 generic miss。
- Failure replay 以 allowlist 合并稳定 source-posting evidence，排除 cookie、token、原始认证 HTML 和任意 payload。Live summary 写入实际 company 与有效 expectations digest；evaluation history 和直接 `--baseline-summary` 只比较兼容 cohort，旧无 identity history 仅与旧无 identity history 比较。

当前结构已经达到 provider/resolver/fetch/evaluation 并行开发门槛；剩余债务按 ownership workstream 继续收缩。

## Ownership Boundaries

| Workstream | Target ownership | 不应修改 |
| --- | --- | --- |
| Stage orchestration | `stages/`, runner, checkpoint contracts | provider parsing internals |
| Provider adapters | `providers/<name>.py` 中的 `ADAPTER`、provider fixtures/tests | registry、stage runner 和 CLI |
| Resolver | website, identity and career discovery services | provider response parser |
| Fetch infrastructure | fetch protocol and wrappers | resolver scoring rules |
| Evaluation | benchmark, summary and reports | live navigation logic |
| Browser evidence | `extension/`, loopback bridge, input normalization | provider parsing and resolver scoring |

需要跨边界时，先通过小型 contract change 集成，再继续并行开发。
