# Message To Li Kai

凯哥你好，不好意思这次收尾比我预期花了更长时间。我已经把项目整理成一个可运行、可审查的 Beta，并把代码、README、架构说明、测试证据和演示流程一起提交了。

这版不再只是从官网找 Career 页的脚本：后端会从 LinkedIn External Apply、官网/Career 探索和 ATS 定向搜索三路产生候选，再通过 Provider Adapter 读取官方库存，最后用 S7 核对公司或招聘主体、provider、tenant、职位名、地点和具体 opening。证据不足时系统会 fail closed，不会猜测 URL。

我也想如实说明当前边界：普通产品代码已经到 `.286`，最新完整 Fresh100 冷启动测量仍是 `.283` 的 36/100 raw Exact、92 个官网、79 个 Career 页面、71 个 verified Job List；已发布结果的序列化证据中没有错误岗位 URL、错误地点、跨公司或跨 tenant，但 strict replay 是 99/100，召回率仍然不足。历史 Frozen100 的 69 Exact 和 100/100 replay 属于旧版本基线，我没有把它当作当前回归成绩。LinkedIn 登录态插件已经完成单岗位、25 条整页扫描、异步提交、弹窗重开恢复和结果渲染验收；当前样本只暴露了站外 Apply 按钮，没有暴露安全目标 URL，所以系统明确报告 target unavailable，没有把按钮冒充成岗位链接。

为了确认交付版本今天仍能跑，我另外做了一次 7 条公开样本的 focused live 演示验收，得到 3 个 S7 Exact，覆盖 Ashby、Greenhouse 和 Workday；3 条 Exact 的公司、tenant、title、location 和 opening 审计全部通过。这个小集合只作为演示证据，不当作泛化成功率。

这次我会把能稳定演示的能力、架构取舍和已知限制都直接展示出来。也谢谢你之前提醒我不要只停留在 AI coding 工具层面；这个过程让我真正碰到了系统边界、身份验证、可回放测试和产品取舍的问题。

GitHub：https://github.com/yinh2025s/ai-job-source-agent

源码包也随消息附上。
