# Phase 3.5B-R1 — Structured Output Failure Diagnosis

日期：2026-10-10（Asia/Shanghai）。**诊断交付完成，live 仍失败；已停止。**

## 原失败与本次结论

3.5B-R 原 synthetic education 请求 HTTP 成功，但只留下 `MODEL_OUTPUT_INVALID`：GLM `glm-5.3-flash`，3878.029 ms，tokens 113/243/356，retry=0。原正文未保留，无法追溯它究竟是 JSON、Pydantic 还是其他解析失败，不能断言它与本次根因相同。

R1 唯一一次获授权请求仍使用原 `profile-facts-v1`、原 education fixture、原模型和原请求参数。HTTP 200，成功读取 `choices[0].message.content` 的字符串；其去除前导空白后的开头是 markdown fence。严格 `json.loads(content)` 抛出 `JSONDecodeError`。因此**本次已证实的直接原因是 assistant content 带 markdown fence，不符合纯 JSON wire contract**；尚未进入 Pydantic 或 business grounding。

未移除 fence、未尝试解析 fence 内部、未 repair、未修改 prompt/schema/业务规则。没有保存完整 assistant content/provider response，故无法断言 fence 内部对象是否符合 schema。没有可用的 parsed JSON shape，本次用 `assistant_content_kind=markdown_fence` 表示可安全确认的结构特征。

## 完整解析链

| 阶段 | 位置 | 失败定位 |
| --- | --- | --- |
| HTTP response | HTTPProvider.post | HTTP/network 异常仍为 MODEL_REQUEST_FAILED |
| provider JSON decoding | OpenAICompatibleProvider.invoke / response.json | RESPONSE_ENVELOPE_INVALID，debug rule=provider_json |
| usage / completion envelope | TokenUsage / choices[0].message | RESPONSE_ENVELOPE_INVALID；usage schema 失败不误报业务 Pydantic |
| assistant content extraction | choices[0].message.content，必须是 str | RESPONSE_ENVELOPE_INVALID；不读取 parsed/tool_calls/reasoning 作为业务结果 |
| structured JSON parsing | json.loads(content) | STRUCTURED_JSON_INVALID；不 repair、不 strip fence |
| Pydantic schema validation | 原 schema.model_validate_json(content) | PYDANTIC_VALIDATION_FAILED，debug errors 含 loc/type/安全消息及 value-free shape |
| business grounding | validate_facts / validate_profile | BUSINESS_VALIDATION_FAILED，原公开 PROFILE_VALIDATION_FAILED 不变 |

保留 `model_validate_json`，避免切换为 Python input validation 后改变 strict JSON 语义。新增的 JSON parsing 只用于定位和 shape，不负责 coercion/repair。RoleModel 原重试架构未变；本次 eval 明确 `max_attempts=1`。

## 修改与隐私边界

- `models/providers.py`：拆分上述 parsing 阶段，HTTP 200 也保留已报告 usage；只允许既有 content 路径，明确拒绝差异 envelope。
- `models/diagnostics.py`：内部 ContextVar 控制诊断细节。默认只给 failure_stage；仅 `APP_ENV=development` 且 `AI_DEBUG_PAYLOADS=1` 保存 errors/shape。shape 限深度、字段数和数组样本数；只保留类型/字段，不保存值。可疑字段名隐藏。
- Pydantic errors 不保存 input/context/url，不直接保存可插入输入或自定义文本的异常消息；missing/extra 使用固定安全消息，其他 constraint 使用固定摘要。
- `models/router.py`：从 Settings 双门禁绑定单次调用的诊断上下文，finally 恢复；AI_OBSERVER 接收失败 metadata。没有新增 response body observer。
- `models/profile.py`：同一校验条件拆分命名，记录 source_quote_contiguous_excerpt、numeric_grounding、source_index_unique_and_in_range、complete_fact_mapping、preserve_fact_type、preserve_fact_numbers。接受/拒绝结果不变。
- `tracing.py`：business validation 的诊断进入 node.validation_result。原 node/API error code 不变；沿用 trace/eval 输出前的 secret redaction。
- `business_eval.py` / `scripts/eval_profile.py`：失败报告包含阶段和细节；成功 schema 后的 grounding 失败会明确 schema_mapping_success=true。本次独立 eval 不创建 DB run，报告中的 calls 即本次 debug metadata。

不记录 API key、Authorization、任意 provider header、hidden reasoning、完整失败正文。原 prompt 文件、ProfileFacts/NormalizedProfile 字段约束、source_quote/数字 grounding 规则、模型、retry/fallback 行为均未改变。原 `PHASE_3_5B.md` / `PHASE_3_5B_R.md` 保留历史。

## GLM request contract 核对

实际请求由 MockTransport 检查，内容仍是 `response_format.type=json_schema`、name=`ProfileFacts`、strict=true、schema=`ProfileFacts.model_json_schema()`，未经转换或放宽。

| schema 特性 | 实际值 |
| --- | --- |
| additionalProperties | 顶层与 Fact 都为 false |
| enum | education/work/project/skill/other |
| arrays / nested objects | facts array → $ref #/$defs/Fact；没有数组套数组 |
| required | 顶层 facts；Fact 的 type/title/content/source_quote 全部 required |
| nullable / defaults | 无 nullable 字段，无业务字段 defaults |
| constraints | facts minItems=1/maxItems=200；title 1–300 字符；content/source_quote 1–20000 字符；非空白 pattern |
| schema name / strict | ProfileFacts / true |

[智谱官方结构化输出说明](https://docs.bigmodel.cn/cn/guide/capabilities/struct-output) 使用 json_object，schema validation 示例是在客户端进行。核对 [官方 OpenAPI](https://docs.bigmodel.cn/openapi/openapi.json) 中 ChatCompletionTextRequest.response_format：type enum 只列 text/json_object，没有 json_schema/strict 或各 schema feature 的服务端支持承诺。[官方 GLM-5.3-Flash 说明](https://docs.bigmodel.cn/cn/guide/models/vlm/glm-5.3-flash) 链接该结构化输出能力说明；[官方快速开始](https://docs.bigmodel.cn/cn/api/introduction) 的输出路径也是 choices[0].message.content。

记录 **provider capability mismatch：现有 strict json_schema 请求超出已公开声明的 response_format contract**。不能由 HTTP 200 推断服务器执行了 strict schema，也不能据此确认具体 keyword 被忽略。未将文档缺失夸大为所有 feature 逐项不支持的证明；未切 json_object、修改 prompt、放宽业务 schema 或做 adapter normalization。本次真实 envelope 的 content 路径读取成功，不存在已证实需要修复的替代 structured field。

## 离线验证

所有下列命令在本次 live 前完成并通过，**0 live requests**：

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

后端 **212 passed**（原 189 + 23 项新诊断测试），前端 **16 passed**；audit 0 vulnerabilities。pytest 禁真实 TCP；contract tests 全使用 MockTransport。新增测试覆盖合法 ProfileFacts、source_quote missing 精确 loc、extra field、enum、fence、非法 JSON、envelope 差异/非字符串/错误 usage/响应 JSON 失败、双门禁隐私、source_quote/数字规则、business eval schema 成功区分、trace 诊断与公开错误 contract。

Profile reference replay 10 cases：omission/addition=0，type=10/10，numbers=6/6。Jobs replay 12 extraction、8 scoreability、5 matching 指标保持原值；所有 eval live_api_calls=0。分数仅为 fixture 回归，不代表 live 模型质量。

## 唯一一次 live 结果

```bash
APP_ENV=development AI_DEBUG_PAYLOADS=1 AI_MODE=live uv run python scripts/eval_profile.py \
  --live --case education --output evals/results/phase_3_5b_r/profile-live-r1.json
```

| 项目 | 结果 |
| --- | --- |
| provider/model | openai-compatible / glm-5.3-flash |
| task/case | profile-facts-v1 / education（原 fixture） |
| HTTP / public error | 200 / MODEL_OUTPUT_INVALID |
| failure_stage | STRUCTURED_JSON_INVALID |
| validation path | choices.0.message.content |
| validation type | JSONDecodeError |
| safe message | Response does not satisfy provider contract |
| assistant_content_kind | markdown_fence |
| latency | 6012.51 ms |
| input/output/total tokens | 113 / 377 / 490 |
| requests / retry | 1 / 0 |
| Pydantic / business | 未执行 |

脱敏 metadata：[profile-live-r1.json](../evals/results/phase_3_5b_r/profile-live-r1.json)。debug 仅对该 subprocess 设置，退出后读取 Settings 确认 AI_DEBUG_PAYLOADS=0，本地 .env 未修改。没有临时原始 response 文件。

## STOP / known limits

R1 新增 live **1 次**；包含原 3.5B-R 的 education 请求累计 2 次，未发第三次。没有 JD/Jev/normalization/matching/真实业务 graph 请求，没有 Phase 4。

已完成诊断能力，**未修复 live 输出合同不匹配**，没有充分证据支持不改变 wire contract 的 adapter 修复。原第一次失败根因仍不可追溯；本次明确为 fence。后续需用户重新授权具体 provider contract 方案与新的 live 预算；本阶段提交 `Phase 3.5B-R1`、确认 working tree clean 后停止，不自动恢复 Step 2–4。
