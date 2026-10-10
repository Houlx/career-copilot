# Phase 4.1 — Resume Live Timeout Validation

日期：2026-10-10（Asia/Shanghai）。

## Scope / configuration

Phase 4 原唯一一次 `resume-01` live 在硬编码 30 秒 timeout 后失败：`MODEL_REQUEST_FAILED` / `ReadTimeout`，latency=30084.153 ms，retry=0，没有模型输出或 usage；原记录保留在 [PHASE_4.md](PHASE_4.md) 和 [live.json](../evals/results/phase_4/live.json)。本阶段只配置 timeout，并对同一 synthetic case 执行一次 60 秒验证。

- `GENERATION_TIMEOUT_SECONDS=30`、`JUDGE_TIMEOUT_SECONDS=30` 为代码与 `.env.example` 默认值，保留原行为。
- Settings / ModelConfig 验证有限数值 `0 < timeout <= 300`；0、负值、超上限、NaN、Infinity 均启动失败，不 clamp。
- 配置路径：Settings → ModelConfig.timeout_seconds → ModelRouter → Provider 构造参数 timeout_seconds → httpx.Client。Provider 不直接读取环境变量，业务节点不知道 timeout。
- Settings API / frontend Settings 安全显示两个角色的 timeout；成功和失败的 AI metadata 均包含 timeout_seconds。网络失败仍为 MODEL_REQUEST_FAILED，ReadTimeout 通过 provider_metadata.error_kind 记录；不暴露原始异常消息。
- 完成离线验证后，本地被 Git 忽略的 `.env` 只新增 `GENERATION_TIMEOUT_SECONDS=60`；Judge 仍 30 秒。确认其他配置完全相同：Generation GLM `glm-5.3-flash`、json_object、key、base URL、AI_DEBUG_PAYLOADS=0 不变。
- 保留 HTTPX 分阶段 timeout 语义，不承诺整次调用的硬性总耗时上限；latency 仍为原 monotonic 全调用耗时。

没有修改 Resume prompt/schema/grounding/language/renderer/workflow/DB/API/frontend Resume flow、structured output、模型、温度、retry、context、output limit、fixtures 或 expected outputs。Jev request shape/scoreability/diagnostics 不变。没有 fallback/repair/streaming/queue 或 Phase 5。

## Offline validation — 0 live requests

以下全部通过：

```bash
uv run pytest                              # 281 passed
uv run ruff check backend scripts
uv run ruff format --check backend scripts # 41 files formatted
npm --prefix frontend run check
npm --prefix frontend test                 # 21 passed
npm --prefix frontend run build
npm --prefix frontend audit                # 0 vulnerabilities
uv run python scripts/eval_profile.py
uv run python scripts/eval_jobs.py
uv run python scripts/eval_resumes.py
uv run python scripts/check_http.py --resume --frontend http://127.0.0.1:5177
```

新增 14 个 backend cases：角色默认值/env override、两角色非法 timeout、generation/judge（含 Jev 和 compatible）MockTransport wiring、ReadTimeout 单尝试/安全 metadata；现有 frontend Settings test 增加显示断言。pytest 默认禁 TCP，frontend mock fetch。三个 eval 均强制 replay，分别报告 live_api_calls=0；Profile 10 cases、Jobs 12 extraction / 8 scoreability / 5 matching、Resume 6 cases 全通过。fixtures 回归不能代表真实模型总体质量。

实际 Uvicorn + Vite 使用 fake/development、空临时目录数据库，沿 Profile → Confirm → Job → Resume → GET/list/trace → outdated → delete 完整通过；额外验证 Settings 两角色 timeout=30、所有实际 AI trace provider=fake/timeout_seconds=30，0 live requests。临时服务关闭，临时数据库已清理，没有访问业务数据库。

## Single live validation

授权预算：仅 1 次新的 synthetic `resume-01`，无 Jev 或其他业务请求；现有 eval 不运行业务 graph、不打开 SQLite。脚本原样使用 Settings，无新增 CLI timeout 参数。

```bash
AI_MODE=live AI_DEBUG_PAYLOADS=0 uv run python scripts/eval_resumes.py --live --output evals/results/phase_4/live-timeout-60.json
```

max_attempts=1，retry=0；结果见 [live-timeout-60.json](../evals/results/phase_4/live-timeout-60.json)。

**结果：本次 live validation 成功，已停止所有 live 活动。** HTTP 200、JSON parsing、严格 Pydantic、Evidence 引用、numeric grounding、requested language contract 及 Markdown render 全通过。

| Field | Result |
| --- | --- |
| provider / model | openai-compatible / glm-5.3-flash |
| prompt / structured_output_mode | resume-generation-v1 / json_object |
| timeout_seconds | 60 |
| latency_ms | 29076.369 |
| input_tokens / output_tokens / total_tokens | 823 / 2244 / 3067 |
| request_count / retry_count | 1 / 0 |
| resume_item_count | 8 |
| evidence_reference_count | 9 |
| invalid_evidence_reference_count | 0 |
| unsupported_fact_item_count | 0 |
| numeric_grounding_failure_count | 0 |
| evidence_backed_item_rate | 1.0 |

成功路径实际依次执行 JSON parsing、schema.model_validate_json、validate_grounding、validate_language、render_markdown；现有 result 保留 schema_mapping_success / markdown_render_success 及全部 grounding metrics，未改变 eval 合同。synthetic output / Markdown 随现有 eval 保存，未记录 key/base URL/真实用户资料。Phase 4 原请求 + Phase 4.1 新请求累计 2 次；本阶段新增严格 1 次。

## Interpretation / limits / STOP

本次证明同一 baseline 在 60 秒配置下能够通过全部现有门禁。实际 latency=29.076369 秒，低于原 30 秒；不同请求的服务延迟会波动，不能由这两次结果证明 30 秒 timeout 是唯一 blocker，或证明提高 timeout 是成功的唯一原因。没有进行更多请求来检验该因果问题。

8 items / 9 refs / zero invalid refs、unsupported items、numeric failures / rate=1.0 是当前确定性定义的指标；引用合法和数字有来源不证明自然语言全部语义 entailment。语言检查仅 enum/请求一致性，不是完整语言质量评估；单一 synthetic 英文 case 不能代表真实资料或其他语言的总体质量。没有真实浏览器 PDF 视觉验收或真实用户数据 live。

按授权成功后停止：没有第二个 case、第二次请求、tuning、fallback、repair、context/output 调整、streaming 或 Phase 5。仅完成文档与 `Phase 4.1` commit，确认 working tree clean 后 STOP。没有额外 live 授权。
