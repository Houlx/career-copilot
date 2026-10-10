# Career Copilot handoff

更新日期：2026-10-10（Asia/Shanghai）

## Status

**Phase 3 — Job Analysis 已完成。完成验证并提交后 STOP。未开始 Phase 4 Resume。**

独立项目；Phase 1 骨架/Observability、Phase 2 Career Profile 全部保留。当前支持确认 Profile → 显式分析 JD → Judge requirements → Generation 全量 Evidence matching → Python validation/Coverage → 原子报告 → UI Evidence 和 outdated。

## Read first next session

1. 本文。
2. `docs/PHASE_3.md`、`docs/ARCHITECTURE.md`；Profile 规则见 `docs/PHASE_2.md`。
3. 按获授权阶段定向阅读代码，不重新全仓库审计。

未收到明确指令不要继续 Phase 4 Resume。

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

**0 live API calls**；未发送真实 Profile/JD 到外部 Provider。pytest 默认禁 TCP，前端 mock fetch，实际 HTTP 仅 loopback。fixtures 全部 synthetic。

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

commit hash 用 `git log -1 --oneline` 获取，避免将自身 commit hash 写入文档。完整验证后提交 Phase 3，确认 working tree clean，STOP。不要开始 Phase 4。
