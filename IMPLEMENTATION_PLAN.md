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

## 当前执行轮次（2026-07-20，`.190`）

### `.190` fresh 100：S4/S5 Career 正确性与已观察库存下钻（Phase B 完成）

已从 `.188` fresh closure 冻结 8 条 `JOB_BOARD_NOT_FOUND`（7 个独立公司表面），并使用同版本 trace/
snapshot 完成只读根因审计。结论不是一个 provider 缺失：Milwaukee Tool 与 B&D Industries 是 S4
把 press/project 页面误认成 Career；Northern Clearing 的官方 ApplicantPro 链接因文字仅为 `HERE`
而在 top-N 前被截断；IGNITE 已经进入包含目标岗位的 HRSmart 列表，但通用库存不认识
`/Posting/view/{id}` 结构；Splashlight、CHAMP 与 NextPlay 仍需要严格区分可验证公开库存、第三方阻塞和
招聘中介身份，不能凭 LinkedIn/搜索摘要补 URL。

实现与回滚 contract 已冻结在 `docs/FRESH_100_V190_S5_ROOT_CAUSE.md`。Phase B 顺序固定为：先补 S4
非招聘内容负向 gate 和跨站显式 Career 导航的 URL-only evidence；再让 S5 bounded traversal 为已注册
listing provider 保留验证槽位；最后扩展“官方 Career action 实际抵达的重复结构化 opening route”
识别。S6/S7 的 title/location/status/company/tenant gate 不放宽，不增加公司特例。

Phase C 使用全新的 focused live 目录运行这 8 条并冻结代码，逐条审核所有 Job List/Exact；随后对同版
snapshot 做 scoped replay，要求 0 mismatch/0 fixture gap，再运行全量 unit/provider/resolver/
architecture gate。focused 结果只关闭对应 failure cluster，不覆盖 `.188` fresh 11/100 或冻结 100
的 69/100；多个主要簇关闭后才运行下一次独立 unified fresh 100。

Phase B 已完成：S4 增加非招聘内容路径冲突 gate，并保留显式跨站 Career 导航的 URL-only evidence；
S5 为已注册 listing provider 保留 bounded traversal 槽位，并仅允许 adapter 证明同一 provider/tenant
时将安全 root locator 规范化为 canonical board。HRSmart 原生 adapter 已读取完整 `viewAll` 库存，
Freshteam widget adapter 只有在当前 Career 页面声明唯一受限资产、资产声明唯一 tenant 且该 tenant 的
公开库存非空时才建立 board。Career evidence 持久化现在保留实际 origin/source URL，当前语义拒绝只
失效 Career 层，不删除已验证 Website。相关负向、provider 与 pipeline 集成测试已补齐；下一 gate 是
全量离线验证，通过后冻结代码并以独立目录运行 8 条 focused live。全量离线 gate 已通过：2466
tests（3 skipped）、25/25 provider、6/6 resolver、46 native adapters / 0 architecture issue；代码将在
focused live 前提交冻结，运行期间不再修改。

### `.189` fresh 100：S2 冷启动传输与候选调度（Phase C 完成）

`.188` fresh 冷启动经人工 Exact 审核后为 11/100，eligible Exact recall 为 11/90；79 条
`SYSTEM_GAP` 中 49 条在 S2 失败。两次使用独立 checkpoint/snapshot 的 49 条 S2-only 重跑均只解析
同样 3 条，其余同样 46 条失败，每轮产生 413/414 个 retry event。失败标签在 7 条上发生变化，成功
集合完全不变，因此根因是可重复的候选调度、重试预算和传输异常契约缺陷，不是一次网络波动。

`.189` 第一轮只处理该通用簇：已冻结
`docs/FRESH_100_V189_S2_ROOT_CAUSE.md` 作为实现契约。Phase B 将把
`IncompleteRead` 等已知 transport exception 统一封装为可快照的 typed failure；按
DNS/connect/TLS/HTTP/read 记录诊断；只重试有直接证据的候选；先收集 LinkedIn/search 证据并为证据
路线保留预算，再有界验证 speculative guess。不得增加公司或 benchmark 特例，不得放宽 homepage
身份验证，也不得用 focused 结果改写 `.188` 统一成绩。

Phase C 固定回归这 49 条：0 worker exception、每条完整 S2 snapshot boundary、speculative retry 为
0、scoped replay 0 mismatch/0 fixture gap，并逐条审核所有新解析官网的公司身份。通过后运行全量离线
gate，再决定是否进入 S5 的 8 条缺陷；只有多个主要簇关闭后才以全新目录运行 code-frozen fresh 100。

Phase B/C 已完成。最终 code-frozen run7 从 `.188` 重复诊断的 3/49 提升为 5/49，retry event 从
413 降至 290，平均 S2 时间从 8.957 秒降至 6.476 秒；speculative candidate 的 scheduled retry 为
0。49/49 均有完整 S2 snapshot boundary，Ken Garff 的 raw `IncompleteRead` 不再逃逸 worker。最终
5 个 selected website 全部通过公司身份审计；中间轮次暴露的 `stuller.org` 和 `teamroyal.org`
低证据假阳性已由通用 speculative-only content-identity gate 拒绝。

same-version focused replay 为 `49/49 reproduced / 0 mismatch / 0 fixture gap / 0 integrity failure`。
Python 3.12 全量门禁通过 2436 tests（3 skipped）、25/25 provider、6/6 resolver、44 adapters/0
architecture issue。报告与逐条 delta 见 `docs/FRESH_100_V189_S2_PHASE_C.md` 和
`artifacts/evaluations/fresh100-v189-s2-focused-20260720-run7/`；完整 live/replay archive SHA-256 为
`ad5d7344d187cdcd74b217140fd2448fd135afbea7c2acf8d7e6db2cd54bbb94`。

剩余 44 条 S2 未解析不能被改标为外部终态，也不继续用扩大猜测域名或 timeout 的方式追求官网召回。
下一轮进入 S5 Phase A：从 fresh closure 中冻结 8 条 `JOB_BOARD_NOT_FOUND`，审计 External Apply、
provider-targeted search 与 verified Career 下钻三路 candidate portfolio，确保 S2 失败不阻塞
provider/tenant 验真。该轮先分析 trace/snapshot，不在 Phase A 修改代码。

### `.188` fresh 100 冷启动独立泛化基线（冻结）

`frozen100-v188` 的代码与原冻结 100 artifacts 保持不变。独立 July 18 fresh 100 冷启动输出位于
`artifacts/evaluations/fresh100-v188-cold-20260720-run1/`：人工审核后为 11 Exact、9
`VERIFIED_NOT_FOUND`、1 `INPUT_IDENTITY_INVALID`、79 `SYSTEM_GAP`；错误地点 URL 1 条。该基线及
对应 replay integrity 结论只用于定位缺陷，任何 focused 修复不得覆盖或回写。

### `.188` 最终冻结 100：跨域 Career 搜索结果的招聘关系验真（完成）

`.187` 冻结 100 unified live 已完成 100/100，当前结果为 68 Exact、88 Job List；Bastion 的
ApplicantPro requisition-code 标题后缀缺陷已恢复 Exact。但同版本 replay 在 Blossom 暴露出新的
correctness defect，因此该轮不能计为最终通过：S4 将搜索结果
`blossomrestaurant.com.sg/careers.html` 仅凭同名公司与 Career 页面形态接受为
`blossom.net` 的招聘页面，并污染了 company evidence；S5 后续虽找到历史 Ashby board，S7 正确
拒绝未验证的招聘关系，但错误 Career 候选本身已经证明 `SYSTEM_GAP` 尚未清零。

`.188` 保留搜索召回，但不再让跨 registrable-domain 的
`unverified branded career microsite search lead` 直接通过普通 Career 页面判定。此类候选必须在
发布或写入 evidence store 前同时证明：页面公司身份匹配、canonical/`og:url` 与最终页面同源、
存在同源可执行岗位入口，并且页面存在可绑定到已验证官方公司的 corporate backlink；缺少任一
关系证据即拒绝。ATS URL 仍进入 provider/tenant 验真，不能借此通用分支建立关系。

本轮 Phase C 顺序固定为：Blossom 负向 fixture 与相关 Career 测试；使用未污染 evidence store
执行 Blossom focused current live；同版本 focused replay 1/1；全量离线 gate；最后以冻结 `.188`
代码和独立 artifact 重跑完整 100 live 与 100/100 replay。只有错误/跨 tenant URL 为 0、完整 closure
matrix 可复现且 `SYSTEM_GAP=0` 后才更新最终数字、提交并推送。

Phase B/C focused gate 已通过。进一步根因确认 `.187` 不只接受了错误的跨域 Career 搜索结果，
还让先前缓存的同名单词域 `blossom.net` 在重新加载后抢先返回，跳过了 LinkedIn company slug
`join-blossom-health` 提供的更强官网候选。`.188` 现在只在 stored domain 与完整 LinkedIn slug domain
冲突时把二者放入同一 revalidation wave；普通无冲突 stored 官网仍保持快速路径。Blossom focused
current live 从原始错误缓存状态出发，在 7.5 秒内恢复
`joinblossomhealth.com -> /careers -> Ashby Blossom-Health -> exact opening`，标题为
`Software Engineer (All Levels)`、地点为 New York City、完整库存 10 条；证据库重新写成连续的
Website/Career/Provider 链。same-version replay 为 `1/1 reproduced / 0 mismatch / 0 fixture gap`。
相关 resolver、Career、stage 测试 221 条通过；下一步为全量离线 gate 和冻结 100 unified live。

最终 gate 已全部完成。全量离线门禁通过 2429 tests（3 skipped）、provider benchmark 25/25、
resolver benchmark 6/6、44 adapters/0 architecture issues。冻结 `.188` unified live 运行 100/100，
耗时 473.2 秒，返回 69 Exact、89 Job List；相较 `.187` 恢复 Blossom 后 Exact 增加 1。same-version
full replay 为 `100 reproduced / 0 mismatch / 0 fixture gap`，record integrity 100/100。独立 Exact
审核确认 69/69 canonical opening URL 与 closure matrix 一致，provider relationship 均 verified，
标题/地点无 identity conflict，错误、跨公司及跨 tenant URL 均为 0。

最终 evidence ledger 为 `69 EXACT / 23 VERIFIED_NOT_FOUND / 5 EXTERNAL_BLOCKED /
3 INPUT_IDENTITY_INVALID / 0 SYSTEM_GAP`；raw exact 为 69%，eligible exact recall 为 69/69，
audited exact precision 为 69/69。统一 live 中 10 条 current transport failure 均有此前 focused
live/replay 支持的非系统终态，不用单次网络结果覆盖更强证据。最终报告见
`docs/FROZEN_100_FINAL_REPORT.md`。

### `.187` 最终冻结 100：S7 标题后缀与 scoped replay 因果状态（进行中）

`.186` 已完成最后五条统一 current live：Panacea 为
`COMPANY_IDENTITY_AMBIGUOUS`，Riverview 为 `OPENING_IDENTITY_AMBIGUOUS`，Southeastern 为
`UNVERIFIABLE_THIRD_PARTY_HANDOFF`，Garan 返回官方 Workable Exact，Great Value 为
`COMPANY_IDENTITY_AMBIGUOUS`；5/5 scoped replay 通过。closure matrix 已机械核对为
`69 EXACT / 23 VERIFIED_NOT_FOUND / 5 EXTERNAL_BLOCKED / 3 INPUT_IDENTITY_INVALID / 0 SYSTEM_GAP`。

随后 `.186` 冻结 100 unified live 完成 100/100：66 条当前 Exact、89 条 Job List、9 条当前网络/访问
失败，未发现错误 URL。Bastion 是唯一真实 correctness regression：ApplicantPro 官方 opening 标题
`Mechanical Project Engineer (BT-26148)` 与目标仅相差终端 requisition code，S6 正确匹配但 S7
错误拒绝。`.187` 只允许删除严格的终端 requisition-code 括号，不删除 `(Propulsion)` 等专业方向，
因此 Bastion 应恢复 Exact 而不放宽普通标题身份。

旧 `.186` 全量 scoped replay 现已执行 100/100，结果为 `99 reproduced / 1 mismatch / 0 fixture gap`；
唯一 mismatch 正是 Bastion 从旧的错误 rejection 转为当前 verified Exact。修复过程中同时补齐 replay
bundle 的阶段因果 contract：最终 evidence store 中由本轮 S5 新写入的 provider board 不得泄漏到
S4；被本轮 invalidation 删除、但 source trace 明确读取过的 stored website/career 必须恢复；只有
source trace 明确选择 `stored_verified_provider_board` 且冻结源 store 的 canonical provider/tenant
一致时才恢复 provider input。Tata、Leadenhall、Stark CEIPAL、SpaceX 和 Actabl focused replay 均已
覆盖这些边界。

`.187` 的剩余 gate 是：Bastion focused current live + replay；全量 unit/provider/resolver/architecture
gate；最后以冻结代码和独立 artifact 重跑同一 100 条 live，再对 `.187` snapshots 执行 100/100
scoped replay。通过条件仍为 closure matrix `SYSTEM_GAP=0`、错误/跨 tenant URL=0，并将新 unified
结果、网络外部终态和 eligible exact recall 写回文档后提交推送。

### `.186` Phase B/C：stored ATS Career 直达 S5 当前验真（进行中）

`.185` 五条 focused live 中，Southeastern、Garan 和 Great Value 已分别收敛为
`UNVERIFIABLE_THIRD_PARTY_HANDOFF`、Workable Exact 和 `COMPANY_IDENTITY_AMBIGUOUS`；full scoped
replay 5/5 可复现。但 Panacea 从上一轮的 `OPENING_NOT_FOUND` 退化为 S4 `NETWORK_TIMEOUT`，
Riverview 从 `OPENING_IDENTITY_AMBIGUOUS` 退化为 `WEBSITE_NOT_RESOLVED`。replay 只证明本轮结果
可复现，不能把错误终态算作 Phase C 通过。

Panacea 根因是 evidence store 已保存可由 Paylocity adapter 明确认出的 Career URL，但 S5 只读取
`provider_boards`，S4 因而重复执行网页导航并在 152 秒后超时。`.186` 增加
`stored_verified_career_provider` 候选类型：只有 record 的公司/LinkedIn key 连续、website 与
career 的归属连续、Career URL 可由支持 listing 的 adapter 识别时，S4 才把它延后到 S5。该记录
只提供候选入口，不授予招聘关系或成功；当前 provider inventory、tenant、title/location/status 和
S7 仍必须重新验证，未重建身份时发布层继续隐藏 stored Job List URL。普通同站 Career 页面、跨
identity record 和不支持 listing 的 URL 不会进入该路径。

Phase C 先分别执行 Panacea 和 Riverview focused current live：Panacea 必须恢复为当前 Paylocity
库存支持的结构化终态；Riverview 必须恢复官方 Career 同页 inventory，若持续网络失败则保留当前
证据并按 retryable/external 分类，不能写公司特例。两条通过后再统一重跑最后五条及 full scoped
replay；随后更新 closure matrix、运行全量离线 gate，并执行冻结 100 条统一 live 回归。

`.186` focused current live 已关闭两条退化。Riverview 在 39.5 秒内恢复官方 website、Career 与
同页 Job List，返回预期 `OPENING_IDENTITY_AMBIGUOUS`，full replay 1/1 通过。Panacea 在 3.1 秒内
跳过重复 S4，读取 Paylocity 当前完整库存；进一步核验发现 source company 是 Pennsylvania 的
skilled-nursing operator，而 cached website 是 Barcelona 的西语 health-data 产品，Paylocity tenant
又属于 Minnesota 的 `Panacea Healthcare Solutions`。因此旧轮次的 `OPENING_NOT_FOUND` 实际混入
了错误公司关系；`.186` 正确收敛为 `COMPANY_IDENTITY_AMBIGUOUS`，不发布 Job List/Opening，full
replay 1/1 通过。下一 gate 为同一代码、独立 artifact 的最后五条统一 current live + replay。

### `.185` Phase B/C：最后五条发布语义与 ATS tenant 恢复（进行中）

`.184` 将后置的 evidence-backed availability/identity terminal 提升为最终发布结论，并补齐
first-party fragment inventory、RN alias、同地点多 opening ambiguity、unlinked recruiting handoff、
replay-safe no-public evidence 和 hiring intermediary 识别。相关 290 tests 通过；使用当前重新验证的
company evidence 对五条 focused live 后，Panacea 为 `OPENING_NOT_FOUND`，Riverview 为
`OPENING_IDENTITY_AMBIGUOUS`，Southeastern 为 `UNVERIFIABLE_THIRD_PARTY_HANDOFF`，Great Value
为 `COMPANY_IDENTITY_AMBIGUOUS`，四条均通过 full scoped replay。Garan 仍因 ATS 搜索与 probe
调度缺陷停在 retryable `FETCH_FAILED`，所以当时不能宣布 Phase C 完成。

`.185` 继续修复通用三路候选实现而非增加 Garan override：provider search 的 source fetch budget
现在与声明的 query budget 一致，title-targeted provider 计划覆盖 Workable；当 Bing 索引无效且
DuckDuckGo challenge 时，tenant fallback 按“已验证官网 slug / 完整法人 slug / LinkedIn slug”与
provider 波次交叉调度，8 次上限不再被一个短 slug 独占。Workable 当前 title-filtered API 在找到
精确标题后会提前停止分页；tenant probe 现在允许“同 tenant + 精确标题候选”建立候选关系，完整
库存仍是无目标候选时的硬门槛，location、状态和 opening URL 继续由 S6/S7 验证。

Garan `.185` focused live 已返回官方 Workable board
`https://apply.workable.com/garan-incorporated/` 和精确 opening
`https://apply.workable.com/garan-incorporated/j/6EA77C8F89/`；title 为
`Junior Financial Operations Analyst`，location 为 New York，identity assertion 为 verified，full
scoped replay 1/1 reproduced、0 mismatch、0 fixture gap。下一步冻结 `.185` 重跑全部五条，复核
Panacea 的 provider identity、四个结构化终态和 Garan Exact；通过后更新 closure matrix 为
`SYSTEM_GAP=0`，执行全量离线 gate 和最终冻结 100 条统一回归。

### `.183` Phase A：最后五条 evidence closure（进行中）

`.182` 后冻结 100 条只剩 Panacea Health Corp、Riverview School、Southeastern Renal
Dialysis、Garan, Incorporated 和 Great Value Hiring 五条 `SYSTEM_GAP`。本轮先审计同一批
`.173` live/snapshot/trace，不把人工“没有 Career”直接升级为终态，也不把 Adzuna/Indeed 搜索
摘要当作公司招聘关系。审计将五条冻结为两个共享缺陷，不增加公司 override：

1. **第一方 Career 页面内嵌岗位库存**：Riverview 的官方 Career 页面正文已经列出
   `PT Overnight RN - School Nurse` 和 `PT Evening Shift RN - School Nurse`，并提供同页 fragment
   与官方 employment application。旧 S5 只寻找独立 Job Board，未把带稳定岗位锚点的官方 Career
   页面建模为完整 first-party inventory，因此错误停在 `JOB_BOARD_NOT_FOUND`。Phase B 只扩展
   通用第一方 inventory contract：岗位标题、稳定 fragment/详情链接、申请动作和公司页面身份都
   必须来自当前官方页面；匹配仍经过 title/location/S7，不信任 Adzuna handoff。
2. **可复现的无公开招聘表面终态**：Panacea、Garan 和 Great Value 的已验证官网已执行 homepage
   navigation、常见路径、bundle、sitemap、ATS probe 与 bounded search，未产生第一方 Career 或
   provider；Southeastern 的官方 Employment 页面明确说明岗位转由 Indeed 发布，但没有可验证的
   handoff URL。旧模型只能返回 `CAREER_PAGE_NOT_FOUND`/`JOB_BOARD_NOT_FOUND`，无法区分“搜索
   尚未完成”和“官方公开表面已完整检查但没有可验证入口”。Phase B 将建立严格、可 replay 的
   no-public-recruiting evidence contract；只有官网身份已验证、必需发现路径均实际完成、没有预算
   耗尽/网络失败、没有未验证强候选时才允许结构化终态。任何 retryable fetch、截断或第一方招聘
   线索都会 fail closed 为 `SYSTEM_GAP`。

Phase C 固定验收这五条和至少两条控制样本：Riverview 必须返回官方同页 exact fragment 或经 S7
验证的同页 opening；其余四条必须返回 replayable 的 verified no-public terminal，0 opening URL。
完整 scoped replay 要求 5/5 reproduced、0 mismatch、0 fixture gap；随后运行全量单测、provider、
resolver 和 architecture gate。通过后 ledger 目标为 `SYSTEM_GAP=0`，再冻结代码运行最终 100 条
统一 live 回归；最终统一回归若暴露新系统缺陷，继续按 A -> B -> C 修复，不能用本轮 focused
结果直接宣告 Goal 完成。

### `.182` Phase B/C：form 内嵌 JSON request identity（已通过）

`.181` 确认 RippleHire replay 已发出 POST，但 outcome tape 拒绝其 body fingerprint。对同一请求
重算后，live fingerprint 为 `41f740...`，replay 为 `781841...`；headers、URL、title、location 和
page 均一致，唯一差异是 `careerSiteUrlParams` 普通 form 字段内部 JSON 的 `token`。request identity
旧逻辑只清洗顶层 form key，不递归解析 JSON string，因此敏感值虽然未写入 artifact，fingerprint
仍依赖原 token，无法由隐私清洗 snapshot 稳定复现。

`.182` 将 request identity 升为 v2：对非敏感 form 字段，只有值是合法 JSON object/array 时才按
现有 structured sanitizer 递归清洗敏感 key 并 canonicalize；普通字符串、非法 JSON、非敏感字段
差异和分页差异保持原语义。Phase C 新 live 为 2/2 Website、Career、Job List，两条当前完整
title-filtered RippleHire inventory 均返回 `OPENING_NOT_FOUND`；full scoped replay 为 2/2
reproduced、0 mismatch、0 fixture gap，identity chain 完全一致，0 opening URL。Tata 两条关闭为
`VERIFIED_NOT_FOUND`，ledger 更新为
`68 EXACT / 23 VERIFIED_NOT_FOUND / 4 EXTERNAL_BLOCKED / 5 SYSTEM_GAP`。下一簇从 Panacea、
Riverview、Southeastern Renal、Garan 和 Great Value 五条中按共享 evidence gap 批量选择。最终
离线门禁为 2393 tests（3 skipped）、25/25 provider benchmark、6/6 resolver benchmark，以及
44 native adapters / 0 architecture issues。

### `.181` Phase B/C：RippleHire 隐私清洗 replay（已由 `.182` 完成）

`.180` live 已重新达到 2/2 Website、Career、Job List，且两条当前 complete title-filtered
inventory 均为 `OPENING_NOT_FOUND`。自动 replay 现在能够恢复 strict RippleHire locator，但第一条
opening tape 在消费 portal GET 后留下 POST 未消费并 fail closed。根因是 snapshot 将 HTML 与 final
URL 的 public routing token 同时清洗为 `[REDACTED]`，outcome tape 又保留原页面的 `source=live`；
adapter 旧逻辑因此拒绝 placeholder，没有执行 inventory POST。

`.181` 只在 HTML hidden token 与 final URL query token 同时等于 `[REDACTED]` 时替换为稳定 replay
placeholder；部分清洗、值不一致、真实非法 token 和跨 tenant 仍拒绝。请求身份会继续按敏感 form
field 清洗，因此 live/replay POST fingerprint 相同且不保存 token。完成标准仍为 fresh live 2/2、
full replay 2/2、0 mismatch、0 fixture gap。

### `.180` Phase B/C：RippleHire replay-safe locator（已由 `.182` 完成）

`.179` 使用仓库既有 evidence seed 工具从 `.104` 已验证 live 迁移 Website、Career 和 provider
relationship 候选；迁移 manifest 为 3 条输入、3 个 identity、0 rejection，且不包含 HTML、库存
或 opening。两条 Tata 随后均当前重验到正确 `/en/` Website、官方 Career 和同 tenant RippleHire，
当前 title-filtered inventory 为 complete empty、`OPENING_NOT_FOUND`，0 opening URL。

首次 2 条 full-outcome bundle 被 replay gate 正确拒绝：RippleHire canonical board 虽然只有公开
tenant host 和固定 `/ripplehire/careers`，adapter 仍将 locator 标为 runtime-only，中央 registry 也
没有严格 replay policy，导致 `scoped_stage_seed_ambiguous` 2/2。`.180` 为 RippleHire 增加精确
`<tenant>.ripplehire.com` host、tenant identifier、固定 path、无 query 的 replay-safe contract；
跨 tenant、apex、错误 path/query 继续拒绝。Phase C 必须重新 live 2/2 并 full replay 2/2，不能
绕过 scoped gate。

### `.179` Phase A/B：Tata 停放域名误验真（已由 `.182` 完成）

两条 Tata 冻结原始输入的 `.178` fresh baseline 已完成。resolver 将机械生成的
`https://tata-technologies.com` 选为 Website，并给广告停放页错误授予 `homepage verified`；
正确的 `https://www.tatatechnologies.com` 因当前 403 没有获选，S4 随后以
`CAREER_PAGE_NOT_FOUND` 终止，既有 first-party Career -> RippleHire contract 完全没有运行。
该 baseline 的 live 和 replay 均为 2/2，但只复现了错误终态，不能用于关闭 ledger。

当前 Phase B 只修通用停放页识别：`findresultsquick.com` 广告 iframe、对应 CDN 和 DMOLA
模板标记出现时，候选必须得到 `parked domain rejected`，不得得到 `homepage verified`，即使页面
title/body 回显完整公司名。回归 fixture 使用虚构公司，不增加 Tata URL 或 company override。
完成后先重跑 Tata 两条；若正确官网仍因 403 无法恢复，再单独审计 durable evidence 的当前重验
边界。Phase C 仍要求两条均沿当前官方 Career -> 同 tenant RippleHire 链得到结构化终态，replay
2/2、0 wrong URL、0 cross-tenant、0 fixture gap、0 mismatch，才可更新 closure ledger。

### `.174-.178` Phase B/C：嵌入式 Job List 与 WordPress 分页（已通过）

本簇只处理 `.173` ledger 中两个“已到官方 Job List、但库存不完整”的 SYSTEM_GAP：
Leadenhall 和 EVONA。EVONA 的真实 WordPress 标记同时包含数字页码、损坏的
`?paged=?paged=N` 参数，以及查询分页到 `/page/N/` 的同页 canonical redirect；旧实现只增加
`paged` key，没有让纯数字链接进入 next-page 控制流，因此 live 仍停在第一页。`.176` 现在仅在
同 origin、同 base path、连续页码、相同非分页参数时接受该链接和等价重定向，跨路径或跨页码仍
fail closed。Focused live 读取当前完整两页 38 条 inventory，返回 `OPENING_NOT_FOUND`；replay
`1/1` 通过。

Leadenhall 的官方 Career 通过 first-party iframe 嵌入 Loxo。原始链接抽取已保留 `iframe_src`，
但 S5 embedded-provider 白名单漏掉该 provenance，导致新 adapter 无法执行。`.178` 将 iframe
作为受限的 first-party embedded handoff，新增 tenant/path 绑定、query echo、same-tenant detail
和完整空结果验证的 Loxo adapter，并补齐 replay-safe locator policy 与 trace method contract。
Focused live 返回完整 title-filtered `OPENING_NOT_FOUND`，replay `1/1` 通过。相关局部门禁
`326/326` 通过，错误 URL、跨 tenant 和 fixture gap 均为零。最终离线门禁为 2389 tests
（3 skipped）、25/25 provider benchmark、6/6 resolver benchmark，以及 44 native adapters /
0 architecture issues。

本簇将 ledger 更新为
`68 EXACT / 21 VERIFIED_NOT_FOUND / 4 EXTERNAL_BLOCKED / 7 SYSTEM_GAP`。下一簇是 Tata
Technologies 两条 parent-career/RippleHire handoff；在其 Phase A 证据审计完成前不修改代码。

### `.178` Phase A：Tata Technologies 证据连续性（已被 fresh baseline 修正）

历史 `.104` live 已经从原始 LinkedIn 公司身份解析到精确的
`https://www.tatatechnologies.com`，再由 first-party Career 的可见 `Search roles` 链接绑定
`tatatechnologies.ripplehire.com`。现有 RippleHire adapter 完整读取 title-filtered inventory，
两条目标均返回 0 candidates、`inventory_complete=true` 和 `OPENING_NOT_FOUND`；没有使用宽泛的
`tata.com` 集团库存，也没有跨 tenant。`.173` 的历史 trace 曾表现为 S2 网络失败，但 `.178`
fresh baseline 证明当前直接缺陷是 S2 将机械连字符的广告停放域名误验为 Website。根因仍不在
parent-career contract 或 provider adapter，但证据恢复假设已由上面的 `.179` Phase A/B 取代。

原 Phase B 假设到此冻结为历史记录；实际实施和验收以 `.179` contract 为准。仍不得硬编码 Tata
URL、恢复旧 opening/no-match，或让 broad parent/group website 证明子公司库存。只有当前官方
RippleHire inventory 再次证明 complete no-match，Tata 才能关闭为 `VERIFIED_NOT_FOUND`。

### `.173` Phase A：剩余 14 条终态闭环（当前活动簇）

`.172` checkpoint `9f54143` 已推送后，剩余 ledger 冻结为 14 条 SYSTEM_GAP，输入为
`/private/tmp/frozen100-v172-next14-input.json`。本轮不再重跑已关闭的 86 条，也不把 manual
observation 直接升级为产品终态。14 条按证据缺口分为：访问受阻/外部承载 5 条，Career/库存
不完整 5 条，身份/标题/输入关系 4 条；完整 live 仍由主线串行运行，调查和局部测试可按互斥
写集并行。

第一项通用缺陷来自 Meta Sunnyvale：官方完整库存中存在多个 exact-title opening，location 为
`Sunnyvale, CA + Redmond, WA ...`，但 S6 未拆分空格包围的 ` + ` 多地点分隔符，错误拒绝 exact
title 后选择 Battery 专业岗位，最终被 S7 正确拒绝。`.173` 统一多地点解析仅接受 `;`、`|`、
换行和两侧有空格的 ` + `，同时用于严格地点匹配与显式冲突判断；不能误拆 `C++`，全部地点均
冲突时仍拒绝。旧 `.172` snapshot 的预回放恢复 exact-title multi-location opening后，`.173`
新 live 已达到 Intuit 加三条 Meta controls `4/4 Exact`；同版本 replay `4/4 reproduced`、
0 mismatch、0 fixture gap，131 条相关测试通过。Meta Sunnyvale 因而从 SYSTEM_GAP 关闭为
Exact，ledger 更新为 `68 EXACT / 19 VERIFIED_NOT_FOUND / 13 SYSTEM_GAP`。

随后 `.173` 对其余冻结 13 条执行统一 live（615.6 秒）和自动 full replay。live 为 3 条已验证
Job List、0 Exact、13/13 完成；replay 为 13/13 reproduced、0 mismatch、0 fixture gap。West Oaks、
Tidelands、Aveanna、Dior 均在已验证官方链上得到非重试 `HTTP_FORBIDDEN`，按现有产品终态统一关闭为
EXTERNAL_BLOCKED，且没有发布 URL。ledger 因而进一步更新为
`68 EXACT / 19 VERIFIED_NOT_FOUND / 4 EXTERNAL_BLOCKED / 9 SYSTEM_GAP`。剩余 9 条分为两个 Job List
库存缺口（Leadenhall、EVONA，已由 `.178` 关闭）、两个 Tata parent-career handoff、Riverview 第三方招聘关系，以及
四个无 Career/无可验证外部 handoff（Panacea、Southeastern、Garan、Great Value）。

其余三组的 Phase B contract 在读取历史 trace 后冻结：第三方 recruiter/Adzuna 不建立第一方
招聘关系；HTTP 403/TLS/bot protection 只有可重复的官方 handoff 与结构化 posting disposition
才能成为 EXTERNAL_BLOCKED；完整官方库存才能宣布 VERIFIED_NOT_FOUND；Career 页面、搜索摘要、
人工未找到均不够。下一次 14 条 gate 必须 14/14 产生结构化终态、full replay 14/14、0 wrong URL、
0 cross-tenant、0 fixture gap、0 mismatch，并逐条审计所有新增 Exact。

### `.172` Phase C：S2 22+6 关闭（已通过）

更新后的验收 contract 要求现有 S2 失败簇在进入任何后续失败簇前完整通过 Phase C。
`.172` 已用同一冻结 22 条目标加 6 条控制样本完成 live `28/28`，随后用该次 scoped
snapshot 完成 full replay `28/28 reproduced`、`0 mismatch`、`0 fixture gap`。Tenet 的
generic-board transient query 已稳定归一到 canonical `/search-jobs` identity；CEIPAL 的
两阶段 continuation 和 DigitalRecruiters 的动态 typed board 也按 live checkpoint 边界严格
复现，不再因第一个非成功上游阶段或 trace 简表丢失 provider identifier 而漂移。

独立 Exact 审计推翻两条旧结论，并在严格发布门上线后重新验收：

1. Intuit 冻结输入 `Software Engineer 1; New York, NY` 在 `.157` 发布的
   `Principal Software Engineer` 已被严格职级门拒绝。`.172` 重新读取官方库存后选择同名
   `Software Engineer 1`，官方地点集合明确包含 New York（同时包含 Mountain View、San Diego
   和 Atlanta），因此该 exact URL 通过 focused live/replay `1/1` 与 title/location audit。
2. Meta Sunnyvale 冻结输入 `Product Design Engineer` 发布的是
   `Battery Product Design Engineer`。额外专业限定没有同一 posting 证据，必须拒绝；其余
   Meta 样本需逐条保持 title 与 location 连续，不能因共享 Meta inventory 自动放行。

本失败簇 Phase B 的实现边界冻结为：

- generic first-party board 的 title/location 搜索参数只作为 request state；live/replay 的
  provider、tenant、canonical board 必须稳定归一到已验证 root，同时保留 `orgIds` 等非瞬时
  组织 scope，禁止跨站或跨 tenant 合并。
- opening title identity 必须拒绝额外职级和额外专业限定；仅大小写、标点、顺序以及明确 alias
  （如 `Sr`/`Senior`）可归一。`Principal -> level 1`、`Battery Product Design -> Product Design`
  和只靠 token 子序列的匹配全部作为负向 contract。
- 不新增 provider，不加 Intuit/Meta 公司特例；修改限制在 title identity、selection gate、
  transient generic-board identity 及其测试。

Phase C 的 14 个 Exact 已逐条审核 company/hiring entity/provider/tenant/title/location；所有 S7
verdict 均为 verified，地点分类仅为 exact/overlap/region/url qualifier，错误 URL 为零。相对
`.162` 没有 Exact 回退，NexCare 新增 Exact，Southeastern 从 Failed 进入保守 Partial。Meta
Sunnyvale 的 `Battery Product Design Engineer` 继续被拒绝；Meta New York、Redmond 和 Intuit
focused controls 为 `3 Exact + 1 title mismatch`，并 full replay `4/4`。本簇关闭后 ledger 为
`67 EXACT / 19 VERIFIED_NOT_FOUND / 0 EXTERNAL_BLOCKED / 0 INPUT_IDENTITY_INVALID / 14 SYSTEM_GAP`。
最终共享门禁通过 2377 tests（3 skipped）、25/25 provider benchmark、6/6 resolver benchmark
以及 43 native adapters / 0 architecture issues。
下一步必须先完成本簇文档、完整离线门禁、commit 和 push，再从剩余 14 条 SYSTEM_GAP 批量选择
下一个互不混杂的 failure cluster；不得回到逐公司 heuristic 循环。

`.172` 在目标更新前已经完成的 Elderwood first-party action-chain 与 MrBeast exact official-domain
tenant binding 保留为已验证 focused 证据（live/replay 各 1/1），但不据此跳过当前 S2 Phase C，
也不把人工观察的 no-job/access-denied 样本提前改写为最终终态。

### `.159` Phase A：S2 retryable 后的候选发现闭环（已冻结，待实现）

本轮只处理 closure matrix 中 22 条以 `website_resolution:FETCH_FAILED` 为当前
终态的记录，不把人工观察直接提升为产品终态。六轮历史 trace 证明这些记录主要
重复遇到 LinkedIn `HTTP 999/451`；`.157` staged 中 22 条非 LinkedIn 记录又都在
约 `92s` 结束。它们不是单纯的官网抓取失败：S2 失败后 S5 仍以
`ats_only=True, exhaustive=True` 执行 4--10 次搜索 dispatch，直到 S4--S5 的软
deadline；没有候选时 S5 最终为 `not_run`，结果投影才重新暴露第一个 S2 failure。

历史 `/private/tmp/route100-v1-results.json` 曾为 22/22 解析 Website、17/22 到达
Career；这些历史结果只能作为待重新验证的候选，不能恢复 Exact、no-public 或
最终 disposition。已确认的控制样本为：Stark Pharma 与 TBK Bank 曾有同一
opening 的 live/replay S7 证据；United Pharma 曾有完整官方库存 no-match；
Redlands 三条应到达 HealthcareSource；Sony 曾误入 Haven Greenhouse，必须继续
拒绝该错误公司/tenant。

本轮冻结以下实现 contract：

1. S2 的 999/451/timeout 是 soft dependency。历史 Website/Career 只作为当前
   run 待重验候选；搜索摘要、旧 opening 与旧 no-match 都不具授权力。
2. provider 搜索采用渐进 wave 和独立 dispatch cap；产品模式不再无条件
   exhaustive。所有 source failure 必须保留 typed trace，阶段必须在公司 deadline
   前完成并写出 finalized boundary。
3. 当 Website 缺失时，允许从 LinkedIn company slug 产生有界 provider tenant
   probe，但只有 adapter 重新读取完整官方库存、tenant/company 关系连续后才进入
   candidate portfolio。
4. 新增不可信 `career_surface` lead。品牌自有 Career 域必须由当前页面的公司身份
   与招聘语义验真，再进入现有 Career/Job Board 流程；search snippet 只发现候选，
   不能宣布成功。
5. 对 Square/PUMA 这类已成功读取且有多重主页身份信号的 extension/区域域，可在
   明确负向碰撞测试保护下作为 Website；父公司/集团域仍只作 provisional handoff，
   必须由 S3/S5 建立招聘关系。
6. 不改变 ADR-0028 evidence schema、S7 identity contract、opening publication
   contract，也不增加上述 22 家的公司特例。

Phase B 分为互斥写集：主线负责 candidate lead contract、Career-surface verifier、
composition 与 stage 集成；并行线 A 负责 provider 搜索 progressive wave、无官网
slug probe 及局部测试；并行线 B 负责 resolver 的多信号 extension-domain 规则及
负向碰撞测试。共享 run configuration、adapter version 和治理文档由主线统一修改。

Phase B focused 验收固定为 22 条失败簇加 6 条 stored-provider 控制样本：无候选
search 最多 4 次 dispatch，单公司不再统一耗到 `92s`；Stark/TBK 恢复原 S7
opening，United 恢复完整官方 no-match，Redlands 到达正确 HCS board，Sony 拒绝
Haven。所有发布 URL 必须经过 S7，wrong-company/cross-tenant/snippet-only 成功为
零；同版本 replay 必须 0 fixture gap、0 outcome mismatch。若新路径产生任何错误
公司、错误 tenant、过期 opening 或无完整 inventory 的 no-public，立即关闭对应
feature flag，回退为保守 `SYSTEM_GAP`，不迁移或清理 durable evidence。

### `.159` Phase C 结果与 `.160` 调整

`.159` 固定 22 条 S2 failure 加 6 条历史 Exact 控制样本的同版本 live 为
`4 Exact / 2 Partial / 22 Failed`，Job List `5/28`，耗时 `1744.7s`；full replay
`28/28` 成功，错误 URL 与跨 tenant 成功仍为零。Hadrian 两条、Gucci、adidas
保持 Exact；Lacoste 只保留 Job List，Solomon Page 只保留 Career。22 条目标记录
没有恢复，因此本轮不能计入 closure matrix，也不能宣布该失败簇完成。

Phase C trace 证明 `.159` 的两个新边界不完整：

- generic Career search 每条代表记录均解析到 10 个搜索结果，但 resolver 在 lead
  阶段要求完整公司 token 出现在 registrable domain。`Redlands Community Hospital`
  的正确 `redlandshospital.*` 因缺少 `community` 被丢弃，当前页面身份/招聘语义
  verifier 从未运行。搜索摘要过滤过严，反而阻止了后续强验真。
- tenant probe 虽然单个 attempt fail closed，却会组合 2--4 个 slug 与 6 个
  provider 端点；本轮实际每公司 6--24 次，重新把单公司推到 `92s`，并挤占 S6。
- `.158` 输入 evidence store 不含 Stark、TBK、United、Redlands、PUMA、Square
  等历史 route 记录；本轮不是 TTL/key mismatch，而是没有 durable candidate。

`.160` contract 因而调整为：搜索层允许 URL 有明确 Career intent、搜索结果文本
包含完整公司身份的 unbound Career lead；该 lead 仍无授权力，必须由当前页面
title/meta/structured identity 与 Career 语义重新验真。多 token 公司允许域名只
包含至少一个 distinctive token，单 token 品牌仍要求完整 host 绑定。ATS 查询先找
公司级官方 board，再由 S6 匹配 title。tenant probe 全局最多 6 次，并优先 LinkedIn
slug、再官网 host、最后公司名；达到上限必须写入 trace 并立即 finalized。历史
route 结果仍不直接恢复 opening/no-match，后续只可经现有 seeding contract 转为
待重验 Website/Career candidate。

- 已完成 `.127-.130` 的跨 run 公司发现证据恢复闭环：Website、Career 和已验证
  provider board 按 ADR-0028 分层保存，历史迁移可读取 S7 结果中独立 verified 的
  provider relationship；普通 transport failure 和带当前页面正向身份确认的
  hosted-domain 提示不会误删整条证据链。
- 已完成 stored provider 的产品调度：S2 当前不可用且已有 verified provider
  candidate 时，S4 不再耗尽剩余预算，S5 立即交给当前 adapter，S6 必须重新读取
  完整官方库存后才能恢复关系和公开 Job List；普通搜索候选、跨 tenant 猜测和缓存
  opening 仍不能通过。
- focused live：Gucci + 两条 Haystack 从 `.127` 的 `0/3` Job List 提升到 `.129`
  的 `2/3`。两条 Haystack 当前 Ashby 完整库存均为 verified no-match；Gucci 命中
  正确 Kering Workday board，但当前 API timeout，因此继续隐藏 URL并保留 retryable
  终态，不计修复完成。
- replay bundle 现在只冻结本次选中公司的公开 evidence-store 记录并注入 scoped /
  legacy replay；不会复制 HTML、inventory、opening、cookie 或 token。终态投影在
  stored candidate 未能重验时保留 S6 的具体网络/库存失败，而不是用泛化 identity
  mismatch 覆盖。
- `.131-.133` 已关闭两个新的通用缺陷。JS raw URL 提取不再把单引号后的 callback
  源码吞入 URL；声明式同源 `/job/{id}` 库存不再被普通导航文字负向词误杀。
  S6 对 generic Job List 会重新建立 bounded first-party declared inventory，避免
  S5 验证出的增强库存页在 stage/checkpoint 边界丢失。Solomon Page focused live
  读取 326 条完整官方库存并发布 `Data Analyst` opening `458677`，S7 verdict 为
  verified；同版本 full replay 1/1 reproduced、0 gap、0 mismatch。
- automatic replay 在 worker 启动前冻结只读 evidence-store snapshot，本轮 live
  学到的 provider board 不会反向污染本轮回放。completion resume 仅在已发布
  Job List/Exact、identity assertion verified 且最终 result-validation success 时，
  才允许忽略上游 retryable；未验证 URL、失败 final gate 和空输出继续重跑。
- `.134-.136` 已实现标准 `hreflang=en-US` 与已验证 deployment gateway 的区域
  handoff。只有明确声明的美国入口、同 registrable site redirect 和当前页面正向
  公司身份同时成立时，才允许保留被出口 IP geo-redirect 的声明入口；跨站、无身份
  和未声明 locale 继续拒绝。SKIMS 因此从 S2 `FETCH_FAILED` 推进到 S4，并进一步
  暴露出 provider adapter 已注册但候选发现层不可达的结构缺陷。
- Pinpoint 现已进入 ATS 定向查询与 verified-company-slug tenant probe，不含 SKIMS
  公司分支。`.136` SKIMS focused live 在 38.2 秒内读取当前公开 Pinpoint inventory，
  发布 `Account Executive, Franchise Partnerships` UUID opening，Job List、Exact 和
  S7 identity verdict 全部通过。二次 replay 的 `replay_input` 现在恢复原始 S2 输入，
  不再把派生官网倒灌为 preferred website；旧 `.135` 两条失败已 2/2 reproduced，
  record integrity 与 outcome gate 均通过。
- `.137-.140` 关闭 Lacoste 的四个可泛化断点：stored website evidence 现在进入与
  preferred/search 相同的区域恢复；已验证 gateway 明确声明但当前 403 的同站 locale
  root 只作为本 run 的 access-controlled handoff，不覆盖 durable verified store；
  官方 host denial 后的搜索只保留 ATS 或同 registrable official site；scheduler v8
  为 `careers.`/`jobs.` 子域保留一个 bounded concrete-host 槽，避免多个同 host path
  猜测耗尽窗口。`.140` Lacoste focused live 用 13.1 秒进入 DigitalRecruiters，发布
  Job List `/en/annonces` 和 S7-verified opening `4371325-account-executive-10016-new-york`；
  automatic full replay 1/1 reproduced、0 gap、0 mismatch。
- `.140-.142` 的 Michael Kors、Saint Laurent、adidas 三品牌 focused cohort 从 `.140`
  `0/3` Exact，经 `.141` `1/3` Job List，推进到 `.142` `3/3` Website；adidas 在 `.142`
  同时恢复 S7-verified Exact。这里的 Website、Job List 和 Exact 是不同版本的聚焦漏斗，
  不能拼接成一个新的 frozen-100 总体成绩。
- `.141-.144` 增加区域 ccTLD sibling 与 access-controlled sibling handoff；只有当前验证的
  同品牌页面、区域/同站关系和既有 identity gate 连续时才使用。URL 中明确的地点 qualifier
  可补充岗位地点证据。Michael Kors 官方招聘链已验证其由 Capri Holdings 承载；系统拒绝
  非生产 Eightfold tenant，并优先使用已有岗位库存而不是重复执行 CTA。`.144` Michael Kors
  focused live 因而恢复 Exact。
- `.145-.152` 关闭 Saint Laurent 链路：Saint Laurent 到 Kering Careers 的官方关系已验证；
  否定式 talent-community CTA 不再被当作职位入口；第一方 `/job-offers/<scope>/<slug>`
  可作为受约束的 opening detail。`.148` 仍受 sandbox 污染，`.149` 已恢复 Career/Job List
  但没有 Exact，`.151` 虽得到 Exact，replay identity 仍因临时 generic-board search query
  漂移失败。`.152` 将该 query 与 tenant identity 解耦后，Saint Laurent focused live 为
  Exact `1/1`，同版本 replay `1/1` 成功，耗时 16.8 秒。
- `.153` 将 adidas 已验证的 adidas Group 官网与 Career handoff 纳入现有 company identity
  registry，不保存具体 opening。它避免低证据 `adidas.com` 猜测在 S2 重试中耗尽预算；统一
  三品牌 focused batch 在同一版本达到 Website/Career/Job List/Exact `3/3`，Michael Kors、
  Saint Laurent、adidas 均通过 S7，同版本 replay `3/3` 成功，总耗时 31.9 秒。
- `.154-.155` 关闭恢复 cohort 的调度与身份断点：LinkedIn company slug 可作为受约束的
  provider tenant 候选，但仍必须读取 provider 库存验真；区域 Career 候选保留明确目标
  locale；预算拒绝在当前 fetch call 内终止重试。Hadrian、Gucci 恢复 Exact，两条 Haystack
  从错误候选降为安全 Partial。统一 17 条 recovery cohort 为 `10 Exact + 7 Partial + 0 Failed`，
  Job List `17/17`，同版本 replay `17/17`。其中四条 LinkedIn 职位已由完整 SmartRecruiters
  库存证明为当前 title/location no-match，不能伪造成 Exact。
- `.156` 修复 declared first-party inventory 的地点证据丢失。公开库存中的 `metro`、
  `locationName`、city/state 等字段按精度保留到 `ListingCandidate`、`RawLink` 和 S7；
  Solomon Page 官方 326 条库存中的 `Data Analyst` `458677` 因而以 `Austin, TX` 通过 S7。
  clean S2 首次尝试仍受 LinkedIn 999 / 未加载持久证据影响；使用上一轮已验证官网冻结上游后，
  S5-S7 live 与 replay 均为 `1/1`。
- `.157` 关闭 Haystack 暴露的两个老问题：`max_job_pages` 现在真正传入 generic inventory，
  不再在 matcher 内硬编码三页；SSR anchor card 支持受约束的同源 job-family UUID detail，
  并从显式 location/map-pin 节点保留 card-local 地点，歧义地点继续 fail closed。真实筛选库存
  完整读取 4 页 `64/64` 条，地点缺失 `0`，没有 Tampa 结果，因此返回有完整证据的
  `OPENING_NOT_FOUND`，而不是 `OPENING_DISCOVERY_INCOMPLETE` 或错误 Exact；live replay `1/1`。
- `.157` 随后完成同版本 17 条统一门禁：`11 Exact + 6 Partial + 0 Failed`、Job List
  `17/17`、top-level error `0`、full replay `17/17`，总耗时 595.3 秒。相对 `.155`，
  Solomon Page 从 retryable timeout 恢复 Exact；两条 Haystack 从 incomplete 变为完整库存
  title/location no-match；其余 14 条保持原终态。trace 中 6 个 `FETCH_FAILED` 是 LinkedIn
  公司页的次级上游 HTTP 999 记录，不改变当前 provider 库存终态。
- `.157` 冻结 100 条 exhaustive 诊断轮为 `45 Exact / 62 Job List`，耗时 4191.8 秒；
  同一输入改用产品默认 staged 路由后为 `51 Exact / 68 Job List`，耗时 2411.1 秒。
  两轮都保留零猜测 URL 与 S7 fail-closed。该对照证明 exhaustive 三路只适合诊断，不能作为
  产品默认调度：它会在已有 provider/tenant 证据时继续消耗搜索预算，挤压 S6 opening window。
- `.158` 第一轮 closure cluster 关闭 Career identity、detail continuity、动态搜索和多 Job Board
  completeness 四个通用缺口。当前重新验证过的 stored Career 可以恢复 same-entity 招聘关系，
  但缓存命中本身不授权；Career redirect/identity mismatch 继续拒绝。公司 evidence store 改为按
  `observed_at` 单调合并，历史 seed 只能补缺，不能用旧 Website/Career 清除较新层。
- generic opening 现在验证 page-bound hydration、嵌套 location 与 listing-to-detail continuity；
  `hiringOrganization.url` 缺失时仍须满足同站 URL、title/location 和 detail identity，特定地点不能
  被 null/broad location 放行。声明式 POST 与浏览器搜索必须改变 route、canonical payload 或
  listing fingerprint；未变化页面记为 `transport_unchanged`。短标签 `Corporate/Retail Opportunities`
  可作为明确 Job List action，多入口只有全部显式入口已观察并访问时才声明 portfolio complete。
- `.158` 固定 7 条 focused gate 为 `5 Exact + 1 VERIFIED_NOT_FOUND + 1 SYSTEM_GAP`、Job List
  `7/7`、错误 URL `0`，同版本 replay `7/7`。两条 hackajob、Lacoste、Bacardi、Randstad 恢复
  S7 Exact；Steve Madden 的两个官方 ADP inventory 均完整为空，闭环为 `NO_PUBLIC_OPENINGS`；
  EVONA 仍有公开 search transport server error，继续留在 SYSTEM_GAP，未伪装成外部阻塞。
- `.158` 阶段 C 门禁通过：2334 tests（3 skipped）、25/25 provider benchmark、6/6 resolver
  benchmark、43 native adapters / 0 architecture issues；沙箱内唯一失败是 loopback bind 权限，
  同命令在获批环境完整通过。focused replay 7/7，无 fixture gap 或 outcome mismatch。
- `.162` 对固定 28 条剩余失败簇执行同版本 focused live，得到 `13 Exact + 9 Partial + 6 Failed`、
  Job List `21/28`、错误 URL `0`，耗时约 1216 秒。旧 28 条 replay 初次为 `27/28`；唯一漂移是
  Tenet 第一方 generic board 的 `orgIds + k` 查询状态改变 canonical identity。后续将同一已验证
  第一方 Career 站点上的搜索参数视为 transient producer state，同时保留跨站/跨 tenant 的硬边界；
  `.172` 已在同一冻结 28 条完整 live 与 scoped replay 中统一验证该修复，正式关闭为 `28/28`。
- `.163` 对六条 S2/官网连续性样本的 focused live 为 `0 Exact + 1 Partial + 5 Failed`；
  Southeastern Renal 恢复官方 Career，但页面只声明岗位转由 Indeed 承载且未给出可验证链接。
  West Oaks、Tidelands、Dior 的已验证官方页面稳定返回 403，Garan 的公开站点及常见 Career 路径
  均未暴露招聘入口。这些记录仍需按证据分别闭环为外部阻塞、无公开库存或系统缺口，不能仅凭
  HTTP 状态提前改写终态；同批 replay 为 `6/6`。
- `.164-.167` 关闭 L'OCCITANE 的动态 Career hub 与集团/兄弟品牌 identity 缺口。第一方 React
  bundle 中的具名招聘 destination 现在作为候选证据进入 S5，但只有 label 与当前招聘主体匹配的
  destination 可优先；存在精确当前实体时，Sol de Janeiro、ELEMIS、Melvita 等兄弟品牌全部以
  `sibling_brand_not_current_hiring_entity` 拒绝。`.166` 曾错误选中 Sol de Janeiro Greenhouse，
  因此不计成功；`.167` 正确验证 `group.loccitane.com -> careers.loccitane.com ->
  careers-group.loccitane.com/search/` 的 Group SuccessFactors 链，读取官方 title-filtered inventory
  后无目标合同岗，闭环为 `VERIFIED_NOT_FOUND`。同版本 replay `1/1`，相关 465 项局部测试通过，
  错误/跨品牌 URL 为 `0`。
- `.168-.169` 关闭 Applicant Manager 强结构列表的 opening continuity 缺口。parser 原本已经从
  官方 table row 读取 title、location 与 `tr<position> -> jobs?pos=<position>`，但构造 candidate 时
  丢弃 location，随后统一 URL gate 又因 `pos` 不属于通用 detail query 而二次拒绝。修复同时保留
  两层约束：location 随候选传递；只有内部验证的 `applicant_manager_table` origin、同源官方 host、
  唯一合法 `pos` 参数才能通过，不全局放宽 query URL。`.169` NexCare focused live 返回 S7-verified
  Exact `https://theapplicantmanager.com/jobs?pos=n513775`，官方 141-row inventory 中 title 完全匹配、
  row-local Saginaw location overlap；replay `1/1`，相关 205 项测试通过。
- `.170-.171` 关闭 Square 所代表的 SvelteKit SSR filtered-inventory 缺口。generic fallback 现在
  保留 Job List 已有的 business-unit query，并按 `q/search/query` 尝试；只有 fallback 查询值与目标
  title 完全绑定时才进入 filtered inventory。受限 JS literal parser 不执行页面脚本，只接受 5 MB
  内、唯一 `jobs.currentPage`/`initialJobsListRequest` marker、有限深度与 token、已知岗位字段、同源
  numeric detail route、查询回显和 page/total 完整性；duplicate ID、未知 URL 字段、表达式、错误 route、
  query mismatch 和截断分页全部 fail closed。`.171` live 从官方 Square scope 读取 4 条完整筛选结果，
  其中 Bay Area、Toronto、London 均与冻结的 New York location 冲突，因此不发布错误 Exact，并闭环为
  `VERIFIED_NOT_FOUND` / `verified_inventory_no_match`；automatic replay `1/1`，相关 184 项测试通过。
- `docs/FROZEN_100_CLOSURE_MATRIX.md` 现在逐条维护 100 条终态。治理审计补回 `.162` live 与
  replay 已经一致、但此前漏记的 13 条闭环：Sony、Tenet、PUMA、Yamaha、Stark、Century 和
  TBK 为 S7 Exact；Redlands 三条、Horizon、Future Beauty Brands 和 United Pharma 为官方
  inventory/Career 证据支持的 Verified Not Found。叠加 `.167/.169/.171` 后，
  治理审计同时补回已有共享门禁中的四条闭环：`.125` Blossom 与 SpaceX Exact、`.126`
  Taskrabbit complete-inventory no-match、`.157` Haystack complete four-page no-match。该审计在
  `.171` 时的 ledger 为 `66 EXACT / 19 VERIFIED_NOT_FOUND / 15 SYSTEM_GAP`；已由上面的
  `.172` Phase C 终态收敛结果取代。focused ledger 仍不是新的统一 100 条自动成功率。
- 当前区域品牌聚焦证据为 SKIMS、Lacoste、adidas、Michael Kors、Saint Laurent 均已通过
  Exact；其中后三条已有 `.153` 同版本统一 batch。下一共享 gate 仍是 17 条 recovery cohort、
  下一共享 gate 是后续 SYSTEM_GAP focused cluster；多个主要簇闭环后才运行冻结 100 条统一回归。
  正式统一 frozen-100 基线仍为 `.125` 的 `46/100` Exact，不用 focused ledger 改写总分。
  低证据 guessed-domain 重试吞掉 S2 预算仍保留为通用调度项，
  不能用 adidas registry 结果掩盖。

- 已完成：第一方 Career 连续 handoff、官网拒绝后的有界剪枝、Yamaha/Solomon 动态 inventory、NexCare 表格 inventory、Meta bounded title sampling、SuccessFactors tenant 恢复、Sony 多 tenant portfolio、detail-vs-list 选择、导航 chunk 与库存 bundle 的独立预算排序、semantic Career action / ADP locator 的 verified handoff、editorial Career 候选降权，以及 Ashby 次级地点归一化。
- 已完成回归：`.109` 冻结 72 条为 `67/53/41/12`，`.110` 为 `67/54/45/11`；Job List 增加四条，Exact 的单条回落来自 adidas 网络 deadline。Gucci、LTIMindtree、Steve Madden 已在整批回归中恢复，Middesk 在 `.111` focused live 恢复 Exact。
- 正在验收：`.113` failure-cluster focused live。S6 独立 opening phase已恢复 adidas Exact；SuccessFactors 已恢复 Paramount Exact；Meta Careers 使用官方 Relay/GraphQL title inventory，冻结 Meta/Instagram 15/15 均为 S7 verified Exact；Redlands 3 条由 HealthcareSource 完整库存准确终止为 verified no-match。第一方高置信 Career action 现在与内嵌 provider 共同保留在 board portfolio；同一可注册域的 title-targeted search 只有在官方 JobPosting、canonical URL、招聘主体、title/location 全部验真后才能进入 S7。Gucci focused live 已因此恢复官方 Kering opening Exact。
- 发布约束：只有 S7 identity 连续且 URL 验证通过才计 Exact；bounded sampling 和 partial inventory 不能证明岗位不存在。

### Frozen-100 Product Closure Goal

`.109 → .110` 的逐条 diff 证明前一轮验收口径不够：Job List 从 41 增至 45，
但 Exact 从 12 降至 11；Gucci、LTIMindtree、Steve Madden 只是到达列表，
并没有完成用户要求的“进入列表后执行搜索并返回具体岗位”。从本轮开始，
Job List 只算中间证据，不算样本修复完成。

主验收 cohort 冻结为
`samples/evaluation/live100_three_route_cohort_20260717.json`，并以用户已批注的
`docs/LIVE_100_THREE_ROUTE_MANUAL_REVIEW.md` 作为 eligibility 证据；其 72 条
non-exact matched regression 是当前修复集。7 月 18 日的 fresh 100 与该 cohort
job ID 零重叠，只作为主 cohort 闭环后的泛化验证，两个 cohort 不混算、不互相
回写基线。

冻结 100 条样本采用以下唯一终态口径：

- 仍开放且可匿名访问：必须返回经过 S7 公司、招聘主体、provider、tenant、
  title、location 与 opening 状态验证的 Exact URL。
- 官方库存确认岗位关闭或不存在：返回 `VERIFIED_CLOSED` 或 verified no-match，
  并保留库存完整性证据。
- 无公开职位、招聘代理未披露客户、登录墙、验证码或仅第三方申请：返回对应
  可核验外部终态，不得算作系统成功，也不得伪造 URL。
- 网络和 caller deadline：必须保留 retryable 分类；S2-S5 不得耗尽 S6 的
  opening-search 保留预算。
- 错误 URL、跨公司、跨 tenant 和仅凭搜索摘要建立身份的成功数必须保持为零。

执行节奏固定为：冻结基线与人工批注 → 批量 failure cluster → 并行修复互斥
模块 → 局部测试与 focused live → 主线统一离线 gate → 串行冻结 100 条回归。
每次回归后更新本节的 Exact、准确外部终态、retryable、system defect 和错误 URL
数量；只要仍有可泛化 system defect，就继续下一轮，而不是用 Job List 增量结束。

第一批关键簇已经冻结：S6 独立预算和真实表单/API 搜索、ADP `srccar` inventory、
Eightfold custom/sandbox tenant 验证、generic board 的声明式搜索 transport，以及
adidas portal 路由/预算回归。Gucci、LTIMindtree、Steve Madden 和 adidas 是本批
focused acceptance；Middesk 的 Ashby secondary location 已 focused Exact，但仍需
进入下一次冻结 100 条统一回归后才计入总体改善。

已批注 7 月 17 日 cohort 的原始基线是 `28/100` Exact。对 matched/focused artifact
以 LinkedIn job URL 去重后的当前证据账本是 56 条 Exact，44 条 remaining 分为 3
verified no-match、22 external blocked/no-public/client-undisclosed、7 retryable 和 12
system defect。Gucci、Yamaha、两条 hackajob 与 Caudalie 本轮 focused Exact 是这份
账本上的 5 条新增证据，因此统一回归前只能写“预计 61 Exact、39 remaining”，不能
称为正式 `61/100`。无公开岗位、
已验证 inventory no-match、外部访问阻断、招聘客户未披露和 LinkedIn-only Apply 不
要求 Exact，但必须得到准确的结构化终态。

`.113` 第一轮 focused acceptance：

- Meta/Instagram：`15/15` Exact，官方 GraphQL 返回 title-filtered inventory，数字
  opening ID、canonical detail、company/tenant/title/location 均通过 S7；旧的随机
  sitemap probe 不再承担主召回。
- adidas、Paramount：各自 focused live Exact；Middesk 先前 focused live Exact，
  仍等待统一冻结 100 回归计入总体。
- Redlands：`0/3` Exact，但 `3/3` 为当前官方 title-filtered complete empty，归入
  verified no-match；历史 fixture 只验证分页代码，不能证明岗位今天仍开放。
- Gucci：focused live 已恢复 Exact。系统保留 Gucci 官方 Career 页明确链接且实际访问
  的 `careers.kering.com` portal，不再因其页面内嵌 Eightfold sandbox 而丢弃；搜索只
  产生 lead，最终 Kering `JobPosting` 仍经过 canonical、招聘主体、title/location 和
  S7 selection evidence 验证。sandbox 继续 fail closed。
- Yamaha：通用 jTable contract 已发现并调用真实匿名 API；解析器只允许最多一层额外
  JSON string encoding，并拒绝三重编码。串行 focused live 完整读取 35 条库存，验证
  同源 detail template 与 UUID opening，最终以官方 `careers.yamaha-motor.com` URL 通过
  S7 Exact；并行工作线中间态导致的旧 TypeError run 已明确作废，不计验收证据。
- Randstad：声明式同源 GET search route 已从页面明确加载的 search chunk 中恢复，并能
  导航到 title-filtered SSR 列表；真实 focused live 同时揭示一项 S7 假阳性风险：当前
  官网存在另一份同标题、同城市 slug 的新岗位，但 selection 没有地点证据、库存范围未知
  且有 8 个候选。`.115` 起该组合必须返回 `OPENING_LOCATION_UNVERIFIED`，不得为了命中率
  发布 Exact；修复后的串行 focused rerun 尚待执行。
- 地区 gateway：resolver 现在只从已验证且身份连续的 gateway 页面跟随明确可见的
  `<a>` locale 链接；US 目标最多尝试 3 个同 corporate registrable site 的 HTTPS 候选，
  最终 URL 仍须明确属于 US。跨站、脚本/嵌入 URL、冲突地区和猜测路径均不请求。
  Caudalie 风格正例及跨站/冲突负例共进入 92 个 resolver 局部测试；focused live 待跑。
- `.115` 五条 focused live 第一轮：SKIMS 通过 Pinpoint 恢复 Exact；Randstad 保留官方
  Career/Job List，但替代 opening 被 S7 以 `OPENING_LOCATION_UNVERIFIED` 正确拒绝，
  `open_position_url=None`。两条 hackajob 的 core/Product query 已真实发送并各自缩到
  单页，仍因 SSR `job-card` 未被 inventory parser 接受而保持
  `OPENING_DISCOVERY_INCOMPLETE`。Caudalie 首页明确发现 `USA → us.caudalie.com`，但该
  visible locale handoff 被 path-probe 调度预算挤出，仍为 `CAREER_PAGE_NOT_FOUND`。
  因此下一轮只修这两个已证实的通用缺口，不把 Job List 中间结果计为完成。
- S7 terminal projection：validation 拒绝不再只藏在 stage trace；顶层 `error_code`
  统一发布 `RESULT_IDENTITY_MISMATCH`，同时继续抑制错误 `open_position_url`，并在
  rejected identity assertion 中保留候选 URL 作为可审计证据。
- `.116` focused closure：hackajob 的真实 localized SSR card 允许重复 CTA 指向同一详情，
  但拒绝一个 card 内两个不同详情 URL；company/location/description heading 不再被误计
  为多个岗位标题。两条冻结岗位分别恢复官方 Apollo Platform 与 Registered Nurse
  opening，并完整通过 S7。区域 gateway 无论原本是否已在 fetch window 内都会获得
  traversal role；只允许再跟随一个页面可见、HTTPS、语义明确的跨站 Career handoff。
- 同源 `data-ajax` POST inventory 只在页面同时声明空默认筛选、同源 endpoint 和
  `{{slug}}` 详情模板时执行；敏感字段、字段溢出、非空默认、跨源、重定向、恶意 slug
  与 malformed payload 全部 fail closed。空筛选返回的 42 条库存标记为 `full`；S7 只
  允许官方标题中的明确地点限定词细化粗粒度地区，`NYC` 可匹配 New York，而 `D.C.`
  不能。Caudalie focused live 最终以官方
  `https://caudalie.career/apply/offer/CJ5G5Z` 通过 S7。标准 6 秒 fetch 的两次中间 run
  在 S4 对该慢站超时，均保留为 retryable 诊断；12 秒 run 只证明完整链路，正式冻结
  100 仍使用统一 gate 配置。
- `.117` gate hardening：冻结 cohort 文件现在可直接作为版本化 `{postings: [...]}` 输入，
  未识别对象仍拒绝；首次全量启动在 0 条有效网络结果后暴露三路 S5 merge 的重复公开 board
  identity。merge 层现在按 provider 与 canonical board URL 保序去重，再建立严格 portfolio；
  Notion 隔离 live 从 `batch_worker_failed` 恢复为 Ashby S7 Exact。失败的 `.116` completion
  不复用，正式 100 条使用全新 `.117` checkpoint 根目录。
- `.117` 统一冻结回归已完成 100/100 网络执行：website/career/job-list/exact 为
  `57/44/40/27`，因此没有达到修复目标，也低于原始 `28/100`；不能将 focused ledger
  合并进总体。42 条 S2 LinkedIn 请求被 HTTP 999/451 拒绝，另有 8 条 transport/budget，
  合计 50 条 retryable；其中 Meta、Redlands、Hadrian、Tata 的重复 posting 占 20 条，
  可通过公司级 S2-S5 evidence reuse 减少最多 16 次重复上游运行。provider search 本轮
  仅产出 9 个候选且 0 个 relationship exact，不能作为有效旁路。
- `.118` 当前修复簇：SmartRecruiters verified storefront 进入 native inventory；S5/S6
  replay 按阶段 handoff 恢复，禁止 downstream URL 倒灌；S6 reserve 提高并在实际窗口
  被侵蚀时保存 board、返回 retryable budget terminal；下一步补公司级 evidence reuse、
  verified relationship hard ordering、location evidence extraction 和 retry resume 性能。
  八个 S7 identity rejection 均保持 fail closed；只有补齐官方地点/tenant/relationship
  证据后才允许 Exact。
- `.118` 四条 focused：Meta 由 verified identity hint 绕过 LinkedIn 999 并恢复 Exact；
  LinkedIn 进入 native SmartRecruiters 且当前完整库存 no-match；Snap 缺地点继续由 S7
  拒绝；Steve Madden 再次证明 ADP S6 仅得到 6ms。`.119` 因此将 S5 portfolio 的
  replay-safe primary 连续前缀安全持久化，遇到 runtime-only suffix 时强制
  `eligible_set_complete=false`，而 runtime-only primary 仍禁止持久化或提升后项；
  目标是 retry 真正从 S6 开始，且 checkpoint 不含 runtime secret。
- `.120` ADP resume contract：上一轮只改了 portfolio 前缀，却没有将 ADP 的公开 tenant
  locator 注册为 replay-safe，导致 Steve Madden 的 S5 checkpoint 根本没有落盘，resume
  仍从 S5 重跑。现在 WFN 与 SRCCAR locator 只有在 canonical HTTPS host、path、query 顺序、
  client/site/locale identifier 完全一致且不含 token、重复参数或跨 tenant 内容时才允许
  checkpoint。Steve Madden 首次隔离 live 为 82.3 秒、S6 `NETWORK_TIMEOUT`；同一 S5
  checkpoint 的 `opening_match` resume 为 16.7 秒，并完整检查 Corporate 与 Retail 两个
  官方 ADP inventory。两个 inventory 当前均为 complete empty，因此该冻结岗位不能诚实
  恢复 Exact；下一步将 portfolio completeness 与 verified closed/no-public terminal 分开
  校准，而不是伪造旧 opening。
- `.120` 冻结 100 正式回归：首轮为 `70/58/55/45`，只复用原子 completion/checkpoint
  重跑 36 个 retryable 后稳定为 `70/58/57/46`；相对 `.117` 的 `57/44/40/27`，官网、
  Career、Job List、Exact 分别提升 `+13/+14/+17/+19`。SpaceX 在 S5/S6 retry 后新增
  Exact，Snap 推进到 verified Job List 后由 S7 继续拒绝地点/身份不足的候选。稳定剩余
  54 条中，29 条为 S2 `FETCH_FAILED`、8 条 `CAREER_PAGE_NOT_FOUND`、2 条 Career budget、
  1 条官网未确认；40/54 尚未进入岗位库存。已进入 inventory 的 14 条分为 5 个 portfolio
  incomplete、3 个 verified no-match、3 个 S7 identity rejection、1 个官方 403、1 个
  Job Board 未找到及 1 个其他外部终态。下一轮主缺口因此从 A/C 类列表搜索转为安全的
  公司级 S2-S5 evidence coalescing 与上游旁路。
- `.120` 自动 failure bundle 在 live 结果、trace、summary、route metrics 均原子落盘后，
  对 ADP scoped replay 留下 4 个未消费 inventory request 并严格失败；该问题不影响正式
  live 数字，但在 replay 修复和 100/100 deterministic gate 通过前不能结束本轮。
- `.121/.122` 将剩余 54 条按事实重新分层，而不是继续把所有非 Exact 当成同一种搜索
  失败。Hadrian、Panacea、Great Value Hiring、LinkedIn、Steve Madden 与 LTIMindtree
  已有人工或完整库存证据支持无公开入口、Easy Apply、岗位撤下或 verified no-match；
  这些记录的目标是准确终态，不是伪造 opening。真实 system defect 集中在区域/错站
  identity、动态 inventory 错误坍缩、generic detail 缺地点和 scoped replay 边界。
- `.122` S2 通用 identity hardening 覆盖 `.cn`/foreign-locale 冲突、区域 fast-path、
  marketplace/deployment 子域、完整 LinkedIn slug 验证槽与产品站/企业站歧义，不含公司
  名特例。S5 已发现动态 endpoint 但 fetch/unverified 时不再输出确定性
  `JOB_BOARD_NOT_FOUND`。S6 对同站 exact-title detail 最多验证 3 条，只接受 canonical
  self URL、精确标题、同站招聘主体和明确地点；刚捕获的真实 Snap/Randstad 页面离线
  replay 从 0/2 提升到 2/2 Exact，其中台湾 Snap 同名岗位被拒绝、洛杉矶岗位被选中。
- `.122` scoped replay 恢复 typed multi-board、replay-safe singleton、URL-only custom/generic
  singleton 以及 cache-backed S2 producer state；所有路径仍要求完整消费 outcome tape。
  ADP 和 LTIMindtree focused replay 已通过。旧 `.120` tape 在 Twitch 处因 `.122` S2
  请求序列变化而出现未消费请求；这是跨 adapter 版本行为分歧，不能通过忽略 tape 条目
  来伪装成确定性回放。`.123` 正式 live 后必须用同版本新 capture 做完整 replay。
- `.122` 十条 S2/detail focused live 中，Blossom 与 Snap 真实网络 `2/2` Exact；Snap
  选择洛杉矶 `R0046024` 并拒绝台湾同名岗位。Lacoste、SKIMS、Michael Kors、Saint
  Laurent、adidas 与两条 Haystack 均在 LinkedIn 公司页 `451/999` 后准确保留为
  retryable `FETCH_FAILED`；三路 S5 仍执行，但当前区域的 Bing RSS/DuckDuckGo 未返回
  可验证 ATS 候选，因此不能把这七条写成系统成功。Taskrabbit 暴露多个已验证同品牌
  TLD 的选择缺陷：`.ai` 的结构化组织分压过官方 `.com`。
- `.123` 对“多个同品牌域均通过、LinkedIn 官方字段不可用”的情况，只有经过页面身份
  验证且与 LinkedIn slug 对齐的 exact-brand `.com` 才能打破 TLD 平局；停放页、错品牌、
  deployment/marketplace 域和未验证 `.com` 仍不得提升。该规则不含公司特例，Taskrabbit
  focused live 是本轮验收样本。
- `.123` Taskrabbit focused live 已选回 `https://www.taskrabbit.com/`，进入官方 Career
  与 Greenhouse，完整读取当前 13 条库存后准确返回 verified no-match。该 run 同时暴露
  route merge 的内部不一致：公开 board 是 Greenhouse，但 `provider_identity` 仍沿用
  first-party generic careers。`.124` 将 first-party inventory card 的 `source_url` 保留为
  typed relationship handoff；merge 只有在派生 ATS identity 仍通过 relationship gate 时
  才能用它替换 generic identity，保证 job-list/provider/tenant 三者一致。
- `.124` Taskrabbit 重跑时 Greenhouse tenant probe 受网络波动超时，虽然 first-party Career
  已经解析出 13 条 provider detail，S5 仍回退为 generic Career 并在 S6 再次超时。`.125`
  将 verified first-party listing inventory 中的原生 ATS detail 直接规范化为 provider board，
  保留 Career `source_url` 为 relationship evidence；该强证据不再依赖搜索引擎或重复 tenant
  probe，未知 provider、跨站来源和未验证 inventory 不会提升。

- `.125` 已用全新目录完成冻结 100 串行 live，首轮为 `57/50/48/45`，只重投 46 条
  retryable completion 后稳定为 `57/50/49/46`。Exact 与 `.120` 持平；官网、Career 和
  Job List 的回落主要来自本轮大量 LinkedIn `451/999` 与 transport failure，不能解释为
  新模块召回提升。Snap、Randstad 在统一 run 中均恢复 Exact，证明 generic detail location
  enrichment 已进入正式 cohort。错误 opening 仍为零，但 Taskrabbit 在 `.com` 暂时超时
  时错误发布了同品牌 `.ai` 官网；Sezzle 则因 Greenhouse 旧/新 host 只规范化一半而触发
  确定性的 `Job board evidence URL must match the board origin` worker contract 异常。
- `.126` 当前修复两个明确 system defect。网站 resolver 在发布同品牌非 `.com` 前为
  exact-brand `.com` 保留独立验证槽；`.com` 若 retryable 阻塞且 LinkedIn 没有官方字段，
  返回 verification-blocked retryable terminal，不再发布另一 TLD。LinkedIn 官方字段和
  parked `.com` 分别作为允许与负向控制。Greenhouse 修复线统一 canonical `JobBoard`、
  evidence URL 和 portfolio identity，保持严格同源 contract，不增加公司特例。
- `.126` fresh focused live 已完成。Sezzle 通过 `sezzle.com/careers` 进入 canonical
  Greenhouse tenant，读取 185 条完整库存并以官方 Financial Analyst detail 通过 S7 Exact；
  Taskrabbit 选择 `taskrabbit.com`，进入 canonical Greenhouse 并准确返回完整库存
  `OPENING_NOT_FOUND`，没有再次发布 `.ai`。两条自动 full-outcome replay 均 reproduced，
  outcome gate 通过、零 fixture gap、零 mismatch。统一离线门禁为 2181 tests（沙箱内唯一
  loopback bind 权限错误在沙箱外 5/5 通过）、25/25 provider、6/6 resolver、43 adapters / 0 issues。

- `.127` 将 `.125` 的 43 条 S2 `FETCH_FAILED` 与 `.120` 逐条按 LinkedIn job URL 对齐：
  其中 17 条、11 家公司在 `.120` 已经发布过经过验证的官网或更深 discovery 证据，
  `.125` 只是被 LinkedIn `451/999` 或 transport 波动阻断；剩余 25 条在两版中均未通过
  S2。该差异证明 stage checkpoint 不能承担跨批次、跨版本的公司事实复用。
- ADR-0028 冻结独立的 verified company discovery evidence store。key 绑定规范化公司名与
  LinkedIn company URL；website、Career、provider board 分层存储并各自 30 天 TTL，原子
  写入、损坏恢复和级联失效。它不保存 exact opening、岗位库存、HTML、cookie、token 或
  durable negative；adapter 版本变化不会擦除候选，但每次使用都必须重新抓取并通过当前
  identity/provider contract。S2 与 S4 已接入“stored candidate 优先重验证”，显式输入仍
  优先；retryable transport 保留候选，确定性身份拒绝才失效。S5 只持久化当前 adapter
  重新识别且 provider/tenant/canonical board 连续的一方 handoff、External Apply 或 provider
  page identity；搜索摘要和 `tenant_name_match` 单独不能入库。
- store 已接入 CLI、live evaluator 与 extension bridge；extension 在配置 `output_dir` 时
  默认使用稳定的 `company-discovery-evidence.json`，也可显式覆盖。`.org` 官网候选轮换与
  branded Career microsite 搜索同时补齐通用召回，不包含公司特例。当前相关 contract 为
  123/123，S2/S4/S5/store 组合为 57/57；全量 2219 tests 中仅沙箱 loopback bind 被拒，
  同一 bridge 测试在沙箱外 5/5 通过。历史证据迁移已经生成；17 条 recovery focused live
  与冻结 100 统一回归仍待执行，尚未计入正式指标。

下一轮执行顺序：P0 审计已经生成的 `.120 → ADR-0028` verified-candidate seed；P1 用全新
checkpoint 对 17 条历史回归做 focused live；P2 对新的 failure cluster 做通用修复并 replay；
P3 串行运行冻结 100 统一 gate。`.140-.153` 的区域品牌 focused 结果不能替代 P3。
外部阻塞、岗位撤下、verified no-match 和明确
retryable 网络失败不得为了数字改写。当前统一正式成绩为 `.125` 的 `46/100` Exact，
同时必须单列准确外部终态、retryable 与 system defect，不能只看 Exact。

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

## 当前稳定化阶段（2026-07-14）

### 阶段结论

从本节生效起，暂停“看到一个失败样本就继续增加 heuristic、provider 或公司规则”的开发方式。当前主线进入 correctness-first stabilization；在本节验收标准全部满足前：

- 不新增 provider。
- 不增加 company-specific branch 或单公司例外。
- 不以提高当前 cohort 的表面 exact rate 为目标。
- 不扩大 `pipeline.py` 的职责；跨 stage 行为必须先冻结 contract。
- 不把 provider benchmark、focused replay、已观察样本或预填 website/career root 的结果称为 blind success。

触发本阶段的事实基线：

- 最新动态 LinkedIn 查询请求 30 家，实际只冻结到 24 家；原始漏斗为 22 website、16 career page、13 job list、7 exact opening。
- 17 个 non-exact 不能统一视为系统缺陷，必须区分正常关闭、无公开岗位、招聘客户未披露、外部阻塞、临时失败和真实 system gap。
- `/private/tmp/.89-budget2-results.json` 中 Fresh Ventures 被错误匹配到 Notion Ashby tenant 的 `Software Engineer, New Grad` opening，S5-S7 却全部成功。这证明当前 exact success 缺少连续 identity evidence chain，历史单一 success-rate 指标不能证明正确性。
- 25/25 provider fixture benchmark 主要证明 adapter 回归稳定，不证明陌生公司的 website/career/tenant discovery 泛化能力。
- `pipeline.py` 仍集中承担 S4-S6 多项职责，legacy `JobSourceAgent.discover()` 与 production `PipelineApplication` 尚未完全收口；本轮只做必要的 contract extraction，不全面重写。

### 稳定化执行状态

本轮 contract implementation 与 replay 发布闭环均已完成，相关集成提交包括 `b810282`、`81bd207`、`76f2de8`、`a99181d` 和 `1679779`。当前状态不是继续扩展 provider，而是等待独立标注后再决定下一轮：

| 范围 | 状态 | 已验证事实 / 剩余动作 |
| --- | --- | --- |
| Opening identity continuity | 已实现 | S3-S6 传递 typed hiring/provider/opening identity；S7 独立 fail-closed，并抑制被拒绝的 exact URL |
| Availability 与 typed error | 已实现 | complete/incomplete/retryable 语义收口；fetch reason、retryability、status 与 request provenance 不再降级为普通 not-found |
| Evaluation contract | 已闭环 | 六类 disposition 与显式 eligibility 已落地；30/30 冻结记录已完成独立于 runtime gate 的 artifact/official-evidence review，并由 SHA-256 绑定工具安全合并 |
| Replay identity outcome gate | 已闭环 | Scoped bundle v7 比较 terminal semantic、identity verdict、failure code 和规范化 identity chain；同 stage rerun 只消费最终 lineage sequence bounds，保留 v6 scoped evidence 读取兼容 |
| 离线门禁 | 已通过 | 1405 tests、provider 25/25、resolver 6/6、architecture 26 adapters / 0 issues |
| 冻结 live gate | 已执行一次 | observed 30-company cohort 已完成；不重跑、不调参后包装为同一轮结果，现有 artifact 用于离线 replay 闭环 |

本轮没有新增 provider，也没有加入公司特例。历史 `.89` 入口改动已在 identity gate 下集成，不再属于“待提交能力”。最终完整 30 条 bundle 达到 30/30 reproduced，均为 record integrity passed、0 fixture gap、0 mismatch。独立标注也已完成，稳定化实现与可信评测阶段均已封板。

## 三路候选发现架构（2026-07-15）

本轮不推翻七阶段，而是将 S5 改为“多路产生未验证候选，统一 adapter/identity 验真”。
官网仍是重要证据来源，但在产品入口默认启用三路候选时不再是 S5 的强制前置条件。

| 工作项 | 状态 | 验收事实 / 剩余动作 |
| --- | --- | --- |
| P0 Candidate contract | 已完成 | immutable `ProviderCandidate`、最多 12 条 pool、严格 public HTTPS/privacy validation；排序不等于验证 |
| P1 三路 discovery | 已完成 | External Apply、显式 website/career ATS、ATS targeted search；搜索支持 bounded exhaustive provider query plan |
| P2 Adapter portfolio | 已完成 | 仅 listing-capable adapter 可进入最多 8 个 board 的 portfolio；search snippet 不进入 typed evidence |
| P3 S2/S4 非阻塞 | 已完成，产品入口默认启用 | 新模式可在 S2/S4 无输出时进入 S5；候选为空回退旧路径；CLI、live evaluator 与 extension bridge 默认启用，`--disable-parallel-candidate-discovery` 可回滚；library/旧 replay 保守关闭 |
| P4 Hiring relationship | 已完成 | External Apply handoff 或严格 company/tenant 相等可授权；substring、标题和搜索排名不能授权 |
| P5 S6 selection evidence | 已完成 | typed title/location/inventory evidence；portfolio 后续 board 命中时切换真实 provider identity |
| P6 S7 final gate | 已完成 | company/provider/tenant/board/opening/title 连续性；location 显式分类，新路径明确 mismatch fail closed |
| P7 Offline release gate | 已完成 | 1461 tests、provider 25/25、resolver 6/6、architecture 26/0、`git diff --check` |
| P8 Product-default graduation | 已完成 | 后续 generic tenant probe、provider verification 和 identity gate 已恢复 SpaceX；Texas/SpaceX 在不传 enable flag 的默认 live 中 2/2 exact、2/2 replay，provider benchmark 25/25 identity expectations |
| P9 SearchBackend recall/cost gate | first-party opening gap 已 focused 验收；陌生 cohort gate 待跑 | Search 仍受公共引擎质量影响；ATS targeted search 保留。高置信第一方 portal 与内嵌 provider 共同进入 portfolio；同一可注册域的搜索 lead 只有在官方 JobPosting 页面、canonical URL、招聘主体、title/location 与 S7 selection 连续验真后才成功。Gucci focused live 已 Exact，snippet 与 sandbox 均不能单独成功 |

配置 contract 升至 deterministic run schema `1.3`、pipeline context schema `1.6`；旧 1.0-1.2
run payload 继续可读并自动保持新功能关闭。实现不加入新 provider 或公司特例。详细决策与安全
边界见 ADR-0025。

最初 P8 只证明 contract/identity 架构可运行，未达到默认启用门槛。后续实现增加了 verified
provider-tenant probe、direct/search wave 短路、candidate-scoped relationship、provider-owned board
canonicalization 和完整 S7 selection evidence。SpaceX 的默认 live 现由 Greenhouse 完整库存恢复 exact，
而跨 tenant、错误 location 和无招聘关系候选继续 fail closed。因此 CLI、live evaluator 和 extension
bridge 已毕业为默认启用；底层 library config 与旧 1.0-1.2 replay 仍为 false，避免静默改变嵌入方和
历史 checkpoint 指纹。

## Fresh 100-posting Live Gate（2026-07-18）

本轮使用五个新岗位族冻结 100 个 LinkedIn job ID，并对 2026-07-17 cohort 做 job-ID
强排重，交集为 0。该 cohort 已被观察，不是预先标注的 blind holdout。

| 工作项 | 状态 | 验收事实 / 后续动作 |
| --- | --- | --- |
| Fresh cohort | 已完成 | 100 postings、95 companies、与旧 live100 job-ID overlap 0 |
| Product funnel | 已完成 | website/career/job-list/exact 为 `94/67/43/25` |
| Retry stabilization | 已完成 | 45 个 retryable completion 自动重跑；exact 从临时 9 恢复到最终 25 |
| External Apply | 无公开输入 | 100/100 public detail 未暴露可用 External Apply URL，不记为算法 0% |
| Provider search | 已完成首轮修复，待新 cohort 测量 | 100 coverage、1 candidate、0 exact 的根因包含 60 次 deadline exhaustion 和 query-plan provider family 被前五条截断；查询顺序已覆盖 Greenhouse、Lever、Ashby、SmartRecruiters，未重写冻结 100 条结果 |
| Route attribution | 已修复 | evaluator 对同源 generic board 的受约束深层 refinement 已补齐；pipeline exact 与 route OR 均为 25/100，malformed trace 0 |
| Manual review | 已完成 failure-cluster 审计 | 人工批注与 trace 审计将真实 system gap、外部阻塞、verified no-match 和无公开 board 分开；不再把全部 75 个 non-exact 当缺陷 |
| Career / provider remediation | 持续 targeted gate | Jushi、Aramark、Stuller、Equifax、Team Royal、WalkMe、Aperia、StatRad、Ivo、Steampunk、Northern Clearing、Alaska Commercial 达到 exact；City of Lubbock、College Station、OneApp、NDIT、Dechert、Conrad、SDS 恢复 verified board；Adapture 以完整 Paylocity 库存确认 no-match，Conrad 已执行官方关键词搜索但当前库存无 frozen Toledo posting，SDS 的 WP Job Manager title/location filtered inventory 权威为空；不把局部结果包装为新的 100 条总体指标 |
| Observed-72 closure | 进行中 | matched `.99`/`.103` 为 `70/55/37/8 → 71/60/43/10`；SKIMS、Bacardi、adidas 新增 Exact，PUMA 当前重试仍为 `NETWORK_TIMEOUT`；Southeastern Renal 只有未链接 Indeed 流程，Meta 只有 verified board；HealthcareSource、ADP、Pinpoint 已集成；`.104` official-host circuit、guessed-path gate、parent/group identity gate 已通过 full gate；targeted live 确认 Tidelands=`HTTP_FORBIDDEN`、Tata Technologies=exact site/RippleHire、LinkedIn guessed board rejected；下一簇为 first-party relationship handoff、dynamic portal 与 phase latency |
| Full replay | 已修复 | cache-derived S2 producer state 可显式重建；Hawaiian Electric 两条 scoped replay 完整消费 tape，无 execution divergence |
| Offline release gate | 已完成 | 1985 tests（3 skipped）、provider 25/25、resolver 6/6、41 native adapters / 0 issues、`git diff --check` |

详细报告见 `docs/LIVE_100_FRESH_20260718_REPORT.md`，人工核对清单见
`docs/LIVE_100_FRESH_20260718_MANUAL_REVIEW.md`。本轮已按 failure cluster 修复 route evaluator、
provider-search query plan、Career 显式 action、JS inventory handoff、官方 Career 目的地保留、
BambooHR 配置、Ashby/iCIMS/Paylocity 入口、ApplicantPro/CATS One/PeopleSoft/
WP Job Manager adapter、
同源匿名 HTML 搜索 transport 和通用 first-party job card；下一次总体
产品指标仍必须来自新的完整串行 cohort，不按公司增加特例，也不回写本轮冻结漏斗。

## 100-posting 三路 OR Live Gate（2026-07-17）

本轮增加 benchmark-only exhaustive route 模式，不改变正常产品的 staged 调度。冻结 100 个
LinkedIn job ID（73 家公司、10 类岗位），对每条同时记录 External Apply、provider-targeted search
和 Website/Career 的 coverage、candidate、verified board、relationship 和 S7 exact attribution。

| 工作项 | 状态 | 验收事实 / 边界 |
| --- | --- | --- |
| Distinct posting cohort | 已完成 | 按 LinkedIn job ID 去重；100/100 输入，cohort digest 已冻结 |
| Exhaustive route probes | 已完成 | deterministic schema `1.4`；100/100 trace well-formed |
| External Apply | 已完成，无覆盖 | 公开 detail 97 条明确无可见 URL、3 条 fetch failure；条件成功率记 N/A |
| Provider search | 已完成 | 19 个 candidate/verified relationship，11 exact；overall 11/100 |
| Website/Career | 已完成 | 59 个 candidate、58 relationship，24 exact；overall 24/100 |
| OR-union | 已完成 | search-only 4、website-only 17、两路共同 7；union exact 28/100 |
| Scoped replay | 有残余风险 | 98/100 reproduced、2 mismatch；不虚报为 passing replay gate |
| 审计产物 | 已完成 | cohort JSON、metrics JSON、100 行 CSV 和 `docs/LIVE_100_THREE_ROUTE_REPORT.md` |

该 cohort 是 observed live evaluation，不是预先人工标注的 blind holdout。28/100 表示运行时 typed
S7 exact 的 route OR 召回，不能替代独立人工 expected URL 所需的 exact precision。

## Career Inventory 收尾（2026-07-16）

本轮沿用 S1-S7、ADR-0024 的 opening identity continuity 与 ADR-0025 的候选/adapter
验真边界。目标是完整消费官网已经公开的 Career 深层导航和库存声明，不用公司特例把已知
样本直接映射到 board 或 opening。稳定边界见
[ADR-0026](docs/adr/0026-follow-career-inventory.md)。

| 工作项 | 状态 | 验收事实 / 安全边界 |
| --- | --- | --- |
| C0 Career 深层导航 | 已完成 | 只跟随显式 Career/job-list command 和受约束的同站导航；无标签、跨站普通链接和猜测 route 不继承 inventory 证据 |
| C1 页面与嵌入 ATS inventory | 已完成 | page link、data attribute、iframe、embedded URL、`script-src` 和 provider config 只产生候选；必须由 listing-capable adapter 识别 canonical tenant board |
| C2 First-party declared inventory | 已完成 | 仅执行单一、可静态证明的 public HTTPS transport；same-origin anonymous GET 或 form-encoded XHR POST 均有 asset、字段、payload 和 candidate 上限 |
| C3 Generic HTML inventory | 已完成 | 只消费严格岗位卡和显式 next pagination；单页无分页、cap、循环、redirect、解析或 fetch 异常保持 incomplete，不能发布 company-wide no-match |
| C4 Provider adapters | 已完成 | Paycor、UltiPro 进入 listing-capable registry；provider module 自己验证 tenant、canonical board、redirect、分页、detail URL 和 inventory completeness |
| C5 已知页面变体 | 已完成，无公司特例 | Tata 官网脚本声明 native XHR form transport；Banks 官网 `script-src` 暴露 Greenhouse handoff，均通过通用 contract 恢复 |
| C6 冻结 observed live gate | 已完成 | 10/10 website、10/10 career、10/10 job list、9/10 exact，147.2 秒；唯一 Tata 的 live reason 为 `BOT_PROTECTION`，不计 exact |
| C7 Full-outcome replay | 已完成 | 10/10 matched、selected、exported、replayed；outcome gate passed |

该 10-company cohort 已被观察，且 evaluation annotation coverage 为 0/10；因此这里只发布 raw
funnel、typed failure 和 replay 结果，不发布 exact precision、conditional exact recall 或
system defect rate。Tata 的 job list 成功只证明 S5 inventory handoff，S6 仍是 partial；403/429、
bot protection、transport failure、歧义声明、payload/tenant/redirect 不一致和预算截断必须保留为
typed blocked/retryable/incomplete，不能降级为 `OPENING_NOT_FOUND` 或 `NO_PUBLIC_OPENINGS`。

## 40-company 稳定化回归（2026-07-17）

本轮使用已经观察过的 40-company cohort 做回归定位，不把它重新表述为 blind measurement。
完整串行的修复前回归从旧基线 `33/23/15/4` 提升到 `35/26/20/13`。其后只对受影响样本执行
targeted live，因此不发布修复后的 40-company overall rate。

| Failure cluster / 工作项 | 状态 | 验收事实 / 边界 |
| --- | --- | --- |
| Homepage evidence equivalence | 已完成 | 同一路径的 HTTPS apex/`www` 可复用 S2 homepage navigation evidence；普通子域、路径变化和非 HTTPS 不等价 |
| Candidate scheduling priority | 已完成 | direct/identity evidence 与显式 same-site job portal 先于 speculative route family；排序不替代页面、tenant 或 inventory 验真 |
| Scoped replay producer dependency | 已完成 | downstream replay 从所需 evidence 的 producer stage 开始；缺少 producer scope/tape 时 preflight fail closed |
| ApplicantStack adapter | 已完成 | canonical tenant `/x/openings`、公开 board fingerprint、同 tenant detail ID、完整/不完整 inventory 分类均由 adapter 所有 |
| Single-tenant visible-detail evidence | 已完成 | 至少两个可见 detail URL 且全部归一到同一 provider/tenant/canonical board 才可提升；单链接或 mixed tenant 不足 |
| Explicit same-site portal priority | 已完成 | 明确 job-list command 的同站 portal 先于 generic traversal；仍受 HTTPS、same-site、region 和 safe-target gate 约束 |
| Targeted live | 已完成 | CHC、Aarris Healthcare、System One 达到 exact；Northwell Health 恢复 verified board；Resonate AI 保持 budget exhausted |
| Release gates | 已完成 | 1570 tests、provider 25/25、resolver 6/6、29 adapters / 0 architecture issues |

剩余风险集中在真实 transport budget 与未覆盖的公开 inventory 变体。下一次总体产品指标必须来自
新的完整串行 cohort；本轮 targeted recovery 只能证明对应 contract 和样本回归，不能外推总体命中率。

### 人工标注 Remediation 首次 checkpoint（2026-07-17）

已完成首批六轮受控修复和第一次冻结 40-company live checkpoint。2026-07-17 用户撤销了
“最多六轮”和“只运行一次 live”的资源限制；该 cohort 已用于开发和
人工核对，分类固定为 `observed development cohort`，不得称为 blind。三路候选发现保持
“只提高召回、统一 adapter/relationship/S7 验真”的边界，没有加入公司 URL map、annotation
override 或 `if company == ...`。

| 指标 | 修复前 | First checkpoint | 结论 |
| --- | ---: | ---: | --- |
| Website | 35/40 | 38/40 | +3 |
| Career | 26/40 | 29/40 | +3 |
| Job list | 20/40 | 22/40 | +2 |
| Raw exact | 13/40 | 18/40 | +5 |
| Conditional exact recall | 0/10 | 4/10 | 只使用明确 eligible 的人工标注 |
| System defect | 10/10 | 6/10 | 同一 eligible system-gap 分母 |
| Wrong expected URL | 0 | 0 | 3/3 frozen expected URL 匹配 |

全量 exact precision 仍不可报告：18 条 exact 中有 15 条没有独立冻结 expected URL。Solace 的
新 Ashby opening 已独立确认属于正确的 `find-solace` 医疗招聘主体，但 S2 官网仍误选同名
`solace.com`；Lilly Pulitzer 存在“旧 LinkedIn posting 已关闭、当前 Workday 新职位活跃”的
时间漂移，无法证明 requisition identity。不得用 runtime S7 verified 代替人工 precision。

First checkpoint 的自动 full replay 因 Gary and Mary West PACE 在 S5 canonical evidence URL 异常前
未 finalize scope 而 fail closed。该 P0 已在候选 contract 边界修复，page-derived board replay
也改为从完整 producer chain 恢复；不补造 tape。其余真实 capture 达到 39/39
reproduced、0 mismatch；success 18/18、partial 10/10。完整证据和 digests 见
`docs/ANNOTATION_REMEDIATION_REPORT.md`。

当前关键路径改为 **人工标注优先的通用缺口修复**。持久化审核清单为
`docs/OBSERVED_40_EXACT13_FAILURE_CHECKLIST.md`：27 个 non-exact 中已有 23 个完成人工核对，
最初 10 个被标记为 `system_gap`。后续招聘身份审核把 Hugh Chatham Health 重新分类为
`eligibility_unknown`；剩余九个有效缺口均已在 targeted 或产品默认 40-company live 中恢复
S7-verified exact：Aarris Healthcare、System One、CHC、SpaceX、Lacoste、Texas Children's
Hospital、Northwell Health、Gary and Mary West PACE 和 Avery Dennison。

以下人工结论不进入缺陷修复队列：`verified_closed`、`no_public_opening`、
`external_blocked`、`identity_rejected`、`eligibility_unknown` 和尚未审核的记录。它们只用于
验证失败分类、用户可理解的结果文案和 fail-closed 行为，不得通过放宽 identity 或伪造 opening
提高 exact 数字。

| 优先级 | 通用 failure cluster | 主验收样本 | 实现目标 / 安全边界 |
| --- | --- | --- | --- |
| R0 | Career 到公开 Job Board 的显式深层导航 | SpaceX、Lacoste | Career 已验证后，有界跟随可见的 `Open Positions`、`Job Offers`、`Search Jobs` 等 command；链接只产生候选，仍由 adapter、tenant 和 relationship gate 验真 |
| R1 | Job List 库存、站内搜索、分页和 detail publication | Northwell Health、Gary and Mary West PACE、Avery Dennison | 已验证 Job List 必须优先消费公开 inventory/搜索 transport、完整分页和可见 detail；不完整库存保持 Partial/Retryable，不能发布错误 no-match 或猜测 detail URL |
| R2 | 官网不可用时的 ATS 候选旁路 | Texas Children's Hospital；Hugh Chatham 作为负向身份样本 | Oracle HCM/Workday 定向候选可绕过 S2/S4 的召回阻塞，但必须建立公司到 hiring entity、provider、tenant、board、opening 的连续证据；搜索摘要不能授权成功 |
| R3 | 已恢复路径防回归 | Aarris Healthcare、System One、CHC | 固定 ApplicantStack、显式 first-party portal 和 UltiPro 的 targeted fixtures/replay，防止“找到 list 但不进入 detail”重新出现 |
| R4 | 同名公司和外部阻塞负向门禁 | Solace、Atrium Health、Focus Health Network | 错误同名公司继续由 S2/S3/S7 拒绝；登录、Teams、私有申请流输出明确 blocked reason，不尝试绕过 |

### 人工标注 Remediation 执行顺序

1. 冻结 unresolved system gaps 的输入、当前 reason code、expected stage transition 和已有官方
   evidence；人工给出的 URL 只能作为测试 oracle，不能成为 production company map。
2. 先按 R0/R1/R2 聚类定位根因，再冻结最小 contract 和负向测试；禁止按公司逐条增加
   `if company == ...`、域名白名单或硬编码 opening URL。
3. R0、R1、R2 在 ownership 可隔离时按 `AGENTS.md` 使用独立 worktree 并行；共享 candidate
   contract、registry、composition root、计划和 changelog 由主线统一修改。
4. 每个 cluster 先运行局部 fixture、provider test 和 scoped replay；通过后再集成，统一运行全量
   unit tests、provider benchmark、resolver benchmark、architecture gate 和 replay integrity。
5. 离线门禁通过后，先串行运行 unresolved 样本的 targeted live。只有错误 URL 为零、完整
   identity chain 通过且防回归样本稳定，才运行一次新的完整 40-company observed regression。
6. 新 live 使用独立 results、trace、summary、snapshot、checkpoint 和 run config，不覆盖历史
   `13/40` 或 First checkpoint artifact；报告必须并列展示 website/career/job-list/exact 漏斗、
   failure distribution、请求数、耗时和 wrong-URL count。
7. 将新 non-exact 再按人工 eligibility 与 stage/provider/reason 聚类；只继续处理覆盖多个样本的
   通用根因，不追逐单家公司长尾。

### 本轮完成标准

- 所有确认 system gaps 均恢复经过 S7 验证的 exact opening，或者有新的独立证据将其重新分类为
  closed、external blocked、no public opening 或 eligibility unknown。
- Aarris Healthcare、System One、CHC 三个已恢复 exact 不回归。
- Solace 等同名/跨 tenant 负向样本继续被拒绝，wrong-company 和 wrong-tenant URL 为零。
- 不新增公司特例，不以搜索 snippet、人工 expected URL 或 URL 猜测作为成功证据。
- 所有 offline gates、targeted live、full observed regression 和文档治理记录完成。

本轮人工确认的简单公开路径不设置迭代轮次上限：只要仍有通用、可验证且不需要放宽身份门禁的
修复方向，就继续推进。只有剩余工作依赖登录态、付费 API、隐私数据、官方资源已失效、身份链
冲突、七阶段 contract 重构或产品决策时，才保留 checkpoint 并输出 blocker / 重新分类报告。
普通网络波动不构成停工理由：先完成全部离线 fixture/replay/documentation，网络恢复后从独立
checkpoint 串行续跑。完整 40-company regression 仍按 failure cluster 批量执行，避免每个局部
改动都重复消耗共享 live 资源。

本轮完成状态：当前有效人工简单缺口 9/9 恢复，Hugh Chatham 依据跨招聘主体身份冲突移出
eligible 分母；产品默认 40-company regression 达到 website/career/job-list/exact
`40/32/28/22`，相对 First checkpoint 为 `+2/+3/+6/+4`。四个冻结 expected URL 为 4/4，
wrong expected URL 与 unsafe exact 均为 0；full-outcome replay 为 40/40、0 mismatch、0 fixture
gap。运行耗时 401.5 秒，最终 stage capture lineage 记录 1,482 次公开 HTTP transaction。整体
exact precision 因 18 个 exact 缺少独立 URL 标签仍不可报告。

## 下一阶段：Blind Holdout 产品基线（2026-07-15）

稳定化反馈要求的 correctness contract 已经封板。下一步不再根据已知失败继续增加
heuristic、provider 或公司特例，而是建立第一批真正陌生、不可回看的产品基线。
本阶段用于回答“系统面对从未用于开发的真实 LinkedIn 输入时有多可靠”，并取代
provider/replay 通过率作为对外成功率依据。

### 阶段硬约束

1. 通过 S1-only LinkedIn public search 收集候选；收集阶段不得调用 S2-S7，不得预填
   website、career、board、opening 或 external apply URL。
2. 从 30-50 家唯一公司中冻结 cohort。候选公司名和 LinkedIn job ID 必须在当前仓库、
   指定历史 artifact root 和完整 Git patch history 中均未出现；缺失历史 root、脏的
   tracked worktree 或不可验证的代码身份一律 fail closed。
3. 冻结 manifest 必须绑定候选池、规范化 cohort、身份表、运行配置、Git commit、
   source tree 和历史审计摘要。冻结后不得修改 production code、测试、run config 或
   cohort；任何变化必须废弃该 cohort，并重新开始一个新的 blind 版本。
4. 只允许一次完整 live execution。one-shot ledger 在发起请求前原子消费；即使进程失败，
   cohort 也从 `blind_unseen` 变为 `blind_observed`，不得重跑后仍称同一次 blind evaluation。
5. Result、trace、summary 和 execution manifest 必须形成摘要链；result/trace 的 company、
   source job、website、career、board、provider、opening、status 和 stages 发生语义漂移时
   整轮报告 fail closed。
6. Codex artifact review 与 human evaluation review 独立生成。V1 使用 40/40 contract-valid
   Codex artifact review 作为明确标注的 baseline authority；未完成、未签名的人类 review 保留为
   独立可选通道，不得混称或暗示为人工指标。未来若发布 human-reviewed 版本，仍必须使用 reviewer
   自有 SSH key 对原始 manifest 做 detached signature。
7. 所有 exact URL 必须核验正确 company/hiring entity relationship、provider tenant、canonical
   board、title、location 和公开可访问性；opening、board 和招聘主体证据必须分别记录，且
   opening/board evidence URL 与被评分 URL 一致。V1 的四条 exact 由 Codex artifact review 完成核验。
8. 本阶段不新增 provider、不加公司规则、不调参恢复数字。exact rate 下降可以接受；跨公司
   URL 或未经验证的 exact success 不可接受。

### 执行清单

| Gate | 状态 | 产物 / 验收标准 |
| --- | --- | --- |
| B0 审计并冻结 blind contract | 已完成 | one-shot runner、历史审计、execution chain、独立 review schema 和攻击性 tests |
| B1 离线门禁与 prep commit | 已完成 | 1414 tests、provider 25/25、resolver 6/6、architecture 26/0；冻结 commit `5b090a2` |
| B2 S1-only 候选与 unseen audit | 已完成 | 160 public cards；40 unique companies；45 historical overlaps rejected；0 prefill |
| B3 冻结 cohort | 已完成 | cohort SHA-256 `04b3d45d...e3376`；2,398 historical files；0 skipped files |
| B4 one-shot live execution | 已完成 | run `d5c9a520-2edc-4493-af38-e1bb178eb506`；40/40；ledger consumed once；artifact chain valid |
| B5 双轨分栏审查 | 已完成 | Codex 40/40 contract-valid 并作为 V1 authority；human 1/40、未签名，单独保留且不纳入本次指标 |
| B6 基线报告 | 已完成 | 已报告 raw exact、exact precision、conditional exact recall、system defect、六类 disposition 和 eligibility unknown；明确 Codex-reviewed authority |
| B7 阶段停止点 | 已完成 | 发布三个 failure clusters（不超过五个候选上限）；未自动修复 |

### 插件产品化工作线（2026-07-15）

该工作线只修改 `extension/`、插件测试和插件文档，不改动 Python pipeline、resolver、
provider、bridge contract 或 blind-holdout 标注。后端稳定化与人工标注继续独立推进。

| Gate | 状态 | 产物 / 验收标准 |
| --- | --- | --- |
| E0 冻结 scan contract | 已完成 | response 保留 `ok`、`records`、`page_url`，新增 `scan_version=2` 与 `ready/not_ready` |
| E1 LinkedIn DOM 正确性 | 已完成 | 当前职位 ID 绑定详情；隐藏/disabled 控件不产生活跃证据；伪造 host 与不安全 Apply URL fail closed |
| E2 Popup 可靠性 | 已完成 | 仅允许 token-authenticated `127.0.0.1`；超时、有限重试、断线/陈旧 run 恢复、重复操作保护 |
| E3 结果展示与安全 | 已完成 | 校验 run payload/rate；只渲染 public HTTPS opening/job-list 链接；失败保留 reason code |
| E4 自动化插件门禁 | 已完成 | 14 个 content DOM 场景、12 个 popup workflow；全量 1421 tests、25/25 provider、6/6 resolver、architecture 26/0 |
| E5 真实登录态验收 | 主流程已通过 | Microsoft 新版 DOM 达到 1 selected job / 1 Apply；即时链接、严格 run 和结果展示已验证；v0.2.2 in-flight reopen 未重复人工执行 |
| E6 当前批次整页扫描 | 真实复验中 | v0.3.1 双模式：Selected 即时详情；Page 串行绑定最多 30 个 currentJobId，逐条等待 Apply 状态，带进度、取消和原选择恢复 |

真实验收只能声明已经执行的范围。当前可以声明“真实 LinkedIn 主流程通过”，但不能把未重复
执行的 v0.2.2 in-flight reopen 写成真人通过；该行为由 popup harness 覆盖。人工 gate 不允许
Computer Use 代替用户已登录 Chrome，也不触发后端规则调整；发现 DOM 失败时先保存最小脱敏
现象与 LinkedIn route，再按通用 selector/readiness failure cluster 修复。

E6 的“Page”只表示 LinkedIn 当前已经加载的结果批次，不代表搜索结果总量。新版卡片不暴露
job URL，必须逐卡触发站内选择后观察 `currentJobId`；每条在有界预算内等待匹配详情的 external、
native 或 closed Apply 状态，超时仍保留诚实的 listed/unknown 记录。整批严格验证不自动启动，
避免把 20-30 条输入变成长时间后台 run。

### Blind 基线报告规则

- `exact_precision` 的分母只能是系统输出的 exact opening，分子必须由声明的 review authority
  验证通过完整 identity chain 且当前公开可访问；目标仍为至少 98%，错误公司 URL 必须为 0。
- `conditional_exact_recall` 只计算 review authority 确认存在 eligible public official opening 的记录；
  unknown eligibility 不得强行进入分母。
- `raw_exact_rate` 使用全部冻结输入，同时必须展示 `exact_public`、`verified_closed`、
  `no_public_opening`、`recruiter_client_undisclosed`、`external_blocked`、`system_gap` 分布。
- `system_defect_rate` 单独统计错误 company/tenant/URL、parser bug、错误失败分类和可恢复 transport
  failure。provider fixture、focused replay、observed cohort 和预填 discovery benchmark 不得混入。

完成 B6 后先停下来审查结果。任何后续修复都属于新迭代，并使用新的 blind holdout；本次已经
观察的 cohort 只能作为 regression cohort，不能再次用于产品泛化声明。

最终原始漏斗为 33/40 website、23/40 career page、15/40 job list、4/40 exact opening。
Codex-reviewed V1 报告为 raw exact 4/40、exact precision 4/4、conditional exact recall 4/4
（另有 36 条 eligibility unknown）和 system defect 36/40；六类 disposition 已分栏发布。该
数字在唯一一次执行后冻结，不因未来独立 human review 而改变；详见
`docs/BLIND_HOLDOUT_V1_REPORT.md`。

### 冻结 Observed Cohort 结果

本次只运行了一次冻结的、开发者已观察过的 30-company cohort，因此只能标记为 `frozen_observed`，不能称为 blind、unfamiliar holdout 或独立精度验收。

| 指标 | 结果 | 可报告性 |
| --- | --- | --- |
| Exact URL / raw exact rate | 19/30 | 可报告，但必须与失败分布同时出现 |
| Pipeline status | 19 success / 5 partial / 6 failed | 可报告 |
| Identity verdict | 19 verified / 6 not_applicable / 5 rejected | 可报告；这是 runtime gate 结果，不等于人工 URL precision |
| Exact precision | 19/19（100.0%） | 由 runtime gate 外的 frozen artifact/official evidence review 得出；只适用于 observed cohort |
| Conditional exact recall | 19/24（79.2%） | 24 条 confirmed eligible；Deloitte、Akkodis 的 eligibility 保持 unknown |
| System defect rate | 7/30（23.3%） | 5 个 identity-evidence false negative，加 2 个 retryable transport gap |

当前 failure clusters：

- Provider relationship unverified（4）：Dematic / KION Workday、Quest Global / Phenom、ReturnPro / Paycom、Adobe / Phenom。
- Opening identity missing（1）：Awesome Motive 的 generic first-party 到 Workable handoff。
- Opening not found（3）：Viking、GPTZero、Percepta；在 inventory/eligibility 外部复核前不自动视为系统缺陷。
- Ambiguous hiring identity（1）：Aventis；不得猜测未披露招聘客户。
- Retryable network timeout（2）：Deloitte、Akkodis；保留 retryable 语义，不转成 not-found。

自动 replay 在读取完整 live artifact 时发现 same-stage rerun 产生重复 ordinal。主线现已只用最终 lineage 的 sequence bounds 排除同 scope 的 orphan record，并从既有 trace/snapshot 离线重建 bundle；完整 30 条与 11 条失败子集分别 30/30、11/11 reproduced。外部返回的非标准 response status `999` 作为 typed transport evidence 原样保留。整个修复没有重新运行 live cohort。

### 阶段入口工作区

进入本阶段时的未提交 `.89` 工作必须完整保留并逐项审查，不得 reset、checkout 或用格式化覆盖：

| 未完成工作 | 文件范围 | 稳定化处理 |
| --- | --- | --- |
| listing/hydration 与 semantic-card extraction | `listing_extraction.py`、`card_listing_extraction.py` 及测试 | 只在 identity gate 后保留；不得仅凭 title/URL pair 产生跨 tenant exact success |
| Career 首页和单数 `Career` evidence | `pipeline.py`、`test_career_surface_detection.py` | 作为 S4 evidence 审查，不得隐式授权任意下游 provider tenant |
| opening incomplete reason contract | `reasons.py`、`opening_availability.py`、`stages/discovery.py`、`evaluation.py` 及测试 | 与 replay/outcome contract 一起冻结，不能只改旧断言求绿 |
| deadline publication reserve | `live_batch_eval.py` 及测试 | 保留为 runner reliability 变更，独立验证，不参与 exact identity 判定 |
| `.89` adapter version | `checkpoint.py` | 最终 contract 确定后统一失效；当前不是发布完成标志 |

上述入口变化已经过 P0 identity contract 集成；最终发布状态仍取决于 replay sequence-bound 修复和修复后的统一离线门禁，而不是 live exact 数量。

### P0-A：Opening Identity Continuity Contract

exact success 必须存在以下连续、可验证且版本化的 identity chain：

```text
source company identity
  -> resolved hiring entity / verified relationship
  -> official career source
  -> provider + tenant + canonical board
  -> opening provider + tenant + canonical board
  -> validated opening URL
```

冻结规则：

1. S3 必须输出招聘主体，或明确记录继续使用 source company；母公司、收购品牌和统一招聘体系必须有显式 verified relationship evidence。
2. S4 只证明 career source 的官方性，不自动信任页面中出现的任意 ATS tenant。
3. S5 成功必须输出结构化 `provider_identity`：provider、tenant、canonical board URL、evidence URL、verification method 和 hiring-entity relationship provenance。
4. Native adapter 成功不得只返回字符串 URL；其 provider、tenant 和 canonical board identity 必须可重新验证。
5. S6 opening 必须绑定 S5 已验证的 provider/tenant/canonical board；标题相同、ATS 相同但 tenant 不同仍必须拒绝。
6. S7 必须独立检查 S3-S6 identity continuity。identity 缺失、冲突或无法证明时 fail closed，不得输出 exact success。
7. acquired-brand/parent-company 路径只在 S3/S4 已冻结的显式关系下通过，不能用“一律同域”或“一律同 provider”代替关系证据。
8. Identity failure 必须进入结构化 reason/evidence，并可由 snapshot/replay 原样复现。

P0-A 必须先写以下通用 contract tests：

- Fresh Ventures 页面链接到 Notion tenant 时拒绝 opening，且代码中不存在公司名特例。
- 正确 company/hiring entity、board tenant 和 opening tenant 连续时继续通过。
- 有显式 verified acquisition/parent relationship 时可受约束通过。
- 标题完全相同但 tenant 不同仍拒绝。
- S5 board 与 S6 opening tenant 不一致时由 S7 拒绝。
- 缺失 provider/tenant identity 的 native success 不能成为 exact success。
- 同一 identity failure 可通过 scoped replay 复现，且 outcome gate 不把它当作普通 opening miss。

### P0-B：Opening Availability And Replay Contract

三个 availability reason 的唯一语义：

| Reason | 使用条件 | 禁止条件 |
| --- | --- | --- |
| `OPENING_NOT_FOUND` | eligible boards 已全部尝试，相关 inventory 已验证完整且非空，但没有可信 title/location match | 未完成 board、分页、provider fetch 或 identity 验证 |
| `NO_PUBLIC_OPENINGS` | 官方完整 inventory 被明确验证为空，且 scope 是 company-wide public inventory | 仅 title-filtered empty、页面解析不到岗位、网络失败 |
| `OPENING_DISCOVERY_INCOMPLETE` | 无 exact opening，且 inventory/eligible boards/identity 证据不足但没有更具体 retryable typed error | 已有完整 no-match/no-public 证据 |

附加规则：

- provider/network/timeout/403/budget error 优先保留具体 typed reason 和 retryable 字段，不得降级成以上三个业务结果。
- portfolio 中任一 eligible board 不完整时，company-wide 结论保持 incomplete。
- production result、stage result、evaluation terminal semantic 和 replay outcome gate 必须使用同一 availability contract。
- 修改旧测试期望前，先用 contract test 证明 fixture 属于 complete、empty、incomplete 或 retryable 的哪一种。

### P1-A：Typed Error Fidelity

- Stage 首先消费 `FetchError.reason_code`、`retryable`、HTTP status 和 typed metadata；`classify_fetch_error(str(exc))` 只允许作为 legacy/untyped fallback。
- 候选回退可以继续尝试，但每次 `FetchError` 必须进入结构化 evidence；不得吞掉异常后返回 `None`。
- 最终 reason 按 evidence tier 和 typed priority 聚合，不能让后续弱 not-found 覆盖前面的 timeout、403、预算耗尽或 provider failure。
- 增加 timeout、403、budget exhausted、fixture missing 和真实 candidate absence 的跨 stage contract tests。

### P1-B：可信评测口径

后续 summary/report 必须同时提供：

1. `exact_precision`：所有 exact 输出中，company/hiring entity/provider/tenant/opening identity 全部正确的比例；目标至少 98%，cross-company URL 必须为 0。
2. `conditional_exact_recall`：仅在人工或独立证据确认存在公开、可访问 official opening 的 eligible 样本中计算 exact recall。
3. `raw_exact_rate`：全部输入中的 exact rate，同时展示各 failure disposition，不能单独报告。
4. `system_defect_rate`：错误公司、错误 URL、parser bug、错误失败分类和可恢复超时的比例。

每个 blind/live record 必须且只能标注为：

- `exact_public`
- `verified_closed`
- `no_public_opening`
- `recruiter_client_undisclosed`
- `external_blocked`
- `system_gap`

评测报告还必须记录 cohort provenance：dynamic requested count、actual frozen count、是否预填 website/career root、是否已被开发者观察、是否 focused，以及 eligible-label 的证据来源。focused、replay、provider fixture 和预填输入不得标记为 blind。

### 并行 Workstream 与 Ownership

本阶段 contract 由主线先冻结，之后才允许以下互斥工作线进入独立 worktree：

| Workstream | 目标 | 唯一 ownership | 独立临时根 | 局部验收 |
| --- | --- | --- | --- | --- |
| Main / Contract | identity dataclass/schema、S7 continuity、composition 与最终集成 | 公共 contract、composition root、S7、ADR、中央文档 | `/private/tmp/stabilize-main-*` | identity contract suite |
| A / Stage identity | S3-S6 只通过 contract 传递 identity，不读取别的 stage 私有 trace | stage-owned modules 和新 stage tests；不修改中央 schema | `/private/tmp/stabilize-identity-*` | stage continuity tests |
| B / Typed errors | typed FetchError propagation 和 final reason priority | error/fetch/stage error tests；不修改 identity contract | `/private/tmp/stabilize-errors-*` | typed-error tests |
| C / Evaluation | precision/recall/raw/defect 与 disposition schema/report | evaluation/reporting 模块和 fixtures | `/private/tmp/stabilize-eval-*` | metric contract tests |
| D / Replay audit | identity failure capture/outcome comparison，只读调查后再分配写集 | replay tests 或只读 artifact audit；不修改 production stage | `/private/tmp/stabilize-replay-*` | scoped replay identity tests |

中央共享文件、`pipeline.py`、schema/version、registry、ADR、`IMPLEMENTATION_PLAN.md` 和最终 Git 操作由主线统一修改。完整 live benchmark、登录态 Chrome 和共享网络出口始终串行。

### 执行顺序与门禁

1. 审计 dirty diff、Fresh→Notion trace、现有 identity objects、typed error 丢失点和 replay comparison；不先实现。
2. 新增 ADR 冻结 identity continuity、availability/replay 和兼容策略；若 schema 变化，先冻结 schema/version contract test。
3. 先写 P0 失败测试，再实现最小 contract extraction 和 fail-closed validation。
4. P0 局部全绿后并行推进 typed error、evaluation 和 replay 适配；不得新增 provider/heuristic。
5. 主线集成后统一运行：全量 unittest、25-case provider regression、6-case resolver regression、architecture validator。
6. 上述离线门禁全绿后，冻结一个未继续调参的 cohort，并且只运行一次；不得边看结果边修改后继续称为同一次 blind evaluation。
7. 统一报告修复前后 `exact_precision`、`conditional_exact_recall`、`raw_exact_rate`、`system_defect_rate` 和六类 disposition。正确性修复导致 exact rate 下降时如实保留。
8. 完成本轮文档、分组 commit 和 push 后停止，不自动进入下一 provider/company 修复轮。

### 本轮验收标准

- Fresh Ventures→Notion 跨 tenant opening 被稳定拒绝，无公司特例。
- 正确 provider/tenant/opening 链路和受约束 acquired-brand 链路无回归。
- every exact success 都携带并通过完整 identity chain。
- availability、evaluation 和 replay 使用同一 complete/incomplete/retryable contract。
- typed provider/network failure 不再退化为普通 not-found。
- 全量离线门禁通过后只执行一次冻结 cohort。
- 报告四个可信指标和六类 record disposition，不再只报告单一 success rate。
- 本轮结束时列出修改文件、contract 变化、测试结果、未解决 failure clusters，并按“覆盖样本数 × 风险 × 预计收益”只给出最多三个下一轮候选任务。

### Provider 扩展暂停条件

Provider/heuristic churn 继续暂停。Replay release blocker、独立 disposition/eligibility 审阅与稳定化门禁均已闭环；只有用户明确进入下一开发轮后，才重新按 failure cluster 评估通用能力。历史 Phase 3 provider backlog 保留为参考，但当前不执行；observed cohort 的 100% exact precision 不能外推为 blind 产品精度。

### 下一轮候选（最多三项）

候选按“覆盖样本数 × 正确性风险 × 预计收益”排序；它们是稳定化候选，不授权新增 provider 或公司规则。

1. **New unfamiliar holdout**：在任何继续调参前冻结真正未观察样本，独立测量 precision、conditional recall 与 cross-company error；当前 observed cohort 的 100% precision 不足以证明泛化。
2. **Provider-neutral relationship promotion contract**：为 Dematic、Quest Global、ReturnPro、Awesome Motive、Adobe 这 5 个 false negative 冻结 first-party career handoff 到 typed provider relationship 的通用正负 contract；不得加入公司或 tenant 特例。
3. **Retryable transport isolation**：对 Deloitte、Akkodis 的 timeout 做串行 transport reliability 验证；完整证据前 eligibility 继续为 unknown，不得降级成 not-found。

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
| CEIPAL | Native page-aware inventory adapter | 强 widget/tenant 绑定；读取公开 multipart inventory，完整分页后才允许 empty/no-match，重建 first-party detail URL |
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
- 26 个 provider module 已自动发现；其中 25 个提供原生 inventory 或受约束 positive evidence，仅 Talemetry 提供 detection-only typed incomplete semantics；generic fallback 继续作为兼容路径。
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

### 9. Checkpoint、Replay 与 Snapshot 已进入严格版本化阶段

Production CLI、live batch 和 failure replay 均通过 `PipelineApplication` 执行。Batch completion、stage checkpoint、attempt/stage evidence lineage、durable publication、typed retry、full-outcome bundle-v6 replay 和 30-company identity gate 已完成；S1-S3 与 S4-S7 的 process hard budget 仍保留。`scripts/export_replay_input.py` 可按 stage/status/reason/provider 输出稳定 replay input，snapshot v3 精确绑定 producer attempt、execution fingerprint 和 stage scope；missing、extra、mismatch、unconsumed 或损坏 outcome 全部 fail closed。

Legacy v1/v2 snapshot materialization 仅用于显式兼容。任何 v3-only 或 mixed scoped index 都会在写入前非零失败并要求 bundle-v6 replay，不能再把现代 capture 过滤成空 fixture success。当前剩余 checkpoint 工作不是“补第一版”，而是统一普通 CLI 与 live batch 的完整 prefix preflight：请求从 S5/S6 恢复时，缺失、损坏、incompatible 或语义无效的 authoritative upstream chain 必须回退到最早缺口或明确失败，不能带 `not_run` prefix 继续执行下游。

当前关键中间产物均以 typed/versioned contract 保存：

- LinkedIn/company 原始输入
- resolved website 及证据
- hiring entity / parent company 决策
- verified career URL
- detected provider 和 board identifier
- provider response 或脱敏 HTML/JSON snapshot
- job candidates 和 title-match 分数
- 最终选择及验证证据

Checkpoint 带 `schema_version`、execution/input fingerprint、run configuration 和 adapter 版本；只有完整兼容且能按顺序应用到 fresh context 的 prefix 才能复用。下一项治理重点是公司级 URL/provider/tenant/opening identity regression matrix，避免总成功率不变却悄悄换成错误 URL。

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

当前状态：已完成可并行扩展的第一版。26 个 provider module 通过导出 `ADAPTER` 自动注册；25 个提供 native inventory 或受约束 positive evidence，仅 Talemetry 使用 page-evidence extension 提供 detection-only typed incomplete contract。Generic fallback 暂时保留 compatibility path。

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

### Phase 3: Complete Provider Adapters（当前暂停）

本 Phase 仅保留历史 backlog。根据 2026-07-14 correctness-first stabilization 决策，在 opening identity continuity、typed error、可信评测和冻结 cohort gate 完成前，不启动任何新 provider 或单公司 heuristic 工作。

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
- `.54` S4 evidence/diversity scheduler workstream 完成：新增独立 `career_candidate_scheduler.py`，`LinkCandidate` 从 `RawLink` 保留结构化 origin，按 authoritative handoff、明确 first-party career navigation、sitemap/search discovered evidence、speculative probe 和低相关普通导航分层；score 只在同层排序。Speculative 候选先覆盖 canonical host + locale-free route family，再延后裸域/`www` 与 locale alias；五次生产预算保留四类 route coverage 和一次最强 family concrete-host fallback，`subdomain_probe`/blind ATS 不会伪装成 evidence，两字母产品路径不会误作 locale。Trace 保存每次 selection phase，而不是被后续空 search 覆盖，并记录 tier/score/origin/host/locale/family role/truncation/remaining。M|R Walls focused live 的前五次从 `.53` 的近重复 localized URL 修正为 `/careers`、`/careers/jobs`、`careers.` 子域、brand join、`www /careers`，真实结果均为 404/DNS，继续诚实返回 retryable `FETCH_BUDGET_EXHAUSTED`，未增加公司特例。最终离线门禁为 826 tests、24/24 provider、6/6 resolver、23-adapter architecture gate / 0 issues；插件真实登录态验收继续 deferred。
- `.55` deterministic execution/replay workstream 完成：ADR-0007 将全部行为型 `AgentConfig` 冻结为 schema `1.0`，result `2.1`、trace、summary、bundle manifest 与 baseline identity 保存同一 canonical payload/digest；stage checkpoint `1.3` 按 pipeline execution fingerprint 隔离 discovery policy。Batch execution schema `1.0` 另绑定 company/website budget、fetch timeout/retry、render、verify 和 offline policy，batch completion `1.1` 与 baseline 同时使用两个 digest，短 timeout 不会污染后续长预算运行。Bundle schema `3` 从 source record 重建原始 agent 配置，混合/损坏/隐式 legacy provenance fail closed，旧 artifact 仅可显式标记 `legacy_defaulted`。成功或已验证 job-list replay 新增官网、招聘主体、career、job-list、exact-opening 与 provider 的 canonical identity gate；自动 failure/full replay 的 mismatch 或 fixture gap 均使 live gate 非零。`.54` clean frozen-30 为 30 website、27 career、26 verified job list、22 exact；相对 `.53` 的 career/job-list 各 -1 全部来自 Akkodis：上游证据可达但 fresh 运行撞到 45 秒 company deadline，下一轮作为通用完成结果发布/cancellation cluster 处理，不增加公司特例。60 秒 Akkodis focused live 恢复官方 Sitecore/Next job list；schema-3 replay 使用 source config 并对完整 URL/provider identity 达到 1/1 reproduced、0 gap、0 mismatch。最终门禁为 846 tests、24/24 provider、6/6 resolver、23-adapter architecture gate / 0 issues；真实登录态插件验收继续 deferred。
- `.56` process deadline/durable-publication workstream 完成：ADR-0008 将 hard budget 从“大对象 pipe 是否及时送达”改为“结果 envelope 是否在 deadline 前完整发布”。Worker 先成为独立 POSIX process-group leader，大结果通过受约束 `AttemptArtifactTransaction` 写入 destination-local temp、file fsync、atomic replace、directory fsync，pipe 只通知 readiness；共享 monotonic publication time 解决截止前完成但 IPC 延迟的假 timeout，序列化越界仍按 timeout，父进程在 timeout 和 final cleanup 终止整个后代树。Snapshot 的 blob、canonical view、artifact 和 sequence 全部在 JSONL index 前持久化；completion save 失败不会暴露 derived results。完整 stage checkpoint 继续按 execution identity 保留，避免下游失败破坏可复用上游证据。最终门禁为 859 tests、24/24 provider、6/6 resolver、23-adapter architecture gate / 0 issues。Akkodis 在原 45 秒条件下 34.5 秒完成 website/career/Sitecore job list/current exact opening；completion restore 为 1/1，8 fixture 的 schema-3 replay 为 1/1 reproduced、0 gap、0 mismatch。插件真实登录态 Scan/Run 继续 deferred。
- `.57` frozen-30 failure-cluster workstream 完成：S2 拒绝 Spaceship 停放模板，并让无直接身份来源的 non-apex fast candidate 等待 LinkedIn official evidence；S4 只在 tier 0-2 强证据未尝试时报告预算耗尽，纯 speculative truncation 返回确定性 not-found；S5 对官网实际可见、registry 可识别且 URL 本身等于 canonical listing root 的 ATS 链接执行 direct handoff，避免 Taleo 链接被 generic cap 截断，同时保留 Paycom redirect 和 Lever detail 的原验证路径；S6 修正 nested anchor 单词边界，并严格接受同源 career child `/job?jid=<stable-id>`。完整 native inventory 已验证 no-match 后停止 HTML query fallback。最终门禁为 871 tests、24/24 provider、6/6 resolver、23-adapter architecture gate / 0 issues；同一 frozen-30 从 `.56` 的 30/27/24/19 提升到 30/28/26/23，General Motors、Adobe、Zello、Awesome Motive exact，Percepta 恢复 Taleo job list。Akkodis 当前完整 Sitecore inventory 无目标标题，移除冗余 fallback 后 36.7 秒内输出 verified `OPENING_NOT_FOUND`，不再撞 45 秒 hard budget。自动 failure replay 为 5 reproduced / 2 mismatch：Percepta HTTP 500 与离线 invalid payload 的结果选择、Akkodis hard-timeout 与 checkpoint recovery 的 transition 分类成为下一 replay reliability cluster；插件真实登录态验收继续 deferred。
- `.58` replay-integrity workstream 完成：ADR-0009 与 failure bundle schema `4` 将 outer company budget interruption 的离线继续执行单独分类为 `budget_recovery`。只有原始 reason、完整成功前缀、严格后移的 failure stage、无 fixture gap 和已建立 identity 全部满足时 gate 才通过；source identity prefix 与 replay full identity 进入 manifest，普通改善和 identity drift 仍失败，显式 expected transition 同样受 identity gate 约束。Snapshot sanitizer 的 unquoted key 只匹配独立 JavaScript identifier，修复 `code` 误伤 Taleo `urlCode`，同时保留 standalone code、CSRF、token、cookie 和 API-key 脱敏。旧 `.57` failure artifacts 为 5 reproduced、1 budget recovery、1 不可逆旧 blob mismatch；新 Percepta 9-fixture capture 对 live Taleo `SERVER_ERROR` 达到 1/1 reproduced、0 gap、0 mismatch。最终门禁为 876 tests、24/24 provider、6/6 resolver、23-adapter architecture gate / 0 issues。Smart Bricks 已确认 first-party 链到 `app.whitecarrot.io/careers/smart-bricks`，但 capture 没有 WhiteCarrot page/API/detail evidence；下一轮先取得匿名公开 contract，再决定通用 adapter，不添加公司特例。插件真实登录态验收继续 deferred。
- `.59` WhiteCarrot workstream 完成：匿名公开证据冻结了 app tenant API、`/share/careers`、custom `*.whitecarrot.ai/jobs` SSR、detail UUID、API 400 missing tenant 与 404 closed detail 语义。原生 adapter 对 app tenant 使用一次性完整 `roles` inventory，对 custom host 使用强 Next/WhiteCarrot SSR item；两种模式均严格验证 origin/path/tenant/UUID/redirect，排除 Talent Pool、draft、跨站和 malformed records，不读取申请表或登录态。Smart Bricks 与 Whitecarrot 两种变体 focused live 2/2 exact，2-record sanitized capture 离线 2/2 exact replay。固定 provider benchmark 扩为 25/25，最终门禁为 891 tests、25/25 provider、6/6 resolver、24-adapter architecture gate / 0 issues。冻结 30-company 仍保留 `.58` 的 30/28/26/23，待下一次统一整批 live 再比较；插件验收按用户决定暂缓，主线转回剩余 failure cluster。
- `.60` canonical provider-board handoff workstream 完成：同一冻结 30-company `.59` 串行 live 为 30 website、28 career、27 verified job list、23 exact，较 `.58` 多 1 个 job list，但 Smart Bricks 被通用 opaque-path heuristic 误判成 detail，停留在 first-party generic listing。S5 现在以 adapter canonical equality 作为可见 provider board 的决定性边界；WhiteCarrot tenant board 可直接 handoff，而 Paycom/Lever detail 或 legacy URL 因 canonical identity 不等继续执行原有 fetch/redirect contract。Smart Bricks focused live 在 9.7 秒内 exact 命中 `AI Engineer`；6-record sanitized capture 物化为 5 fixtures、0 privacy exclusion，0.5 秒离线 exact replay。`ADAPTER_VERSION` 提升到 `2026-07-14.60`；最终门禁为 892 tests、25/25 provider、6/6 resolver、24-adapter architecture gate / 0 issues。下一轮先批量调查 `.59` 剩余 failure cluster，再统一重跑完整 cohort；插件验收继续暂缓。
- `.61` failure-replay resume workstream 完成：并行审计确认 Eightpoint、GPTZero、Viking、Aventis 均为正确产品终态，Percepta 为可确定性 replay 的真实 Taleo HTTP 500；不修改 matcher、provider contract 或招聘主体边界。Viking 的 fixture gap 来自 S6 replay 丢失 page-derived typed locator。Bundle 现在对 `page_evidence`/`page_probe` 从 S5 重跑并由 snapshot 重新验证 locator，URL-native handoff 仍从 S6 开始，不从 trace 重建运行时身份。`.59` failure replay 从 5 reproduced / 2 gaps 提升为 6 reproduced / 1 gap；唯一 Akkodis gap 保持 fail closed。最终门禁为 894 tests、25/25 provider、6/6 resolver、24 adapters / 0 issues；下一轮进入 hard-timeout durable-stage recovery。
- `.62` hard-timeout durable-stage recovery 完成：并行调查证明 `.59` Akkodis 实际已原子保存 S4/S5，真正超时点是 S6 Sitecore 第 8/9 页附近，但旧 parent fallback 固定重建成 S4 failure。Live runner 现在对 timeout/remote error 按 execution fingerprint 恢复连续、兼容的 success/not-applicable checkpoint prefix，遇到损坏、版本不兼容、语义非法或中间缺口立即停止；gap 后 URL/trace 不得泄漏，S7 failure 的 pipeline 语义保持 failed。Akkodis focused live 在 43.6 秒内完整读取 9 页、83 条 inventory，输出 `OPENING_NOT_FOUND`；16 fixtures、0 privacy exclusion，离线 outcome 1/1 reproduced、0 gap。`ADAPTER_VERSION` 提升到 `2026-07-14.62`；最终门禁为 900 tests、25/25 provider、6/6 resolver、24 adapters / 0 issues。下一轮在统一 30-company 回归前先评估 provider pagination soft-stop 是否需要独立 contract，避免只依赖 45 秒 hard kill。
- `.63` S4 transient-evidence classification 完成：同一冻结 30-company `.62` 统一 live 为 30 website、28 career、27 verified job list、23 exact，Smart Bricks 的 WhiteCarrot exact 增益被 Dematic 的一次官网 career fetch timeout 抵消；failure bundle 为 6 reproduced / 1 Akkodis mismatch。Dematic 上一轮完整命中 Workday exact，本轮首页仍明确给出 `/about/careers/`，但该 tier-1 page link 超时后旧逻辑错误输出 `CAREER_PAGE_NOT_FOUND`。Candidate failure 现携带 reason/retryable/origin/evidence tier，tier 0-2 的可重试失败会成为 S4 真实终态；纯 speculative probe 仍不能制造临时失败。Focused live 已验证 Dematic 改为 retryable `NETWORK_TIMEOUT`。`ADAPTER_VERSION` 提升到 `2026-07-14.63`；最终门禁为 901 tests、25/25 provider、6/6 resolver、24 adapters / 0 issues。Akkodis mismatch 进一步证明 S6 paginated provider 需要在 hard deadline 前 cooperative soft stop；该通用 contract 进入下一轮。插件真实登录态验收继续 deferred。
- `.64` cooperative pagination budget workstream 完成：ADR-0010 冻结可选 `FetchBudget` capability、timeout + publication reserve、Python 3.12 wrapper delegation、nonfinite fail-closed、partial inventory 与负向结论边界；`FetchClient`、result schema 和 checkpoint schema 均不改变。Sitecore/Next JobSearch 在后续页前执行 guard，reserve 不足时不发请求，保留正向 candidates 并输出 retryable `FETCH_BUDGET_EXHAUSTED`、`inventory_complete=false`。Guard 将被拒绝请求按 ADR-0006 保存为脱敏 terminal outcome，离线 replay 不使用 wall-clock 或 trace 重建。Akkodis 同配置 focused live 在 44.7 秒读取 8 页/80 of 83 records 后 cooperative stop，保留官方 job list、无伪造 opening；failure bundle 1/1 reproduced、0 gap、0 mismatch。最终门禁为 912 tests、25/25 provider、6/6 resolver、24 adapters / 0 issues。同一冻结 30-company 统一回归为 30/29/28/24，pipeline 24 success / 5 partial / 1 failed；相对 `.62` 的 website/career/job-list/exact 为 +0/+1/+1/+1，6 个 non-success 全部 reproduced、0 gap、0 mismatch。剩余结果由官方 empty、完整 no-match、未披露招聘主体、Taleo server error 和 Sitecore budget partial 构成，没有新的未知 provider cluster。CEIPAL、Workday 和 iCIMS 只在同类 failure cluster 或 provider fixture 证明收益后迁移，避免无证据的大范围行为变化。
- `.65` Taleo location-filter recovery 完成：公开 shell/JavaScript 证明 `searchjobs` 的 endpoint/method/header 与 adapter 一致，同时证明 `LOCATION` 是 OLF structured field，不保证接受 LinkedIn 自由文本。只有非空 location 请求返回 5xx 时，adapter 才向同一 tenant/portal 单次降级为 title-only；成功 inventory 继续由客户端 location 评分，其他错误保持 incomplete。分页 metadata 必须完整，page number、page size 与 total 在整段 inventory 中保持一致，否则 fail closed。Percepta 从两次稳定 HTTP 500 恢复为 `total=0` 的完整 title-filtered official inventory，15.9 秒输出 verified `OPENING_NOT_FOUND`；request-aware snapshot 通过 body fingerprint 区分失败 location 请求和成功 title-only 请求，离线 1/1 reproduced、0 gap、0 mismatch。该修复不增加 exact opening，也不解析 Taleo shell 的静态 no-results 占位文案。最终门禁为 919 tests、25/25 provider、6/6 resolver、24 adapters / 0 issues。
- `.71` redirect-aware same-run cache workstream 完成：代码审计确认 normal S4-S7 在同一 downstream child 中共享 fetcher，Adobe 重抓不是缺少跨进程 HTML contract，而是 S4 请求 `careers.adobe.com` 后跳转到 `/us/en`，旧 cache 只保存 request URL，S5 按 final URL 查询时 miss。Page cache 现在把 request/page/final URL 绑定到同一 bounded LRU entry，别名共同淘汰且只存在于当前公司进程；checkpoint resume、旧 snapshot 和 trace 均不能命中。并行审计发现并修复 resume preflight 的语义缺口：完整 checkpoint chain 必须能顺序应用到 fresh context，unsupported/malformed updates 或 success stage 缺少 required output 时从 S4 重建。Adobe focused live 为 1/1 website、career、job list、exact，44.8 秒得到 Workday `R168316-1`；capture 对 `careers.adobe.com` 仅 1 次 GET，无 final URL 重抓。全量门禁为 1004 tests、25/25 provider、6/6 resolver、24 adapters / 0 issues；`ADAPTER_VERSION` 为 `2026-07-14.71`。下一轮继续按 frozen cohort 的非插件 failure matrix 聚类，真实登录态插件验收保持 deferred。
- `.72` typed-board discovery completion workstream 完成：Akkodis 的 first-party landing 已通过 `page_evidence` 绑定 Sitecore typed board，但旧 S5 又用 URL-only detector 检查 customer-owned URL，误判 generic 后执行五次 blind ATS search，挤占 S6 pagination 预算。现在 listing-capable typed board 直接结束 S5；detection-only evidence 仍执行 fallback，Talemetry 也明确恢复 `supports_listing=false`。Architecture gate 分别约束两类 capability。复用 `.69` 已脱敏 landing fixture、其余 Sitecore API live 的 focused run 在 27.3 秒读取 9 页、83/83 records，无 `ats_search_fallback`，得到 verified `OPENING_NOT_FOUND`；同 snapshot 从 S4 离线 0.3 秒复现。两次完整网络尝试均在 S4/S5 landing fetch 超时，未冒充完整 live success。最终门禁为 1006 tests、25/25 provider、6/6 resolver、24 adapters / 0 issues；`ADAPTER_VERSION` 为 `2026-07-14.72`。插件验收继续 deferred，下一轮仍由非插件 failure matrix 决定。
- `.73` listing-first/cache/replay reliability workstream 完成：S5 对同站强 listing route 执行 asset/page-probe 之前的有界验证，visible canonical provider link 与纯页面 typed evidence 保持优先，失败后恢复原 fallback 且不重复 trace；Akkodis 不再为已声明的 job-results route 下载三个无关 Next bundle。Page cache 仅在内部 key 中统一 HTTP(S) origin 的空 path 与 `/`，使 redirect final-root alias 在同一 process 命中；跨 S1-S3/S4-S7 process 的第二次首页请求继续保留，不引入 durable HTML cache。Failure bundle 对缺少 diagnostic trace 的 `results.json` 使用 registry capability 推断是否必须从 S5 重验 page-derived board，不从结果或 trace 拼装 locator；`.72` 与新 `.73` frozen-30 的 results/trace replay 均为 30/30 reproduced、0 gap、0 mismatch。最终门禁为 1020 tests、25/25 provider、6/6 resolver、24 adapters / 0 issues。新串行 frozen-30 为 30/29/28/24，24 success / 5 partial / 1 failed，与 `.72` 持平；Akkodis S5 为 55ms，但 S4 16.3 秒网络波动后 S6 仅完成 70/83，按 reserve 保持 `FETCH_BUDGET_EXHAUSTED` partial。`ADAPTER_VERSION` 为 `2026-07-14.73`；插件真实验收继续 deferred，下一轮优先分析 S4 候选调度与跨 phase 证据成本，不猜 Sitecore location/page-size 参数。
- `.74` S4 explicit-list scheduling workstream 完成：frozen trace 证明 Akkodis 的同站 `job-results` embedded route 得分更高却因 tier/boost 不一致排在普通 careers link 后，先消耗一次 6 秒 timeout。Scheduler v2 只把 HTTPS 来源页声明的、HTTPS 同 concrete host、现有 scorer 已标记为 `explicit job-list route` 的 embedded candidate 提升到 first-party tier，并给予与 homepage career navigation 相同 boost；identity tier-0、跨 host、HTTP、普通 embedded reference 和 eligibility threshold 不变。Fresh focused live 第一候选变为 `/en-us/careers/job-results`，S4 10.1 秒、S5 57ms，S6 完整读取 83/83 后 verified no-match；focused replay 1/1，旧 frozen-30 replay 30/30，均 0 gap / 0 mismatch。对 provider-result reuse 的并行审计只找到 VELOX/Breezy 与 Kobie/Lever 两次 parse，底层 transport 已缓存且 location query contract 不同，因此不增加高风险低收益 cache。最终门禁为 1027 tests、25/25 provider、6/6 resolver、24 adapters / 0 issues；`ADAPTER_VERSION` 为 `2026-07-14.74`。下一轮继续评估 S4 stage-wide 总 fetch budget 与跨进程首页 evidence handoff，先冻结 contract 再实现；插件验收继续 deferred。
- `.75` S4 transport-budget workstream 完成：ADR-0011 将总网络上界从 candidate loop 提升为 `find_career_page` 整个 stage 的真实 delegate dispatch budget。Run-configuration schema 升至 `1.1`，CLI/live 默认 32、library 默认 unbounded；PageCache/Snapshot/Retrying/transport-counter 的顺序保证 cache hit=0、retry attempt=1、pre-dispatch reject=0，typed exhaustion 和 privacy-safe phase trace 不改变原 evidence/failure contract。Legacy `1.0` payload/digest 保持完全一致；replay composition 复用原 versioned config，修复 checkpoint seed 与 pipeline fingerprint 因静默 schema 升级而分离的问题。旧 frozen-30 replay 30/30；新 live 的 S4 最大 22/32、平均 3.96、0 rejection。整批原始结果 30/28/27/23，Dematic 唯一一次 tier-1 `NETWORK_TIMEOUT` 随后同配置 focused 16.1 秒 exact 恢复，证明不是 cap 回归；full replay 30/30、failure replay 7/7、focused replay 1/1，全部 0 gap / 0 mismatch。最终门禁为 1045 tests、25/25 provider、6/6 resolver、24 adapters / 0 issues；`ADAPTER_VERSION` 为 `2026-07-14.75`。跨进程 S2→S4 homepage evidence handoff 需要新的持久化/隐私 contract，留到独立 ADR；插件真实验收继续 deferred。
- `.76` checkpoint homepage-navigation handoff workstream 完成：ADR-0012 将跨 S1-S3/S4-S7 process 的复用限制为 execution-bound、URL-only typed evidence，不引入 durable HTML cache。S2 只从最终 verified `Page` 生成 exact homepage 与最多 8 个 query-free public HTTPS career/ATS candidates；context/checkpoint schema 分别升至 `1.2`/`1.4`，S4 exact-match 后仍逐 URL fetch/verify，缺失、损坏、不匹配或候选失败均恢复 legacy 路径。Frozen-30 中 21/27 个 S4 run 使用 handoff，20 个只 dispatch 1 次；总量从 107 降至 70（-34.6%），平均 2.59，最大仍为无 evidence 的 VELOX 22/32，0 rejection。首次完整 live 为 30/29/28/22：Suffolk 因可见 iCIMS board 的 presentation query 未通过 canonical equality 产生真实回归，Adobe 为 Phenom transient timeout，Akkodis 为既有 cooperative partial；不降低 expectations。通用 canonical path 修复后 Suffolk/Adobe focused 2/2 exact、2/2 replay；根 URL `""`/`"/"` replay alias 修复后完整 snapshot 30/30 reproduced、0 gap、0 mismatch，其他 path/query/request identity 继续严格。最终门禁为 1067 tests、25/25 provider、6/6 resolver、24 adapters / 0 issues；`ADAPTER_VERSION` 为 `2026-07-14.76`。完整 cohort 不用 focused 结果伪装重算，最后发布的 full baseline 仍为 30/29/28/24；插件真实验收继续 deferred。
- `.77` typed completion resume 与 snapshot terminal-outcome workstream 完成：ADR-0013 只自动重提完整 stage chain 中首个明确 `retryable=true` 的 non-success，明确 non-retryable 与不完整/歧义 metadata 均 fail-closed restore；失败 stage 及下游 checkpoint 定向失效，上游 checkpoint 和旧 completion 保留到新 completion 原子发布。Completion trace/summary 区分 restore、non-retryable restore、unclassified restore 与 retryable resubmit，crash stale tmp 只在同 fingerprint lock 内清理。Career search 对单次 invocation 内明确 non-retryable 的来源熔断；frozen live 中 Kobie search dispatch 从 6 降到 4，但整批 S4 dispatch 受 blind-ATS 变化影响仍为 70。Snapshot page/failure 共享 sequence 现在按完整 request identity 只物化最新终态，Kobie 和 Akkodis replay mismatch 被通用修复。Frozen-30 首轮 30/28/27/22 后，第一次 resume 为 restored 27 / retryable 3，Dematic 恢复 exact；第二次为 restored 28 / retryable 2，Adobe 恢复 exact，Akkodis 保留 retryable partial。最终 full baseline 为 30/29/28/24，6/6 failure replay、30/30 full replay、0 gap/0 mismatch；门禁为 1081 tests、25/25 provider、6/6 resolver、24 adapters / 0 issues，`ADAPTER_VERSION=2026-07-14.77`。下一持久化工作项是 attempt/stage-scoped snapshot evidence lineage：为 capture outcome、stage checkpoint 和 completion 冻结 scope，避免新 attempt 在某 stage 未产生 outcome 时混用旧 attempt；在此之前 replay mismatch 继续 fail closed。真实插件验收按用户要求 deferred。
- `.78` attempt/stage-scoped replay workstream 完成：ADR-0014 将一个 company invocation 的 opaque attempt ID 贯穿 S1-S3 与 S4-S7 process phase，每个执行 stage 发布 execution-bound `StageEvidenceLineage`；恢复 checkpoint 保留旧 producer，重算后缀使用新 producer，completion 因此可冻结合法 mixed-attempt chain。Snapshot v3 为每个 terminal page/failure 保存 exact scope membership 和 stage-local ordinal，scope 以 count、sequence bounds 与 privacy-safe descriptor digest finalize；cache hit 作为消费 stage outcome 记录，zero-request stage 不借旧证据。Strict outcome tape 按 ordinal 消费并拒绝 missing/early/extra/unconsumed/divergent request；bundle v6 为每个 source occurrence 隔离 record ID、checkpoint root、application、runtime cache 和 cursor，outcome gate 按 record ID join。Legacy v1/v2 snapshot 只允许显式 bundle v5 `legacy_global_latest`，不得与 scoped selection 混用。Context/checkpoint/completion schema 分别为 `1.3`/`1.5`/`1.2`，`ADAPTER_VERSION=2026-07-14.78`。真实 `SIGKILL` acceptance 已验证 S1-S4 从旧 attempt 恢复、S5-S7 新 attempt 重算、未 finalize S5 evidence 保持 orphan 且 bundle v6 只消费 durable references。最终门禁为 1138 tests、25/25 provider、6/6 resolver、24 adapters / 0 issues；产品 frozen-30 baseline 仍为 30/29/28/24，本轮不虚报 live 增益。下一轮回到陌生样本 failure cluster 与 provider hardening；插件真实登录态验收继续 deferred，不阻塞后端主线。
- `.79` regional board/provider hardening workstream 完成：S5 contract 传递 target location，leading language-country locale 产生 typed region；匹配 region 优先、中立 URL 可 fallback、明确冲突 URL 在 direct provider、page evidence、visible provider、portal、traversal 和 ATS search 统一排除，匹配板的 retryable failure 不会被错误地区掩盖。Sitecore Next 允许 record brand 匹配页面主品牌或 page-bound dictionary brand，language 只比较 primary subtag，country/tenant/ID/duplicate/mixed-page 继续严格。Akkodis 90 秒 focused live 在 45.2 秒完成：选择 U.S. board，排除 Belgian board，读取九页 83/83 official records，目标 LinkedIn title 不存在，因此 verified `OPENING_NOT_FOUND`；fresh bundle v6 为 1/1 reproduced、0 gap、0 mismatch。首次 fresh capture 还暴露 scoped replay 用 `read_text()` 归一 CRLF 导致假 digest corruption；现改为复用 byte-validation 已解码的 exact UTF-8 text，并加入 CRLF 回归。`ADAPTER_VERSION=2026-07-14.79`；最终门禁为 1148 tests、25/25 provider、6/6 resolver、24 adapters / 0 issues。Frozen-30 baseline 仍为 30/29/28/24；下一轮继续按其陌生样本 failure cluster 排序，插件验收继续 deferred。
- `.80` regional-hub/replay-determinism workstream 完成：S5 只允许 S4 已验证且非 provider/detail 的初始 career root 作为跨地区导航 hub，所有下游 board、listing、detail 和 candidate 继续执行 target-region gate；Dematic 可从官方 Australia landing 找到 U.S. Workday board，但 Australia board 不会被提升。Bundle v6 从 authoritative S1-S4 records 重建 original source、website 和 career-root 输入语义，避免 exported output 倒灌成 trusted input；执行结束后再附加 replay provenance。Scoped outcome tape 以完整 request identity 和原子 consumed state 支持独立并发请求换序，同时保持重复 identity 的 capture order，任何 missing/extra/mismatch/unconsumed 继续 fail closed；trailing-dot fixture path 在 Python 3.10-3.14 间保持一致。Fresh frozen-30 为 30 website、29 career、27 verified job list、23 exact；full scoped replay 30/30 reproduced、0 gap、0 mismatch。Dematic 同配置 retry 及 focused replay 1/1 exact，首轮 timeout 仍诚实保留。最终离线门禁为 1159 tests、25/25 provider、6/6 resolver、24 adapters / 0 issues；真实登录态插件验收按用户要求继续 deferred。
- `.81` production-gate/evaluation/snapshot reliability workstream 完成：固定 25-case provider benchmark 不再直接调用 legacy agent，而是通过 production `PipelineApplication`、S1-S7、fetch wrapper、execution fingerprint、run configuration 和 evidence lineage；25/25 exact/expectations 保持通过。Evaluation 为每家公司和 failure-cluster company 生成唯一 durable terminal semantic，只读 typed stage result/evidence，不让中间 trace error 覆盖最终证据；summary/report/baseline delta 可区分 exact、verified no-match、no-public-openings、identity ambiguity、retryable、external block、unsupported、replay infrastructure 和 unresolved discovery。Legacy snapshot CLI 对 v3-only/mixed scoped index 在写入前输出 `SCOPED_SNAPSHOT_REQUIRES_BUNDLE_V6` 并非零退出。Fresh frozen-30 为 30/29/27/24；typed completion resume 恢复 Akkodis 后为 30/29/28/24，Percepta 仍保持明确 Taleo/network retryable。Final semantics 为 24 exact、3 verified no-match、1 no-public、1 identity-ambiguous、1 retryable；full scoped replay 30/30 reproduced、0 gap、0 mismatch。`ADAPTER_VERSION=2026-07-14.81`；最终门禁为 1168 tests、25/25 production provider、6/6 resolver、24 adapters / 0 issues。下一优先级是 URL/provider/tenant/opening identity regression gate、CLI checkpoint-prefix parity 和新陌生 holdout cohort；插件真实验收继续 deferred。
- `.82` result-identity/checkpoint-prefix/unfamiliar-holdout workstream 完成：ADR-0015 允许 expectation 冻结非空 identity 子集，并对声明的 website、career、provider、public URL tenant、board URL 和 opening URL 严格比较；company identity matrix、field drift 和 bounded report 让 aggregate rate 无法掩盖 URL/company swap。CLI、live resume、typed completion retry、显式 rerun和 parent-timeout recovery 统一使用连续 authoritative checkpoint prefix；replay input 不再绕过 gap，rerun 在写入前失败，完整恢复显式覆盖 S1-S7。新 15-company title-only holdout 在运行前冻结 official opening URL；最终为 13/15 website、12/15 career、8/15 verified job list、7/15 title-matched opening，但 strict exact identity 只有 6/15。首轮暴露的 P-1 AI→P&G 与 CyberArk→Palo Alto 两个 cross-company false positive 通过 body-only incomplete identity veto 和 Workday auxiliary-route rejection 降为诚实未发现，错误公司 success 为 0；ATS second-source fallback、受约束 acronym/TLD candidate 和 explicit first-party jobs portal priority 均无公司分支。`ADAPTER_VERSION=2026-07-14.82`；最终门禁为 1217 tests、25/25 provider、6/6 resolver、24 adapters / 0 issues。下一轮优先按剩余 S2 acronym verification、S5 provider recall 和 multi-board scope cluster 继续提高 strict unfamiliar success；插件验收作为独立手工 gate。
- `.83` direct-evidence/S2/S5 command workstream 完成：ADR-0016 将 candidate provenance 从 trace 标签提升为执行顺序 contract，先验证 preferred/LinkedIn-official wave；恰好一个候选通过完整 homepage/identity/redirect/parking gate 后不再等待 speculative guesses，直接证据失败或冲突时仍恢复原有有界 fan-out。短品牌标题只新增受限 legal-entity suffix identity；机构 fallback 只接受 4 字符以上、全词首字母、精确 `.edu` 且主机返回 401/403，DNS/timeout/404/其他 TLD/短缩写和更强冲突证据继续拒绝。S5 portal promotion 与 link scoring 共用 job-list command taxonomy，补齐 `find jobs/roles` 和 `explore jobs/roles`，仍要求 HTTPS、同 registrable site 和 jobs/careers/apply 子域。Focused live 中 RIVR 与 Atira 均恢复 exact，Bosch 恢复官方 job list，SNHU 恢复官网但因全站 403 和搜索无有效候选诚实停在 S4；没有公司特例。`ADAPTER_VERSION=2026-07-14.83`；最终门禁为 1226 tests、25/25 provider、6/6 resolver、24 adapters / 0 issues。下一轮先冻结 Visa multi-board portfolio 的 board-local completeness/attempt cap/checkpoint contract，再单独冻结 Hostinger first-party literal same-origin GET inventory 的 SSRF、payload、snapshot 和 replay contract；插件手工验收保持独立。
- `.84` multi-board/fail-closed workstream 完成：ADR-0017 冻结 `JobBoardPortfolio` 的 typed identity、eligible-set completeness、1-8 board 上限、versioned attempt cap 和 all-or-nothing checkpoint。S5 只在主板与目标 title 受众冲突时搜索替代板，每个候选必须由 listing-capable native adapter 验证；搜索错误、熔断、provider failure 或 fetch cap 都使集合 incomplete。S6 只有在全部 eligible board 完整检查后才能输出 company-wide empty/no-match，未尝试板输出 `JOB_BOARD_PORTFOLIO_INCOMPLETE`；单个 complete board 保留旧 trace/evidence。Workday/SmartRecruiters 新增 public URL 与 identifier 严格绑定的 replay-safe policy。Run config/context/checkpoint schema 为 `1.2`/`1.4`/`1.6`，`ADAPTER_VERSION=2026-07-14.84`。Visa focused capture 到达官方 general Workday 与 exact `Sr Data Scientist`，同时发现 S2 拒绝 website-only 输入后 context 仍可残留该 URL；现对无 career-root/external-apply 的 S2 failure 清空 handoff，禁止下游绕过。最终门禁为 1249 tests、25/25 provider、6/6 resolver、24 adapters / 0 issues。下一后端项是 Hostinger first-party dynamic inventory；插件真实验收继续独立手工执行。
- `.85` first-party dynamic inventory/title-identity workstream 完成：ADR-0018 冻结 literal GET、exact-origin HTTPS `api`/`api-proxy`、3 page assets + 1 static dependency、5 MB / 5,000 rows、all-or-nothing same-native-board、typed fetch failure、explicit empty、privacy-safe trace 和 request-aware replay contract。Opaque Nuxt chunks 按声明尾部优先，避免部署 hash 的字母顺序决定 recall；`api-proxy` 只合成 `Bearer <page hostname>`，从不读取或转发 bundle Authorization，因此清洗后的 scoped bundle 仍可消费同一请求。Hostinger live 从 `JOB_BOARD_NOT_FOUND` 改为 verified Ashby board，完整读取 77 条当前岗位；首次 S6 将 `AI Engineer` 错配为 `Full Stack Engineer (Automation & AI Agents)`，现增加 ordered title identity / equal normalized multiset gate，在保留 `AI Algorithm Engineer Intern` 与 `Sr`/`Senior` 合法变体的同时拒绝该 false positive。最终 live 为 website/career/job-list 成功、`OPENING_NOT_FOUND` verified no-match；fresh scoped replay 同结果、0 fixture gap / 0 divergence。`ADAPTER_VERSION=2026-07-14.85`；最终门禁为 1266 tests、25/25 provider、6/6 resolver、24 adapters / 0 issues。冻结 15-company strict exact baseline 仍为 6/15，必须整批重跑后才能更新；插件真实登录态验收继续作为独立手工 gate。
- `.86` upstream-terminal replay/search fallback workstream 完成：ADR-0019 允许 allowlisted original source + scoped lineage 的 company-only terminal result 进入 bundle-v6，并把 lineage 最后 stage 作为 replay `stop_after`；完整性门禁仍要求 source/filter/selection/export/result/trace/comparison 全等，未知 source、缺失或非连续 scope 均拒绝。Replay summary 分开报告 matched/selected/exported/replayed。冻结 15-company clean rerun 为 13 website、12 career、11 verified job list、7 current opening、7 pre-frozen exact；完整 capture 15/15 reproduced、0 gap、0 mismatch。S4 对 RSS 有 raw 结果但零有效 candidate 的情况在预算内继续 Bing HTML，已有有效 RSS candidate 仍停止。最终门禁为 1271 tests、25/25 provider、6/6 resolver、24 adapters / 0 issues。下一轮优先冻结 Bosch e-Spirit CaaS inventory 和 CyberArk acquired-brand portal handoff contract；P-1/Visa 的输入证据与 regional identity 作为独立 S2 workstream，三个 Ashby no-match 不放宽标题门禁。`ADAPTER_VERSION=2026-07-14.86`；插件真实登录态验收继续手工执行。
- `.87` runtime-provider/acquired-brand workstream 完成：ADR-0020 冻结 e-Spirit CaaS page-bound public credential、origin-pinned request、HAL pagination 和持久化隐私边界；ADR-0021 将 acquired-brand handoff 限制为已验证 career root 中同一可见容器的明确关系与 `Search All Jobs` 命令，只在 S5 probe 一个 parent portal，不改变 S3 identity；ADR-0022 冻结 TalentBrew tenant/site/CDN 指纹、localized SSR search、分页完整性和 replay-safe locator。Bosch focused live 通过 e-Spirit CaaS 精确命中 `Data Scientist` opening 并完成 replay。CyberArk 进入 Palo Alto Networks TalentBrew job list，完整读取 148 条过滤记录后确认没有 exact normalized `Data Scientist`，拒绝错误的相近 parent-company role，输出可信 `OPENING_NOT_FOUND`；scoped replay 1/1。`ADAPTER_VERSION=2026-07-14.87`；最终门禁为 1318 tests、25/25 provider、6/6 resolver、26 adapters / 0 issues。下一轮回到冻结陌生样本的最大 failure cluster；插件真实登录态验收继续作为独立手工 gate。
- `.88` replay-preflight/S2/S4 reliability workstream 完成：ADR-0023 要求 scoped replay 在写 input、checkpoint 或启动 pipeline 前验证 effective start 后存在连续 capture boundary；active stage 超时但尚未 finalize scope 时输出原子 `replay_plan_integrity_failed` manifest 和 bounded record diagnostics，live runner 保留完整 summary 后正常 gate failure，不再 traceback。S2 仅把 title 读取范围扩展到最多 64 KiB 的 `head`，普通 identity token 仍限制在前 5 KiB；Visa 因此稳定验证 regional homepage，不降低短品牌门槛。S4 search candidate 与 homepage candidate 共用 target-title audience mismatch；ATS portfolio 先执行一次 provider-neutral careers 查询，再执行 provider-specific bounded queries。Fresh frozen-15 为 14/12/12/8，较 `.86` 为 +1/+0/+1/+1；full outcome replay 15/15。P-1 继续 evidence-insufficient，SNHU/Visa 在当前公共证据下停于 S4，CyberArk verified no-match，Bosch exact；Hostinger 本轮 provider fetch failure 保持 retryable。`ADAPTER_VERSION=2026-07-14.88`；最终门禁为 1322 tests、25/25 provider、6/6 resolver、26 adapters / 0 issues。下一后端轮先以新的陌生样本或冻结 30-company failure cluster 判断是否实现 Talemetry inventory，不能因 provider coverage 指标单独立项；插件真实登录态验收继续独立手工执行。
- `.70` verified-career-root classification workstream 完成：`.69` 统一 frozen-30 live 为 30/29/27/23，Akkodis 38.3 秒 cooperative partial、无 hard timeout；Adobe 在队尾因 S5 重抓已验证 career root 超时而错误输出确定性 `JOB_BOARD_NOT_FOUND`。S5 现在把该 root 作为 tier-0 `verified_career_page` evidence，保留 typed retryable reason；同一 snapshot 定向 replay 稳定输出 `NETWORK_TIMEOUT`、0 fixture gap，并由 gate 显式报告 source outcome transition。后续页面复用必须保持 same-run ephemeral 或另行冻结 durable contract，不能用 trace 或旧 snapshot 偷渡页面。`ADAPTER_VERSION` 提升到 `2026-07-14.70`；离线门禁为 1000 tests、25/25 provider、6/6 resolver、24 adapters / 0 issues。真实登录态插件验收继续 deferred。
- `.69` frozen-cohort reliability workstream 完成：schema-5 full replay 对 source/filter/selection/export/result/trace/comparison 数量做 30/30 完整性门禁；baseline identity 绑定 observed total，partial summary 不能冒充完整 cohort。Adobe 暴露的通用簇被拆为 S5 retryable evidence classification、Phenom landing typed handoff 与 deadline-aware search；Akkodis 暴露的通用簇被拆为 Sitecore 首次/分页统一 reserve、native budget interruption 禁止 generic fallback，以及 parent timeout durable-stage provenance。`.68` frozen live 是 30/29/27/23；同 snapshot `.69` replay 为 30/29/28/23，Adobe 已恢复 official Phenom job list。新的 Viking/Akkodis/Adobe focused live 为 3/3 official job list、0 hard timeout；Viking 是 verified no-match，Akkodis 是 cooperative budget partial，Adobe 是 retryable network partial；完整新 capture replay 为 3/3 reproduced、0 gap、0 mismatch。`ADAPTER_VERSION` 提升到 `2026-07-14.69`；离线门禁为 999 tests、25/25 provider、6/6 resolver、24 adapters / 0 issues。真实登录态插件验收继续 deferred。
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
- provider-specific ATS 能力已迁移到自动发现的独立 registry/adapter modules，共 26 个 module；25 个提供 inventory/positive evidence，仅 Talemetry 为 detection-only
- Greenhouse、Lever、SmartRecruiters、Workday、Ashby、BambooHR 已接 structured API
- iCIMS、SuccessFactors、Workable、Rippling 已加入原生 structured page / embedded JSON / verified-link extraction，但还需要更多真实站点 live hardening
- browser fallback 已经从全量渲染升级为 smart fallback + render budget
- batch evaluator 已经能输出 results / trace / summary，固定离线 benchmark 可作为回归测试

最诚实的当前状态：

> 七关状态模型、统一错误码、benchmark 矩阵和 SOLID 并行开发架构已完成第一版。Correctness stabilization 已实现 S3-S7 typed opening identity continuity、S7 fail-closed validation、complete-inventory availability、typed fetch failure fidelity、六类 evaluation disposition、安全 annotation merge 和 identity-aware replay outcome gate，没有新增 provider 或公司特例。一次冻结但已观察的 30-company live 为 19 exact、19 success / 5 partial / 6 failed；独立 artifact review 得到 exact precision 19/19、conditional exact recall 19/24（另 2 条 eligibility unknown）、system defect 7/30。完整 scoped replay 为 30/30 reproduced、0 gap、0 mismatch。该结果不能称为 blind，下一轮应先冻结陌生 holdout；provider/heuristic 扩张继续暂停。
