# AI Job Source Agent - Beta Project Summary

## Executive Summary

AI Job Source Agent 从 LinkedIn 职位或预提取的公司/岗位记录出发，寻找公司官网、
Career 页面、官方 Job List 和具体开放岗位。项目的核心原则是：搜索和网页导航只
产生候选，只有完整身份链通过验证后才能发布岗位 URL；证据不足时系统 fail
closed，而不是猜测结果。

本次交付冻结为可运行、可审查的 Beta。普通产品代码版本为
`2026-07-29.286`。最新完整 Fresh100 冷启动测量来自 `.283`，两者不会混为同一
版本成绩。召回率仍是主要限制，但已经发布的 URL 保持严格的公司、provider、
tenant、title 和 location 边界。

## Problem Definition

LinkedIn 上的公司名称和岗位信息不能直接、稳定地映射到官网开放岗位：

- 品牌可能由母公司或被收购后的主体统一招聘；
- Career 页面可能跳转到 Workday、Greenhouse、Lever、iCIMS 等第三方 ATS；
- 同一 provider 上存在大量 tenant，URL 相似不代表属于同一公司；
- 页面可能依赖 JavaScript、分页、搜索表单、API 或登录状态；
- 搜索摘要可能过期、跨公司或指向已关闭岗位；
- 网络失败和“岗位不存在”必须区分。

因此，产品目标不是尽可能返回链接，而是尽可能返回**可验证的具体 opening**，
并对无法验证的记录给出诚实、可追踪的失败终态。

## Architecture

系统保留 S1-S7 七阶段 pipeline，并在候选发现侧使用三个入口：

```text
LinkedIn job
   |-- visible External Apply
   |-- official website / Career traversal
   `-- provider-targeted search
                 |
                 v
        normalized candidate pool
                 |
                 v
       Provider Adapter Registry
                 |
                 v
       official inventory + match
                 |
                 v
    S7 identity continuity gate
                 |
          Exact or fail closed
```

三路发现只提高候选召回，不提供成功证明。统一候选池负责规范化、去重、provider
识别和有界调度。Provider Adapter 负责 provider-specific 的 tenant、canonical
board、岗位库存和 opening identity。S7 在发布前验证：

```text
LinkedIn source company
-> hiring entity / relationship evidence
-> provider
-> tenant
-> canonical job board
-> opening
-> title and location
```

任一环节缺失或冲突，系统清空具体岗位 URL 并返回结构化 partial、retryable、
blocked、not-found 或 identity-rejected 结果。

## Engineering Work

### Provider And Resolver Boundaries

Provider-specific 解析位于独立 adapter 中，公共 pipeline 不依赖公司名称或岗位
ID 特例。Registry 统一选择 adapter，shared contracts 管理候选、招聘关系、库存
完整性和 opening identity。这种边界允许单独增加或测试 provider，而不改变 S7
安全规则。

### Evidence And Replay

每个阶段输出结构化状态、reason code、retryability 和 evidence。Checkpoint、
snapshot 和 replay 都绑定版本化 execution identity。Replay 不只是检查程序能否
运行，还比较官网、招聘主体、provider、tenant、Job List 和 opening 是否保持
一致；fixture 缺失、身份漂移或未消费证据都会失败。

### Fail-Closed Safety

系统拒绝 credential URL、private host、错误 tenant、跨公司招聘关系、冲突地点
和不完整 provider handoff。搜索结果和页面 snippet 永远不能单独成为成功证据。
网络超时也不会被直接解释成“岗位不存在”。

## Current Evidence

### Focused Beta Demonstration

The frozen `.286` code completed a fresh seven-record public demonstration run
on 2026-07-31. It produced three S7 Exact openings across Ashby, Greenhouse, and
Workday; the measurement-bound identity audit passed 3/3 with no company,
provider, tenant, title, location, or opening issue. The remaining records
showed one verified no-match, one external inventory failure, and two discovery
failures without publishing an opening. The artifact privacy scan found zero
credential shapes.

This was a curated presentation set, not a benchmark. Its full replay bundle
did not pass record integrity, so the project does not claim 7/7 replay for this
set. The deterministic offline fixture demo remains the stable presentation
path.

### Authenticated Extension Acceptance

Chrome extension `0.4.0` completed a logged-in LinkedIn acceptance on 2026-08-01.
The selected scan returned exactly one identity-bound posting; the page scan
hydrated and processed 25/25 job cards; External Apply and LinkedIn-native Apply
states remained distinct. A one-record backend submission immediately displayed
`Running`, survived popup close/reopen without losing its run, and completed with
the honest typed result `CAREER_PAGE_NOT_FOUND`. The visible External Apply button
did not expose a safe target URL, so the client reported
`external_apply_observed; target unavailable` instead of fabricating one.

This proves the plugin workflow and current DOM contract, not broad External
Apply URL coverage or an Exact-rate claim. Extension `0.4.1` promotes the same
accepted behavior with release metadata only.

Extension `0.6.2` keeps that evidence boundary while removing reviewer setup and
opaque batch waits: it auto-pairs with the local bridge, restores a six-hour
tab-scoped scan snapshot after popup closure, serializes whole runs, processes
up to four companies within the active run, and displays monotonic progress such
as `Running 7/25`. A real External Apply URL is rendered directly when the DOM
exposes a safe target; a URL-less LinkedIn button remains an explicit observation.

### Current Development Measurement

最新权威完整 Fresh100 测量使用 adapter `.283`，在 100 条 development cohort
记录上冷启动运行：

| Metric | Result |
| --- | ---: |
| Verified website | 92/100 |
| Career page | 79/100 |
| Verified Job List | 71/100 |
| Raw S7 Exact | 36/100 |
| Strict replay | 99 reproduced / 1 mismatch |
| Serialized wrong opening URL | 0 |
| Serialized wrong location | 0 |
| Cross-company publication | 0 |
| Cross-tenant publication | 0 |

这 36 条是 raw Exact，不应表述为正式 eligible recall 或正式 precision：该批
development cohort 没有完整的 ground-truth 终态标注；其中 34 条具有完整
artifact identity path，2 条依赖 provisional identity path。Replay 的 1 条
mismatch 来自 snapshot sanitizer 对嵌套州字段的脱敏导致地点精度变化。

普通产品代码此后推进到 `.286`，增加了候选路线终态的类型化与可回放报告，但
没有运行获批的完整 Fresh100 冷启动 gate。因此 `.286` 不继承或重写 `.283` 的
36/100 成绩。

### Historical Frozen100 Baseline

Frozen100 在历史版本上记录 69 Exact、23 Verified Not Found、5 External
Blocked、3 Input Identity Invalid、0 System Gap，并通过同版本 100/100 replay。
69 条 Exact 的历史审计为 0 错误 URL、0 跨公司和 0 跨 tenant。

这是独立的历史基线，不是 `.286` 的当前回归证明，也不与 Fresh100 合并计算。

## What The Beta Demonstrates

- 一个真实可运行的 LinkedIn/company-to-opening 后端，而非静态结果页面；
- External Apply、官网探索和 ATS 定向搜索的多入口候选模型；
- 可扩展、可独立测试的 Provider Adapter Registry；
- 公司、招聘主体、provider、tenant、title、location 的最终 S7 安全门；
- 对 partial、blocked、not-found、retryable 和 identity conflict 的结构化表达；
- snapshot-backed replay、阶段 evidence 和可追踪决策；
- 真实登录态 LinkedIn 插件的单岗位/整页扫描、异步提交、重开恢复和结果渲染；
- 找不到可靠答案时不编造 URL。

## Known Limitations

1. **召回率有限。** Fresh100 raw Exact 为 36/100，主要缺口仍在陌生公司的官网
   identity、动态 Career inventory、搜索候选生成和匿名访问限制。
2. **最新产品版本未完成新的全量 live 测量。** `.286` 只能引用 `.283` 的权威
   Fresh100 结果，不能宣称已经提升完整 cohort 成绩。
3. **Fresh100 是 development cohort。** 它不再是 blind holdout，也没有完整
   ground-truth 标注，不能支持正式泛化结论。
4. **一条 replay mismatch 尚未关闭。** 当前严格 replay 是 99/100，而非
   100/100。
5. **External Apply 目标覆盖仍有限。** 插件工作流已经通过真实登录态验收，但
   LinkedIn 可能只暴露站外 Apply 按钮而不在 DOM 提供目标 URL；此时系统会明确
   报告 target unavailable，并继续使用普通后端路径，不能宣称 External Apply 命中。
6. **开放网页天然不稳定。** 限流、bot protection、页面改版和地区网络会影响
   latency 与 coverage；离线 demo 证明确定性 contract，不代表实时覆盖率。

## Recommended Next Steps

如继续产品化，优先级应是：

1. 在独立的 20-30 条登录态样本上测量 External Apply 目标 URL 覆盖与净新增 Exact；
2. 在未参与实现的新 cohort 上测量净新增 Exact，而不是继续调 Fresh100；
3. 只修复至少跨三家公司复现的 provider-family 或 transport contract；
4. 将 live 抓取放到稳定、合规的运行环境并增加速率控制和观测；
5. 为评测样本建立独立 ground truth，正式报告 eligible recall 和 precision；
6. 保持 S7、tenant 隔离和 fail-closed 规则不变。

## Reviewer Takeaway

这个 Beta 没有把有限的召回率包装成完整产品。它展示的是一个更重要的工程
取舍：在不可靠的开放网页上，将候选发现与最终可信判断分离，并通过 provider
contract、身份连续性、结构化失败和 replay 控制假阳性。当前系统仍会漏掉岗位，
但不会为了数字轻易发布无法证明的岗位链接。

## Release Verification

The final CPython 3.12 handoff gate passed 2,964 tests (4 skipped), provider
benchmark 25/25, resolver benchmark 6/6, and architecture validation with 48
native adapters / 0 issues. A tracked/new-file credential-shape scan found zero
matches, and the release archive uses a narrower source allowlist plus its own
fail-closed privacy scan.

The `0.1.0-beta.2` extension gate adds 47 focused content, popup, bridge, and
loopback HTTP tests plus the logged-in acceptance above. It does not rerun or
rewrite the Fresh100 measurement.
