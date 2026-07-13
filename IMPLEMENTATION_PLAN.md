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
| CEIPAL | Detection-only page-aware adapter | 强 widget 指纹与 tenant 绑定；bot/未知 schema 保持 typed incomplete，不构造岗位 |
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
- 23 个 provider module 已自动发现；其中 22 个提供原生 inventory 或受约束 positive evidence，仅 Talemetry 提供 detection-only typed incomplete semantics；generic fallback 继续作为兼容路径。
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

### 9. Checkpoint 已公司级落盘，Replay/Snapshot 已完成第一版

当前 batch checkpoint 能避免整个批次结果丢失；`live_batch_eval.py` 还把 S2/S3 和 S4-S6 分成两个 killable checkpoint，因此官网解析成功后，career discovery timeout 会保留已验证官网和 identity evidence。`scripts/export_replay_input.py` 可以把 prior results/trace 按 stage、stage status、reason code、provider 过滤成新的 input，保留 verified website、career root、LinkedIn title 和 replay metadata；每条 replay record 已包含 `checkpoint_schema_version`、`result_schema_version`、`adapter_version` 和稳定 input fingerprint。`--snapshot-dir` 可以把 live fetch 的页面保存成脱敏、fixture-compatible snapshots，后续可用 `Fetcher(fixtures_dir=.../sites, offline=True)` 读回。

Production CLI 已支持真正的任意阶段 checkpoint：`--checkpoint-dir` 按公司指纹保存每个 `StageExecution`，`--resume-from-stage` 恢复上游 updates/trace，`--rerun-stage` 从指定关卡向下失效，`--stop-after-stage` 支持聚焦执行。剩余工作是让 live batch 在保留 process hard budget 的同时复用同一 application service，并完成 snapshot 的跨进程离线重放。

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

当前状态：已完成可并行扩展的第一版。23 个 provider module 通过导出 `ADAPTER` 自动注册；22 个提供 native inventory 或受约束 positive evidence，仅 Talemetry 使用 page-evidence extension 提供 detection-only typed incomplete contract。Generic fallback 暂时保留 compatibility path。

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

### Phase 3: Complete Provider Adapters

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
- provider-specific ATS 能力已迁移到自动发现的独立 registry/adapter modules，共 23 个 module；22 个提供 inventory/positive evidence，仅 Talemetry 为 detection-only
- Greenhouse、Lever、SmartRecruiters、Workday、Ashby、BambooHR 已接 structured API
- iCIMS、SuccessFactors、Workable、Rippling 已加入原生 structured page / embedded JSON / verified-link extraction，但还需要更多真实站点 live hardening
- browser fallback 已经从全量渲染升级为 smart fallback + render budget
- batch evaluator 已经能输出 results / trace / summary，固定离线 benchmark 可作为回归测试

最诚实的当前状态：

> 七关状态模型、统一错误码、benchmark 矩阵和 SOLID 并行开发架构已完成第一版。S1-S7 均有独立 stage class，23 个 provider module 自动发现；其中 22 个提供 native inventory 或受约束 positive evidence，仅 Talemetry 保持 detection-only。CEIPAL 已实现 first-party widget 到 tenant iframe、公开 multipart inventory 与 first-party exact URL 的完整 contract，credential 仅以脱敏 request identity 进入 snapshot。ADR-0005 让 page-derived provider board 以隐私最小、checkpoint-safe 的类型化 contract 从 S5 进入 S6；schema 1.2 再以注册式 provider policy 强制 locator 的 origin/path/identifier 和敏感内容边界。ADR-0006 用 request-aware snapshot v2 区分 POST pagination、记录 terminal fetch failure，并让 failure bundle 从 authoritative typed handoff 恢复。S3 identity dependency、visible explicit-empty 与 fetch-budget exhaustion 现在有互斥 typed outcome。`.53` 通过 817 tests、24/24 provider、6/6 resolver 和 23-adapter architecture gate；同一 frozen-30 为 30/28/27/22，8/8 non-success replay，0 gap、0 mismatch。最终 release 指标仍是陌生冻结样本上的 URL 正确性和成功率；Unpacked extension 已安装，真实登录态 LinkedIn Scan/Run 验收继续 deferred，不阻塞其余主线。
