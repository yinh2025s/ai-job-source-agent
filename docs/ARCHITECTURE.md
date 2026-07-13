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

## Browser Extension Boundary

`extension/` 是 S1 evidence adapter，不是第二套 pipeline。Content script 只读取当前 LinkedIn Jobs DOM 中可见的 company/title/location/job URL、company URL 和可选 External Apply URL。它不读取 cookie、不实现 ATS detection、不猜测 Apply redirect，也不验证岗位。

`scripts/extension_bridge.py` 只绑定 loopback，使用 bearer token 和 Chrome-extension Origin gate 接收最多 30 条记录，并通过后台 run manager 调用统一 `PipelineApplication`。Bridge 可以持久化 results/trace/summary，但不包含 resolver/provider 规则。S5 必须通过 provider registry 识别 External Apply board，S6 继续负责真实库存验证。

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

HTTP、browser、retry 和 snapshot 通过组合实现相同 contract。

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
- 18 个主要 provider 已使用原生 adapter，包括 Rippling、Google Careers、page-aware Phenom、Paycom、RippleHire、Taleo、Eightfold、JazzHR 和 Avature；Meta Careers 和 generic fallback 仍依赖 compatibility path。
- S2 根据输入 provenance 区分用户当前声明与历史回放证据：普通 direct input 可直接采用显式官网；`replay_input` 官网只能作为优先候选，必须通过 bounded verification，停放、托管或身份不符时继续进入 LinkedIn/search/guess resolver。验证槽分配保证历史候选确实被请求，同时保留严格的 verified-homepage 选择门槛。
- 独立 `content_probe.py` 可供 S4/S5 从官网自己声明的同站 module bundle 读取公开 Magnolia Delivery payload，但只在 endpoint、app base、品牌一致 CMS host、HTTPS 标准端口和同 host response 全部验证后合并内容；该 probe 只补充页面证据，provider URL 仍进入原生 adapter 做 board/inventory 验证。
- `live_batch_eval.py` 只负责公司级并发、两段 process hard budget 和输出；实际 S1-S7 执行委托 `PipelineApplication`，S1-S3 与 S4-S7 通过 filesystem stage checkpoint 衔接。每段先向 fetch wrapper 注入略早于 outer budget 的 soft deadline，逐请求压缩 socket timeout，并为结构化收尾和 checkpoint 发布预留最多 1 秒；process kill 只作不合作底层调用的最后保险。
- Fetch wrappers 已满足显式 `FetchClient` protocol 和跨实现 contract suite；deadline wrapper 在零重试时仍生效，并在每次初始/重试请求前执行预算门禁；browser live variants 仍需持续验证。
- S5 first-party traversal 使用有界 BFS；同分 listing route 优先保留 source locale prefix，redirect 到已访问 canonical page 不消耗有效 page budget。Known-ATS embed 和 registry-backed board 只负责进入 adapter boundary，最终 board root 仍由 adapter 识别和规范化。
- Bounded BFS 可穿过 career root 下最多两层的 staff、business-services、professional、student/lateral audience taxonomy；只有官网明确使用 job-opportunity 语义并指向同 registrable domain 的 jobs/careers 子域时，才允许在 portal 被 challenge 阻挡时保留官方 job-list root。
- First-party provider configuration 在 bounded link extraction 内派生 board；当前 Greenhouse template API 和带 embed 指纹的 Lever `accountName` 配置会优先于普通页面链接，最终仍由原生 adapter 验证 tenant 和目标标题。
- Filesystem stage checkpoint store 已支持原子保存、兼容性校验、安全 cache miss 和从指定 stage 向下失效。
- Production CLI 和 live batch 均由 `PipelineApplication` 和通用 runner/store 执行；live batch 保留两段 process hard budget。
- Stage store 通过 fingerprint 级进程锁和原子替换保证并发安全；checkpoint trace 明确记录 save、restore、miss 和 invalidate。
- Sanitized live snapshots 使用跨进程发布锁和内容寻址的不可变 page/artifact blobs；canonical fixture view 保持 Fetcher 兼容。`scripts/replay_snapshots.py` 会验证 blob 与最终 canonical view，并把重复 URL 的最后一个完整版本转换成 deterministic fixture tree。
- `scripts/replay_failure_bundle.py` 将结果筛选、snapshot replay 和离线 `PipelineApplication` 串成自包含失败复现 bundle；live batch 可在运行结束后自动调用该边界生成常规 regression artifact，reporting 可汇总 checkpoint action/stage activity 和 bundle 状态。

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
