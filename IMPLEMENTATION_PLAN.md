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
- 允许 future override map

相关模块：

- `job_source_agent/website_resolver.py`

当前限制：

- 仍然可能选错短品牌名或同名公司官网
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
| Google Careers | Partial adapter | 可生成 title query URL |
| Meta Careers | Partial adapter | 可生成 title query URL |
| Lever | Native API adapter | 自动发现；使用 `api.lever.co/v0/postings/{company}` |
| Greenhouse | Native API adapter | 自动发现；使用 `boards-api.greenhouse.io/v1/boards/{board}/jobs` |
| SmartRecruiters | Native API adapter | 自动发现；使用 `api.smartrecruiters.com/v1/companies/{company}/postings` |
| Ashby | Native API adapter | 自动发现；使用 `api.ashbyhq.com/posting-api/job-board/{board}` |
| Workable | Native structured-page adapter | 自动发现；解析 embedded JSON 并还原 `apply.workable.com/{company}/j/{shortcode}/` |
| iCIMS | Native structured-page adapter | 自动发现；解析 JSON-LD / embedded JSON 并还原 `/jobs/{id}/{slug}/job` |
| Workday | Native CXS API adapter | 自动发现；构造 `/wday/cxs/{tenant}/{site}/jobs` 并用 title payload 搜索 |
| SuccessFactors | Native structured-page adapter | 自动发现；解析 job links/embedded JSON 和 `jobReqId` |
| Rippling | Structured HTML adapter | 支持公开 board 中的静态职位链接和 title matching |
| BambooHR | Native listing API adapter | 自动发现；使用公开 `/careers/list` JSON 返回职位并还原详情 URL |

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
- Product Manager / Data Analyst 这类品牌和成熟公司样本成功率明显高于随机 long-tail AI Engineer 样本。
- 2026-07-11 focused live checks: Cricut reached `https://cricut.com/careers`; Carv's public Rippling board matched `Growth Product Manager` to its exact job-detail URL. The full Carv homepage-to-board run remains sensitive to transient website timeouts.
- Follow-up live verification: ReachMobi now maps `Product Manager` through BambooHR to `/careers/270`; MatrixSpace reaches its localized careers page and Ashby board; ONEOK retains its legitimate Workday board instead of a false `/assets/logo` URL.

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

- 193 unit tests passing

## 当前主要短板

### 1. Core Modules Still Violate SOLID Boundaries

当前已完成第一轮 SOLID 拆分，但仍有兼容层需要逐步迁移：

- S4-S6 已通过独立 stage/context/runner 执行，但 `JobSourceAgent` 仍保留 discovery helper 和兼容 facade。
- 9 个 provider 已迁移为原生 adapter；Google Careers、Meta Careers、Rippling 和 generic fallback 仍走 compatibility path。
- `live_batch_eval.py` 已使用 composition root，但调度、预算和 checkpoint 仍在同一 runner script 中。
- Fetcher 已有显式 protocol 和跨实现 contract suite；S2/S3/S7 已有独立 stage，尚待接入 production runner/checkpoint flow。
- 原生 adapter 已支持包内自动发现；新 provider 不再需要修改中央 registry。

Phase 2.5 并行门槛已经达到。后续可以让 Provider、Pipeline、Resolver、Fetch 和 Evaluation 工作线并行，同时继续收缩 legacy compatibility path。

### 2. Provider Adapter Still Incomplete

虽然已经开始做 provider-specific adapters，但仍有一部分 ATS 只是结构化页面抽取，还没有完成稳定 live search/list API。

仍需系统补齐或 live hardening：

- iCIMS hosted search pagination/API 变体
- SuccessFactors 更多 theme/AJAX payload 变体
- Workable public API research 和真实站点 hardening
- Ashby embedded JSON fallback
- Rippling 独立原生 adapter

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

- 搜索结果中 provider page 为空时更精确地触发 render
- live batch 中验证 render budget 不会拖垮吞吐

### 4. Search Fallback Needs Better Sources

当前 search fallback 使用 Bing RSS、Bing HTML 和 DuckDuckGo HTML。

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

当前官网解析对短名称公司仍可能选错。

后续需要：

- LinkedIn company page website field 更强解析
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

### 9. Checkpoint 已公司级落盘，Replay/Snapshot 已完成第一版

当前 batch checkpoint 能避免整个批次结果丢失；`live_batch_eval.py` 还把 S2/S3 和 S4-S6 分成两个 killable checkpoint，因此官网解析成功后，career discovery timeout 会保留已验证官网和 identity evidence。`scripts/export_replay_input.py` 可以把 prior results/trace 按 stage、stage status、reason code、provider 过滤成新的 input，保留 verified website、career root、LinkedIn title 和 replay metadata；每条 replay record 已包含 `checkpoint_schema_version`、`result_schema_version`、`adapter_version` 和稳定 input fingerprint。`--snapshot-dir` 可以把 live fetch 的页面保存成脱敏、fixture-compatible snapshots，后续可用 `Fetcher(fixtures_dir=.../sites, offline=True)` 读回。

真正的任意阶段 checkpoint 仍待补：snapshot 已经能保存 provider HTML/JSON，replay input 已有兼容性元数据，CLI 也已有有限的 `--resume-from-stage`；但还没有 `--rerun-stage`，也没有真正按每个 stage checkpoint 复用和失效中间结果。

后续需要保存关键中间产物：

- LinkedIn/company 原始输入
- resolved website 及证据
- hiring entity / parent company 决策
- verified career URL
- detected provider 和 board identifier
- provider response 或脱敏 HTML/JSON snapshot
- job candidates 和 title-match 分数
- 最终选择及验证证据

checkpoint 必须带 `schema_version`、输入指纹和代码/adapter 版本。Replay input 已完成第一版元数据；后续阶段级 checkpoint 也必须遵循同样兼容性规则。只有输入和相关版本兼容时才能复用，避免错误地使用过期结果。

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

当前进度（2026-07-12）：1.1 的固定离线 benchmark 已有 11 个 provider/path fixture，并由 `samples/benchmark_expectations.json` 声明 provider、最低成功关卡和 exact-opening 要求；回归不满足预期会以非零退出。固定 live benchmark 第一版已加入 `samples/live_benchmark_companies.json` 和 `samples/live_benchmark_expectations.json`。1.2 的 JSON 漏斗、公司七关矩阵、`provider × stage × status`、`provider × reason_code`、阶段耗时 P50/P95、跨运行 regression delta 和 Markdown summary report 已实现。

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

当前状态（2026-07-12）：Phase 2.5 并行门槛已达到并完成两轮并行验证。版本化 contracts、S2-S7 独立 stage classes、provider registry、9 个原生 adapter、adapter 自动发现、composition root、architecture validator 和跨 fetcher contract suite 已实现；193 个单元测试、11/11 固定离线 benchmark 和离线 CLI smoke 均通过。S2/S3/S7 尚待接入 production runner/checkpoint flow。

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

当前状态：S2 website、S3 hiring identity、S4 career、S5 job-board、S6 opening 和 S7 validation 都已有独立 stage class。S4-S6 已由 `PipelineStageRunner` 执行；`JobSourceAgent.discover()` 保留兼容 facade。S2/S3/S7 的 production runner/checkpoint 接入留给后续 Pipeline 工作线。

目标：

- 将 S2-S7 拆成单一职责 stage；S1 保持独立 discovery source。
- Runner 只负责依赖顺序、预算、retry policy、checkpoint 和取消。
- 保留 `JobSourceAgent.discover()` 作为兼容 facade，内部委托新 runner。
- 每个 stage 只读自己的声明输入，只写自己的声明输出。

验收标准：

- S4、S5、S6 可以用固定 `PipelineContext` 独立运行和测试。
- 一个 stage 的 parser/strategy 变化不要求修改其他 stage。
- 重构后 193 个测试和固定 benchmark 结果一致。
- Stage failure 会确定性地生成下游 `not_run` 或允许的降级状态。

#### 2.3 Introduce Provider Adapter Registry

当前状态：已完成可并行扩展的第一版。Greenhouse、Lever、SmartRecruiters、Workday、Ashby、BambooHR、iCIMS、SuccessFactors 和 Workable 已迁移为原生 adapter；provider module 通过导出 `ADAPTER` 自动注册。Google Careers、Meta Careers、Rippling 和 generic fallback 暂时保留 compatibility path。

目标：

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

当前状态（2026-07-12）：已通过并完成真实并行验证。两轮共十条独立工作线在不修改中央 registry 的前提下交付 stage/provider/fetch 变化；主线 architecture validator、193 个测试、11/11 benchmark 和 CLI smoke 全部通过。跨工作线测试发现并修复了 Workable 非法端口 URL 回归。

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

### Phase 3: Complete Provider Adapters

以下是已知 adapter backlog，不代表固定执行顺序。进入本 Phase 后，应由 Phase 1 的失败分布选择优先项。

#### 3.1 Workday Adapter

当前状态：

- 已实现 Workday CXS API URL 构造
- 已实现 POST JSON payload，使用 LinkedIn title 作为 `searchText`
- 已解析 `jobPostings.title` 和 `jobPostings.externalPath`
- 已用离线 fixture 验证 API result -> concrete job URL

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

目标：

- 增强 hosted search pagination
- 覆盖更多 iCIMS script payload 变体
- 做 live smoke，验证真实站点返回稳定 job list

验收标准：

- 离线 fixture 覆盖 search page + detail page + JSON-LD + embedded JSON
- live smoke 能返回 iCIMS job list

#### 3.3 SuccessFactors Adapter

当前状态：

- 已支持 keyword query URL
- 已识别 `career_job_req_id` / `jobReqId`
- 已支持 embedded JSON job record
- 可从 `jobReqId` 保守还原 detail URL

目标：

- 支持 `successfactors.com`, `sapsf.com`
- 增强 list/search API 或 AJAX payload extraction
- 覆盖更多 SuccessFactors URL 变体
- 解析 list/detail page

验收标准：

- 离线 fixture 覆盖 list + query detail
- error page guard 保持有效

#### 3.4 Ashby Adapter

当前状态：

- 已支持从 Ashby board URL 构造 `api.ashbyhq.com/posting-api/job-board/{board}`
- 已解析 `jobs.title` 和 `jobs.jobUrl`
- 已用离线 fixture 验证 API result -> concrete job URL

目标：

- 补 Ashby embedded JSON fallback
- 从 board page 抽取 title / department / location / URL
- title match 后返回具体 opening

验收标准：

- 离线 fixture 覆盖 Ashby structured response
- live smoke 覆盖至少一个 Ashby board

#### 3.5 Workable Adapter

当前状态：

- 已支持 Workable query URL pattern
- 已支持 embedded JSON job record
- 可从 `shortcode` 保守还原 `apply.workable.com/{company}/j/{shortcode}/`

目标：

- 支持 `apply.workable.com/{company}`
- 增强 query URL / embedded posting JSON
- 研究并接入稳定 public API，如可用
- 识别 detail URL

验收标准：

- 离线 fixture 覆盖 board + detail
- live smoke 覆盖一个 Workable board

### Phase 4: Browser Fallback

当前状态：

- 已实现 `SmartRenderedFetcher`
- CLI 支持 `--render-js` smart fallback、`--render-budget`、`--render-js-always` 和 `--render-screenshot`
- live batch runner 支持 `--render-js`、per-company `--render-budget`、render events trace 和截图 artifact snapshot
- Playwright-managed Chromium 缺失时可 fallback 到本机 Google Chrome channel
- 单测覆盖静态优先、JS shell fallback、静态失败 fallback、budget guard、local Chrome fallback、artifact source trace 和 snapshot artifact metadata

目标：

- 继续优化 render 触发条件
- 用 live benchmark 验证 render budget 不会拖垮吞吐

验收标准：

- 对一个 JS-heavy career page，static fails but browser succeeds
- batch run 不会无限卡住
- 不触发本地 Python crash reporter

### Phase 5: Stage Checkpoint, Retry And Safer Batch Runner

#### 5.1 阶段级 Checkpoint 和离线重放

当前状态（2026-07-12）：已完成 replay-level checkpoint metadata。`scripts/export_replay_input.py` 导出的每条 replay record 会写入 checkpoint schema version、result schema version、adapter version 和 stable input fingerprint；`scripts/validate_replay_input.py` 可以在复用旧 replay 前验证这些元数据是否仍兼容当前代码；`live_batch_eval.py --resume-from-stage career_discovery|job_board_discovery|opening_match` 可以复用 replay input 中的 verified website/career root 并跳过 S2/S3 官网解析。现有 input loader 会忽略这些额外字段，因此向后兼容。真正的任意 stage-level checkpoint store 和 `--rerun-stage` 仍未完成。

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

当前状态（2026-07-12）：已完成第一版 fetch-level retry wrapper。`RetryingFetcher` 会根据 `classify_fetch_error -> reason_spec.retryable` 判断是否重试；timeout、DNS、rate limit、server error 等可重试，HTTP 403、login/bot protection、parser/title mismatch 等不会重试。live batch runner 已接入 `--fetch-retries` 和 `--retry-base-delay`，retry events 会进入 `source_trace` 或 result trace。

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

当前状态（2026-07-12）：已完成第一版 bounded company concurrency。`live_batch_eval.py --workers N` 使用 thread pool 调度公司级任务，每个公司内部仍通过 process-level hard budget 防止 DNS/socket/native code 卡死；主线程按完成顺序写 results / trace / summary checkpoint。2-company/2-worker fixed live smoke 已通过。

目标：

- 每家公司独立 hard budget
- 每家公司实时 checkpoint
- S2/S3 与 S4-S6 分阶段 checkpoint，保留已验证中间结果
- 汇总成功率、失败类型、provider 分布

建议实现：

- process-level killable worker for operations that may block in DNS/socket/native code
- future: bounded concurrency on top of the current per-company hard budget
- per-company timeout by internal budget checks plus outer process deadline
- output:
  - `results.json`
  - `trace.json`
  - `summary.json`

验收标准：

- 30 companies batch 不会因为单家公司卡住而丢失结果
- 能输出 provider-level failure distribution

### Phase 6: Evaluation And Reporting Hardening

当前状态（2026-07-12）：

- 已建立固定离线 benchmark set
- 已建立固定 live benchmark 第一版
- 覆盖 Greenhouse、Lever、SmartRecruiters、Workday、Ashby、iCIMS、SuccessFactors、Workable、Google Careers
- `scripts/benchmark_eval.py` 可输出 results / trace / summary
- `scripts/live_batch_eval.py` 已支持持续写入 summary checkpoint
- Markdown report 已包含 rates、S1-S7 funnel、stage duration、provider/reason 分布、regression 和公司 stage matrix

目标：

- 将固定 live benchmark 扩展到每个主要 provider 至少 5 家
- 保存带时间戳的历史 baseline 和 regression artifact
- 不只依赖 LinkedIn 当天随机结果

建议 benchmark：

- 5 known Lever
- 5 known Greenhouse
- 5 known Ashby
- 5 known Workday
- 5 known iCIMS / SuccessFactors
- 5 known JS-heavy company career pages

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
- provider-specific ATS 能力已经建立第一版，但当前仍集中在中央 matcher，下一步会迁移到独立 registry/adapter modules
- Greenhouse、Lever、SmartRecruiters、Workday、Ashby 已接 structured API
- iCIMS、SuccessFactors、Workable 已加入 structured page / embedded JSON extraction，但还需要更多真实站点 live hardening
- browser fallback 已经从全量渲染升级为 smart fallback + render budget
- batch evaluator 已经能输出 results / trace / summary，固定离线 benchmark 可作为回归测试

最诚实的当前状态：

> 七关状态模型、统一错误码、benchmark 矩阵和 SOLID 并行开发架构已完成第一版。S2-S7 都有独立 stage class，9 个主要 ATS 已迁移到自动发现的原生 adapter，CLI/live runner 使用统一 composition root。两轮并行开发通过 193 个测试和 11/11 benchmark 验证；下一步重点是接入 S2/S3/S7 checkpoint runner、迁移 Rippling，以及继续做 live hardening 和 benchmark 扩展。
