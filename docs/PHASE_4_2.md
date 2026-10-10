# Phase 4.2 — Generation Reasoning Control Support

日期：2026-10-10（Asia/Shanghai）。Capability gate 通过；实现仅限可选 Generation role-level inference 配置。**本阶段模型 live requests = 0**；没有执行用户人工 Test 1，没有进入 Phase 5。

## Capability verification

只读取官方公开文档，没有向模型 endpoint 发请求，也没有用字段试错。读取本地配置时仅核对 endpoint host/path、model 和 structured-output mode，未输出 key。实际使用智谱标准 `open.bigmodel.cn` `/api/paas/v4`，model=`glm-5.3-flash`，Provider 拼接 `/chat/completions`。

官方来源（2026-10-10 核对）：

- [GLM-5.3-Flash/FlashX 模型页](https://docs.bigmodel.cn/cn/guide/models/vlm/glm-5.3-flash)：Model Code、Chat Completion API 链接、文本参数与 GLM-5.3 一致、推荐 reasoning effort、强制 thinking，以及结构化输出能力链接。
- [官方 OpenAPI](https://docs.bigmodel.cn/openapi/openapi.json)：`servers[0]`、`paths["/paas/v4/chat/completions"]`、`components.schemas.ChatCompletionTextRequest` / `ChatCompletionVisionRequest` 的属性。
- [思考模式](https://docs.bigmodel.cn/cn/guide/capabilities/thinking-mode)：该模型默认开启 thinking 且不能关闭；标准 API 与 Coding Plan 的 preserved-thinking 默认值不同。
- [结构化输出](https://docs.bigmodel.cn/cn/guide/capabilities/struct-output)：JSON 模式的字段和客户端校验说明。

| 核对项 | 文档结论 |
| --- | --- |
| reasoning/inference control | 支持；调节推理程度，不是关闭 thinking |
| exact field / location | JSON request body 顶层 `reasoning_effort` |
| 当前模型合法值 | `low` / `high` / `max` |
| Provider default | `max`；应用 unset 不等于显式发送 max |
| 当前模型 | VisionRequest 明确列出 `glm-5.3-flash` 及该系列 effort 档位 |
| 当前 endpoint | 标准 `/api/paas/v4/chat/completions`，无需 Coding Plan endpoint |
| non-streaming | 支持，`stream` 默认 false，effort 没有 stream-only 条件 |
| JSON compatibility | 模型页明确支持结构化输出且文本参数相同；JSON 模式文档为 `response_format={"type":"json_object"}`。据这些文档合并确认，没有 live 验证组合 |
| 限制 | effort 在 thinking 开启时生效；该模型仅支持 enabled。其他模型/endpoint 的合法值不能由本配置推断 |

文档不一致保留：OpenAPI TextRequest 的 model enum 未列出 Flash，VisionRequest 列出 Flash 和 effort 却未列 `response_format`；TextRequest 的 response-format 描述写“三种”但 enum 实际仅 text/json_object。JSON 兼容性判断采用模型页明确的能力支持及文本参数继承说明，而非把缺失 schema 属性当作禁止组合。未声称官方提供了 low + JSON + non-streaming 的专门示例或本阶段运行验证。模型 API 文档 HTML 获取超时，使用同站官方 OpenAPI 核对完整 contract。

## Configuration and provider boundary

```text
Settings.GENERATION_REASONING_EFFORT
→ ModelConfig.reasoning_effort
→ ModelRouter.generation_model
→ OpenAICompatibleProvider(config)
→ optional top-level request field
```

配置采用严格 `Literal["low", "high", "max"] | None`；默认 None。空字符串、大小写变体、带空格值、其他模型的 medium/xhigh/none 等均启动失败，没有 fallback、映射或自动 capability detection。

未配置时 request 中字段完全 absent，不发送 null；完整 request body 与 Phase 4.1 一致，json_schema/json_object 原行为均保留。非空时仅 Generation OpenAI-compatible 发送配置值；不额外设置 thinking、stream、temperature 或任何其他参数。文档对当前 GLM 的确认不表示所有 OpenAI-compatible endpoint 都支持该字段；其他 endpoint 需用户核对后显式配置。

Settings API 沿用 `models` allowlist，Generation 条目增加 `reasoning_effort: null | low | high | max`，页面只读显示 Reasoning effort / Default、Low、High 或 Max。没有运行时编辑控件，不返回 URL、key 或 raw payload。Judge Settings 条目、Jev native request 和诊断/scoreability 语义保持原样。

Generation AI metadata 在执行前写入 reasoning_effort，因此 success、timeout、provider error 和 schema error 都有配置值；fake/replay 同样表达该配置但不模拟推理效果。该字段表示配置传入 Provider，并非服务端执行回执。现有 observer → RunTrace → DB → Debug API 路径复用，无 DB migration。

Provider 仍只读取 assistant content 与现有 usage 三项；忽略 response 中的 reasoning / reasoning_content / thinking / chain_of_thought 和 reasoning-token details。不把隐藏推理写入 trace/debug/HTTP/eval/数据库，不扩展 token schema。

本地 `.env` 未修改，low 未启用。Generation timeout 仍 60，Judge 30；项目默认 timeout 仍均 30。所有 prompt、schema、业务规则、output token limits、retry、ProfileFacts/normalization、工作流图不变。没有 task-level override、新 replay fixture、live eval 开关或脚本自动模型请求。

## Offline validation

- `uv run pytest`：319 passed；默认 pytest-socket 禁 TCP，Provider 使用 MockTransport。
- `uv run ruff check backend scripts`、`uv run ruff format --check backend scripts`：通过。
- `npm --prefix frontend run check`、`test`（25 passed）、`build`、`audit`：通过，0 vulnerabilities。
- `uv run python scripts/eval_profile.py`：10-case reviewed replay，omission/addition=0、type=10/10、numbers=6/6。
- `uv run python scripts/eval_jobs.py`：原 reviewed extraction/scoreability/matching replay 回归通过。
- `uv run python scripts/eval_resumes.py`：6-case reviewed replay 回归通过，包含预期失败 cases。
- 实际 `check_http.py --resume --frontend http://127.0.0.1:5177`：临时空 DB、AI_MODE=fake，Profile/Job/Resume/Settings/trace 通过。临时进程环境显式 low/json_object/60s；Settings 和 5 条 Generation trace 的 low metadata 验证通过。服务和 DB 已清理，未写业务 DB。

新增测试证明两项业务 task 的完整默认 wire body，以及三种显式值；包括 JSON 模式组合、严格 env 校验、Router 传递、Judge 隔离、Settings 隐私、fake metadata、success/timeout/provider/schema 错误 metadata，以及持久化 trace/Debug HTTP 中不含隐藏推理。前端覆盖 Default/Low/High/Max 的只读展示。

所有自动验证 **0 live requests**。Replay 分数仅为 fixture 回归，不代表当前模型真实质量。

## User-only manual Test 1 (not executed)

代码完成后由用户自行：

1. 在本地 `.env` 设置 `GENERATION_REASONING_EFFORT=low`。
2. 完整重启后端，保持现有 model、prompt、retry 和其余参数。
3. GET `/api/settings`，在 `models` 的 Generation 条目确认 structured_output_mode=json_object、timeout_seconds=60、reasoning_effort=low；Settings 页面应显示 Low。
4. 打开 `localhost:5173`，使用与历史完全相同的 Skills-heavy 输入，点击 AI 整理。
5. 在 Debug Runs 找到 `extract_profile_facts`，确认 reasoning_effort=low 并记录结果。

完全相同 Test 1 输入：

```text
计算机专业背景，4 年日本大型互联网企业开发经验，熟悉 React / TypeScript、Node.js SSR、Java / Spring Boot，具备大型 Web 系统开发、技术升级、性能优化及 0→1 产品开发经验。

专业技能
编程语言：TypeScript / Java / SQL / C++
Web 开发：React / Redux / Node.js / SSR / Spring Boot / REST API / MySQL
工程实践：性能优化 / 单元测试 / BDD / CI/CD / Git / Docker / Code Review
计算机系统：RISC-V / RTL / 近似计算 / 硬件加速
其他：AI 辅助开发 / 日语 N1 / TOEFL 103 / TOEIC 850
```

历史人工 baseline（用户提供，未重跑）：

```text
Default provider reasoning baseline:
facts=12
input_tokens=314
output_tokens=4485
total_tokens=4799
latency_ms=57003.418
retry=0
HTTP=200
```

请复制回 `fact_count / input_tokens / output_tokens / total_tokens / latency_ms / retry_count / reasoning_effort / HTTP或error`，并观察事实提取质量。low result 尚未提供，不能提前给效果结论。

单次 low 即使显著变快，也不能证明 reasoning low 导致全部 latency 降低：相同 Test 2 已有一次 >60s timeout（retry=1，总耗时约120198ms）、另一次48.6s成功的波动。后续同时比较 output_tokens、fact count、quality 和 latency；token/fact 变化比单次 latency 更值得关注。现有 output_tokens 是 completion usage，不单独核算 reasoning tokens。

未来仅可在用户另开阶段后研究 task-specific Generation inference policy，包括 profile-facts-v1、profile-normalize-v1、jd-requirements-v2、matching-v1、resume-generation-v1。本阶段没有实现该策略，也没有 chunking、prompt tuning、streaming、retry redesign、Resume revision 或 Phase 5。

## Git / stop

提交名 `Phase 4.2`；hash 通过 `git log -1 --oneline` 获取。提交并确认 working tree clean 后 STOP，无任何额外 live 授权。
