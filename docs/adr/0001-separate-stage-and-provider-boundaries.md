# ADR-0001: Separate Stage Execution And Provider Adapters

- Status: accepted
- Date: 2026-07-12

## Context

当前 pipeline 已有七关结果 schema、多个 provider 规则和 105 个单元测试，但 S4-S7 仍集中在 `JobSourceAgent`，provider detection、API request、response parsing 和 title matching 仍集中在 `opening_matcher.py`。

继续直接增加 ATS 会要求多个并行任务同时修改中央文件，增加 merge conflict、回归和 provider 规则互相影响的风险。现有 `--resume-from-stage` 只能复用部分上游字段，也无法提供真正的任意阶段重跑。

## Decision

在继续扩大 provider 覆盖之前：

1. 将 S1-S7 定义成使用版本化 `PipelineContext` 和 `StageExecution` 的独立 stage。
2. 建立 `ProviderAdapter` contract 和 registry。
3. 每个 provider 将 detection、request construction 和 response parsing 收进自己的模块。
4. Runner 通过依赖注入获得 stage、fetch client、provider registry 和 checkpoint store。
5. 不同公司允许 bounded parallel，同一家公司的 hard-dependent stages 保持顺序执行。
6. 新增 provider 不允许继续扩大中央 `if provider == ...` 分支。

迁移期间保留现有 CLI 和 result schema 的兼容 facade，先做 behavior-preserving refactor，再开发新能力。

## Consequences

正面影响：

- Provider 可以按独立分支并行开发和测试。
- Stage 可以 checkpoint、resume、rerun 和离线 replay。
- Runner、业务规则和网络实现可以分别测试。
- 新增 adapter 的回归范围更清楚。

成本和风险：

- 需要一次受 benchmark 保护的中等规模重构。
- 迁移期间会短暂存在 facade 和新 contract 两套入口。
- Contract 设计必须保留现有 trace 和向后兼容字段。

## Validation

- 现有全量单测和固定离线 benchmark 在重构前后结果一致。
- 新增一个示例 provider 只需要新增 adapter、fixture、测试和一个 registry registration。
- S4、S5、S6 可以独立运行并分别写入 checkpoint。
- `--rerun-stage` 能使指定 stage 及下游失效，而不重新执行兼容的上游 stage。

