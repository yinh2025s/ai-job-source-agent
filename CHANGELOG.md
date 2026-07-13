# Changelog

本文件记录每个可交付迭代的功能、修复、架构和兼容性变化。格式参考
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/)，版本号遵循
[Semantic Versioning](https://semver.org/spec/v2.0.0.html)。

所有开发任务在合并前都必须更新 `Unreleased`。发布时再将这些条目移动到带日期的版本下。

## [Unreleased]

### Added

- 增加正式的开发治理、架构边界和 ADR 记录机制。
- 增加版本化 stage context/execution、最小 fetch client、provider adapter 和 checkpoint store contracts。
- 增加可独立运行的 S4 career、S5 job board、S6 opening stages 和顺序 stage runner。
- 增加 provider registry，并将 Greenhouse structured API 迁移为首个原生 provider adapter。
- 原生 provider adapter 改为包内自动发现，新增 ATS 不再需要修改中央 registry。
- 增加 composition root，集中构造 fetch wrappers、provider registry 和 agent，并让 CLI/live runner 使用统一依赖组合。
- 增加独立 S2 website、S3 hiring identity 和 S7 result validation stages；招聘主体和 career root 通过声明式 context 输出传递。
- 将 Lever、SmartRecruiters、Workday、Ashby、BambooHR、iCIMS、SuccessFactors 和 Workable 迁移为自动发现的原生 adapter。
- 将 Rippling 迁移为自动发现的原生 HTML adapter，验证同一 company board 的具体职位链接。
- 增加跨 Fetcher/Retry/Snapshot/SmartRender 实现的 FetchClient contract suite。
- 增加 `scripts/validate_architecture.py`，自动验证原生 adapter contract、唯一性和 registry 接管状态。
- 增加通用 `ApplicationRunner`，支持按 S1-S7 顺序执行、`start_at`/`stop_after` 和复用上游 stage result。
- 增加原子写入的 filesystem stage checkpoint store，使用 schema、adapter version 和 input fingerprint 验证兼容性并支持下游失效。
- Production CLI 改由 `PipelineApplication` 执行完整 S1-S7，并增加 `--checkpoint-dir`、`--resume-from-stage`、`--rerun-stage` 和 `--stop-after-stage`。
- Live batch 改为两段 process-budget 包裹同一 `PipelineApplication`：S1-S3 完成后逐 stage 落盘，S4-S7 从 checkpoint 恢复；增加 live `--checkpoint-dir`、`--rerun-stage` 和 deterministic offline batch 参数。
- 增加安全 snapshot replay CLI，将脱敏 live snapshot 校验并转换成可由 fixture Fetcher 直接消费的离线 replay 目录。
- 增加 failure replay bundle CLI，一次完成失败结果筛选、snapshot 校验、fixture 生成、离线 S1-S7 执行和 results/trace/summary/manifest 输出。
- 固定离线 benchmark 增加 Rippling exact-opening 样本，从 11 家扩展到 12 家。
- Markdown summary report 增加 `provider x stage x status` 和 `provider x reason_code` 可靠性表。
- Provider registry 增加可选 page-evidence adapter 扩展；iCIMS 支持 Jibe customer-owned career domains、页面 search override 隔离和同源 `/api/jobs` structured listing。
- Provider registry 增加可选 `PageProbeProviderAdapter`，允许 adapter 在严格有界、同源的公开 payload 中验证 opaque first-party career page，而不把网络探测耦合进中央 registry。
- Greenhouse adapter 支持从 first-party `__NEXT_DATA__` 中识别完整 Greenhouse job schema，并对 custom frontend canonical URL 做同源校验。
- Greenhouse adapter 支持 Nuxt static career payload：验证同源 preload、解析 Greenhouse-shaped devalue inventory，并仅允许岗位 URL 在裸域与 `www` 域之间规范化。
- Sitemap index discovery 在排队阶段执行 10-file 全局 fan-out 上限，并在 trace 中记录被截断数量，避免大型多地区 sitemap 绕过 company budget。
- Snapshot fixture 路径加入脱敏 query fingerprint，分页/筛选响应不再互相覆盖；redirect request alias 使用当次 immutable blob，仍保持 hash、路径和正文脱敏验证。
- 增加版本化 runtime policy、`.python-version`、runtime checker 和 `make offline-gates/live-gate`；release 自动化固定 CPython 3.12，项目安装暂时排除已实测 native crash 的 Python 3.14。
- 增加 GitHub Actions：push/PR 在 CPython 3.10-3.13 运行测试并在 3.12 重跑全部 offline gates；51-company live gate 仅手动触发，始终上传 results/trace/summary artifacts。
- Live LinkedIn batch 增加独立版本、查询参数绑定、公司列表 hash、文件锁和原子发布的 discovery manifest；恢复默认复用同一 cohort，`--no-resume` 显式刷新，summary 记录 manifest action/path。
- SuccessFactors adapter 支持 `*.jobs.hr.cloud.sap` 新 Career Site：解析页面 CSRF/locale，调用同源 recruiting v1 API 并还原 canonical job URL。
- 增加 opening availability diagnostics：聚合 `verified_inventory_no_match`、`verified_inventory_empty`、`discovery_incomplete` 和显式 `source_posting_closed`，并写入 S6 evidence、summary 与 Markdown report。
- RippleHire 作为第 14 个原生 provider 自动接入：支持稳定 board 规范化、匿名 cookie session、公开 XML inventory、单关键词 query translation、50 条有界分页、exact-title early stop、同 tenant redirect/API 校验和 detail URL 重建。
- Taleo 作为第 15 个原生 provider 自动接入：支持 custom-domain FacetedSearch board、公开 shell tenant 配置、匿名 REST inventory、keyword/location 查询、响应 pageSize 驱动的有界分页、exact-title early stop 和同 tenant detail URL 重建。

### Changed

- 将 SOLID 架构拆分设为继续扩展 ATS provider 之前的前置阶段。
- 明确 stage、provider、fetcher、orchestration 和 reporting 的依赖方向与并行开发边界。
- 第一轮和第二轮 provider/stage 并行开发通过统一集成门禁；一个 Workable 脏 URL 问题由跨工作线全量测试发现并修复。
- 官网解析对短名和歧义名增加 LinkedIn slug、搜索 title/snippet、主页 title 和 canonical domain 身份证据，降低误认官网风险。
- Smart browser fallback 会识别没有可用职位链接的非空 JS shell，保留结构化 JSON/static link 页面，并记录 render budget 耗尽事件。
- Architecture validator 会拒绝原生 adapter 中未登记的 literal reason code；`PROVIDER_FETCH_FAILED` 已纳入统一重试和 owner 语义。
- LinkedIn CLI 入口只负责产生公司输入，官网与招聘主体解析移入 S2/S3，避免入口脚本绕过 stage contract。
- Filesystem stage store 增加 fingerprint 级进程锁、目录同步、临时文件清理和并发 invalidate/load/save 安全语义；trace 增加 checkpoint save/restore/miss/invalidate 事件。
- iCIMS 原生 adapter 增加最多 5 页 hosted-search pagination、嵌套 payload 和跨页去重，并拒绝跨 tenant redirect。
- SuccessFactors 原生 adapter 增加 AJAX/theme/嵌套 JSON、分页 metadata 和同 tenant URL 校验。
- Provider 解析语义更新后将 `ADAPTER_VERSION` 提升到 `2026-07-12.1`，旧 stage checkpoint 会安全失效。
- Ashby adapter 保持 Posting API 优先，并在 API 失败、空或异常时回退同 board embedded JSON；Workable 增加同 account 公开链接、嵌套 payload 和分页 metadata 支持。
- Evaluation summary 和 Markdown report 增加 checkpoint action/stage activity 统计。
- Live batch 的持续与最终 summary 改为聚合独立 trace records，修复 `results.json` 保持精简时 checkpoint save/restore activity 被错误报告为空的问题。
- Snapshot 正文和 browser artifact 改为内容寻址的不可变 blob，并用跨进程锁串行发布 canonical fixture 与 manifest；replay 对重复 URL 采用最后一个完整版本并报告 superseded records，修复 Workday POST 分页和 query 变体覆盖旧文件后导致的 hash mismatch。
- Live batch 增加 `--failure-bundle-dir` 和 `--failure-bundle-limit`，在运行结束后自动把 partial/failed/unsupported trace 与 snapshot 转换为离线 replay bundle；全绿或没有可重放记录时生成明确的 skipped manifest。
- Provider attribution 优先使用 opening/job-board stage evidence，避免 Greenhouse 返回外部 apply host 时被错误归类；replay export 使用同一归属规则。
- 固定 live benchmark 从 6 家扩展到 9 家，新增 SanDisk/SmartRecruiters、ONEOK/Workday 和 Carv/Rippling 覆盖。
- 固定 live benchmark 继续扩展到 11 家，新增 Plum/Workable 和 ReachMobi/BambooHR 覆盖。
- 固定 live benchmark 扩展到 12 家，新增 Ardent Health/customer-owned iCIMS Jibe，并由 Brex first-party Greenhouse 支持将基线提升到 12/12 job list、10/12 exact opening 和 12/12 expectations。
- 固定 live benchmark 扩展到 13 家，新增 DeLaval/SAP SuccessFactors Career Site；达到 13/13 job list、11/13 exact opening 和 13/13 expectations。
- 固定 live benchmark 扩展到 21 家，新增 4 个 Ashby 和 4 个 Lever 官方 board；两个 provider 均达到 5 家覆盖，基线达到 21/21 job list、19/21 exact opening 和 21/21 expectations。
- 固定 live benchmark 扩展到 23 家，新增 Datadog 和 Airbnb 两个大型 Greenhouse board；Greenhouse 达到 5 家覆盖，基线达到 23/23 job list、21/23 exact opening 和 23/23 expectations。
- 固定 live benchmark 扩展到 27 家，新增 NVIDIA、Adobe、Salesforce 和 Autodesk 的 wd1/wd5/wd12 Workday tenant；Workday 达到 5 家覆盖，基线达到 27/27 job list、25/27 exact opening 和 27/27 expectations。
- BambooHR adapter 增加标准端口、单 tenant、API redirect、候选 URL/ID 校验及 retryable fetch failure；`ADAPTER_VERSION` 提升到 `2026-07-12.3`。
- Ashby/Workable 解析语义更新后将 `ADAPTER_VERSION` 提升到 `2026-07-12.2`。
- Page-aware provider 识别和 iCIMS Jibe 解析语义加入后将 `ADAPTER_VERSION` 提升到 `2026-07-12.4`，避免恢复旧 S5/S6 generic checkpoint。
- Workday CXS 请求增加同源 `Origin`/`Referer`、tenant/redirect/detail URL 隔离，并将不兼容的 `limit=50` 改为 20 条有界分页。
- Greenhouse custom frontend 加入后将 `ADAPTER_VERSION` 提升到 `2026-07-12.5`；Brex live expectation 提升为必须精确 opening。
- SAP Career Site v1 加入后将 `ADAPTER_VERSION` 提升到 `2026-07-12.6`；DeLaval/SuccessFactors 加入固定 live benchmark 并要求精确 opening。
- iCIMS adapter 增加同 tenant keyword iframe search 和传统 hosted HTML job-link 解析，拒绝跨 tenant/非数字 ID/非详情路径；`ADAPTER_VERSION` 提升到 `2026-07-12.7`。
- 固定离线 benchmark 增加 traditional iCIMS HTML fixture，扩展到 13/13 exact opening；固定 live benchmark 增加 Prime Healthcare、Peraton 和 Chenega，扩展到 30/30 job list、28/30 exact opening 和 30/30 expectations。
- SmartRecruiters adapter 增加 target-title `q` 查询、有界 offset pagination、exact-title early stop、API/company redirect 隔离和 public detail URL 校验；`ADAPTER_VERSION` 提升到 `2026-07-12.8`。
- 固定 live benchmark 增加 Bosch、Ubisoft、Delivery Hero 和 SGS，SmartRecruiters 达到 5 家覆盖；基线扩展到 34/34 job list、32/34 exact opening 和 34/34 expectations。
- Snapshot replay 可恢复进程中断产生的唯一 EOF 截断尾行，并显式报告 skipped/corrupt-tail 统计；中间损坏和完整非法记录仍严格失败。
- Live batch 增加版本化 company-completion store：每家公司以 input fingerprint、adapter version 和原子 envelope 独立发布；重启默认跳过兼容的已完成公司，`--no-resume` / `--rerun-stage` 可强制重跑，结果按原输入顺序重建。
- Workable adapter 接入官方 public jobs cursor API，支持 title query、最多 5 页、重复 token 停止、exact-title early stop、同 tenant board/API redirect 校验和旧 HTML fallback；`ADAPTER_VERSION` 提升到 `2026-07-12.9`。
- 固定 live benchmark 增加 Workable、Town Web、ClassWallet、Huzzle，并将已迁离 Lever 的 Veeva 样本替换为 Wishpond；Workable 达到 5 家 exact-opening 覆盖，集合扩展到 38 家。
- Live batch 的 later-stage resume 会验证完整 checkpoint chain：S5 可恢复 S1-S4，S6 可恢复 S1-S5；链不完整时基于 replay 官网证据回退到 S4，否则返回结构化失败。
- Rippling adapter 合并真实 Next.js `__NEXT_DATA__` 与 anchors，保留 location/department/language，支持 `es-419` locale，并区分空 board、损坏 state 和 JS shell；BambooHR 在主 location 为空时使用真实 `atsLocation` fallback；`ADAPTER_VERSION` 提升到 `2026-07-12.10`。
- 固定 live benchmark 新增 AllVoices、Terradot、RevOptimal、Spangle AI、Soundstripe、beehiiv、Signal 1 和 SAI360；Rippling、BambooHR 均达到 5 家 exact-opening 覆盖，集合扩展到 46 家。
- Process budget 在 worker 无结果崩溃、timeout、忽略 `SIGTERM` 和并发运行时统一正常 join 或强制 kill/reap，避免残留子进程；真实 `SIGTERM`/`SIGKILL` 测试已证明 S4/S5 落盘后可从 S5/S6 恢复且 checkpoint 无损坏。
- Smart browser trigger 增加真实 Workable `#app` shell，过滤 locale/self-link 和静态资源误报；浏览器以 DOMContentLoaded 为硬边界，并仅用剩余预算等待 networkidle，避免长轮询页面丢弃已可用 DOM。
- LinkedIn saved/public payload parser 只采信明确 Website label 或与 company identity 同对象的官网字段，规范化 locale LinkedIn company/job URL 并删除 tracking query；不再把第一个外部 URL 当官网。
- Website resolver 支持单字符品牌的精确 LinkedIn slug/domain 证据，并要求多词公司 canonical/主页证据覆盖完整 identity，拒绝 `Google DeepMind → google.com` 一类父域误判。
- Career search 按 Bing RSS、Bing HTML、DuckDuckGo HTML 逐源 fallback，并使用独立 source-fetch budget；只接受官方 career/job path 或包含完整公司 identity 的 ATS URL，安全解码 redirect、去重 ATS filter query；S2/S4 语义变化将 `ADAPTER_VERSION` 提升到 `2026-07-12.11`。
- 新增 6-case 固定离线 resolver benchmark，覆盖短名、非 `.com`、多词父域陷阱、canonical migration 和纯负样本。
- 新增原子、内容寻址 evaluation history：run 记录 UTC 时间、summary hash、commit、adapter version、Python/platform 和 benchmark command，自动与 latest baseline 生成 regression delta。
- `RetryingFetcher` 增加有界 exponential backoff + jitter、deadline-aware sleep、可注入 clock/RNG/sleeper 和逐次 retry trace；429、5xx、timeout、DNS 可重试，403、登录墙和 parser/title mismatch 不重试，耗尽后保留原始 `FetchError`。
- 新增 32-company/6-worker crash-recovery stress test，真实 `SIGKILL` 主进程后只恢复未完成公司，并验证稳定输入顺序、无重复、单 worker 失败隔离、原子 JSON 和无残留临时文件。
- SuccessFactors Cloud SAP adapter 增加页面 locale 优先、record-level `supportedLocales` 和 exact-title early stop；新增 W. L. Gore、Colas、Telstra Broadcast Services、Nova 四个独立 live tenant。
- 固定 live benchmark 扩展到 50 家：50/50 官网、career/job list 和 expectations，49/50 exact opening；SuccessFactors 达到 5 家独立 live 覆盖。
- SuccessFactors/retry/checkpoint 语义更新后将 `ADAPTER_VERSION` 提升到 `2026-07-12.12`。
- iCIMS Jibe canonical URL 增加同 origin、`/jobs/{id}` 和 payload job ID 一致性校验；新增 GovCIO 第五个独立 iCIMS live tenant，并将固定 live benchmark 提升到 51/51 expectations、51/51 job list、50/51 exact opening。
- 增加固定 5-company JS-heavy Workable cohort evaluator：确定性 contract 验证 static shell render trigger、browser evidence 和共享预算；可选 Playwright live 模式会在 evidence 不足时非零退出。12 秒 live baseline 为 5/5 browser career evidence、4/5 DOM exact links、5/5 不超预算。
- Live phase 的绝对 deadline 注入 retry wrapper，使退避 sleep 在外层 process hard kill 前主动遵守剩余预算。
- Atomic live artifact writer 会在重启时清理同一目标的遗留临时文件；32-company `SIGKILL` recovery stress 连续五轮通过。
- iCIMS/retry budget 语义更新后将 `ADAPTER_VERSION` 提升到 `2026-07-12.13`。
- Google Careers 从 detection-only compatibility path 迁移为自动发现的第 11 个原生 adapter：使用公开 SSR title search、提取 canonical detail URL，并拒绝 credentials、非标准端口、跨域 redirect、非 Careers 路径和无数字 job ID 的候选。
- Google Careers 原生迁移后将 `ADAPTER_VERSION` 提升到 `2026-07-12.14`；13/13 provider benchmark 保持 exact-opening 全绿。
- JS-heavy 固定 cohort 从单一 Workable 扩展为 5 家、4 个 provider、4 类技术栈：Plum、Workable、Apple Jobs、Intuitive Apps 和 BlueFit；脱敏真实 static/browser capture 的确定性回放为 5/5，并将 provider/technology diversity 纳入门禁。
- 跨 provider 12 秒 Playwright live gate 保持诚实非零：连续运行分别为 3/5 和 4/5 career/job evidence，均 5/5 触发 browser 且不超预算，暴露 DOMContentLoaded/异步内容的时间敏感性。
- Meta Careers 原生迁移调查确认默认静态响应只提供 Comet/Relay shell，列表依赖动态内部 GraphQL；匿名 browser 可见 numeric canonical job detail，但在形成稳定 rendered contract 前继续保留 compatibility path。
- Browser navigation 在 `DOMContentLoaded` timeout 后会无额外等待地检查当前 DOM：仅当存在可用 job/career link，或至少 120 字符且包含招聘语义时保留；空 root/noscript shell 继续抛原始 timeout，`networkidle` 仍只使用剩余预算。
- JS-heavy evaluator 改为 saved/live 共用严格 evidence gate：要求成功 render event、结构化 heading/nav 文本、可选 URL、最小正文长度、无 loading/错误状态，并输出 visible length、matched evidence、error class 和逐例 pass；旧宽松 5/5 被校正为 Plum/Workable/Apple 3/5，Intuitive Apps 卡在 loading、BlueFit 仅有筛选 UI。
- Browser/checkpoint 语义更新后将 `ADAPTER_VERSION` 提升到 `2026-07-12.15`。
- Browser navigation 在 networkidle 提前完成后会用剩余预算等待通用 job DOM 条件，修复 Meta Relay 等客户端数据晚于 lifecycle event 的空壳截取；不增加固定 sleep，软超时后仍返回当前 DOM。
- 严格 JS-heavy cohort 用 Spotify、IIC Lakshya 和 Meta 替换 loading/空列表/时序波动样本，达到 saved/live 5/5；覆盖 5 个 provider 和 5 类技术栈，Meta 从 static HTTP 400 fallback 到 browser，Meta、Apple、IIC 均验证精确 job URL。
- Evaluator 修复成功 static-error fallback 被初始 HTTP error 错误标为最终失败的问题；初始错误保留在 render event，成功 browser outcome 的最终 error class 为 null。
- Website resolver 的有界验证预算按 LinkedIn/search/slug 直接证据分配，不再被高分 speculative guesses 全部占用；同时清理 YC/funding/legal parenthetical qualifier，并在 trace 中记录 candidate source。
- Website resolver 拒绝 parked-domain marketplace redirect；单词品牌的产品扩展域名需要 LinkedIn 精确域名或 canonical 组织证据，修复 `Paramount -> paramountplus.com` 假阳性。
- Live batch 在 S4-S7 timeout 或 worker failure 时保留已完成的 S1-S3 stage results、官网、招聘主体、career root 和 trace，不再把下游预算耗尽误报为官网解析失败。
- S2 resolver/checkpoint 语义更新后将 `ADAPTER_VERSION` 提升到 `2026-07-13.16`；409 个测试、13/13 provider、6/6 resolver 和 51/51 fixed live expectations 均通过。
- S5 hidden ATS discovery 增加有界 iframe/data-attribute/escaped URL/redirect evidence，识别 Oracle 和 Eightfold listing root；BFS 只遍历同站点或已知 ATS，拒绝 credentials、非标准端口、资源 URL、登录/profile 路径和循环。
- Oracle Candidate Experience login/profile 路径不会再被提升为公开 listing root；通用 `search-results` route 进入 listing traversal，真实 replay 将 Snowflake 修正为 Phenom search-results root。Uber 继续保留 first-party public list，不把 candidate-account tenant 误报为职位列表。
- S6 新增独立 listing extraction：关联父卡片中的 heading/paragraph title 与 “See role” 链接，并安全解码纯 JSON 与多段 JavaScript assignment state；所有候选统一通过 same-origin/known-ATS detail URL 校验。
- Plaid 父卡片与 Snowflake Phenom/Ashby 真实 replay 从 0/2 提升到 2/2 exact opening；S5/S6 语义更新后将 `ADAPTER_VERSION` 提升到 `2026-07-13.17`。
- 本轮通过 426 个测试、13/13 provider、6/6 resolver 和 clean 51-company fixed live：51/51 expectations、51/51 job list、50/51 exact opening。
- Replay career root 现在按 provenance 处理：direct input/identity rule 保持兼容信任，历史 replay root 必须重新验证强就业语义、job-detail evidence 或 provider evidence；拒绝 Reddit/Twitch channel 和 Zillow consumer 页面等仅靠 `/careers` path 的假根节点。
- S5 generic root 增加有界 ATS-only search，每个 provider query 获得独立 RSS 机会；候选必须由原生 adapter 返回非空列表，speculative tenant 还必须通过目标标题严格门禁，修复同名 Glean SmartRecruiters 假阳性。
- Link extraction 增加 form action、Greenhouse embed canonicalization 和 JS template assignment 解析；provider config 优先占用候选预算，真实 Glean 页面可稳定提取 `gleanwork`，Twitch embed 可直接提升为 Greenhouse board。
- Glean、Reddit、Zillow、Twitch 均分别通过 focused exact-opening live；Plaid、Snowflake 保持 exact。Uber Seattle 与 Starbucks Nashville 标题未在当前官方 inventory 中确认，继续报告 partial。
- S4/S5 provenance、search 与 tenant verification 语义更新后将 `ADAPTER_VERSION` 提升到 `2026-07-13.18`；440 个测试、13/13 provider、6/6 resolver 和 clean 51/51 fixed live expectations 通过。
- Native provider opening trace 现在记录库存读取状态、候选数量和最佳标题分数；只有明确的来源 posting 状态才报告 `OPENING_CLOSED`，网络/解析不足继续诚实标记为 `discovery_incomplete`。S6/checkpoint 语义更新后将 `ADAPTER_VERSION` 提升到 `2026-07-13.19`；447 个测试、13/13 provider、6/6 resolver 和 architecture gate 通过。
- Generic S5 不再把 career landing page 自动记作 job list：只接受 provider、具体岗位或实际 traversal 到的 first-party listing/search route 证据；Morgan Stanley focused live 从错误 `/people` 修正到官方 career opportunities search，Nuro 无公开 listing evidence 时返回 `JOB_BOARD_NOT_FOUND`。S2 ambiguous non-`.com` 候选若首页缺失品牌身份，不能仅凭 LinkedIn slug 入选，修复 Clera dashboard 假官网。README live 示例恢复当前 20 秒 S2/S3 默认预算；S2/S5 checkpoint 语义更新后将 `ADAPTER_VERSION` 提升到 `2026-07-13.20`。本轮 451 个测试、13/13 provider、6/6 resolver、architecture gate 和 clean 51/51 fixed live expectations 均通过，fixed live 保持 51/51 job list、50/51 exact opening。
- S4 generic search 在 Bing RSS 已返回结果但无有效候选时不再重复请求同一 query 的 HTML 源，并将普通 career query 限为 3 条、ATS-only sweep 保留 5 条；推测 ATS tenant 优先通过原生 adapter 和严格标题门禁验证，避免先下载无关 board shell。Fetch wrapper 即使零重试也会在每次请求前遵守调用方绝对 deadline，并把单次 timeout 压缩到剩余预算；live runner 为 stage 收尾和 checkpoint 发布预留最多 1 秒，process hard kill 仅作最后保险。两组共 8 家 focused live 中，原先 7 个 hard timeout 全部转为可回放结果，Mercor 成功保持，Clera 假官网被拒绝，其余 6 家返回结构化 `CAREER_PAGE_NOT_FOUND`；S4/fetch/checkpoint 语义更新后将 `ADAPTER_VERSION` 提升到 `2026-07-13.21`。本轮 458 个测试、13/13 provider、6/6 resolver、architecture gate 和 clean 51/51 fixed live expectations 均通过，fixed live 保持 51/51 job list、50/51 exact opening。
- S5 traversal 允许已知 ATS 的合法 embed listing 进入原生 adapter 验证，同时继续拒绝资源、登录和未知 embed 路径；provider URL evidence 返回 adapter 规范化 board root。First-party listing 候选同分时优先保留当前 locale path，重定向回已访问 root 不再消耗有效 page budget。7-company focused live 从 0/7 提升到 3/7 job list 和 1/7 exact opening：Epistemix 通过 Ashby embed 精确命中，Quest Global/Viking 到达 locale-preserving Phenom search-results；其余 4 家继续返回 `JOB_BOARD_NOT_FOUND`。S5/checkpoint 语义更新后将 `ADAPTER_VERSION` 提升到 `2026-07-13.22`。本轮 462 个测试、13/13 provider、6/6 resolver、architecture gate 和 clean 51/51 fixed live expectations 均通过，fixed live 保持 51/51 job list、50/51 exact opening。
- Phenom 作为第 12 个原生 provider 自动接入：在 customer-owned `search-results` 页面用 `phApp`/`refNum`/CDN/eager-state 组合指纹识别 tenant，支持 direct 和 `phApp ||` 初始化、多次增量 config、有界 `keywords/from` SSR pagination、exact-title early stop、同源 detail URL 重建及跨 tenant/redirect 拒绝。Title-filtered empty inventory 明确标记为 no-match，不再误报全站无公开岗位。离线 provider benchmark 扩展到 14/14 exact opening；Quest Global/Viking focused live 均归属 Phenom，Quest 命中 exact opening，Viking 返回结构化 `OPENING_NOT_FOUND`。Provider/checkpoint 语义更新后将 `ADAPTER_VERSION` 提升到 `2026-07-13.23`。本轮 468 个测试、14/14 provider、6/6 resolver、12-adapter architecture gate 和 checkpointed 51/51 fixed live expectations 均通过，fixed live 保持 51/51 job list、50/51 exact opening。
- Paycom 作为第 13 个原生 provider 自动接入：将 legacy nonce URL 规范化为稳定 portal，解析公开页面的临时 session config，调用 title-filtered job preview API，支持最多 5 页、exact-title early stop、tenant/service redirect 隔离和 numeric detail URL 重建。S5 traversal 改为接受 registry-backed HTTPS board，ReturnPro live 从 `JOB_BOARD_NOT_FOUND` 提升到具体 `AI/ML Engineer` opening。Snapshot 脱敏增加 `sessionJWT`、`protectedSessionJWT` 和 `authToken` 驼峰字段，adapter trace 不记录凭证。Provider/checkpoint 语义更新后将 `ADAPTER_VERSION` 提升到 `2026-07-13.24`；本轮 473 个测试、15/15 provider、6/6 resolver、13-adapter architecture gate 和 checkpointed 51/51 fixed live expectations 均通过，fixed live 保持 51/51 job list、50/51 exact opening。
- Bounded link extraction 增加 Lever embed 配置识别：页面必须同时包含 embed 指纹和合法 `leverJobsOptions.accountName`，派生 board 会优先于普通链接并继续经过原生 Lever adapter 的 tenant/title gate。Influur live 从 first-party Webflow careers 页命中具体 Lever `AI Engineer` opening。S5/checkpoint 语义更新后将 `ADAPTER_VERSION` 提升到 `2026-07-13.25`；本轮 475 个测试、15/15 provider、6/6 resolver、13-adapter architecture gate 和 clean 51/51 fixed live expectations 均通过，fixed live 保持 51/51 job list、50/51 exact opening。
- S5 bounded BFS 增加 first-party career audience taxonomy：只遍历 career root 下最多两层的 staff/business-services/professional/student/lateral 页面，并只在官网明确使用 job-opportunity 语义时接受同 registrable domain 的 jobs/careers 子域 portal。Kirkland live 到达官方 U.S. staff jobs portal；Cloudflare challenge 和无公开目标标题证据使 `AI Engineer II` 继续保持 `OPENING_NOT_FOUND`。S5/checkpoint 语义更新后将 `ADAPTER_VERSION` 提升到 `2026-07-13.26`；本轮 477 个测试、15/15 provider、6/6 resolver、13-adapter architecture gate 和 clean 51/51 fixed live expectations 均通过，fixed live 保持 51/51 job list、50/51 exact opening。
- Fetcher 改为每个 worker thread 复用仅驻内存的 cookie-aware opener，在保持 batch 并发的同时支持匿名多请求 provider session；cookie 不进入 snapshot 或 trace。Snapshot 正文增加 hidden-input token 脱敏，replay 会为 redirect 的 sanitized request URL 物化可消费 alias，并按记录顺序解决路径冲突。
- Mphasis focused live 从失效的旧 career URL 修正为当前官网证据链，成功导航到 RippleHire board；91 个 provider-filtered 候选中未确认已过期的 LinkedIn 标题，因此保守返回 `OPENING_NOT_FOUND`。真实 12-record capture 可离线重放同一 S5/S6 结果。Provider/S5/replay 语义更新后将 `ADAPTER_VERSION` 提升到 `2026-07-13.27`；本轮 487 个测试、16/16 provider、6/6 resolver、14-adapter architecture gate 和 clean 51/51 fixed live expectations 均通过，fixed live 为 51/51 job list、50/51 exact opening，4 workers 用时 97.5 秒。
- Kforce focused live 从 first-party careers 页面进入 custom-domain Taleo board，当前 `AI Engineer` 官方过滤库存为 0，结果保持 verified `OPENING_NOT_FOUND`。Snapshot 脱敏增加 `sessionCSRFToken`，真实 capture 未脱敏字段为 0，并可离线重放。Ashby API fetch/parser 失败且 embedded fallback 空时改为 retryable `PROVIDER_FETCH_FAILED`，不再误报官方空库存。Provider/replay 语义更新后将 `ADAPTER_VERSION` 提升到 `2026-07-13.28`；495 tests、17/17 provider、6/6 resolver 和 15-adapter architecture gate 通过。两次 51-company no-resume live 均保持 51/51 job list，轮换网络 timeout 均由 focused exact recovery 验证，未降低 expectations。
- Eightfold 作为第 16 个原生 provider 接入：支持 `*.eightfold.ai` 和 customer-owned `/careers` 页面，使用 `smartApplyData`/PCS gate/tenant/inventory 组合指纹，读取 title/location-filtered SSR 首屏并通过公开 `/api/apply/v2/jobs` 做最多 5 页分页、exact-title early stop 和同 host numeric detail URL 校验；hosted tenant slug 可从已验证页面映射到 API customer domain。S5 仅探测官方 career 页中带明确 `view/search/browse jobs/roles` 文案的跨站 listing root，且跨站页面必须通过原生 URL 或 page-aware provider evidence 才能计作 job list。Netflix focused live 从 `jobs.netflix.com` 导航到 `explore.jobs.netflix.net` 并精确命中目标 AI Platform opening；脱敏后的 8-record capture 物化为 3 个 fixture，0.2 秒离线重放同一结果。Snapshot sanitizer 增加任意属性顺序的 meta `_csrf`/token 脱敏，旧未完全脱敏 capture 会被 replay validator 拒绝。`ADAPTER_VERSION` 提升到 `2026-07-13.29`；506 tests、18/18 provider、6/6 resolver 和 16-adapter architecture gate 通过。Clean 51-company live 保持 51/51 job list、48/51 exact、50/51 expectations；唯一严格失败 Ardent Health 为 iCIMS 6 秒 timeout，随后 focused 1/1 exact recovery，expectations 未下调。
- Nuro first-party Nuxt careers 页面现在通过通用 page-probe contract 读取同源 static payload，解析 91 条 Greenhouse-shaped records，并精确命中 `Software Engineer, AI Platform - New Grad`。10-record 脱敏 capture 物化为 3 个 fixture，完整离线回放在 0.3 秒内得到同一岗位 URL。`ADAPTER_VERSION` 提升到 `2026-07-13.30`；508 tests、18/18 provider、6/6 resolver 和 16-adapter architecture gate 通过。51-company live 最终为 51/51 job list、50/51 exact、51/51 expectations；Homebrew Python 3.14.2 在 multi-worker 调度期间发生两次 native interruption，company completion checkpoint 恢复 49 家后由单 worker 完成剩余 2 家，未丢失已发布结果。
- Cisco replay 将下一个通用缺口定位为 sitemap fan-out 和 query snapshot collision，而不是新 ATS：现有 Phenom adapter 可读取五页、50 条 title-filtered candidates，当前 LinkedIn 标题未达到匹配阈值，因此稳定返回 verified `OPENING_NOT_FOUND`。11-record focused capture 物化为 9 个 query-aware fixtures，0.3 秒离线重放与 live 的 candidate count、total hits 和 board URLs 完全一致。`ADAPTER_VERSION` 提升到 `2026-07-13.31`；513 tests、18/18 provider、6/6 resolver 和 16-adapter architecture gate 通过。51-company gate 因 Homebrew Python 3.14.2 多次 native termination 只能由 completion fragments 完成，碎片汇总为 49/51 job list、39/51 exact、40/51 displayed expectations；其 3 个严格失败 Ardent Health、Datadog、Airbnb 随后全新 focused 3/3 exact recovery。下一 release 工程优先迁移到稳定 Python 运行基线并保留 crash-safe checkpoint。
- Release runtime 固定为 CPython 3.12：`pyproject.toml` 声明 `>=3.10,<3.14`，可测试 policy 区分 supported runtime 与 release baseline，Makefile 将 runtime check、517 tests、18/18 provider、6/6 resolver、16-adapter architecture 和 51-company live 统一到同一解释器。Homebrew 3.14.2 会被明确拒绝，3.12.6 offline gates 全绿；首次 3.12 `--no-resume` live run 实际发布 51/51 completion、51/51 job list、50/51 exact 和 51/51 expectations，且 macOS 未生成新的 Python crash report。Codex PTY 提前停止显示输出，但重新连接确认 `restored: 51, pending: 0`。
- CI/runtime 闭环接入 GitHub Actions：supported matrix 覆盖 3.10、3.11、3.12、3.13，release-offline job 固定 3.12 执行 Makefile contract；手动 live workflow 使用 20 分钟 job timeout、禁止并发取消，并在成功或失败时保留 14 天证据 artifacts。首次 Linux CI run `29240521415` 全部成功。
- Dynamic LinkedIn S1 resume 闭环完成：旧 runner 在重连时重新搜索，真实 cohort 从 30 漂移为 25 并导致 completion 无法对应；新 manifest 在 downstream 前同步发布并与 ATS adapter version 解耦。真实 3-company smoke 第二次为 `restored: 3, pending: 0`，没有重新搜索。随后冻结的 30-company AI Engineer cohort 经多次 PTY 重连始终保持 30 家，最终基线为 27/30 官网、17/30 career、14/30 job list、11/30 exact。CPython 3.12 下 521 tests、18/18 provider、6/6 resolver 和 16-adapter architecture gate 通过。
- JazzHR 作为第 17 个原生 provider 接入：识别 HTTPS `*.applytojob.com/apply/jobs`，规范化租户 board，用 Resumator root + public search form 指纹读取完整公开 inventory，并仅接受同租户 `/apply/jobs/details/{id}` opening；跨租户 redirect/detail、credentials、异常端口和弱页面证据全部拒绝。Waltonen focused live 从 `JOB_BOARD_NOT_FOUND` 提升到具体 `AI Programmer` opening，8.0 秒完成；4-record 脱敏 capture 物化为 3 fixtures，0.2 秒离线重放同一结果。`ADAPTER_VERSION` 提升到 `2026-07-13.32`；527 tests、19/19 provider、6/6 resolver 和 17-adapter architecture gate 通过。
- Regional hiring discovery 与 Avature 闭环完成：S2 contract 传递 job location，美国岗位会拒绝明确外区 redirect，并在同一 brand host 上做 3 路有界 U.S. root recovery；S4 同域 sitemap/navigation candidate 保持 homepage locale，跨区重罚，同时修正 homepage self-link、career root 与 executive/student audience 排序；S5 接受同 registrable brand `apply` 子域的明确 job-search portal。Avature 作为第 18 个原生 page-aware provider 接入，用 `avature.portal.*` meta + same-host SearchJobs route 验证 customer domain，执行 title-filtered inventory，并校验同 portal numeric JobDetail。Deloitte live 从错误 Southeast Asia/Australia 路径提升为 U.S. exact `Agentic AI Engineer — Healthcare AI` job `355577`；带全球 sitemap 的完整 live 44.1 秒 exact，8-record capture 0.3 秒离线 exact replay。`ADAPTER_VERSION` 提升到 `2026-07-13.33`；538 tests、20/20 provider、6/6 resolver 和 18-adapter architecture gate 通过。
- S2 短品牌消歧现在把“LinkedIn company slug 与候选域主标签完全一致”作为强身份锚点，但只在 slug 确实包含品牌之外的消歧文本且主页已验证时生效，避免给普通同名域重复加分或压过地区冲突。Finch 从错误的 `finch.com` 修正为 `finchlegal.com`，沿官方 careers 页进入现有 Ashby adapter 并 exact 命中 `Machine Learning Engineer`；8-record live capture 物化为 7 fixtures，0.2 秒离线回放同一结果。`ADAPTER_VERSION` 提升到 `2026-07-13.34`；539 tests、20/20 provider、6/6 resolver 和 18-adapter architecture gate 通过。
- S2 将 Salesforce `my.site.com`、`l.ink` 和 `bit.ly` 从公司官网候选中排除，并在 fetch 后重新拒绝跳转到这些托管/短链 host 的猜测域，避免继承原域身份分。三词以上品牌可生成受限的“前置词首字母 + 末词”缩写域，LinkedIn slug 的 `-ai/-app/-tech` 后缀可用于还原候选；缩写域只有在主页 title 重复同一缩写时才补齐公司身份。S5 对官方 first-party careers payload 中无文本的已知 ATS detail 先规范化到原生 board，不再被 generic detail 提前返回截断。Standard Template Labs 从错误 `l.ink` 修正为 `stlabs.com`，进入 `st-labs` Ashby board 并 exact 命中 `AI Engineer`；5-record capture 物化为 3 fixtures，0.3 秒离线 exact replay。`ADAPTER_VERSION` 提升到 `2026-07-13.35`；543 tests、20/20 provider、6/6 resolver 和 18-adapter architecture gate 通过。

## [0.1.0] - 2026-07-12

首个可运行工程基线。

### Added

- LinkedIn public job discovery、公司官网解析和招聘主体映射。
- 七关 pipeline 结果模型、标准 reason code、trace 和结果验证。
- Career page、job board 和具体 opening 发现流程。
- Greenhouse、Lever、SmartRecruiters、Ashby、Workday、BambooHR 等结构化接口支持。
- iCIMS、SuccessFactors、Workable、Rippling 等结构化页面抽取支持。
- 静态 fetch、有限重试、Playwright smart fallback、本地 Chrome fallback 和脱敏 snapshot。
- 公司级 bounded concurrency、hard time budget 和持续 checkpoint。
- 固定离线/live benchmark、replay export/validation、阶段 resume 和 Markdown summary report。

### Known Limitations

- Google Careers、Meta Careers 和 generic fallback 仍保留 compatibility path；其余 10 个主要 ATS 已使用原生 adapter registry。
- SuccessFactors、iCIMS 的更多 tenant/theme 变体以及 5 家独立 SuccessFactors live 覆盖仍未完成。
- 固定 5 家 JS-heavy browser cohort 和 30+ 公司中断恢复压力验收仍未完成。
- Live 成功率仍受未知 ATS、JavaScript 页面、防爬、岗位下架和网络质量影响。
