# Beta Demo Script

Audience: 李凯 / engineering reviewer

Target length: 3-5 minutes

Product code: `2026-07-29.286`
Latest authoritative full Fresh100 measurement: `.283`

## Before The Demo

From the repository root, use CPython 3.12 and run the deterministic offline
demo once:

```bash
make beta-demo PYTHON=python3.12
```

Keep these files open before presenting:

- `README.md`
- `docs/ARCHITECTURE.md`
- `/tmp/ai-job-source-agent-beta-demo/results.json`
- `/tmp/ai-job-source-agent-beta-demo/trace.json`

This is a deterministic product walkthrough, not a live benchmark. Do not run
a large public-web batch during the presentation. If the network is stable, an
individual public input may be shown separately and labelled as a live example.

## 0:00-0:35 - Problem And Product Boundary

Show the README and say:

> 这个项目解决的不是“搜索网页里有没有相似链接”，而是从 LinkedIn 职位出发，找到公司真实使用的招聘系统，并返回经过验证的具体开放岗位 URL。开放网页很脏，品牌可能由母公司招聘，招聘页也可能在 Workday、Greenhouse、Lever 等第三方系统，所以错误 URL 比没有结果更危险。
>
> 我把产品边界设成 fail closed：证据链不完整时返回结构化 partial、blocked 或 rejected，不猜测岗位 URL。

Point out that the backend accepts either LinkedIn discovery or pre-extracted
company/job records. The authenticated Chrome extension is an accepted input
client, but keep the deterministic backend as the primary presentation path so
the demo does not depend on LinkedIn DOM or network state.

## 0:35-1:25 - Architecture

Show the architecture diagram in `docs/ARCHITECTURE.md` and say:

> 候选发现有三个入口：LinkedIn 可见的 External Apply、官网或 Career 页面中的 ATS 链接、以及面向 ATS 的定向搜索。三路只负责提高召回，不能直接宣布成功。
>
> 所有候选进入统一候选池，经 Provider Adapter 识别 provider、tenant、canonical job board 和官方岗位库存。最后 S7 重新检查公司或招聘主体、provider、tenant、title、location 和 opening URL 的连续性。任何一段对不上就拒绝发布。

Emphasize three design decisions:

1. Candidate ranking controls inspection order, not correctness.
2. Provider adapters own provider-specific parsing; the shared pipeline does
   not accumulate company-specific branches.
3. The trace records why each stage accepted, rejected or stopped.

## 1:25-2:35 - Run The Product

Show the command above, then open
`/tmp/ai-job-source-agent-beta-demo/results.json`.

Say:

> 这是与产品相同的七阶段后端，只是网络响应由本地 fixture 提供，所以现场演示不会受限流、页面变化或网络波动影响。输入包含 LinkedIn 岗位、公司、职位名和地点；输出分别给出官网、Career、Job List、具体 opening，以及每个阶段的状态和原因码。

Use Aurora Data as the Exact result and point out:

- source company, target title and location;
- verified Career and Job List;
- concrete `open_position_url`;
- `pipeline_status` and the seven structured stage results.

Then use Nimbus Robotics to show a deliberate `RESULT_IDENTITY_MISMATCH`: the
Career/Job List can look plausible, but S7 keeps `open_position_url` empty.

Open `/tmp/ai-job-source-agent-beta-demo/trace.json` and say:

> Trace 不是调试日志拼接，而是可审计证据：它记录候选来源、选择理由、provider 身份和阶段边界。相同 snapshot 可以进入 replay，检查代码是否在同一证据上产生相同终态。

For the rejected result, say:

> 这里系统没有足够证据，因此明确返回对应的非成功终态，并保持 opening URL 为空。这就是 fail closed，而不是“找一个看起来像的链接”。

Do not imply that the two offline fixtures prove public-web coverage. A separate
`.286` focused live acceptance completed 7/7 public records on 2026-07-31: three
S7 Exact results passed a 3/3 measurement-bound identity audit, one record was a
verified no-match, one stopped at external inventory failure, and two stopped in
discovery. Show `docs/BETA_DEMO_EVIDENCE.md` if asked; do not rerun the live set
during the presentation or report 3/7 as a general success rate.

## 2:35-3:35 - Measurement And Correctness

Show the measurement section of the Beta README or project summary and say:

> 当前普通产品代码是 `.286`。最新一次完整 Fresh100 冷启动测量仍来自 `.283`，因为 `.286` 没有获批重新跑完整批次，所以我没有把不同版本混成一个成绩。
>
> `.283` 的 100 条开发样本中，系统找到 92 个官网、79 个 Career 页面、71 个已验证 Job List 和 36 个 raw Exact。序列化证据中没有发现错误岗位 URL、错误地点、跨公司或跨 tenant 发布。严格 replay 是 99 条 reproduced、1 条 mismatch。
>
> 这个结果说明安全边界有效，但召回率仍然不足。36 条也不是正式 precision 或 eligible recall，因为这批 development cohort 没有完整终态人工标注，其中两条 Exact 的身份路径仍是 provisional。

Then distinguish the historical baseline:

> Frozen100 在它自己的历史版本上曾达到 69 Exact 和 100/100 replay。这是历史基线，不是 `.286` 的当前回归证明，我不会把它与 Fresh100 合并宣传。

## 3:35-4:20 - What I Learned And Where It Goes Next

Say:

> 这项工作的主要工程难点不是写更多 selector，而是处理招聘主体变化、ATS tenant 隔离、动态库存、网络失败分类以及可回放性。我也意识到前期在提高覆盖率上投入过多，没有足够早地冻结 Beta 边界。
>
> 如果继续产品化，我会优先做两件事：第一，在更大的登录态样本上测量 External Apply 目标 URL 覆盖和净新增 Exact；第二，基于新的陌生样本按共同根因补 provider family，而不是针对单家公司增加 heuristic。当前交付先停在一个能运行、能审计、不会编造 URL 的 Beta。

## Closing Line

> 我希望这份提交展示的不只是一个搜索脚本，而是我如何把不可靠的开放网页转化成有边界、有证据、可测试的工程系统。当前召回率还有空间，但系统会明确知道什么时候不能给答案。

## Questions To Expect

**为什么 Exact 只有 36/100？**
开放网络中的候选生成、动态库存和匿名访问仍有明显缺口。系统没有通过放宽公司、tenant、title 或 location 来换取更高数字，因此很多记录保守停在 partial 或 unresolved。

**为什么不直接相信搜索结果？**
搜索摘要会过期、混入同名公司或错误 tenant。它只能产生候选，最终必须读取官方 provider 页面或库存并通过 S7。

**为什么离线 demo 有意义？**
它证明同一产品 pipeline、adapter、identity gate 和输出 contract 可以确定性运行；它不证明实时网页覆盖率，真实覆盖由独立 live measurement 报告。

**插件现在能用吗？**
可以。`0.4.0` 已在真实登录态 LinkedIn 完成单岗位扫描、25/25 整页扫描、提交、运行态、弹窗重开恢复和结果渲染验收。当前 `0.6.2` 进一步自动连接本地 bridge、恢复已扫描记录，并把多岗位验证显示成 `Running 7/25` 这类真实进度；同一批最多四家公司并发。DOM 暴露安全 External Apply 目标时插件直接显示超链接；只有按钮而没有 URL 时会明确说明 LinkedIn 未暴露目标，不会猜链接。这证明工作流可用，不代表 External Apply URL 覆盖率或 Exact 成功率。
