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
- Greenhouse adapter 支持从 first-party `__NEXT_DATA__` 中识别完整 Greenhouse job schema，并对 custom frontend canonical URL 做同源校验。
- SuccessFactors adapter 支持 `*.jobs.hr.cloud.sap` 新 Career Site：解析页面 CSRF/locale，调用同源 recruiting v1 API 并还原 canonical job URL。

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

- S4-S6 仍集中在 `JobSourceAgent` 中，尚未成为真正独立的 stage runner。
- Provider 识别、请求构造和响应解析仍集中在 `opening_matcher.py` 的条件分支中。
- 任意 stage checkpoint store 和 `--rerun-stage` 尚未完成。
- Live 成功率仍受未知 ATS、JavaScript 页面、防爬和网络质量影响。
