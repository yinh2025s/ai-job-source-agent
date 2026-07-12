# Architecture

## Purpose

AI Job Source Agent 将 LinkedIn 或固定公司输入转换成经过验证的官网、招聘页、job board 和具体 opening。系统优先保证证据可追踪、失败可分类和结果不编造。

## Dependency Direction

依赖只能由外向内流动：

```text
CLI / batch scripts
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
- 10 个主要 provider 已使用原生 adapter，包括 Rippling；Google Careers、Meta Careers 和 generic fallback 仍依赖 compatibility path。
- `live_batch_eval.py` 只负责公司级并发、两段 process hard budget 和输出；实际 S1-S7 执行委托 `PipelineApplication`，S1-S3 与 S4-S7 通过 filesystem stage checkpoint 衔接。
- Fetch wrappers 已满足显式 `FetchClient` protocol 和跨实现 contract suite；browser live variants 仍需持续验证。
- Filesystem stage checkpoint store 已支持原子保存、兼容性校验、安全 cache miss 和从指定 stage 向下失效。
- Production CLI 和 live batch 均由 `PipelineApplication` 和通用 runner/store 执行；live batch 保留两段 process hard budget。
- Stage store 通过 fingerprint 级进程锁和原子替换保证并发安全；checkpoint trace 明确记录 save、restore、miss 和 invalidate。
- Sanitized live snapshots 可通过 `scripts/replay_snapshots.py` 验证并转换成 deterministic fixture tree；更高层的“一条命令重放指定失败样本”仍待整合。

当前结构已经达到 provider/resolver/fetch/evaluation 并行开发门槛；剩余债务按 ownership workstream 继续收缩。

## Ownership Boundaries

| Workstream | Target ownership | 不应修改 |
| --- | --- | --- |
| Stage orchestration | `stages/`, runner, checkpoint contracts | provider parsing internals |
| Provider adapters | `providers/<name>.py` 中的 `ADAPTER`、provider fixtures/tests | registry、stage runner 和 CLI |
| Resolver | website, identity and career discovery services | provider response parser |
| Fetch infrastructure | fetch protocol and wrappers | resolver scoring rules |
| Evaluation | benchmark, summary and reports | live navigation logic |

需要跨边界时，先通过小型 contract change 集成，再继续并行开发。
