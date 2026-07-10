# AI Job Source Agent Implementation Plan

本文档记录当前项目进度、已经实现的能力、仍然不完整的部分，以及后续补齐计划。它的目的不是包装结果，而是让后续开发和汇报都能围绕清晰的工程路线推进。

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
| Lever | Structured API adapter | 使用 `api.lever.co/v0/postings/{company}` |
| Greenhouse | Structured API adapter | 使用 `boards-api.greenhouse.io/v1/boards/{board}/jobs` |
| SmartRecruiters | Structured API adapter | 使用 `api.smartrecruiters.com/v1/companies/{company}/postings` |
| Ashby | URL/provider recognition | 目前主要依赖 HTML link extraction 和 title matching |
| Workable | URL/provider recognition | 已有 query URL pattern，未做结构化 API |
| iCIMS | URL/provider recognition | 已有 search query URL 和 job detail pattern，未做 API/JS extraction |
| Workday | URL/provider recognition | 已有 query URL 和 job detail pattern，未做 tenant API/embedded JSON extraction |
| SuccessFactors | URL/provider recognition | 已有 query URL 和 job detail pattern，未做完整 search/list API |

已验证的离线 fixtures：

- Workday job detail
- iCIMS job detail
- SmartRecruiters job detail
- SuccessFactors job detail
- Greenhouse structured API
- Lever structured API
- SmartRecruiters structured API

### 7. Batch Evaluation

已实现：

- `scripts/live_batch_eval.py`
- 每家公司处理后 checkpoint 写结果
- 支持 fast mode：
  - `--skip-sitemap`
  - `--fetch-timeout`
  - `--career-search-timeout`
  - `--max-career-candidates`
  - `--max-job-pages`

已知结果：

- mixed fast batch 曾达到 8/27 official job-list successes
- Product Manager 类大厂/品牌样本比随机小公司更容易成功
- random small-company AI Engineer 样本最弱

当前限制：

- 单进程批量跑仍然慢
- 并发 subprocess 方案曾触发本机 Python crash reporter，已停止使用
- 后续需要安全的 asyncio/thread worker + per-company budget

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

- 29 unit tests passing

## 当前主要短板

### 1. Provider Adapter Still Incomplete

虽然已经开始做 provider-specific adapters，但只有 Greenhouse、Lever、SmartRecruiters 进入结构化 API 阶段。

仍需系统补齐：

- Workday tenant/job search extraction
- SuccessFactors search/list extraction
- iCIMS hosted search extraction
- Workable API / embedded JSON extraction
- Ashby posting API / embedded JSON extraction

### 2. Browser Rendering Not Integrated Into Batch Flow

已有 `RenderedFetcher`，但还没有作为 smart fallback 自动接入：

- 静态 fetch 失败时自动 render
- 搜索结果中 provider page 为空时 render
- JS-heavy career page render 后再抽链接
- render budget / screenshot trace

### 3. Search Fallback Needs Better Sources

当前 search fallback 使用 Bing HTML。

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

### 4. Official Website Resolver Needs Stronger Evidence

当前官网解析对短名称公司仍可能选错。

后续需要：

- LinkedIn company page website field 更强解析
- search result title/snippet scoring
- homepage content verification
- company alias / parent-company relationship table

### 5. Live Success Rate Is Not Yet Product-Grade

当前系统适合展示架构和工程思路，但还不是稳定产品。

主要原因：

- real websites dirty and slow
- ATS provider coverage incomplete
- JS-heavy pages not fully supported
- batch run lacks robust parallel execution and budgets

## 下一阶段计划

### Phase 1: Complete Provider Adapters

优先级最高，因为这是当前系统成功率的主要瓶颈。

#### 1.1 Workday Adapter

目标：

- 从 `*.myworkdayjobs.com` / `*.workdayjobs.com` URL 中解析 tenant / board path
- 支持 Workday search URL / query params
- 尝试解析页面 embedded JSON
- 识别 job detail URL
- 如果具体 opening 找不到，稳定返回 Workday job board

验收标准：

- 离线 fixture 覆盖 Workday job list + detail
- live smoke 至少能稳定返回 Workday board URL
- title mismatch 不产生假阳性 opening

#### 1.2 iCIMS Adapter

目标：

- 支持 `careers-*.icims.com/jobs/search`
- 支持 `searchKeyword`
- 解析 job card / detail URL
- 识别 `/jobs/{id}/{slug}/job` 详情页

验收标准：

- 离线 fixture 覆盖 search page + detail page
- live smoke 能返回 iCIMS job list

#### 1.3 SuccessFactors Adapter

目标：

- 支持 `successfactors.com`, `sapsf.com`
- 识别 `career_job_req_id` / `jobReqId`
- 支持 keyword query URL
- 解析 list/detail page

验收标准：

- 离线 fixture 覆盖 list + query detail
- error page guard 保持有效

#### 1.4 Ashby Adapter

目标：

- 支持 Ashby board API 或 embedded JSON
- 从 board page 抽取 title / department / location / URL
- title match 后返回具体 opening

验收标准：

- 离线 fixture 覆盖 Ashby structured response
- live smoke 覆盖至少一个 Ashby board

#### 1.5 Workable Adapter

目标：

- 支持 `apply.workable.com/{company}`
- 支持 query URL / embedded posting JSON
- 识别 detail URL

验收标准：

- 离线 fixture 覆盖 board + detail
- live smoke 覆盖一个 Workable board

### Phase 2: Browser Fallback

目标：

- 将 `RenderedFetcher` 作为 fallback 接入 pipeline
- 仅在静态 fetch 失败或页面明显 JS-heavy 时启用
- 为每家公司设置 render budget
- trace 中记录 `source=browser`

验收标准：

- 对一个 JS-heavy career page，static fails but browser succeeds
- batch run 不会无限卡住
- 不触发本地 Python crash reporter

### Phase 3: Safer Batch Runner

目标：

- 支持并发，但避免之前 subprocess crash
- 每家公司独立 soft budget
- 每家公司实时 checkpoint
- 汇总成功率、失败类型、provider 分布

建议实现：

- thread pool + bounded workers
- no multiprocessing by default
- per-company timeout by internal budget checks
- output:
  - `results.json`
  - `trace.json`
  - `summary.json`

验收标准：

- 30 companies batch 不会因为单家公司卡住而丢失结果
- 能输出 provider-level failure distribution

### Phase 4: Evaluation And Reporting

目标：

- 建立固定 live benchmark set
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

1. Workday adapter
2. iCIMS adapter
3. SuccessFactors adapter
4. Ashby adapter
5. Workable adapter
6. Browser fallback
7. Safe parallel batch runner
8. Fixed benchmark report

## 当前可汇报说法

当前项目已经不是一个简单脚本，而是一个可扩展的 job-source discovery pipeline：

- LinkedIn discovery 已经接入
- 官网解析和品牌/母公司招聘体系映射已实现
- career page discovery 有 homepage/common path/sitemap/search fallback
- provider-specific ATS adapter 层已经建立
- Greenhouse、Lever、SmartRecruiters 已接 structured API
- Workday、iCIMS、SuccessFactors 等 enterprise ATS 已有识别和初步匹配，但还需要补完整 adapter
- batch evaluator 和 traceability 已经有雏形

最诚实的当前状态：

> 架构方向正确，关键模块已经搭起来，部分 ATS 已进入结构化 API 阶段；但距离稳定产品还需要继续补齐 enterprise ATS adapters、browser rendering fallback 和可靠 batch evaluation。
