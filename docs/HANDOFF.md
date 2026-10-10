# Career Copilot handoff

更新日期：2026-10-10（Asia/Shanghai）

## Status

**Phase 2 — Career Profile 完成并提交。STOP。未开始 Phase 3。**

项目独立建立；没有读取或复制 Waku Career Agent 代码。Phase 1 骨架和可观测性仍通过全部验证。当前已可执行 raw → Judge facts → Generation draft → 用户编辑 → 显式确认 → 稳定 Career Evidence 的完整流程。

## Read first in the next session

1. 本文。
2. `docs/PHASE_2.md` 与 `docs/ARCHITECTURE.md`；需要 Phase 1 背景时读 `docs/PHASE_1.md`。
3. 按获授权阶段定向阅读实际涉及代码，不重新全仓库审计。

未收到明确指令不要继续 Phase 3 Job Analysis。

## Delivered

- SQLite schema v2 幂等增加 career_profile / career_evidence，保留 v1 runs/nodes。
- 原文、永久追加 raw_history、Judge facts、AI 原始整理、用户编辑、上次确认快照分开保存。
- 独立六节点 `profile_workflow` 固定 LangGraph：load / extract / validate facts / normalize / validate profile / persist。
- `profile-facts-v1` Judge 与 `profile-normalize-v1` Generation；Generation 输入仅 facts。strict Pydantic、原文 quote、来源引用、类型和数字校验；diagnostic-v1 未改语义。
- 五个 Profile 业务 API，expected_version 乐观并发检查；Graph 最后写回再次检查。
- 用户确认是普通 Python/API，不调用模型；Profile 与 Evidence 在同一事务确认，失败整体回滚。
- Evidence ID 为 Python 分配的内容/来源 SHA-256；重复确认、保存与刷新稳定，实际变化保守生成新 ID，旧证据 inactive 保留。
- Profile 页面 raw 保存、显式整理、结构化编辑/保存、确认、状态、失败/重试、Evidence 和 trace 链接；Dashboard 状态、版本、数量和更新时间。
- 两个 AI 节点完整沿用 Phase 1 model/prompt/token/latency/retry tracing；全文默认隐藏，显式开启仍先脱敏。
- 两个 Profile tasks 独立强制 offline replay，不写业务表或 runs；Debug UI 可选择任务。
- 10 个 synthetic reviewed extraction cases、对应 facts/normalization replay fixtures、deterministic eval 命令。

## Semantics to preserve

- 有效 Profile 优先 user_edited_profile，否则 ai_normalized_profile。
- raw resave / normalization 替换 / 编辑发生实际变化时取消当前确认；之前确认快照和 Evidence 保留。新 raw 未整理时禁止确认旧草稿。
- AI 失败不写业务数据，完整保留调用前状态；如果用户此前已显式保存新 raw，撤销确认来自该次保存，旧确认快照与证据仍保留。
- raw 首次保存版本 1；raw 变化、成功替换 normalization、实际编辑、从未确认变确认各 +1。相同内容的重复保存/确认/无编辑重复 normalization 是 no-op。
- 必须通过 `ProfileStore.confirmed_profile()` 读取可供未来业务使用的内容；active evidence 存在不等于当前 Profile 已确认。
- **Outdated：一旦 confirmed Profile 后续被修改，未来已有 Job Analysis 和 Resume 应标记 outdated。** 后续表保存所用 profile_version，与 current version 比较并检查确认门禁。本阶段没有提前创建 jobs/resumes 表。
- source_index/type/title/content/raw provenance 进入 ID；来源或顺序实际变化可保守换 ID，不做语义 entity matching。

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
```

最终验证：后端 **71 passed**，前端 **11 passed**；Ruff、format、TypeScript/Prettier、生产构建均通过，npm audit **0 vulnerabilities**。

离线 reference replay eval：10 cases；fact omission=0、unsupported addition=0、type=10/10、numeric preservation=6/6。另有扰动测试验证指标确实识别错误；这些分数是回归检查，不代表 live 模型质量。

实际 loopback HTTP `uv run python scripts/check_http.py --profile` 已在空临时 DB、fake 模式运行通过：Vite proxy → FastAPI → LangGraph → SQLite，raw/edit/confirm、六节点 trace、稳定 IDs、两任务独立 replay。另终止并重启实际后端进程，确认 Profile/Evidence 完整恢复。临时服务已关闭，测试数据未写入项目业务 DB。

**0 live API calls**。自动测试默认禁 TCP；Provider contract 只用 MockTransport。未运行真实 Provider 或发送真实职业资料。

## Run

```bash
uv sync --locked
npm ci --prefix frontend
cp .env.example .env
uv run uvicorn career_copilot.main:app --host 127.0.0.1 --port 8000 --reload
# 另一个终端
npm --prefix frontend run dev
```

前端 5173，后端 8000；Profile `/profile`，Debug `/debug/runs`。SQLite 默认 `data/career-copilot.sqlite3`，启动自动从 schema v1 增加到 v2。默认 fake；其 Judge 将原文保留为一条 other fact，Generation 原样逐条整理，用于流程验证。replay 缺 fixture 明确失败，不回退 live。

## Known limits / deliberate deferrals

- quote/数字/引用校验无法证明所有自然语言语义 grounding，仍须用户审阅；无 live provider quality tuning。
- Evidence ID 不做复杂 diff、实体匹配、同义词或多语言等价；AI 重新抽取改变 source_index 时可换 ID。
- UI 编辑已有条目；新增/删除事实可修改 raw 后重新整理。未保存表单不跨页面保留。
- run 的 normalizing / failure 状态由 UI 当次请求显示；没有 durable checkpoint/crash recovery。
- 没有完整浏览器 E2E；有 mock fetch 交互和实际本地 HTTP 集成验证。
- raw_history / inactive Evidence 会增长；仅单用户本地服务，无 auth；Debug API development-only。
- 没有 JD、matching、coverage、resume、RAG、vector、embedding、Agent loop、MCP、Skill/Plugin 或下游业务表。

## Commit / stop

当前 commit hash 请用 `git log -1 --oneline` 获取，避免在自身 commit 文档内写不可自洽 hash。完成提交后确认 working tree clean，然后 STOP。
