# Career Copilot

独立的、面向个人求职的 Workflow-first AI 应用。当前只完成 **Phase 1：项目骨架与可观测性基础**。没有复制或读取 Waku 项目代码；职业资料、岗位分析、简历生成均留待下一阶段。

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

打开 http://127.0.0.1:5173 。进入概览，点击“运行诊断”，然后点击“查看节点追踪”。Debug Runs 页面：http://127.0.0.1:5173/debug/runs 。Settings 页面展示脱敏后的服务配置；修改 `.env` 后重启后端生效。

默认 `AI_MODE=fake`，dummy workflow 在任何模式下都不调用模型。SQLite 自动创建于 `data/career-copilot.sqlite3`，不进入 Git。

## 已实现

- React + TypeScript + Vite shell：概览、Settings、Debug Runs；其余业务页明确标注后续阶段。
- FastAPI → 真实 LangGraph StateGraph → 两个确定性节点 → 状态变更 → SQLite Trace → HTTP response。
- 独立 Judge / Generation 配置，支持共享配置回退；业务节点不读取环境变量。
- `fake` / `replay` / 显式 `live` Provider 层，Pydantic 输出校验，OpenAI-compatible Chat Completions JSON Schema 输出，失败最多重试一次。
- Run UUID、workflow/version、Node Timeline、摘要、变化字段、校验、错误码、耗时，以及 AI model / prompt version / token usage 的追踪接口。
- 独立 diagnostic AI node replay：输入哈希与 prompt version 精确寻址，不改用户数据，也不创建业务记录。
- 默认关闭全文调试内容；只有开发环境且 `AI_DEBUG_PAYLOADS=1` 才写入/返回全文。API key、Bearer、常见 secret 字段脱敏。生产环境 Debug API 返回 404。

## 目录

```text
backend/
  career_copilot/
    api.py                  # HTTP、配置展示、debug、错误响应
    config.py               # 环境配置与角色配置
    database.py             # SQLite schema v1：runs / nodes
    errors.py               # 明确错误分类
    tracing.py              # run、节点追踪、摘要 diff、脱敏
    models/
      prompts.py            # 版本化任务与结构化输出
      router.py             # Judge / Generation、fake / replay / live
    workflows/dummy.py      # CareerState、固定边图
    smoke.py                # 双重显式开启的 live 诊断命令
  fixtures/replay/          # 非个人信息的合法 diagnostic fixture
  tests/                    # 默认阻断 TCP sockets
scripts/check_http.py       # 实际本地 HTTP 集成检查
frontend/
  src/                      # React shell、API 类型、交互测试、样式
  vite.config.ts            # /api 本地代理；不需要 CORS
  package-lock.json
docs/                      # 架构、Phase 1 设计、HANDOFF
pyproject.toml
uv.lock
.env.example
```

## API

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/health` | 健康状态与阶段 |
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

该命令通过 Vite proxy 执行 dummy graph 并检查 SQLite trace、state diff 与独立 replay；只新增诊断 run，不调用模型。

## 配置与成本边界

- 默认 `AI_MODE=fake`：仅执行允许的 deterministic diagnostic 输出，不生成职业事实。
- `AI_MODE=replay`：从 `REPLAY_DIR` 读取合法 fixture，缺少记录明确报错，不自动回退 live。
- `AI_MODE=live`：启用真实 Provider。启动时检查两个角色的 model/key/base URL 配置。
- 角色覆盖：`JUDGE_MODEL / JUDGE_BASE_URL / JUDGE_API_KEY` 与 `GENERATION_MODEL / GENERATION_BASE_URL / GENERATION_API_KEY`；未配置时回退 `MODEL / BASE_URL / API_KEY`。
- 当前唯一可执行 AI task 为 `diagnostic-v1`，最大输出 128 tokens，temperature=0；其余业务任务只预留命名方向，尚未实现。
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

## 检查

```bash
uv run pytest
uv run ruff check backend scripts
uv run ruff format --check backend scripts
npm --prefix frontend run check
npm --prefix frontend test
npm --prefix frontend run build
npm --prefix frontend audit
```

pytest 默认禁用 TCP socket，仅允许 TestClient 所需 Unix socket。Provider contract 测试使用 `httpx.MockTransport`，不会访问真实 Provider。前端测试使用模拟 fetch。普通测试 **0 live API calls**。

参考：[LangGraph StateGraph](https://reference.langchain.com/python/langgraph/graph/state/StateGraph/compile)、[FastAPI lifespan testing](https://fastapi.tiangolo.com/advanced/testing-events/)。设计见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)，后续 session 先读 [docs/HANDOFF.md](docs/HANDOFF.md)。
