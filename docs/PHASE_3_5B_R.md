# Phase 3.5B-R — Model Responsibility Refactor

日期：2026-10-10（Asia/Shanghai）。**实现及离线 regression 完成；阶段验收未完成，live Step 1 失败后已停止。** 未开始 Phase 4。

## Responsibility and contracts

| Task / prompt version | Role | Live provider | Contract |
| --- | --- | --- | --- |
| profile-facts-v1 | generation | OpenAI-compatible GLM | 原 ProfileFacts，不改 prompt/grounding/fixtures |
| profile-normalize-v1 | generation | OpenAI-compatible GLM | 原 NormalizedProfile |
| jd-requirements-v2 | generation | OpenAI-compatible GLM | title + requirements(text/importance/category)，禁止 scoreability |
| scoreability-v1 | judge | Jev native Choice | ScoreabilityResult：requirement_index + 三值 scoreability |
| matching-v1 | generation | OpenAI-compatible GLM | 原 Matching，全量 active Evidence |

Python 继续负责 requirement IDs、validation、原有四词 safety guard、Evidence 引用和 Coverage。没有扩展 guard，没有迁移 importance/category/matching/grounding judgment。

`profile-facts-v1` 只更改 role；其 prompt 文本、schema、validators、replay 输入/输出不变。旧 `jd-extract-v1` prompt、schema 及所有历史 replay 文件保留原样；当前业务流程不再调用旧 task。SQLite 仍为 schema v3，数据库/前端 Job Report contract 不变。

## Workflow and native mapping

`job_analysis_workflow` 更新为 `job-analysis-v2`，九个固定节点：

```text
load_confirmed_profile
→ extract_job_requirements (generation / jd-requirements-v2)
→ validate_requirements
→ classify_scoreability (judge / scoreability-v1)
→ apply_scoreability_guard
→ match_requirements (generation / matching-v1)
→ validate_matches
→ calculate_coverage
→ persist_job_analysis
```

依据 [TypeSafe native API](https://docs.typesafe.ai/api)，一个 state 下可以提交多个 Choice questions。Provider 对每个已提取 requirement 建一个问题，question key 为 Python index 字符串；instructions 明确指向 state.requirements[index]，criteria 固定 SCORED / NEEDS_CONFIRMATION / NON_SCORABLE。只传 requirement text/category/importance，不传整份 JD、Profile 或 Evidence。

Provider 将原生 choice 转成业务 ScoreabilityResult，workflow 不访问 Jev wire schema。严格拒绝缺漏/额外 answer、重复 JSON key、未知 label、错误 answer type、非法 criteria/probability。Python 再检查 index 完整性、唯一性、范围；req-001 等 ID 仍由持久化层生成。空 requirements 跳过分类 HTTP；零 SCORED 跳过 matching，Coverage=null。

保留原 guard：只有 SCORED 的身体健康、吃苦耐劳、爱岗敬业、道德品质转 NON_SCORABLE。节点保留 raw_scoreability，guard trace 保留 scoreability_adjusted；全文仍遵守默认隐藏。Coverage 公式原样：required=2、preferred=1，MATCH=1、PARTIAL=.5、GAP=0，仅 SCORED，百分比一位小数。

AI trace 继续包含 role/provider/model/prompt_version/latency/tokens/retry_count/provider_metadata。沿用既有字段 `model_role`、`model_name`，OpenAI-compatible trace provider 字符串为 `openai-compatible`；配置值为 `openai_compatible`。Jev metadata allowlist 保存 response_model/question_count/request_id，持续脱敏，不保存 reasoning。

## Fake, replay and evaluation

- fake v2 提取固定 synthetic text/importance/category；fake scoreability 使用独立 deterministic synthetic 分类，不扩展生产 guard。
- 新 tasks 支持 task + prompt_version + canonical input hash 精确 replay；缺记录明确失败，无 live fallback。旧 reviewed references 未改。
- 独立 replay API/Debug UI 支持新旧 tasks，不创建 workflow run 或业务数据。
- `scripts/eval_jobs.py --suite scoreability` 直接使用 reviewed requirements 的 text/importance/category 作分类输入，不把 reference label 发给 Provider。8 个 cases 合并为一次请求，原始/guard 后 accuracy、三类 accuracy、请求和 schema/mapping 成功状态、latency、usage、retry 分别记录。
- `scripts/eval_profile.py` 支持单 case live facts 校验；Jobs live extraction 限一个 case。matching live 被 CLI 拒绝。
- 默认强制 replay，忽略 ambient AI_MODE=live；live 必须同时有 AI_MODE=live 和 --live。所有 live eval max_attempts=1，事先打印 provider/model/suite/case count/planned request count。
- Eval 直接调用模型层，不运行真实业务 graph、不打开 SQLite，不读写任何业务数据。结果 JSON 保存 synthetic 输出或失败的脱敏 metadata；失败退出非零。结果目录：[evals/results/phase_3_5b_r](../evals/results/phase_3_5b_r)。

授权顺序如下；**本次已在第一条失败，后面三条未执行，不应自动重跑：**

```bash
AI_MODE=live uv run python scripts/eval_profile.py --live --case education --output evals/results/phase_3_5b_r/profile-live.json
AI_MODE=live uv run python scripts/eval_jobs.py --live --suite jd_extraction --case jd-01 --output evals/results/phase_3_5b_r/jd-live.json
AI_MODE=live uv run python scripts/eval_jobs.py --live --suite scoreability --case score-0 --output evals/results/phase_3_5b_r/scoreability-single-live.json
AI_MODE=live uv run python scripts/eval_jobs.py --live --suite scoreability --output evals/results/phase_3_5b_r/scoreability-suite-live.json
```

计划 4 次请求，实际 **1 次**，无重试。未运行 normalization/matching live suite 或真实业务 graph，未发送真实用户数据，未进行 prompt tuning。

## Offline regression

本阶段完整执行：

```bash
uv run pytest
uv run ruff check backend scripts
uv run ruff format --check backend scripts
npm --prefix frontend run check
npm --prefix frontend test
npm --prefix frontend run build
npm --prefix frontend audit
uv run python scripts/eval_profile.py
uv run python scripts/eval_jobs.py
```

全部通过：后端 **189 passed**，前端 **16 passed**；Ruff、format、TypeScript/Prettier、build 通过，npm audit **0 vulnerabilities**。所有自动测试禁真实 TCP，回归和两个离线 eval **0 live requests**。

Profile replay：10 cases，omission/addition=0、type=10/10、numbers=6/6。
JD v2 replay：12 cases，missing/addition=0、importance/category=12/12。
Scoreability replay：8 cases，raw=8/8、guarded=8/8；SCORED=2/2、NEEDS_CONFIRMATION=2/2、NON_SCORABLE=4/4。
Matching replay：5 cases，status=5/5、valid citations=4/4、unsupported positive=0/4。

新增 routing、Choice request/batch mapping、缺漏/额外/重复/非法 criteria、usage/latency/secret redaction、workflow 分类缺失原子失败、新 tasks fake/replay 无网络无写入、CLI 双 gate/无重试/失败 metadata，以及 raw 7/8 → guarded 8/8 的扰动指标测试。原 Profile/Job regression 适配新职责；Report、Evidence、门禁、原子保存、Coverage 测试保留。

以上 replay 是 reviewed fixture 回归，不是 live 质量证明。未重跑额外 HTTP/浏览器 E2E。

## Live execution: stopped at Step 1

唯一请求：synthetic education case，GLM Profile facts。

| Field | Result |
| --- | --- |
| Role / provider | generation / openai_compatible |
| Configured model | glm-5.3-flash |
| Prompt version | profile-facts-v1 |
| Request success | true（收到 HTTP 成功响应及 usage） |
| Schema/mapping success | false：MODEL_OUTPUT_INVALID |
| Latency | 3878.029 ms |
| Input / output / total tokens | 113 / 243 / 356 |
| Requests / retry count | 1 / 0 |

脱敏记录：[profile-live.json](../evals/results/phase_3_5b_r/profile-live.json)。模型结果未通过现有严格结构化 contract；未保留失败响应正文，因此不猜测具体字段原因，也不能把 HTTP 成功视为 extraction 成功。现有异常边界只返回安全错误码及 metadata。

JD extraction、Jev 单 case、Jev 8-case benchmark **均未执行**。Jev raw/guarded/per-class accuracy、latency、tokens **暂无本轮 live 数据**，不能填 0 或复用历史 diagnostic 结果。没有继续发送请求以修复/调参。

## Limits and next authorized work

阶段验收仍缺：GLM Profile extraction 成功、GLM JD extraction 成功、Jev 单 case及 reviewed benchmark。继续前需用户明确授权调查 Step 1 的结构化输出失败，并重新确定 live 请求预算。本次仅交付职责重构和离线验证，通过率不能决定未来 importance/category/matching 的架构归属。

原 [PHASE_3_5B.md](PHASE_3_5B.md) 保留失败探索记录：Jev 不能原生承担开放文本 extraction。当前改用生成模型提取，并不消除此能力边界。

完成文档与提交，确认 working tree clean 后 STOP。不要开始 Phase 4。
