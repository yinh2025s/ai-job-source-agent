# Agent Collaboration Guide

本文件是本仓库长期有效的多代理协作规范。所有主代理、子代理和独立
worktree 任务都必须遵守；任务中的更具体约束可以收紧本规范，但不能降低
正确性、安全性、测试覆盖或文档质量。

## Default Operating Mode

- 默认效率优先，并主动寻找可并行的独立工作线。
- 每次制定或调整计划时，先按依赖关系、写入范围和共享资源做一次 fan-out
  检查。存在两个以上互不阻塞且写入范围可隔离的任务时，直接使用子代理或
  独立 worktree 并行执行，无需再次征求用户同意。
- 主线始终保留关键路径、共享 contract、复杂架构决策、code review、最终
  集成、全量测试、live gate、commit 和 push 的责任。
- 若没有并行，进度汇报必须简要说明原因，例如强依赖、同文件冲突、共享
  登录态或同一 live benchmark 资源。

## Workstream Contract

启动每条并行工作线前，任务说明必须明确：

1. 目标和不在范围内的事项。
2. 唯一文件 ownership；列出允许写入的文件或目录。
3. 输入，包括冻结 fixture、trace、schema、接口或版本。
4. 输出，包括代码、测试、分析报告或文档。
5. 验收标准和只需运行的局部测试。
6. 独立 worktree、临时目录、checkpoint、snapshot 和测试输出路径。

代理必须知道还有其他任务同时修改仓库，不得回退、覆盖或格式化其他人的
改动。只读调查线不得修改文件。发现跨 ownership 需求时只报告给主线，由
主线统一修改共享文件。

## Ownership And Conflict Rules

- 不同工作线不得同时修改同一文件。
- 中央共享文件默认由主线拥有，包括 composition root、公共 contract、
  schema/version、registry、治理文件和跨模块入口。
- Provider 工作只修改自己的 adapter、fixture 和测试，不增加中央
  `if provider == ...` 分支。
- Stage、resolver、fetch、evaluation、browser evidence 和 documentation 按
  `docs/ARCHITECTURE.md` 与 `DEVELOPMENT_GOVERNANCE.md` 的边界拆分。
- 必须改变共享 contract 时，主线先冻结 contract 和 contract test，再把互不
  重叠的适配工作分发出去。
- 不重复委派同一分析或实现；已有支线负责的问题，主线只做必要的集成审查。

## Isolation

- 每条写入线使用独立 Git worktree 和分支。
- 每条线使用独立的 `/private/tmp` 子目录、checkpoint root、batch completion
  root、snapshot root 和测试输出。
- 不共享可变 cache，不并发写同一 benchmark artifact，不让两个进程使用同一
  completion store。
- 中间产物必须放在独立临时目录，不提交 live cache、cookies、tokens、原始
  登录态页面或未脱敏 snapshot。

## Test And Integration Flow

- 子任务只运行 ownership 内的局部测试和 `git diff --check`。
- 主线审查并集成所有支线后，统一运行全量单测、provider benchmark、resolver
  benchmark 和 architecture gate。
- 完整 live benchmark 只由主线运行一次；先批量收集 failure cluster，再并行
  修复，最后统一回归，避免逐个问题重复跑完整门禁。
- 自动 smoke、fixture 和离线 replay 不能替代明确要求的真实系统验收。
- 任何并行加速都不能放宽“不编造 URL”、租户隔离、身份验证、隐私或失败
  分类标准。

## Shared And Serial Resources

以下任务默认串行并由主线协调：

- 同一冻结 cohort 的完整 live benchmark。
- 共享网络出口容易触发限流的抓取。
- 真实 LinkedIn 登录态、Chrome unpacked-extension 安装和 DOM 扫描。
- 同一 checkpoint/cache/snapshot root 的写入。
- 最终 Git rebase、merge、commit、tag 和 push。

内部 company worker 可以做 bounded concurrency，但不得同时启动另一套完整
live benchmark。遇到限流、登录墙或网络不稳定时，保留 checkpoint 并分类，
不要用增加并发掩盖问题。

## Reasoning And Delegation

- 主线使用较高推理强度处理架构、复杂 bug、共享 contract、安全边界和 review。
- 边界清楚的 adapter、fixture、测试、只读分析和文档任务使用中等推理强度。
- 紧邻关键路径、下一步立即依赖结果的工作由主线完成，不委派后原地等待。
- 适合委派的是可以与主线当前工作同时推进、输入输出稳定且写集互斥的任务。

## Project-Specific Hard Constraints

- S2 LinkedIn website evidence cache 必须遵守
  `docs/adr/0003-cache-linkedin-website-evidence.md`：key、30 天 TTL、schema
  失效、损坏恢复、原子写入、隐私和 trace provenance 均不得由支线自行改变。
- 自动 extension/bridge smoke 不能替代一次真实已登录 Chrome 中的插件安装、
  LinkedIn Jobs 扫描和结果核验。
- Eightpoint、Aventis Solutions、M|R Walls 等失败样本先做证据分类；不得为可能
  没有公开官网岗位或客户未披露的公司添加公司特例。
- 最终产品指标是冻结陌生样本上的 verified website、job-list 和 exact-opening
  成功率以及 URL 正确性，不是 cache hit、任务数量或并行度。

## Progress Reporting

每轮进度更新说明：

- 当前有哪些并行工作线。
- 每条线的目标和文件 ownership。
- 主线正在推进的关键路径。
- 哪些共享 gate 会在集成后统一执行。
- 计划发生大幅变化、contract 变化或出现跨线冲突时，立即通知用户。

完成一轮时先汇总各线结果和发现的 failure cluster，再由主线决定下一轮
fan-out。Git commit 是详细变更记录；`CHANGELOG.md`、`IMPLEMENTATION_PLAN.md`、
架构文档和 ADR 按 `DEVELOPMENT_GOVERNANCE.md` 同步更新。
