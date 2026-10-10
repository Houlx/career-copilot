# Phase 3.5B — Business Live Evaluation

日期：2026-10-10（Asia/Shanghai）。**未完成：Step A 的原生 extraction mapping 存在接口能力阻塞。** 本次仅提交阻塞记录；没有实现 mapping、live eval CLI 或运行 benchmark，不应作为阶段验收通过。

## Verified capability boundary

核对 [TypeSafe 官方 API](https://docs.typesafe.ai/api) 和 [文档索引](https://docs.typesafe.ai/llms.txt)：`POST /v1/systemone` 的 state/questions 接口公开支持三种 question：

- Noul：返回 yes/no 概率。
- Choice：从调用方提供的 criteria 中选择一个选项；最多 255 个选项。
- Score：对调用方提供的有序等级返回概率加权分数。

这些 question 没有任意文本或动态条目数组的 extraction 输出类型。instructions/criteria 接受结构化 JSON，并不意味着 answers 可以生成任意 JSON schema。

[官方 pre-parsed value extraction 示例](https://docs.typesafe.ai/cookbooks/pre_parsed_value_extraction_cookbook) 先用 Python regex 找候选值，再用 Choice 选择，最后由 Python 复制原始 span。这是候选提取加判定流程，不能直接视为当前两个生成式 extraction task 的等价 provider 映射。

## Existing contracts and requested mapping

| Task | Requested provider | Existing output | Status |
| --- | --- | --- | --- |
| profile-facts-v1 | Jev | 动态 facts 数组；type/title/content/source_quote | 无已验证的等价 native mapping |
| jd-extract-v1 | Jev | title 和动态 requirements 数组；text/importance/category/scoreability | 无已验证的等价 native mapping |
| profile-normalize-v1 | OpenAI-compatible GLM | entries | 现有 adapter 支持 schema；本次未 live 验证 |
| matching-v1 | OpenAI-compatible GLM | matches | 现有 adapter 支持 schema；本次未 live 验证 |

现有 Jev adapter 仍只支持 judge-diagnostic-v1，业务 tasks 在发送数据前返回 MODEL_OUTPUT_INVALID。没有增加不存在的 text/array question 类型，没有让 Jev 使用 chat/completions，没有使用 reviewed reference 构造答案候选。

若先用其他模型生成候选，会新增调用并改变业务节点分工；若先用 Python 分句、选 span、复制字段，会引入新的提取策略及召回边界。两者均不能声称是在本阶段“不改业务语义”下得到的原始任务 baseline。需要支持开放文本/数组提取的原生接口资料，或者用户明确调整任务分工/候选提取范围后再继续。

## Execution and results

按要求的 A → B → C → D → E 顺序，Step A 阻塞后未进入后续步骤。

- 本次真实 Provider requests：单 case **0**，suite **0**，合计 **0**；无 retry。
- 无本次 benchmark model version、质量指标、token totals、latency 或 eval result 文件；未执行的结果不可记录为 0 分或成功。
- 现有 datasets 保持不变：Profile 10、JD extraction 12、scoreability 8、matching 5 个 synthetic reviewed cases。
- 未读取业务 DB，未执行 Profile/Job graphs，未发送真实用户数据，未修改 prompt/schema/validator/fixture。
- 仅文档变更；未重跑自动测试、frontend checks 或 offline eval。历史 Phase 3.5A 验证结果见 PHASE_3_5A.md，不能充当本阶段验证。
- 未开始 Phase 4。

## Next step

先解决 Step A 的接口能力与任务 contract 差异，再按原授权实现 mapping、双 live gate、显式 suite/case/request budget、独立结果文件及 trace，完成 mock/regression 后才逐项进行四个单 case。不能用 Phase 3.5A diagnostic smoke 成功推断业务 extraction 可执行。
