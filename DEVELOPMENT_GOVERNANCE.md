# Development Governance

本文档规定项目如何记录变化、做架构决策、并行开发和发布版本。目标是让 Git 历史、路线图和实际代码保持一致。

## Source Of Truth

| 内容 | 唯一记录位置 | 更新时机 |
| --- | --- | --- |
| 单次代码变化 | Git commit | 每个完整、可验证的开发任务 |
| 可交付变化 | `CHANGELOG.md` 的 `Unreleased` | 每个开发任务合并前 |
| 当前能力与后续路线 | `IMPLEMENTATION_PLAN.md` | milestone 状态或优先级变化时 |
| 稳定架构边界 | `docs/ARCHITECTURE.md` | 模块职责或依赖方向变化时 |
| 重大技术决策 | `docs/adr/` | 决策实施前或同时 |
| 发布版本 | `pyproject.toml` | release commit |

`pyproject.toml` 是包版本号的 canonical source。完成后续 packaging cleanup 前，
`job_source_agent.__version__` 必须与它保持一致。

## Change Workflow

每个开发任务遵循以下流程：

1. 用 benchmark、fixture、snapshot 或失败 trace 定义问题。
2. 标记所属 stage、provider、reason code 和预期影响。
3. 确认修改只落在一个清晰 ownership boundary 内。
4. 先补失败测试或固定复现输入，再实现修改。
5. 运行局部测试、全量测试和相应 benchmark。
6. 更新 `CHANGELOG.md`；必要时同步计划、架构文档或 ADR。
7. 创建单一目的、可独立回滚的 commit。

纯格式整理可以合并记录，但任何行为、接口、schema、依赖、命令行或架构变化都必须有独立 changelog 条目。

## Parallel Development Rules

- `main` 始终保持可运行，功能开发使用独立分支。
- 一个任务只能有一个主要 ownership area，避免多个分支同时修改中央文件。
- Provider 工作只修改自己的 adapter、fixture 和测试；不得新增中央 `if provider == ...` 分支。
- Stage 工作只通过稳定 contract 交换数据，不读取其他 stage 的内部 trace 结构。
- Fetcher 工作保持统一 `fetch()` contract，不向 resolver 暴露具体网络实现。
- Reporting 只消费版本化 result/checkpoint schema，不依赖 live runner 内部状态。
- 跨边界需求先修改 contract 和 contract test，再由各模块分别适配。

推荐分支：

```text
codex/stage-runner
codex/provider-<name>
codex/resolver-<topic>
codex/fetcher-<topic>
codex/benchmark-<topic>
```

## Architecture Decision Records

以下变化必须增加 ADR：

- 新增或替换核心抽象。
- 修改 stage 依赖或顶层成功语义。
- 修改 result/checkpoint schema 的兼容性规则。
- 引入新的外部服务、持久化方式或并发模型。
- 接受会长期存在的技术折中。

ADR 一旦 accepted 不直接改写历史结论；后续决策通过新的 ADR supersede 旧记录。

## Release Policy

- Patch：兼容 Bug 修复和可靠性增强，例如 `0.1.1`。
- Minor：向后兼容的新能力或新 adapter，例如 `0.2.0`。
- Major：破坏性 CLI、schema 或 contract 变化，例如 `1.0.0`。

发布步骤：

1. 全量测试和固定 benchmark 通过。
2. `Unreleased` 条目移动到新版本和发布日期下。
3. 更新 `pyproject.toml` 和 `job_source_agent.__version__`。
4. 创建 release commit 和 annotated Git tag。
5. 保存本次 benchmark summary 和 regression report。

## Definition Of Done

一个任务只有同时满足以下条件才完成：

- 行为和异常路径有自动测试。
- 没有降低“不编造 URL”和错误页防护标准。
- 没有绕过已定义的 stage/provider contract。
- 全量测试和相关 benchmark 无回归。
- `CHANGELOG.md` 已更新。
- 路线图、架构或重大决策发生变化时，相应文档已同步。
- Commit message 能单独说明这次变化的目的。

