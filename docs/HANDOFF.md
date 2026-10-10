# Career Copilot handoff

更新日期：2026-10-10（Asia/Shanghai）

## Status

**Phase 1 完成。STOP。等待 Phase 2 指令。**

项目从空目录独立建立，没有读取或复制 Waku Career Agent 代码。只实现骨架与可观测性基础；业务页面仍为明确的后续阶段占位。

## Read first in the next session

1. 本文。
2. `docs/PHASE_1.md` 与 `docs/ARCHITECTURE.md`。
3. 若收到 Phase 2 指令，再定向阅读 `api.py`、`config.py`、`database.py`、`tracing.py`、`models/`、`workflows/dummy.py`。

不要重新全仓库审计，不默认继续 Phase 3/4。

## Delivered

- FastAPI application factory + lifespan SQLite init，health/settings/dummy/debug/replay API。
- 真正 LangGraph StateGraph 两节点固定流程，最小 TypedDict state。
- WAL SQLite runs/nodes，run_id、节点 sequence、摘要 diff、错误定位。
- ModelRouter 双角色独立配置，fake 默认、输入 hash replay、显式 live structured provider。
- diagnostic-v1 prompt、Pydantic strict 输出、128 token 上限、最多 retry 1 次。
- AI metadata hook（model/prompt/tokens/latency/retries）、显式 debug payload 与 secrets 脱敏。
- React + TypeScript shell、只读 Settings、Debug Runs list/detail/timeline、独立 replay 按钮。
- Python/Node lockfiles、默认禁 TCP socket 的测试、mock Provider contract 测试。

## Validation

```bash
uv run pytest
uv run ruff check backend scripts
uv run ruff format --check backend scripts
npm --prefix frontend run check
npm --prefix frontend test
npm --prefix frontend run build
npm --prefix frontend audit
```

最终验证：后端 32 项测试通过；前端 6 项交互测试通过；Ruff、TypeScript/Prettier、生产构建和 npm audit 均通过（0 vulnerabilities）。实际 HTTP 集成检查 `uv run python scripts/check_http.py` 通过，覆盖 Vite proxy → FastAPI → LangGraph → SQLite Trace，以及独立 replay。**0 live API calls**；live smoke 入口存在但没有执行。前端检查包括 TypeScript、格式、交互测试、生产构建。

## Run

```bash
uv sync --locked
npm ci --prefix frontend
cp .env.example .env
uv run uvicorn career_copilot.main:app --host 127.0.0.1 --port 8000 --reload
# 另一个终端
npm --prefix frontend run dev
```

前端 5173；后端 8000；SQLite `data/career-copilot.sqlite3`。API `/api` 前缀经过 Vite proxy。Debug UI `/debug/runs`。主配置默认 fake，不自动回退 live。

## Known limits / deliberate deferrals

- 当前只支持 diagnostic AI task；业务 prompt 只有预留名称，尚无业务逻辑。
- Debug payload 默认只保存形状摘要，字符串 diff 不显示原文；显式开启后额外显示已脱敏全文。
- 没有 durable checkpoint/crash recovery；中断中的 run 可能保持 running。
- 没有价格估算、业务 eval 数据集、完整浏览器 E2E 或真实 Provider 验证；后续对应阶段再添加。
- Settings 不在浏览器写入凭据；编辑 .env 并重启后端。
- Debug API 只在 development 显示，无 auth，服务面向本机。
- 本阶段只建立 trace 表；业务表在对应 Phase 再增加，避免过度设计。

## Next authorized scope (only after instruction)

Phase 2：Career Profile onboarding、Judge fact extraction、Generation normalization、用户编辑与确认、稳定 Evidence IDs、raw 永久保留、Profile trace/replay/eval fixtures。修改已确认 Profile 后，给既有下游结果做 outdated 的版本关系；下游表如尚不存在，应在后续阶段接入而不是提前做业务功能。

Phase 2 完成后同样更新本文、测试、commit、确认 working tree clean，然后 STOP。

当前提交 hash 请使用 `git log -1 --oneline` 获取；避免在自身 commit 内容中保存不可自洽的 hash。
