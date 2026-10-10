# Career Copilot

完整工作流图见 [LangGraph 结构图](docs/LANGGRAPH.md)，覆盖 Profile、岗位分析、简历生成、简历修改 Agent 与 Dummy 诊断。

独立的、面向个人求职的 Workflow-first AI 应用。当前 **Phase 5 完成**，结构化输出由 Pydantic contract 驱动。Generation task policy：`profile-facts-v1 → low`，`jd-requirements-v2 / matching-v1 / resume-generation-v1 / resume-revision-agent-v1 → high`；其余 Generation tasks 在 role reasoning unset 时使用 Provider 默认行为。优先级为 task-specific → role-level fallback → provider default。Settings 只读展示 task policy，trace 记录 effective reasoning 与来源。修改配置或代码后重启后端生效。当前交接见 [HANDOFF.md](docs/HANDOFF.md)，结构化输出设计见 [STRUCTURED_OUTPUT_CONTRACT.md](docs/STRUCTURED_OUTPUT_CONTRACT.md)；历史配置能力见 [PHASE_4_2.md](docs/PHASE_4_2.md) 和 [PHASE_4_3.md](docs/PHASE_4_3.md)。

Generation/GLM 负责 Profile facts、normalization、JD requirements、matching 和 Resume generation；Judge/Jev 只负责有限候选上的 scoreability。结果和指标见 [PHASE_3_5B_R.md](docs/PHASE_3_5B_R.md)。原 Jev 开放文本 extraction 的失败探索保留在 [PHASE_3_5B.md](docs/PHASE_3_5B.md)。

Generation 的 `GENERATION_STRUCTURED_OUTPUT` 可选 `json_schema`（默认，保留原 strict/schema 请求）或 `json_object`（GLM 本地显式配置）；两者共用原 Pydantic/business validation，继续拒绝 markdown fence。Settings API/页面和 AI trace 展示模式，不展示密钥。

## 本地启动

需要 Python 3.12+、[uv](https://docs.astral.sh/uv/) 和 Node.js 22.12+ / 24+（本次使用 Python 3.12、Node 24）。

```bash
uv sync --locked
npm ci --prefix frontend
cp .env.example .env
```

两个终端分别运行：

```bash
uv run uvicorn career_copilot.main:app --host 127.0.0.1 --port 8000 --reload
```

```bash
npm --prefix frontend run dev
```

打开 http://127.0.0.1:5173/profile 。粘贴原始职业资料，保存后明确点击“AI 整理”，查看或编辑结果并保存，最后点击“确认职业资料”。刷新后资料与 Evidence 仍然存在。概览显示 Profile 状态与版本；诊断入口继续可用。Debug Runs 页面：http://127.0.0.1:5173/debug/runs 。Settings 页面展示脱敏后的服务配置；修改 `.env` 后重启后端生效。

确认资料后打开 `/jobs`，粘贴 JD 并明确点击“分析岗位”。报告可展开 Evidence，显示 Python 计算的岗位要求覆盖度；在 Job Detail 选择简历语言并明确点击 Generate Resume，打开 `/resumes/:id` 查看正文、Markdown、Evidence 或 Print / Save as PDF；修改 Profile 后旧报告标记 outdated。fake 使用明确标识的 synthetic 岗位要求，仅验证流程。

默认 `AI_MODE=fake`，dummy workflow 在任何模式下都不调用模型。SQLite 自动创建于 `data/career-copilot.sqlite3`，不进入 Git。

## 已实现

- Resume Revision：受限 REVISE / ASK_USER / NO_CHANGE、独立 conversation、结构化 proposal、Before/After、显式 Accept/Reject、不可覆盖的 parent history、并发及 outdated 门禁。使用 [Phase 5](docs/PHASE_5.md) 中的局部 Agent；其他业务 workflow 保持固定。revision history 中的 Resume 保留且禁止删除。

- Resume：显式生成、zh/en/ja、七节点固定图、逐事实引用/数字校验、Markdown、历史/删除/outdated、Evidence 快照、浏览器打印。
- Jobs 输入、历史、详情；九节点固定 Job Analysis 图，全部 active Evidence 匹配、严格引用校验、Python Coverage、原子保存和 outdated。

- React + TypeScript + Vite：Profile 输入、整理、编辑、确认、失败反馈；概览、Settings、Debug Runs。
- 原始资料历史、AI 原始版本、用户版本分开保存；显式确认事务生成稳定 Evidence ID，版本检查拒绝旧请求覆盖。
- 六节点 Profile LangGraph，Generation 提取事实并仅使用已验证 facts 整理。
- FastAPI → 真实 LangGraph StateGraph → 两个确定性节点 → 状态变更 → SQLite Trace → HTTP response。
- 独立 Judge / Generation 配置，支持共享配置回退；业务节点不读取环境变量。
- `fake` / `replay` / 显式 `live` Provider 层，Pydantic 输出校验，OpenAI-compatible Chat Completions JSON Schema 输出，失败最多重试一次。
- Run UUID、workflow/version、Node Timeline、摘要、变化字段、校验、错误码、耗时，以及 AI model / prompt version / token usage 的追踪接口。
- 独立 diagnostic / Profile AI node replay：输入哈希与 prompt version 精确寻址，不改用户数据，也不创建业务记录。
- 默认关闭全文调试内容；只有开发环境且 `AI_DEBUG_PAYLOADS=1` 才写入/返回全文。API key、Bearer、常见 secret 字段脱敏。生产环境 Debug API 返回 404。

## 目录

```text
backend/
  career_copilot/
    api.py                  # HTTP、配置展示、debug、错误响应
    config.py               # 环境配置与角色配置
    database.py             # SQLite schema v5：runs / nodes / profile / evidence / jobs / resumes / revision
    errors.py               # 明确错误分类
    tracing.py              # run、节点追踪、摘要 diff、脱敏
    models/
      prompts.py            # 版本化任务与结构化输出
      router.py             # Judge / Generation、fake / replay / live
      providers.py          # Jev decision / OpenAI-compatible wire adapters
    profile_store.py        # 版本、原始历史、确认与 Evidence 事务
    profile_eval.py         # deterministic 事实对比指标
    workflows/profile.py    # 六节点固定 Profile 图
    workflows/dummy.py      # Phase 1 诊断固定图
    smoke.py                # 双重显式开启的 live 诊断命令
  fixtures/replay/          # synthetic diagnostic / Profile fixtures
  tests/                    # 默认阻断 TCP sockets
scripts/check_http.py       # 实际本地 HTTP 集成检查
scripts/eval_profile.py     # 默认离线 replay，显式单 case live gate
scripts/eval_jobs.py        # JD / scoreability / matching synthetic eval
scripts/eval_resumes.py     # 6-case grounding eval，独立 synthetic live gate
evals/profile_extraction/   # 10 个 synthetic reviewed cases
frontend/
  src/                      # React shell、API 类型、交互测试、样式
  vite.config.ts            # /api 本地代理；不需要 CORS
  package-lock.json
docs/                      # 架构、Phase 1/2/3 设计、HANDOFF
pyproject.toml
uv.lock
.env.example
```

## API

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/health` | 健康状态与阶段 |
| GET | `/api/profile` | current Profile、状态、Evidence（初始 null） |
| POST | `/api/profile/raw` | raw_input + expected_version（初始 0） |
| POST | `/api/profile/normalize` | expected_version → run_id + draft |
| PUT | `/api/profile` | expected_version + profile.entries → user edited draft |
| POST | `/api/profile/confirm` | expected_version → confirmed Profile + Evidence |
| GET | `/api/jobs` | 历史报告及 outdated |
| POST | `/api/jobs/analyze` | raw_jd → job + run_id，要求当前 Profile 已确认 |
| GET | `/api/jobs/{job_id}` | 完整报告和 Evidence |
| DELETE | `/api/jobs/{job_id}` | 删除报告、要求与匹配 |
| POST | `/api/jobs/{job_id}/resumes` | 显式 language → resume_id + run_id + resume |
| GET | `/api/jobs/{job_id}/resumes` | 该岗位简历历史 |
| GET | `/api/resumes/{resume_id}` | 简历、Markdown、Evidence 快照和 outdated |
| DELETE | `/api/resumes/{resume_id}` | 只删除简历 artifact |
| GET | `/api/settings` | 安全的只读配置摘要 |
| POST | `/api/workflows/dummy` | `{"message":"Career Copilot"}` → run_id + state |
| GET | `/api/debug/runs?limit=50&offset=0` | 最近运行、耗时、tokens、model |
| GET | `/api/debug/runs/{run_id}` | timeline、state diff、节点详情 |
| POST | `/api/debug/replay` | `{"task":"diagnostic","payload":{}}` 独立 replay |

后端同时支持 `/debug/runs` 和 `/debug/runs/{run_id}` JSON API 别名。前端同名路径展示 UI。OpenAPI：http://127.0.0.1:8000/docs 。

```bash
curl -sS http://127.0.0.1:8000/api/workflows/dummy \
  -H 'Content-Type: application/json' \
  -d '{"message":"Career Copilot"}'
```

两个本地服务启动后，可运行实际 HTTP 集成检查：

```bash
uv run python scripts/check_http.py
```

该命令通过 Vite proxy 执行 dummy graph 并检查 SQLite trace、state diff 与独立 replay。加 `--profile` 可测试完整 Profile 流程，要求服务连接空的临时数据库；会写入 synthetic Profile/Evidence。加 `--jobs` 包含 Profile 流程并验证 Jobs、Coverage、Evidence、trace、replay 和 outdated，同样要求空临时数据库。加 `--resume` 再验证生成、GET/list/trace、outdated 和删除。所有模式均要求 fake，0 live API calls。

## 配置与成本边界

- 默认 `AI_MODE=fake`：执行 deterministic diagnostic；Profile 把原文保留为一条 other fact，再逐条原样整理，用于流程检查，不模拟模型提取质量。
- `AI_MODE=replay`：从 `REPLAY_DIR` 读取合法 fixture，缺少记录明确报错，不自动回退 live。
- `AI_MODE=live`：启用真实 Provider。启动时检查两个角色的 model/key/base URL 配置。
- Provider：`JUDGE_PROVIDER=jev`、`GENERATION_PROVIDER=openai_compatible`；未显式配置 provider 时保留 OpenAI-compatible 兼容默认。Jev 支持 diagnostic 和批量 native Choice scoreability，不支持开放文本 extraction。
- OpenAI-compatible 角色覆盖：`JUDGE_MODEL / JUDGE_BASE_URL / JUDGE_API_KEY` 与 `GENERATION_MODEL / GENERATION_BASE_URL / GENERATION_API_KEY`；未配置时回退 `MODEL / BASE_URL / API_KEY`。
- Jev 必须配置专用 `JUDGE_MODEL / JUDGE_BASE_URL / JUDGE_API_KEY`，不回退共享配置；base URL 包含 version（不含 `/systemone`），model ID 不硬编码。
- 新增 `judge-diagnostic-v1` / `generation-diagnostic-v1`（各 128 output tokens），只使用 synthetic 输入。原可执行 AI tasks：`diagnostic`（prompt `diagnostic-v1`，128 tokens）、`profile-facts-v1`（Generation）及 `profile-normalize-v1`（Generation，各 6000 output tokens）；temperature=0。
- `GENERATION_TIMEOUT_SECONDS` / `JUDGE_TIMEOUT_SECONDS`：默认均 30 秒，必须满足有限数值 `0 < timeout <= 300`，非法配置启动报错。角色配置注入 Provider；Settings API/页面和 AI trace 显示 `timeout_seconds`。本地 Phase 4.1 Generation 显式覆盖 60 秒，Judge 保留 30 秒。沿用 HTTPX 各阶段 timeout 与原 latency/retry 语义。
- `GENERATION_REASONING_EFFORT=low|high|max` 是可选全局 fallback，task-specific policy 优先。默认 unset；`profile-facts-v1` 显式 low，`jd-requirements-v2`、`matching-v1` 和 `resume-generation-v1` 显式 high，其余 Generation task 使用角色配置或 Provider 默认行为。
- `.env`、数据库、个人信息和 secret 不提交 Git。Settings 不返回 secret 或 provider URL。

独立 replay：

```bash
curl -sS http://127.0.0.1:8000/api/debug/replay \
  -H 'Content-Type: application/json' \
  -d '{"task":"diagnostic","payload":{}}'
```

fixture 文件名：`{task}--{prompt_version}--{sha256(canonical_input_json)}.json`，记录 `task`、`prompt_version`、`output`。Canonical JSON 对 key 排序，UTF-8，紧凑 separators。仅手工保存已确认合法的输出；普通调用不会自动保存个人输入或模型输出。

Live smoke 必须同时配置真实凭据、`AI_MODE=live` 和 `--live`，显式选择角色。先 Judge 成功，才执行 Generation；任一失败即 STOP：

```bash
AI_MODE=live uv run python -m career_copilot.smoke --live --role judge
AI_MODE=live uv run python -m career_copilot.smoke --live --role generation
```

每条 smoke 仅一次请求，固定 synthetic 输入，保存 diagnostic trace，不读取或写入 Career 业务表，不执行业务 graph。缺任一 live gate 不构造 client。Generation 保留 `json_schema` structured output；Jev 使用原生 state/questions API。常规模型调用仍最多两次尝试，无嵌套重试、fallback 或 repair。默认 `AI_DEBUG_PAYLOADS=0`；本地开启检查后建议恢复。真实结果和限制见 [PHASE_3_5A.md](docs/PHASE_3_5A.md)；Phase 3.5A Judge `jev-1.13.0` / Generation `glm-5.3-flash` 各一次成功，latency 750.411 / 1085.165 ms，tokens 283+21 / 41+63；该历史阶段真实请求总数为 2，未执行真实业务 workflow；本轮 3.5B-R 另有 1 次失败的业务 task 验证，详见新阶段记录。

## Profile 边界与评估

Profile 使用整数版本；raw 保存、整理结果替换、编辑、确认发生实际变化时递增。重复保存/确认无变化时不递增。修改 raw 或 Profile 会撤销当前确认，保留上次确认快照和 Evidence；新 raw 未整理时不能确认旧草稿。未来下游必须读取 `confirmed_profile()` 的确认门禁，并保存所使用的 `profile_version` 判断 outdated。

```bash
uv run python scripts/eval_profile.py
# 可选：传入 case id → structured facts 的离线预测 JSON
uv run python scripts/eval_profile.py --predictions /path/to/predictions.json
```

10 个 synthetic reviewed cases 覆盖教育、工作、项目、技能、数字、中英混合、缺失信息及模糊表述。默认 replay 对比是回归验证，不代表 live 模型质量。事实完整性仍需用户审阅；数值、source quote 和引用校验不能证明所有语义均有来源。详细规则见 [docs/PHASE_2.md](docs/PHASE_2.md)。

当前使用 Generation `jd-requirements-v2`、Judge `scoreability-v1` 和 Generation `matching-v1`（各 6000 output tokens、temperature=0），均支持独立 offline replay；历史 `jd-extract-v1` 保留原语义。`uv run python scripts/eval_jobs.py` 运行 12 个抽取、8 个 scoreability、5 个 matching reviewed synthetic fixtures；可传 `--predictions`。新增 `--suite scoreability` / `--case` / `--output`；live 需要 `AI_MODE=live` + `--live`，单次尝试，8-case 分类仅一个批量请求。R3 已完成 JD 单例及 Jev 单例/8-case 批量 live 验收，共 3 次请求后停止，不自动重跑。规则、Coverage 公式、trace、已知限制见 [docs/PHASE_3.md](docs/PHASE_3.md)。

## 检查

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
uv run python scripts/eval_resumes.py
```

pytest 默认禁用 TCP socket，仅允许 TestClient 所需 Unix socket。Provider contract 测试使用 `httpx.MockTransport`，不会访问真实 Provider。前端测试使用模拟 fetch。普通测试 **0 live API calls**。

参考：[LangGraph StateGraph](https://reference.langchain.com/python/langgraph/graph/state/StateGraph/compile)、[FastAPI lifespan testing](https://fastapi.tiangolo.com/advanced/testing-events/)。设计见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)，后续 session 先读 [docs/HANDOFF.md](docs/HANDOFF.md)。

Revision 位于 Resume Detail 的 Revise with AI 区域。提出要求后查看 diff，点击 Accept revision 才生成新 Resume；没有 Evidence 的新经历需先更新 Profile。Revision 暂不支持切换语言。离线运行 `uv run python scripts/eval_resume_revision.py`；阶段实现验证全部 0 live requests。
