# Phase 3.5B-R2 — GLM Provider Contract Adaptation

日期：2026-10-10（Asia/Shanghai）。**实现、离线 regression 及唯一一次 synthetic Profile live 验证完成，已停止。**

## 为什么调整 Provider contract

R1 已确定上一次 HTTP 200 后的 assistant content 以 markdown fence 开头，失败在 STRUCTURED_JSON_INVALID，尚未进入业务 schema/grounding。智谱公开文档明确使用 json_object，未提供完整 OpenAI-style json_schema + strict 服务端执行保证；相关官方来源和原失败限制保留在 [PHASE_3_5B_R1.md](PHASE_3_5B_R1.md)。

本轮区分 Provider 输出格式与 Career 业务合法性：改用明确支持的 wire contract，本地仍严格执行原业务 schema。没有按模型名/URL 猜测 capability，没有全局移除 json_schema 支持。

## 配置与实现

- Settings 新增 `GENERATION_STRUCTURED_OUTPUT`，枚举 json_schema/json_object，未指定默认 json_schema。对应 ModelConfig.structured_output_mode，generation 角色显式选择；Judge 配置及 Jev 原生 wire protocol 不变。
- OpenAICompatibleProvider 的 json_schema 请求与以前一致：type=json_schema，json_schema 内包含 schema name、strict=true、原 Pydantic model_json_schema。
- json_object 模式只发送 `response_format={"type":"json_object"}`，不附加 json_schema/strict/schema 参数，也不将 schema 塞入 prompt。任务 prompt 已要求 strict JSON，原文保持不变。
- 两种模式后续共用 `response.json → choices[0].message.content → json.loads → schema.model_validate_json → existing business validation`。继续拒绝 missing、extra、enum、wrong type、fence，以及非法 source_quote/数字 grounding；不 repair、strip、coerce、fallback 或更换模型。
- schema 继续通过原有 invoke 的 schema 参数传入；PromptSpec/任务分派不变，未将业务 schema 随 Provider capability 丢弃。
- AI metadata 新增 structured_output_mode；Settings API 的 models allowlist 展示该字段。Jev 返回 null，避免把原生 Choice/Noul 标成 JSON 模式。Settings 页面显示配置值，不新增密钥/URL 输出。
- Profile eval 成功结果显式增加 schema_mapping_success/business_validation_success；只有严格 schema 和 grounding 都通过才到该成功分支。现有 eval metrics 算法未改。

本地忽略的 `.env` 已显式添加 `GENERATION_STRUCTURED_OUTPUT=json_object`。其余 GLM model/key/base URL 和 Jev 设置未改，`.env` 不提交。`.env.example` 保留默认 json_schema 并说明 GLM 可选择 json_object。debug 只对本次 subprocess 开启，结束后确认默认 AI_DEBUG_PAYLOADS=0。

修改文件：config.py、models/providers.py、models/router.py、api.py、scripts/eval_profile.py、前端 Settings 类型/显示、相关测试及 .env.example。业务 schema、validators、prompt、fixture、评估算法和数据库均未修改；R1 的四类 failure stage/诊断门禁保持不变。

## MockTransport / 离线 regression

独立 Provider tests 对两种模式分别检查完整 response_format、metadata、合法结果、缺字段、extra field、错误 enum/类型、fence/非法 JSON、envelope 差异与 usage 错误。json_object 没有任何 schema 参数；json_schema 的原参数保持一致。通过 schema 的输出仍受原 grounding 规则约束，两个模式均测试 source_quote 和 numeric_grounding 拒绝。另测试配置默认值、环境 override、非法枚举及 Settings API 隐私。

以下命令全部在 live 前通过，**0 live requests**：

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

后端 **237 passed**（R1 的 212 + 25），前端 **16 passed**。Ruff/format、TypeScript/Prettier、build 全通过，audit 0 vulnerabilities。pytest 禁 TCP，网络响应由 MockTransport 提供。

Profile replay 10 cases：omission/addition=0、type=10/10、numbers=6/6。Jobs replay：12 extraction missing/addition=0、importance/category=12/12；8 scoreability 各类 2/2、2/2、4/4；5 matching status=5/5、citations=4/4、unsupported positive=0/4。两个 eval live_api_calls=0。这些仍是 reviewed fixture 回归分数。

## 唯一一次 live 结果

```bash
APP_ENV=development AI_DEBUG_PAYLOADS=1 AI_MODE=live uv run python scripts/eval_profile.py \
  --live --case education --output evals/results/phase_3_5b_r/profile-live-r2.json
```

| 项目 | 结果 |
| --- | --- |
| provider / model | openai-compatible GLM / glm-5.3-flash |
| task / fixture | profile-facts-v1 / 原 education case |
| structured_output_mode | json_object |
| HTTP | 200 |
| structured JSON parsing | 通过，无 fence normalization |
| schema_valid / schema_mapping_success | true / true |
| business_validation_success | true（原 source_quote / numeric grounding） |
| failure_stage | 无 |
| latency | 6027.08 ms |
| input / output / total tokens | 166 / 335 / 501 |
| requests / retry | 1 / 0 |

[完整脱敏结果及 calls metadata](../evals/results/phase_3_5b_r/profile-live-r2.json)。独立 eval 不创建 DB trace 或执行业务 graph，metadata 记录在结果文件中；没有保存完整 provider envelope、reasoning、header 或 API key。

实际结构化输出：type=education，title=计算机学士学位，content/source_quote 均为“在示例大学获得计算机学士学位。”。schema 与 business validation 全部通过。

**评估并非满分**：fact_omission=1、unsupported_fact_addition=1、type_correct=0/type_total=1、numeric=0/0。reference title 为“示例大学”；评估器按 `(type,title,content,source_quote)` 精确比较 facts，type 指标也先按 `(title,source_quote)` 匹配，所以不同标题导致这三个计数。实际返回的 type 是合法 education，content/source_quote 与 reference 一致。保留原始指标，不修改 reference、评估逻辑或 prompt；合同验证成功不能代替语义质量评估。

## 请求预算、限制与 STOP

本阶段新增 live **1 次**，retry=0；连同 3.5B-R/R1，synthetic Profile education 累计 **3 次**。没有第二次 R2 请求，没有 JD extraction、Jev、normalization、matching 或真实 Career workflow live；没有 Phase 4。

本次单 case 证明该请求在 json_object 模式下通过四层验证，不能证明未来所有输出均为纯 JSON或都满足业务 schema，也不能证明其他 GLM 任务可用。json_object 不承担服务端业务 schema enforcement；错误仍由本地严格校验拒绝。未做 prompt tuning、质量优化或跨 case benchmark。

阶段提交 `Phase 3.5B-R2`，确认 working tree clean 后停止。目前没有后续 live 授权；若用户要恢复 3.5B-R Step 2–4，需要明确授权新的 JD/Jev 范围与请求预算。
