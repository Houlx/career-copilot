# Phase 1 implementation

目标：证明真实的 HTTP → LangGraph → Node → State Mutation → State Diff → Trace → HTTP Response 链路，无真实模型调用。

## Acceptance mapping

| 要求 | 实现 |
| --- | --- |
| Backend / SQLite / graph | api.py / database.py / workflows/dummy.py |
| 最小 CareerState | message、counter、result，TypedDict |
| ModelRouter 与双角色 | models/router.py、config.py |
| fake / replay / live | 允许列出的 diagnostic task、hash-keyed fixtures、httpx structured output |
| Run / node trace | tracing.py，UUID、sequence、状态、耗时、错误码 |
| State diff | 仅 changed fields 的 before/after 摘要 |
| Prompt version | diagnostic-v1；业务 prompt 名称仅预留 |
| Debug API / UI | /api/debug/runs 与 /debug/runs 别名；前端 /debug/runs |
| Settings | 只读配置、模式、双角色、prompt 版本，不显示 secret |
| 自动测试与 0 live calls | pytest-socket、MockTransport、Vitest mocked fetch |
| 文档交接 | README、ARCHITECTURE、HANDOFF |

## Dummy semantics

输入 `{"message":"  Career Copilot  "}`：

1. prepare_input：strip message，记录字符串长度摘要的变化。
2. mutate_state：counter 从 0 到 1，result 变为 `Ready: Career Copilot`。
3. 返回 run_id 与最终 state。
4. 两节点 trace 均已提交 SQLite；列表模型为空、tokens=0。

诊断接口在 production 仍可运行；只有 debug 读取/replay 端点隐藏。Phase 1 没有正式个人业务状态或权限流程。

## Verification scope

后端集成测试通过真实 LangGraph 和临时磁盘 SQLite 执行，包含应用重启后持久化读取。前端测试验证明确点击运行、trace 跳转、配置展示、replay、隐藏开发导航、错误反馈和初始加载不会自动触发 workflow。

实际 loopback HTTP 冒烟覆盖 FastAPI、Vite proxy、诊断运行、两节点 trace 和独立 replay。它是本地集成检查，不是完整浏览器 E2E。live Provider 未执行；只验证其 mock transport 协议、结构校验、usage 累加及一次 retry 上限。

## Phase boundary

不实现 Profile extraction/normalization/edit/confirm，不创建 profile/evidence 业务表，不实现 JD matching、Coverage 或 resume。下一 session 等待明确 Phase 2 指令。
