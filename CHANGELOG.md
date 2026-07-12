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

### Changed

- 将 SOLID 架构拆分设为继续扩展 ATS provider 之前的前置阶段。
- 明确 stage、provider、fetcher、orchestration 和 reporting 的依赖方向与并行开发边界。

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
