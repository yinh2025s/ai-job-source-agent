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
| 多代理长期协作规则 | `AGENTS.md` | 并行策略或隔离规则变化时 |
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

## Deterministic Artifact Governance

- 改变候选数、fetch/search budget、sitemap/search policy 或 timeout 时，必须通过
  `DeterministicRunConfig` 更新 execution identity；不得复用仅按公司输入命中的 artifact。
- 改变 company/website wall-clock budget、fetch timeout/retry、render policy、verify limit
  或 offline mode 时，必须更新 `BatchExecutionConfig`；batch completion 和 baseline 必须 miss。
- Result、trace、summary 和 replay manifest 必须携带同一 canonical run configuration
  digest。不同 digest 的 benchmark 不得直接声明 regression delta。
- Replay 不得静默使用 composition defaults。缺少配置的历史 artifact 只能显式进入
  legacy 模式，并在 manifest 标记 provenance，不能声称完整 deterministic reproduction。
- 成功 replay 除 pipeline status 外必须验证 URL/provider identity；fixture smoke 或仅比较
  failure signature 不能替代 full-outcome gate。
- 自动 replay 的 mismatch 或 fixture gap 必须使 live gate 非零；bundle 文件成功生成不等于验收通过。
- Run configuration 只允许固定数值/布尔字段；路径、cookies、tokens、headers、HTML 和
  登录态信息不得写入该 contract。

## Parallel Development Rules

- `main` 始终保持可运行，功能开发使用独立分支。
- 一个任务只能有一个主要 ownership area，避免多个分支同时修改中央文件。
- Provider 工作只修改自己的 adapter、fixture 和测试；不得新增中央 `if provider == ...` 分支。
- Stage 工作只通过稳定 contract 交换数据，不读取其他 stage 的内部 trace 结构。
- Fetcher 工作保持统一 `fetch()` contract，不向 resolver 暴露具体网络实现。
- Reporting 只消费版本化 result/checkpoint schema，不依赖 live runner 内部状态。
- 跨边界需求先修改 contract 和 contract test，再由各模块分别适配。

每轮并行任务还必须满足以下运行隔离规则：

- 开始前冻结共享 contract、schema、cache key、TTL、版本失效、损坏恢复和隐私语义；实现过程中不得由支线自行改变。
- 每条写入线使用独立 Git worktree、独立 checkpoint root 和独立临时目录，并在任务说明中列出唯一文件 ownership；只读调查线不得修改文件。
- 两条线不得同时修改同一文件。需要跨 ownership 的改动由主线在集成阶段完成，支线不得覆盖或回退其他人的修改。
- 支线只运行自己 ownership 内的定向测试，不同时运行完整 live benchmark。主线合并后统一运行全量离线门禁和同一冻结 cohort。
- 自动 bridge smoke 和静态 extension 测试不能替代一次真实登录态 Chrome 中的 unpacked-extension 安装、LinkedIn DOM 扫描和结果核验。
- 失败调查先分类公开岗位为空、招聘主体未披露、外部阻塞和系统能力缺口；不得用公司特例把正常空结果伪装成成功。
- 并行数量和 cache hit 只属于工程过程指标；验收仍以冻结陌生样本的官网、job-list、exact-opening 成功率与不编造 URL 为准。

S2 evidence-cache 并行轮次的文件 ownership 固定如下：

| Workstream | 可写文件 | 运行范围 |
| --- | --- | --- |
| Main S2 | `identity_evidence.py`、`website_resolver.py`、对应 resolver 测试、ADR/架构/版本文档 | 集成后全量门禁与冻结 cohort |
| Product wiring | `composition.py`、`cli.py`、`extension_bridge.py` 及各自测试 | 定向离线测试 |
| Cache contract | `tests/test_identity_evidence.py` | 定向 contract 测试 |
| Failure analysis | 无，只读 results/trace | 不运行完整 live，不写公司特例 |

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
