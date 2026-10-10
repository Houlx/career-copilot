# Phase 2 — Career Profile

完成日期：2026-10-10（Asia/Shanghai）。仅 Career Profile；没有开始 Phase 3。

## Data model / migration

SQLite schema v2 在 v1 runs/nodes 基础上幂等增加两个业务表，保留已有 trace。`PRAGMA user_version=2`；遇到未知更高版本拒绝初始化。没有大型 migration framework。

`career_profile` 是 id=1 的单用户 current Profile：

- `raw_input`：当前原文，保存时不 strip；20,000 字符上限，拒绝全空白。
- `raw_history`：JSON 原文历史，每次实际变更追加原文、时间、版本；早期原始输入永久保留在本地 DB。
- `extracted_facts`：最后一次成功 normalization 的 Judge facts。
- `normalized_raw_input`：对应的 raw 快照，用于阻止新 raw 直接确认旧整理结果。
- `ai_normalized_profile` / `user_edited_profile`：独立 JSON 版本；有效版本优先用户编辑。
- `confirmed` / `confirmed_at` / `profile_version` / timestamps。
- `last_confirmed_profile`：上次确认的有效内容、版本、时间；raw 或草稿变化不覆盖它，下一次成功确认才替换。

`career_evidence`：业务 `id TEXT PRIMARY KEY`、profile_version、type、title、raw_content、normalized_content、active、timestamps。类型是 education/work/project/skill/other。raw_content 为 JSON 文本，包含 source_quote、extracted_content、显式 user_edit；normalized_content 为当前有效条目的内容。

raw resave、编辑或成功替换 normalization 时撤销当前确认，保留上次确认快照及 Evidence。新 raw 未完成整理时不允许编辑/确认旧草稿。AI 失败不做任何业务写入，故不会破坏调用前的 Profile、确认状态或 Evidence。若调用前用户已保存新 raw，当前确认已由该显式保存撤销，旧确认快照与证据仍保留。

## Versions / transactions

首次 raw 保存版本为 1。实际 raw 变化、成功替换整理结果、实际用户编辑、从未确认变为确认时分别 +1。相同 raw 保存、相同编辑、相同 facts/AI 内容且无用户编辑的重复 normalization、已经确认后的重复确认是 no-op，不改版本、ID 或时间。

所有写 API 要求 `expected_version`；创建前是 0。`BEGIN IMMEDIATE` 在同一连接内检查并更新。冲突返回 409；工作流加载与最后写回均校验版本，拒绝并发期间变旧的模型结果。普通读取开启 read transaction，保证 Profile 与 Evidence 是同一 SQLite 快照。

确认在一个事务里：读取当前有效 Profile → 校验 → 将旧 Evidence inactive → upsert 当前 Evidence → 更新 confirmed、confirmed_at、version、确认快照。任何中途 SQL 失败整体回滚，包括已经写入第一条 Evidence 后第二条失败。

未来业务必须使用 `ProfileStore.confirmed_profile()` 的门禁；不能单独因为存在 active Evidence 就绕过确认。

## Evidence IDs

Python 分配：`career-{type}-{sha256(canonical entry + raw provenance)}`，模型没有 ID 字段，不使用自增主键。canonical JSON key 排序，保留 Unicode，哈希输入包含 source_index。

确认和刷新不重新编号；重复操作无内容变更不重新编号。条目内容、类型、标题、来源或 source_index 发生变化时保守生成新 ID，旧行标 inactive 保留。未变化条目复用 ID，upsert 更新其 profile_version。source_index 随重新 extraction 改变可能导致 ID 改变，本阶段不做实体匹配或语义去重。

## Model contracts / grounding

`profile-facts-v1`：Judge，strict Pydantic `ProfileFacts`，禁止额外字段。facts 中每条含 type/title/content/source_quote；quote 必须是 raw 的连续原文片段。prompt 明确禁止推断技术、年限、管理职责、成果数字、学历等价及夸张润色。数值校验拒绝 facts 标题/内容中出现 quote 未包含的数值。

`profile-normalize-v1`：Generation，strict `NormalizedProfile.entries`，每条含 source_index/type/title/content。**输入仅为 Judge facts，不含 raw_input**。每个 fact 必须恰好有一条 entry，引用合法且唯一，type 保持一致，标题+内容中的数字集合保持一致。两个 prompt 都 temperature=0，max output tokens=6000；diagnostic-v1 语义和 128 token 限制不变。

这些是结构、原文引用、数字层的确定性校验，不能证明所有自然语言语义均受来源支持。最终事实边界仍依赖 Judge prompt 与用户明确审阅确认。没有 live quality tuning。

用户可编辑现有条目的类型、标题、内容；用户新增描述会记录为 user_edit，并保留原 Judge quote。编辑不调用 AI。当前 UI 不支持增加/删除条目；可修改原始资料后重新整理。API 支持保留现有来源的非空子集，不支持无来源的新 source_index。

## Fixed workflow / tracing

```text
START
→ load_raw_profile
→ extract_profile_facts
→ validate_profile_facts
→ normalize_profile
→ validate_normalized_profile
→ persist_profile_draft
→ END
```

独立 LangGraph `profile_workflow`，版本 profile-v1。所有六个节点经 Phase 1 RunTrace wrapper，AI 节点分别记录 role/provider/model/prompt/tokens/latency/retry_count。没有模型选边；确认不属于 Graph。

ErrorCode 保留 MODEL_REQUEST_FAILED / MODEL_OUTPUT_INVALID / PERSISTENCE_FAILED，业务 fallback 分别 PROFILE_EXTRACTION_FAILED、PROFILE_NORMALIZATION_FAILED；另有 PROFILE_VALIDATION_FAILED、PROFILE_NOT_FOUND、PROFILE_CONFLICT。失败响应含 run_id（Graph 已开始时），原始异常文本不写入 trace 或响应。

`AI_DEBUG_PAYLOADS=0` 默认只有形状摘要、变化字段；显式打开且处于 development 才记录 raw、facts、模型输入/输出、prompt。写入前沿用既有 secret/Bearer/sk-token redaction。默认隐藏旧 payload，不记录 hidden reasoning。

## API / frontend

| Endpoint | Body / output |
| --- | --- |
| GET /api/profile | profile（不存在时 null），有效版本、状态、Evidence、原始历史与确认快照 |
| POST /api/profile/raw | raw_input + expected_version |
| POST /api/profile/normalize | expected_version → profile + run_id |
| PUT /api/profile | expected_version + profile.entries |
| POST /api/profile/confirm | expected_version → confirmed profile + evidence |

Profile 页面：raw textarea、保存、显式 AI 整理、逐条编辑、保存编辑、显式确认、Evidence 数量/ID、查看原始 AI 版本及 trace 跳转。区分 empty/raw_saved/normalizing/draft_ready/edited/confirmed/failed；前端运行/失败状态不做 crash recovery 持久化。未保存 raw 或编辑会禁用依赖按钮；运行时阻止重复提交。

Dashboard：Not started / Draft / Confirmed、Evidence count、Profile version、Last updated（Asia/Shanghai）。未确认时 count 是保留的上次确认 Evidence 数量，不代表当前版本获准下游使用。保存后导航回来或刷新从 DB 恢复；未保存表单不跨页面保留。

## Fake / replay / evaluation

fake Judge 把任意输入原文包为单条 other fact，Generation 逐条原样整理，不硬编码任何真实用户资料；这是离线流程 fixture，不是提取质量模拟。

两个 AI tasks 都可以通过 `POST /api/debug/replay` 独立执行，按 task + prompt_version + canonical input hash 精确选择 fixture。强制 replay，即使主模式 live 也不构造 HTTP client；无 fixture 明确失败。既不创建 run，也不改 Profile/Evidence。Debug UI 提供任务选择及 synthetic 固定输入。

`evals/profile_extraction/cases.json` 有 10 个 synthetic reviewed reference cases，覆盖教育、工作、项目、技能、数字、中英技术名称、缺失信息、模糊表达。每个 case 的 facts 与 normalize 输出均有 replay fixture。

```bash
uv run python scripts/eval_profile.py
uv run python scripts/eval_profile.py --predictions /path/to/case-id-to-facts.json
```

指标：精确 `(type,title,content,source_quote)` multiset 的 missing/extra 数量；按 `(title,source_quote)` 匹配的 type_correct/type_total；每个 source 的显式数字集合保留数。严格对比会把措辞差异计为 omission/addition，不做同义词或多语言等价判断。默认 reference replay 的 0 omissions、0 additions、10/10 types、6/6 numbers 仅证明回归链路；可传离线 predictions 对比其他输出，另有扰动测试确保指标能识别错误。

## Validation / limits

后端覆盖 v1 upgrade、restart、raw history、编辑、显式确认、IDs 稳定、部分写入回滚、AI/DB 失败保留、并发冲突、facts/normalized 非法输出、引用/数值边界、metadata、payload redaction、独立 offline replay。Provider 协议测试只用 MockTransport，fake/replay 不构造网络客户端；pytest 默认禁 TCP。

前端测试覆盖输入、整理、编辑、确认、failure/retry、normalizing 防重、确认展示、保存后导航恢复、Dashboard 状态和 Phase 1 诊断。HTTP 检查 `scripts/check_http.py --profile` 仅用于空临时 DB + fake 服务；覆盖实际 Vite proxy/FastAPI/LangGraph/SQLite、确认、稳定 IDs、两任务 replay。另实际重启后端进程验证恢复。

没有 live API calls，没有模型质量评估、实体合并、复杂 Evidence diff、durable checkpoint、auth/multi-user 或完整浏览器 E2E；原始历史和 inactive Evidence 随本地使用增长。

**Outdated rule**：一旦 confirmed Profile 后续被修改，未来已有 Job Analysis 和 Resume 应标记 outdated。后续 Job/Resume 保存生成时的 profile_version，与 current version 比较即可；确认门禁和版本关系都必须检查。本阶段不创建 jobs/resumes 表。
