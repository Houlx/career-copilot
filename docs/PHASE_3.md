# Phase 3 — Job Analysis

完成日期：2026-10-10（Asia/Shanghai）。仅 Job Analysis；未开始 Phase 4 Resume。全部实现验证使用 fake/replay，0 live API calls。

## Schema and atomic publication

SQLite schema v3 幂等增加 `jobs`、`job_requirements`、`job_matches`，保留 v1/v2 Profile 和 trace；未知更高版本拒绝启动。

- jobs：UUID id、raw_jd、title、status、profile_version、coverage、created_at、updated_at、completed_at。支持 draft/analyzing/completed/failed 状态约束；当前仅在成功时原子插入 completed，失败不创建业务行。raw JD 原样保存，最大 30,000 字符。
- job_requirements：job_id + Python 分配的 req-001 等复合主键、text、importance、category、scoreability、sequence。一次报告内 ID 稳定，不跨 extraction 做语义 identity。
- job_matches：job_id + requirement_id 复合主键和外键、status、reason、evidence_ids JSON。删除 Job 级联删除要求和匹配，保留 Profile/Evidence 和 trace。

模型阶段全部通过后 `BEGIN IMMEDIATE`，复查当前 confirmed Profile 及版本、引用，然后插入 Job/全部要求/全部匹配、计算并存 Coverage 后提交。要求插入后或匹配中途失败均完整回滚。Profile 在分析过程中被修改则返回 PROFILE_CONFLICT，拒绝发布旧结果。

## Contracts and deterministic policy

Judge `jd-extract-v1` 输出 strict `JobExtraction`：title + requirements，每条仅含 text / importance / category / scoreability；无业务 ID。required/preferred；category 为 education/experience/skill/certification/language/capability/logistics/personal_trait/other。最多 100 条。保留 OR、条件学历表达，不机械地将全部职责变为评分项。空 requirements 合法。

scoreability 为 SCORED / NEEDS_CONFIRMATION / NON_SCORABLE。可验证技能、学历、项目、可观察业务能力通常 SCORED；出差、搬迁、班次、驾照、availability 等现实条件待用户确认；健康、道德和模糊性格通常 NON_SCORABLE。Python 仅对 SCORED 中包含「身体健康、吃苦耐劳、爱岗敬业、道德品质」的要求改为 NON_SCORABLE，输出 `scoreability_adjusted` 布尔值进入节点摘要/diff；不引入大型关键词系统。团队协作交付等可观察行为可保留评分。

Generation `matching-v1` 输入带原始 zero-based requirement_index 的所有 SCORED 要求 + **全部 active 当前确认版本 Evidence**（id/type/title/normalized_content）。不检索、不截取证据，不把 retrieval miss 当事实缺失。输出 strict `Matching.matches`：requirement_index / MATCH|PARTIAL|GAP / evidence_ids / reason。Python 映射真实 req ID。

Python 验证每个 SCORED 恰有一个结果，无多余或遗漏，MATCH/PARTIAL 必须引用非空 Evidence，所有引用须存在、active、属于使用的 confirmed Profile 版本。GAP 可空引用；若引用，仅解释相关经历为何不足。Python 不尝试证明自然语言语义绝对正确。历史报告通过保留的 inactive Evidence 展示旧引用内容，不将历史证据用于新匹配。

Coverage 完全由 Python：required=2，preferred=1；MATCH=1，PARTIAL=0.5，GAP=0。仅 SCORED 进入分子/分母：`100 × Σ(weight × status_value) / Σ(weight)`，保留一位小数。零 SCORED → null，跳过 Generation 请求，UI 显示「可自动评分的岗位要求不足」。UI 名称「岗位要求覆盖度」（JD Requirement Coverage），说明「表示当前已确认职业资料对可自动评分岗位要求的覆盖程度，不代表获得面试或录用的概率。」

读取时运行时计算 outdated：当前 Profile 不存在、未确认，或版本不同均 true。修改及再次确认均不更新或删除历史 Job，也不自动重分析。

## Workflow / trace / API / UI

独立 `job_analysis_workflow`，版本 job-analysis-v1，固定八节点：

```text
load_confirmed_profile → extract_job_requirements → validate_requirements
→ apply_scoreability_guard → match_requirements → validate_matches
→ calculate_coverage → persist_job_analysis
```

第一节点必须使用 `ProfileStore.confirmed_profile()`，禁止 last_confirmed_profile 绕过门禁。每节点沿用 RunTrace，AI 节点记录 role/provider/model/prompt version/tokens/latency/retry；failed run 可定位失败节点。纯 Python scoreability adjustment 记录在输出摘要。默认不保存/返回全文，显式开发调试继续脱敏，不记录异常原文或 hidden reasoning。

| API | Behavior |
| --- | --- |
| GET /api/jobs | 倒序历史，含 outdated |
| POST /api/jobs/analyze | raw_jd，显式运行，返回 job + run_id |
| GET /api/jobs/{id} | 报告、要求、匹配和引用的历史 Evidence |
| DELETE /api/jobs/{id} | 删除报告及要求/匹配 |

`/jobs` 支持粘贴 JD、明确点击分析、loading、防重复、failure/retry、确认门禁、历史列表。`/jobs/:id` 显示 title、Coverage、版本、outdated、raw JD、importance/category/scoreability、status/reason，展开 Evidence 显示 type/title/normalized_content；不默认展示 raw JSON。Dashboard 保留 Profile 状态/版本并显示最近五个报告、Coverage 和 outdated。Debug Runs 可查看图及选择两个新任务独立 replay。未新增 Resume 功能。

## Offline replay / evaluation

两个任务分别按 task + prompt_version + canonical input hash 精确寻址；Debug replay 强制 offline，不创建 workflow run，不写业务数据，不回退 live。Debug UI 使用有对应 fixture 的 synthetic 输入。没有 fixture 时明确失败。

fake extraction 返回固定 synthetic demo job（Python、SQL、出差、健康），**不代表对粘贴 JD 的真实提取**；UI 明确标识。fake matching 用完整证据文本字面包含匹配，组合 `and` / `/` 的部分支持可 PARTIAL，否则 GAP，引用只来自输入。它仅用于流程验证，无语义准确率保证。

```bash
uv run python scripts/eval_jobs.py
uv run python scripts/eval_jobs.py --predictions /path/to/predictions.json
```

predictions 是 suite → case ID → structured output。12 个 jd_extraction、8 个 scoreability、5 个 matching synthetic reviewed fixtures；JD 覆盖 required/preferred、教育、技能、语言、经验、健康、性格、OR、条件学历等。Scoreability 评估 Judge 输出经过 Python guard 后的最终结果。输出 omission/addition、importance/category accuracy、各 scoreability accuracy、status agreement、valid citation rate、unsupported positive match rate，附原始分子分母；无分母时 rate=null。抽取以精确 text 匹配，不做语义等价。unsupported positive 按 reviewed reference status/citations 判断，不能证明任意新输入的语义正确性。扰动测试验证指标能识别错误。

默认 reference replay 的全对分数只证明回归链路，不代表 live 模型总体准确率。所有 fixture 为 synthetic，没有提交真实用户 Profile/JD。

## Verification / known limits

保留 Phase 1/2 所有检查，另加 eval_jobs。`scripts/check_http.py --jobs` 包含 Profile 流程，仅连接 **空临时数据库、fake、development** 服务；实际 Vite proxy HTTP 验证报告、八节点、Coverage、Evidence、两任务 replay、outdated 和门禁。额外实际后端进程重启验证持久化。

- 后端 111 tests；前端 16 tests。前端为 mock fetch 交互，HTTP 为真实本地服务；未增加完整浏览器 E2E。
- 结构和引用合法不等于语义完全正确，MATCH/PARTIAL 边界及 fresh extraction 数量波动接受。没有 live quality tuning。
- 全量 Evidence 受模型上下文限制；当前 Profile 小，未引入 batching/RAG/embedding。输出 token 上限 6000，超限/非法输出明确失败。
- 本地单用户，无 auth、分页/筛选、durable checkpoint 或崩溃恢复；历史列表当前返回完整报告，适合小规模本地使用。
- NEEDS_CONFIRMATION 只展示，不实现回答和纳入评分。Profile gate 的前端状态加载后不持续订阅变化，后端每次仍强制检查。
- 未保存 JD 不跨导航保留；失败保留当前输入，trace 保留失败定位；业务表不保存 failed draft。
- 延后 semantic identity、canonical cache、专业 ontology、复杂 rubric、Resume/Agent/插件/MCP/多代理。
