# Career Copilot

独立的、面向个人求职的 Workflow-first AI 应用。当前完成 **Phase 3：Job Analysis**。没有复制或读取 Waku 项目代码；简历生成留待 Phase 4。

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

确认资料后打开 `/jobs`，粘贴 JD 并明确点击“分析岗位”。报告可展开 Evidence，显示 Python 计算的岗位要求覆盖度；修改 Profile 后旧报告标记 outdated。fake 使用明确标识的 synthetic 岗位要求，仅验证流程。

默认 `AI_MODE=fake`，dummy workflow 在任何模式下都不调用模型。SQLite 自动创建于 `data/career-copilot.sqlite3`，不进入 Git。

## 已实现

- Jobs 输入、历史、详情；八节点固定 Job Analysis 图，全部 active Evidence 匹配、严格引用校验、Python Coverage、原子保存和 outdated。

- React + TypeScript + Vite：Profile 输入、整理、编辑、确认、失败反馈；概览、Settings、Debug Runs。
- 原始资料历史、AI 原始版本、用户版本分开保存；显式确认事务生成稳定 Evidence ID，版本检查拒绝旧请求覆盖。
- 六节点 Profile LangGraph，Judge 提取事实，Generation 仅使用 Judge facts 整理。
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
    database.py             # SQLite schema v3：runs / nodes / profile / evidence / jobs
    errors.py               # 明确错误分类
    tracing.py              # run、节点追踪、摘要 diff、脱敏
    models/
      prompts.py            # 版本化任务与结构化输出
      router.py             # Judge / Generation、fake / replay / live
    profile_store.py        # 版本、原始历史、确认与 Evidence 事务
    profile_eval.py         # deterministic 事实对比指标
    workflows/profile.py    # 六节点固定 Profile 图
    workflows/dummy.py      # Phase 1 诊断固定图
    smoke.py                # 双重显式开启的 live 诊断命令
  fixtures/replay/          # synthetic diagnostic / Profile fixtures
  tests/                    # 默认阻断 TCP sockets
scripts/check_http.py       # 实际本地 HTTP 集成检查
scripts/eval_profile.py     # 离线 replay eval
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

该命令通过 Vite proxy 执行 dummy graph 并检查 SQLite trace、state diff 与独立 replay。加 `--profile` 可测试完整 Profile 流程，要求服务连接空的临时数据库；会写入 synthetic Profile/Evidence。加 `--jobs` 包含 Profile 流程并验证 Jobs、Coverage、Evidence、trace、replay 和 outdated，同样要求空临时数据库。所有模式均要求 fake，0 live API calls。

## 配置与成本边界

- 默认 `AI_MODE=fake`：执行 deterministic diagnostic；Profile 把原文保留为一条 other fact，再逐条原样整理，用于流程检查，不模拟模型提取质量。
- `AI_MODE=replay`：从 `REPLAY_DIR` 读取合法 fixture，缺少记录明确报错，不自动回退 live。
- `AI_MODE=live`：启用真实 Provider。启动时检查两个角色的 model/key/base URL 配置。
- 角色覆盖：`JUDGE_MODEL / JUDGE_BASE_URL / JUDGE_API_KEY` 与 `GENERATION_MODEL / GENERATION_BASE_URL / GENERATION_API_KEY`；未配置时回退 `MODEL / BASE_URL / API_KEY`。
- 可执行 AI tasks：`diagnostic`（prompt `diagnostic-v1`，128 tokens）、`profile-facts-v1`（Judge）及 `profile-normalize-v1`（Generation，各 6000 output tokens）；temperature=0。
- `.env`、数据库、个人信息和 secret 不提交 Git。Settings 不返回 secret 或 provider URL。

独立 replay：

```bash
curl -sS http://127.0.0.1:8000/api/debug/replay \
  -H 'Content-Type: application/json' \
  -d '{"task":"diagnostic","payload":{}}'
```

fixture 文件名：`{task}--{prompt_version}--{sha256(canonical_input_json)}.json`，记录 `task`、`prompt_version`、`output`。Canonical JSON 对 key 排序，UTF-8，紧凑 separators。仅手工保存已确认合法的输出；普通调用不会自动保存个人输入或模型输出。

Live smoke 只为未来操作提供入口，本阶段**没有执行**。必须同时配置真实凭据、`AI_MODE=live` 和 `--live`：

```bash
AI_MODE=live uv run python -m career_copilot.smoke --live
```

该命令只调用 Judge diagnostic，不执行职业业务或写入用户数据。Provider 必须支持 `response_format: json_schema`；不支持时明确报错，不做自动降级或 repair loop。

## Profile 边界与评估

Profile 使用整数版本；raw 保存、整理结果替换、编辑、确认发生实际变化时递增。重复保存/确认无变化时不递增。修改 raw 或 Profile 会撤销当前确认，保留上次确认快照和 Evidence；新 raw 未整理时不能确认旧草稿。未来下游必须读取 `confirmed_profile()` 的确认门禁，并保存所使用的 `profile_version` 判断 outdated。

```bash
uv run python scripts/eval_profile.py
# 可选：传入 case id → structured facts 的离线预测 JSON
uv run python scripts/eval_profile.py --predictions /path/to/predictions.json
```

10 个 synthetic reviewed cases 覆盖教育、工作、项目、技能、数字、中英混合、缺失信息及模糊表述。默认 replay 对比是回归验证，不代表 live 模型质量。事实完整性仍需用户审阅；数值、source quote 和引用校验不能证明所有语义均有来源。详细规则见 [docs/PHASE_2.md](docs/PHASE_2.md)。

Phase 3 新增 Judge `jd-extract-v1` 与 Generation `matching-v1`（各 6000 output tokens、temperature=0），均支持独立 offline replay。`uv run python scripts/eval_jobs.py` 运行 12 个抽取、8 个 scoreability、5 个 matching reviewed synthetic fixtures；可传 `--predictions`。规则、Coverage 公式、trace、已知限制见 [docs/PHASE_3.md](docs/PHASE_3.md)。

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
```

pytest 默认禁用 TCP socket，仅允许 TestClient 所需 Unix socket。Provider contract 测试使用 `httpx.MockTransport`，不会访问真实 Provider。前端测试使用模拟 fetch。普通测试 **0 live API calls**。

参考：[LangGraph StateGraph](https://reference.langchain.com/python/langgraph/graph/state/StateGraph/compile)、[FastAPI lifespan testing](https://fastapi.tiangolo.com/advanced/testing-events/)。设计见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)，后续 session 先读 [docs/HANDOFF.md](docs/HANDOFF.md)。
