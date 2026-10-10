# Career Copilot handoff

更新日期：2026-10-10（Asia/Shanghai）

## Status

**Phase 3.5B-R3 Step 2–4 Live Acceptance 完成，已停止，未开始 Phase 4。**

R2 已验证 GLM json_object Profile contract。本轮 GLM `openai-compatible / glm-5.3-flash / json_object` 的 JD jd-01 通过 HTTP 200、JSON parsing、严格 Pydantic 及原 JD validation：title=`Synthetic role 1`，1 个 `Python / required / skill` requirement。exact metrics：requirement_missing=0、unsupported_addition=0、importance_correct/total=1/1、category_correct/total=1/1，两个 accuracy 均 1.0。title 与 reference 相同，但现有 metrics 不评估 title。

Jev `jev-1.13.0` native Choice 单例 score-0（身体健康）raw/guarded/reference 均 NON_SCORABLE。8-case reviewed benchmark 合并为一次请求，raw=8/8、guarded=8/8（均 100%）；SCORED=2/2、NEEDS_CONFIRMATION=2/2、NON_SCORABLE=4/4（raw/guarded 相同）。guard corrected=0、harmed=0，全部 guard_adjusted=false。

JD / Jev single / Jev batch latency=7952.355 / 550.898 / 527.180 ms；input/output/total tokens=151/547/698、495/51/546、2122/389/2511。实际请求数 **3**，retry 全为 0；合计 input/output/total tokens=2768/987/3755，调用 latency 合计 9030.433 ms。所有 request_success/schema_mapping_success=true。

详见 [PHASE_3_5B_R3.md](PHASE_3_5B_R3.md) 的逐 case、结果链接和限制。仅文档/result 变化，未重跑完整 regression；无网络结果核验通过。AI_DEBUG_PAYLOADS=0，无真实用户数据、业务 graph/DB、normalization/matching live、prompt tuning 或 guard 扩展。100% 仅描述这 8 个 synthetic cases。提交 Phase 3.5B-R3、确认 clean 后 STOP，没有额外 live 授权。

以下 R2 及更早状态保留为当时记录；其中未执行 JD/Jev 的说明为历史，R3 为最新状态：

**历史：Phase 3.5B-R2 Provider Contract Adaptation 完成，唯一一次新 synthetic Profile live 通过四层验证，已停止。**

Generation structured-output capability 显式可配置：`GENERATION_STRUCTURED_OUTPUT=json_schema|json_object`，默认 json_schema，保留旧 strict/schema wire request；本地 GLM `.env` 显式为 json_object。两种模式本地严格 schema/grounding、R1 failure diagnostics 共用且未放宽；prompt、fixture、业务 validators 未改。Settings API/页面和 AI metadata 展示 structured_output_mode，Jev 配置与原生 contract 不变。

live 前全部离线检查通过：后端 **237 passed**、前端 **16 passed**、Ruff/format、frontend check/build/audit（0 vulnerabilities）、Profile/Jobs eval（**0 live requests**）。随后仅原 education case 一次：GLM `glm-5.3-flash` / json_object，HTTP/JSON/Pydantic/business 全通过，6027.08 ms，tokens 166/335/501，retry=0。eval 精确匹配因 title 与 reference 不同记 omission=1/addition=1/type=0/1；实际 type=education，content/source_quote 相同，不能把合同成功当质量满分。debug 已恢复 0。

详见 [PHASE_3_5B_R2.md](PHASE_3_5B_R2.md) 及 [脱敏结果](../evals/results/phase_3_5b_r/profile-live-r2.json)。R2 request count=**1**；3.5B-R/R1/R2 synthetic Profile education 累计 3 次。没有 JD/Jev/normalization/matching/真实 graph live，没有 Phase 4。提交 `Phase 3.5B-R2`、确认 clean 后 STOP。当前没有后续 live 授权；恢复 Step 2–4 须用户重新授权范围与预算。

以下 R1/3.5B-R 状态保留为历史背景：

**历史：Phase 3.5B-R1 Structured Output Failure Diagnosis 完成，唯一一次新的 live 请求仍失败，已停止。**

本轮先通过全部离线验证：后端 **212 passed**、前端 **16 passed**、Ruff/format、frontend check/build/audit（0 vulnerabilities）、Profile/Jobs eval（0 live requests）。之后仅调用原 synthetic education 一次，retry=0：GLM `glm-5.3-flash` HTTP 200，6012.51 ms，tokens 113/377/490。明确失败在 `STRUCTURED_JSON_INVALID`，path=`choices.0.message.content`，type=`JSONDecodeError`，assistant_content_kind=`markdown_fence`；未进入 Pydantic 或 business validation。

新增 value-free 分层诊断，外部错误合同保持不变；只有 development + AI_DEBUG_PAYLOADS=1 保存安全的 loc/type/msg/shape。本次 debug 仅对子进程开启，已确认恢复 0；不保存完整失败正文。prompt/schema/业务规则/模型/retry/fallback 均未改变，没有 strip fence 或 repair。

原 3.5B-R 第一次失败无正文，根因不可追溯，不能断言等同于本次 fence 原因。智谱文档仅列出 text/json_object，记录当前 json_schema/strict 请求的 **provider capability mismatch**，不能推断 strict 已执行；没有据此更改请求格式或放宽 schema。

R1 live request count=**1**；连同原 3.5B-R education 请求累计 2 次。详见 [PHASE_3_5B_R1.md](PHASE_3_5B_R1.md) 与 [脱敏结果](../evals/results/phase_3_5b_r/profile-live-r1.json)。提交 `Phase 3.5B-R1` 并确认 clean 后 STOP。后续需用户重新授权 provider contract 处理方案与 live 预算；不能自动继续 JD/Jev、Step 2–4 或 Phase 4。

以下 3.5B-R 状态保留为历史背景：

Generation/GLM：profile-facts-v1（只改 role）、profile-normalize-v1、jd-requirements-v2、matching-v1。Judge/Jev：scoreability-v1。Job graph 为 job-analysis-v2，新增 classify_scoreability，九节点；Jev 一个请求批量 Choice，Python 完整映射校验后继续原有最小 guard。Report/DB/frontend contract、Coverage、全量 Evidence matching 不变。旧 jd-extract-v1 与历史 replay 保留。

完整 regression 全通过：后端 **189 passed**、前端 **16 passed**、Ruff/format、check/build/audit（0 vulnerabilities）、Profile/Jobs 离线 eval，自动验证 **0 live requests**。新 raw/guarded scoreability replay 均 8/8；这是 fixture 回归分数。

本阶段真实请求 **1 次**，无重试：GLM `glm-5.3-flash`，synthetic Profile education case，HTTP 成功但严格输出校验失败 `MODEL_OUTPUT_INVALID`；3878.029 ms，tokens 113/243/356。按照用户规定“Step 1 失败 → STOP”，没有执行 JD extraction、Jev 单 case 或 8-case benchmark；Jev live 质量/latency/tokens 暂无数据。没有 prompt tuning、真实用户数据或真实业务 graph。详见 [PHASE_3_5B_R.md](PHASE_3_5B_R.md) 和 [脱敏结果](../evals/results/phase_3_5b_r/profile-live.json)。失败正文未保留，具体字段原因未知。

该历史调查已在 R1 执行一次，后续边界以上文 R1 为准；不要自动重试或启动 Phase 4。原 [PHASE_3_5B.md](PHASE_3_5B.md) 作为 Jev 开放文本 extraction 能力阻塞记录保留。历史 Phase 3.5A diagnostic 两次成功仅证明 wire smoke，不能替代业务验收。

## Read first next session

1. 本文及 `docs/PHASE_3_5B_R3.md`，再读 R2 及历史 R1/3.5B-R 文档。
2. `docs/PHASE_3_5B.md`：历史接口能力发现。
3. `docs/PHASE_3_5A.md`、`docs/PHASE_3.md`、`docs/ARCHITECTURE.md`；Profile 规则见 `docs/PHASE_2.md`。
4. 按获授权阶段定向读代码，不重新全仓库审计。

以下 Phase 3 交付/验证为历史记录；当前模型职责和 workflow 以上文及 3.5B-R 为准。

## Delivered in Phase 3

- SQLite schema v3：jobs / job_requirements / job_matches；v1/v2 幂等升级，保留 Profile/Evidence/trace，未知更高版本拒绝。
- 独立固定八节点 `job_analysis_workflow`（job-analysis-v1）。第一节点使用 `ProfileStore.confirmed_profile()`；最终事务内复查版本及引用，避免并发发布旧结果。
- Judge `jd-extract-v1` strict title + requirements（importance/category/scoreability），Python 分配 req-001 等报告内稳定 ID。
- Python 最小 scoreability guard：SCORED 中身体健康、吃苦耐劳、爱岗敬业、道德品质改 NON_SCORABLE，trace 保存 scoreability_adjusted；观察得到的协作能力仍可 SCORED。
- Generation `matching-v1` 使用所有 SCORED requirements + 全部 active 当前 confirmed version Evidence；无 retrieval/RAG。只向模型提供 id/type/title/normalized_content。
- 每个 SCORED 恰有一个 MATCH/PARTIAL/GAP；positive 必须引用非空有效 active 当前版本 Evidence。无多余/漏要求，所有引用严格验证。
- Coverage 由 Python：required=2、preferred=1；MATCH=1、PARTIAL=.5、GAP=0，仅 SCORED，加权百分比保留一位小数，零 SCORED=null 且跳过匹配模型调用。
- 原子最终插入 completed Job/requirements/matches/Coverage。任何模型、校验、插入失败均无半完成业务数据；Profile/Evidence/已有 Job 保留。当前不保存 failed draft。
- 读取时 `!current.confirmed || saved_version != current_version` 判 outdated，Profile 不存在也 outdated；历史 Job 不删除、不自动重分析，引用可展示保留的 inactive Evidence。
- 四个 Jobs API：GET /api/jobs、POST /api/jobs/analyze、GET/DELETE /api/jobs/{id}。
- `/jobs` 输入、明确分析、loading、防重复、failure/retry、确认门禁、历史；`/jobs/:id` 报告、版本、outdated、raw JD、要求、匹配解释和可展开 Evidence。Dashboard 最近五个报告及 Coverage/outdated。未实现 Resume 功能。
- 八节点 trace、两 AI 节点完整 metadata，沿用默认全文隐藏和脱敏。两个 tasks 独立强制 offline replay，不写 runs/business；Debug UI 提供 synthetic 示例。
- 12 extraction / 8 scoreability / 5 matching synthetic reviewed fixtures 与 replay；独立 `scripts/eval_jobs.py`，支持 suite → case ID → output 的离线 predictions。

## Preserve Phase 2 semantics

- 有效 Profile 优先 user_edited_profile，否则 ai_normalized_profile；原文历史永久追加，AI 原始版本、用户版本、确认快照分开。
- raw、normalization、实际编辑撤销当前确认；新 raw 未整理时不能确认旧草稿。last_confirmed_profile/旧 Evidence 保留，不能绕过当前确认门禁。
- 首次 raw v1；实际保存变更、替换 normalization、编辑、从未确认变确认分别 +1；无变化操作 no-op。
- 所有 Profile 写要求 expected_version；模型结果最终保存再次检查。确认与 Evidence 在同一事务。
- Evidence ID 为 Python canonical entry + raw provenance SHA-256；实际变化保守换 ID，旧行 inactive 保留，不做语义实体匹配。

## Validation

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

最终验证：后端 **111 passed**，前端 **16 passed**；Ruff、format、TypeScript/Prettier、生产构建全部通过，npm audit **0 vulnerabilities**。

Profile reference replay：10 cases，omission=0、addition=0、type=10/10、numbers=6/6。
Jobs reference replay：12 extraction missing/addition=0、importance/category=12/12；8 scoreability SCORED=2/2、NEEDS_CONFIRMATION=2/2、NON_SCORABLE=4/4；5 matching status=5/5、valid citations=4/4、unsupported positive=0/4。均有扰动指标测试；**这些是 reviewed fixture 回归分数，不代表真实模型总体准确率**。

实际 `uv run python scripts/check_http.py --jobs --frontend http://127.0.0.1:5177` 已在空临时 DB、fake、development 服务运行通过，包括 Phase 1/2 流程。另终止并重启实际后端进程，确认完整 Job/requirements/matches/Coverage/outdated 恢复。临时服务已关闭；项目业务 DB 未写入测试数据。

**Phase 3 历史验证为 0 live API calls**；Phase 3.5A 另完成上文两次 synthetic smoke，未发送真实 Profile/JD 到外部 Provider。pytest 默认禁 TCP，前端 mock fetch，实际 HTTP 仅 loopback。fixtures 全部 synthetic。

## Run

```bash
uv sync --locked
npm ci --prefix frontend
cp .env.example .env
uv run uvicorn career_copilot.main:app --host 127.0.0.1 --port 8000 --reload
# another terminal
npm --prefix frontend run dev
```

前端 5173、后端 8000；`/profile` → 确认 → `/jobs`；Debug `/debug/runs`。默认 fake。fake Profile 原样保留为 other fact；fake JD 固定 synthetic 要求，并非实际粘贴 JD 提取；fake matching 用完整 Evidence 字面包含及组合部分支持展示 MATCH/PARTIAL/GAP，仅验证流程。

## Limits / deferrals

- 无 live quality tuning；引用合法不保证语义判断绝对正确。接受 fresh extraction 数量/拆分波动、MATCH/PARTIAL 边界波动。
- 全量 Evidence 适用于当前小 Profile，未来上下文限制可再处理；当前无 RAG、embedding、batching 或大型 ontology/rubric。
- NEEDS_CONFIRMATION 仅展示，未实现回答；失败不持久化 draft，未保存 JD 不跨导航保留。
- 无完整浏览器 E2E、auth/multi-user、分页/复杂筛选、durable checkpoint/crash recovery。历史列表返回完整报告，适合小规模本地使用；run 崩溃可保留 running。
- Profile raw_history/inactive Evidence 持续增长，保留历史报告引用；UI 不默认显示 raw provenance JSON。
- 未实现 Resume/Resume revision、canonical cache、semantic identity、conversation/Agent loop、Plugin/Skill/MCP、多代理。

## Commit / stop

commit hash 用 `git log -1 --oneline` 获取，避免将自身 hash 写入文档。本轮 R3 已完成 Step 2–4，提交文档/result 并确认 working tree clean 后 STOP。历史 Step 1 失败已由 R1 诊断、R2 contract 适配承接。不要自动补发 live 请求，不开始 Phase 4。
