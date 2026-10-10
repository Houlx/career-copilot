# Phase 3.5A — Live Provider Validation

日期：2026-10-10（Asia/Shanghai）。Provider / synthetic smoke 范围；不开始 3.5B live eval 或 Phase 4 Resume。

## Status

**已完成代码、全部离线检查和两次真实 synthetic smoke。** Judge → Generation 各一次成功，无重试，真实 Provider 请求总数 **2**。两次 structured output、provider/model/latency/usage 均已核对落库 trace；无 Career 业务数据访问或业务 graph 执行。配置/文档不包含 key，验证后本地 AI_DEBUG_PAYLOADS 已恢复为 0。提交后确认 working tree clean，STOP。

## Provider architecture

`ModelRouter` 保留 `judge_model` / `generation_model`。`RoleModel` 在 live invoke 分支根据配置构造 adapter；fake/replay 不构造 adapter 或 HTTP client，配置真实 key 也不会触发请求。业务节点继续仅调用 role task。

- `JUDGE_PROVIDER=jev` → 专用 `JevProvider`，POST `{JUDGE_BASE_URL}/systemone`，Bearer auth，body 为 model/state/questions。
- `GENERATION_PROVIDER=openai_compatible` → `OpenAICompatibleProvider`，POST `{GENERATION_BASE_URL}/chat/completions`，沿用 strict `json_schema` → Pydantic contract。
- 兼容已有配置，Settings 未指定 provider 时仍选 `openai_compatible`；`.env.example` 明确推荐 Judge Jev / Generation OpenAI-compatible。OpenAI-compatible 角色保留已有 shared MODEL/BASE_URL/API_KEY fallback。Jev 必须配置自己的 JUDGE_MODEL/JUDGE_BASE_URL/JUDGE_API_KEY，绝不回退共享 OpenAI key/URL。
- 默认 AI_MODE=fake。live 启动先校验两个角色的有效 model/key/base URL；空 model/key、非 HTTP(S) 或无 host URL 拒绝。Settings API 仅增加 provider 类型，仍不返回 key 或 URL。

Jev wire contract 按 [TypeSafe 官方 API reference](https://docs.typesafe.ai/api)；base URL 是包含 API version 的根路径，例如 `https://api.typesafe.ai/v1`，不要包含 `/systemone`。模型 ID 完全由环境配置决定，无自动切换或 fallback。Noul answer 的 `noul` 必须为 0–1 有限数值，diagnostic 用 `>=0.5` 转成 strict bool，原 probability 保存在 metadata。此阈值只用于 synthetic diagnostic，不构成业务评分策略。

## Diagnostic tasks / CLI

- `judge-diagnostic-v1`：synthetic candidate has Python and SQL experience，问是否有 Python experience；期望 `{ "decision": true }`。
- `generation-diagnostic-v1`：同一 synthetic 输入，期望 `{ "message": "ok" }`。
- 原 `diagnostic-v1`、四个 Profile/Job prompt 及业务 schema/fixtures 不变。

先在本地 `.env` 配置两个角色的 provider/model/base URL/key；不要将 key 写入命令行、文档、fixture 或聊天。严格按顺序执行：

```bash
AI_MODE=live uv run python -m career_copilot.smoke --live --role judge
# Judge 成功后才执行下一条；失败即 STOP。
AI_MODE=live uv run python -m career_copilot.smoke --live --role generation
```

CLI 同时要求 `AI_MODE=live` 和 `--live`，缺任一项都不会构造 router/client 或打开 DB。`--role` 必填。每条 smoke 只有一次 HTTP 尝试，失败即退出非零；本阶段仅授权总共两个真实请求。常规模型调用保持 initial + retry 1，adapter 无嵌套重试/repair/fallback。

Smoke 不调用 Profile/Job graph 或 store，不读写 career_profile/career_evidence/jobs/job_requirements/job_matches，只初始化现有 schema 并保存 diagnostic run/node trace。输入固定 synthetic，无用户职业资料。默认结构化输出只在 CLI 展示；trace 保留 shape summary 和 bool decision，全文 payload 仍遵守现有 opt-in。`AI_DEBUG_PAYLOADS=1` 可在本地查看 structured output，完成后恢复 `AI_DEBUG_PAYLOADS=0`。

## Tracing and failures

复用 `RunTrace` lifecycle 与现有 JSON trace，无 DB migration。成功和失败均记录 role/provider/model/prompt version/latency_ms/retry_count；成功 CLI 返回 run_id/output/metadata。latency 为本地 monotonic 计时，包含 HTTP/client/validation，常规重试时覆盖所有尝试。

Jev 返回 usage 时将 input_tokens/output_tokens 记入现有 token 字段；缺失或 null 记 0，绝不估算。total_tokens 为两个已知计数之和。OpenAI-compatible 保留原 usage 累加规则。provider_metadata allowlist 保存 response_model、probability、`x-typesafe-request-id`（有则保存，截断至 256 字符）。不持久化任意响应字段、headers 或 hidden reasoning。现有 trace redact 同时处理这些 metadata，CLI 结果也脱敏。

认证/HTTP/timeout/network → MODEL_REQUEST_FAILED；成功 HTTP 的非法 JSON/answer/schema → MODEL_OUTPUT_INVALID。错误只返回 code/run_id，trace 可记 HTTP status 和异常类别，不记录异常全文、Authorization 或 key。配置校验 CLI 不打印 Pydantic 默认带输入的异常。

## Offline validation

新增 MockTransport tests 覆盖 Jev endpoint/auth/body/response/metadata/usage/error/retry/secret、Generation role smoke、CLI 双门禁和合法开启路径、两个角色 fake/replay 不构造 provider。SQLite authorizer 拒绝两个 smoke 的任何业务表数据读取/写入。所有自动测试禁 TCP，0 live calls。

检查命令沿用 HANDOFF，全部通过：后端 **153 passed**（既有 111 + 新增 42），Ruff check/format、frontend check、**16 tests passed**、build、npm audit **0 vulnerabilities**。eval_profile 10 cases omission/addition=0、type=10/10、numbers=6/6；eval_jobs 12 extraction / 8 scoreability / 5 matching，沿用 Phase 3 全通过结果。自动测试和 eval 均 **0 live calls**。离线回归分数不能证明 live 模型质量。

## Live results

| Role | Provider | Result | Model | Latency | Input/output/total tokens | Requests |
| --- | --- | --- | --- | --- | --- | --- |
| Judge | Jev | 成功：decision=true | jev-1.13.0 | 750.411 ms | 283 / 21 / 304 | 1 |
| Generation | OpenAI-compatible (GLM) | 成功：message=ok | glm-5.3-flash | 1085.165 ms | 41 / 63 / 104 | 1 |

两次 retry_count=0。Judge 返回 probability=0.95、response_model=jev-1.13.0；usage 为 Provider 实际返回计数，没有估算。Generation 返回极小 strict structured object，完成 HTTP/auth/model/json_schema/Pydantic/usage/tracing 验证。

持久化 run IDs：Judge `396f97e1-3c29-49b4-8bc8-90df12e8289e`；Generation `e22dde3e-5fac-42ae-a91c-948040f69e87`。本地 opt-in debug payload 已核对 structured output，并检查两份 trace 无已配置 secrets/Authorization；随后恢复 AI_DEBUG_PAYLOADS=0。trace 保留在本地忽略的数据库中，不提交。

本次配置时将 Judge provider 显式设为 jev，并将完整 endpoint 地址改为 adapter 约定的 version base URL；model ID 使用用户提供值，不硬编码。

## Known limits / stop

Jev 仅映射 judge-diagnostic-v1；profile-facts-v1 / jd-extract-v1 的 Jev mapping 留到 Phase 3.5B。当前若配置 Jev 后调用这些业务 task，会在发送任何数据之前明确 MODEL_OUTPUT_INVALID；不改业务语义或尝试用 Jev 伪装 Chat Completions。Generation Provider 必须支持现有 json_schema contract，否则 smoke 明确失败，不自动降级。

两个 smoke 成功也不代表业务质量通过；不得运行真实 Profile normalization / JD analysis / matching。完成验证和提交后 STOP。
