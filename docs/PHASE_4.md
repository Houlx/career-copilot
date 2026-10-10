# Phase 4 — Evidence-Grounded Resume Generation

日期：2026-10-10（Asia/Shanghai）。

**实现及离线验收完成；唯一一次 synthetic live 因 ReadTimeout 失败，已停止。Phase 4 的 live 成功验收条件尚未满足。** 没有调整 prompt、timeout、Provider contract 或重试。后续 live 需要用户重新授权；没有 Phase 5。

## Workflow 与职责

Job Detail → 用户选择 zh/en/ja 并明确 Generate Resume → 独立固定 `resume_generation_workflow` / `resume-generation-v1`：

```text
load_resume_context
→ generate_resume
→ validate_resume_schema
→ validate_resume_grounding
→ validate_resume_language
→ render_resume_markdown
→ persist_resume
```

Generation / GLM 新增 `resume-generation-v1`；Jev 不增加任务。既有 Profile、JD extraction、scoreability、matching 和 Coverage 不变。Resume 只读取保存的 Job requirements/matches/Coverage，以及当前确认版本的 active Evidence，不重跑 Job Analysis，没有动态边或 Agent loop。

输入严格裁剪为 job title/raw_jd/coverage、requirements id/text/importance/category/scoreability、matches requirement_id/status/evidence_ids/explanation、Evidence id/type/title/normalized_content、requested_language。无 raw Profile 历史、inactive Evidence、trace、密钥或调试 metadata。

Prompt 明确事实只能来自 Evidence，JD 和 match explanation 不是事实来源；MATCH 优先，PARTIAL 不夸大，GAP 不伪造补齐，不发明数字、技术、公司、职位、学历、日期或人格评价。json_object 不在 wire 上携带 JSON Schema，因此新 task prompt 明确描述输出字段和类型；Provider adapter 沿用现有实现，没有 Resume 专用 wire 处理。

## Schema 与 grounding

使用简洁的 sections/items，避免把无引用的 company/title/date 字段变成事实逃逸通道：

```json
{
  "language": "en",
  "headline": {"text": "Python engineer", "evidence_ids": ["career-work-<sha256>"]},
  "summary": [{"text": "Supported claim", "evidence_ids": ["career-work-<sha256>"]}],
  "sections": [{"kind": "experience", "items": [{"text": "Complete supported fact including any role/company/date context", "evidence_ids": ["career-work-<sha256>"]}]}]
}
```

严格 Pydantic：extra forbid、strict types、所有字段必填，非空 headline/summary/sections/items/text，每项至少一个引用；Evidence ID 限定 `career-(education|work|project|skill|other)-<64 lowercase hex>`，拒绝重复引用和重复 section。kind 为 skills/experience/projects/education/additional。无独立模型 item ID，列表顺序是展示顺序，因此不存在 duplicate item ID 的接受路径。无事实支持的可选 section 必须省略；不强制教育或工作等不适用 section。

每个事实，包括 headline、每条 summary、各 section item，都检查引用存在、active、当前 profile_version。支持一项多个 Evidence，不限制一 Evidence 一 bullet。数字复用 Phase 2 `numbers()` helper：逐事实收集数字 token，要求为该项所引用 normalized_content 的数字 token 子集，覆盖整数、小数、百分比、年份及版本数字；不从 Evidence title、JD 或其他未引用 Evidence 借数字。

确定性指标：

- `invalid_evidence_reference_count`：所有事实中的无效引用出现次数。
- `unsupported_fact_item_count`：引用为空或至少有一个无效引用的事实项数。空引用通常先被 schema 拒绝；该指标不代表 semantic entailment。
- `numeric_grounding_failure_count`：数字 token 不被引用来源覆盖的事实项数。
- `evidence_backed_item_rate`：引用非空且全部有效的项数 / 总事实项数。
- `resume_item_count` 包含 headline、summary 和 section items；`evidence_reference_count` 为全部引用出现次数。

**Python 只验证结构、引用有效性和数字来源，不能证明自然语言的全部语义必然成立。** 技术、资格、GAP/PARTIAL 的语义真实性仍依赖 Prompt 和人工审阅。语言只确定性检查 enum、非空内容及与请求一致，不做复杂 NLP 或 LLM-as-judge。合法引用不等于事实语义正确。

## Persistence、门禁与历史

SQLite schema v4 幂等升级 v1/v2/v3，新增单表 `resumes`：id、job_id、profile_version、language、status、structured_resume_json、markdown、evidence_json、created_at，及 job/time index。既有 Profile/Evidence/Job/trace 保留；未知更高版本拒绝。

初始读取同一事务确认 Job 存在、completed、current；Profile confirmed，版本与 Job 一致。最终 `BEGIN IMMEDIATE` 内重新读取 Profile/Job，复查版本、状态和全部 Evidence refs，再渲染并原子插入 completed artifact。Profile 中途变化返回 PROFILE_CONFLICT，Job 删除/失效或 Evidence inactive 也拒绝。没有 failed draft 或半完成 Resume。

每次明确生成独立 UUID，不覆盖旧 Resume。Evidence 快照只保存实际引用的 id/type/title/normalized_content，旧 Evidence inactive 或后续确认复用同一 ID 都不会改变历史引用显示。

动态 outdated：当前 Profile 未确认、版本与 Resume 不同、linked Job outdated/版本不同/非 completed、或者 linked Job 不存在。Profile 更改不删除或重生成历史 Resume。job_id 保留逻辑绑定，刻意不设级联删除外键；Job 删除后 Resume 仍可展示且 outdated。删除 Resume 只删除该 artifact，不影响 Profile/Job/Evidence。

## API 与前端

| Method | Path | Behavior |
| --- | --- | --- |
| POST | `/api/jobs/{job_id}/resumes` | 显式 `{language: zh/en/ja}`；执行图；返回 resume_id、run_id、resume |
| GET | `/api/jobs/{job_id}/resumes` | 按创建时间倒序历史列表及动态 outdated |
| GET | `/api/resumes/{resume_id}` | 结构、Python Markdown、历史 Evidence 快照、版本、语言、outdated |
| DELETE | `/api/resumes/{resume_id}` | 仅删除 Resume |

错误区分 JOB_NOT_FOUND、JOB_NOT_COMPLETED、JOB_OUTDATED、PROFILE_VALIDATION_FAILED、PROFILE_CONFLICT、MODEL_OUTPUT_INVALID、RESUME_GROUNDING_FAILED、RESUME_VALIDATION_FAILED、RESUME_NOT_FOUND，沿用 generation/persistence 错误。

Job Detail 增加独立语言选择，默认中文，与 UI locale 解耦；明确点击生成，不自动调用。loading、同步 ref 锁防止重复点击、错误反馈、列表 open/delete。Job outdated 禁止生成。

`/resumes/:resumeId` 展示 linked Job、语言、版本、current/outdated、结构化正文、保存的 Markdown View、逐事实展开 Show Evidence。浏览器 print 按钮调用 window.print；A4 print CSS 隐藏导航、控件、Evidence、页眉页脚，白底黑字，标题避免与后文分离，段落/条目避免断页。HTML 通过 React text nodes 展示同一 structured payload，无 raw HTML；Python renderer 转义 Markdown 控制字符，不将 Evidence IDs 放进正文。没有 backend PDF/DOCX 依赖。

## Trace、fake/replay 与 eval

七个节点全部使用现有 RunTrace。Generation metadata 沿用 role/provider/model/prompt_version/structured_output_mode/latency/tokens/retry。grounding 和 language 结果写入 validation_result；失败时保留安全 counts。普通 trace 只保存形状与长度；全文需 development + AI_DEBUG_PAYLOADS=1，默认 0。

Fake 为 deterministic identity demo：summary、skill、experience 都引用输入中已有 Evidence，不模拟文风、相关性或翻译质量。完整 fake workflow 0 live。

独立 Debug replay API/UI 新增 resume-generation-v1。task + prompt_version + canonical hash 精确寻址，缺失/非法记录明确失败，无 live fallback，不写业务状态或 runs。

`evals/resumes/cases.json` 有 6 个 reviewed synthetic cases：有效 baseline、伪造合法格式引用、零引用事实、99% 无来源数字、与请求不一致的语言、缺失 summary。baseline 为合成的确认版本 3 Evidence / completed Job context，MATCH Python、PARTIAL React（缺 TypeScript）、GAP Kubernetes，所有内容人工构造；独立 harness 不连接业务 DB。其余五项为有意非法输出，验证各层能拒绝。

```bash
uv run python scripts/eval_resumes.py
uv run python scripts/check_http.py --resume --frontend http://127.0.0.1:5177
```

离线结果：[offline.json](../evals/results/phase_4/offline.json)，6/6 预期接受/拒绝通过。baseline 6 items / 7 refs，invalid refs=0、unsupported=0、numeric failures=0、backed rate=1.0。fixture 分数只验证回归，不代表真实模型总体质量。

## Regression 与实际 HTTP

全部通过（自动验证 0 live requests）：

```text
uv run pytest                                  267 passed
uv run ruff check backend scripts              passed
uv run ruff format --check backend scripts     passed
npm --prefix frontend run check                passed
npm --prefix frontend test                     21 passed
npm --prefix frontend run build                passed
npm --prefix frontend audit                    0 vulnerabilities
uv run python scripts/eval_profile.py           passed
uv run python scripts/eval_jobs.py              passed
uv run python scripts/eval_resumes.py           6/6 expected outcomes
```

后端新增 30 个 cases：门禁、strict schema、无效/inactive/其他版本 Evidence、空/重复引用、数字、language、跨 Evidence 数字、renderer exact text、并发修改 Profile/删除 Job/变更 Job 状态/撤销 Evidence、原子失败、restart/history/delete、隐私、replay、v3 migration。前端新增 5 个 tests：显式动作/语言/loading/防重复、失败恢复、outdated gate/list/delete、detail/Markdown/Evidence/print、历史 outdated 可读。

实际 Vite + Uvicorn 使用 fake、development、空 `/tmp/career-copilot-phase4-http.sqlite3`，`--resume` 通过 Profile → Confirm → Job → Resume → GET/list/trace → Profile changed → outdated → delete。无真实业务 DB 写入。临时服务在检查后关闭。

## 唯一一次 Live baseline 与 STOP

离线全部通过后运行一次：

```bash
AI_MODE=live AI_DEBUG_PAYLOADS=0 uv run python scripts/eval_resumes.py --live --output evals/results/phase_4/live.json
```

仅 `resume-01`，1 GLM request，max_attempts=1，retry=0；无 Jev、Profile extraction/normalization、matching 或真实 UI workflow。只有 synthetic 上下文，无真实用户资料，未开启全文 debug。

[Live result](../evals/results/phase_4/live.json)：provider=`openai-compatible`，model=`glm-5.3-flash`，structured_output_mode=`json_object`，latency=**30084.153 ms**；错误 `MODEL_REQUEST_FAILED` / `ReadTimeout`。Provider 沿用 30 秒 timeout，**没有收到可验证响应**，HTTP success、JSON、Pydantic、grounding、Markdown 成功条件均未成立。usage 未返回，记录 input/output/total=0/0/0 是缺失值约定，不能证明实际消耗为零。resume item/ref/invalid refs/unsupported/numeric metrics **不可用**，不能伪造为 0 或通过。

失败后立即停止 live，没有调 prompt、改 timeout、fallback、repair 或第二次请求。不能声称 Phase 4 完整验收成功。重新验证需新授权；本阶段未开始 Resume Revision、Cover Letter、Tracker 或 Phase 5。

## Known limits

- 结构/引用/数字验证不能证明语义真实性；轻量 language validation 不识别混合语言或错误翻译。
- Fake 原样复制 Evidence，不模拟语言或内容质量；live 输出质量与 grounding 尚未验收，失败原因仅能定位请求超时。
- item 为自包含事实列表，没有复杂雇主/日期分组、模板、编辑器或复杂版本树。
- 有 print CSS 和按钮测试，无真实浏览器分页/PDF 视觉验收；无 auth、pagination、durable checkpoint。
