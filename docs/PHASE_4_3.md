# Phase 4.3 — Task-Specific Generation Inference Policy

日期：2026-10-10（Asia/Shanghai）。只实现确定性 task inference configuration，没有进入 Phase 5。Coding Agent 与全部自动验证的 live requests = 0。

## Motivation

真实浏览器 Profile extraction 在 Provider 默认 reasoning 下 latency 和 output tokens 明显偏高。用户已手工用同一个 Test 1 完成 baseline 和三次 low 实验；以下是用户提供的历史观察，本阶段没有重新执行。

## Baseline and manual low experiments

| Setting | Facts | Input tokens | Output tokens | Total tokens | Latency ms | Retry count |
| --- | --- | --- | --- | --- | --- | --- |
| Provider default | 12 | 314 | 4485 | 4799 | 57003.418 | 0 |
| Low 1 | 7 | 314 | 386 | 700 | 4755.08 | 0 |
| Low 2 | 9 | 314 | 581 | 895 | 7009.205 | 0 |
| Low 3 | 9 | 314 | 504 | 818 | 5436.106 | 0 |

三次 low 平均 latency 约下降 90%，output tokens 约下降 89%。用户人工检查没有发现明显关键事实缺失，主要变化是同类信息合并、fact 粒度变粗，例如编程语言或 Web 技术各归为一条事实。这是人工检查，不是完整 semantic evaluation；粗粒度 Evidence 暂时可接受，尚未证明对下游 matching/Resume 更好。实验仅支持 profile-facts-v1，不支持把整个 Generation role 设为 low。

## Policy and resolution

唯一显式 task policy：`profile-facts-v1 → low`。`profile-normalize-v1`、`jd-requirements-v2`、`matching-v1`、`resume-generation-v1`、`generation-diagnostic-v1` 均无 task override。

```text
Task-specific override → role-level configured fallback → provider default
```

`PromptSpec.reasoning_effort` 默认 None，仅 facts 配置 low。RoleModel 在每次 invoke 时解析有效值与来源，并在 live 分支复制 ModelConfig 注入 Provider，不修改共享 role config。没有 task 名称模式匹配、任务类别推断、模型/URL 检测或大型 policy framework。Workflow 只调用 task，不处理 reasoning；OpenAICompatibleProvider 只接收最终配置，不读取业务 task policy。

保留 Phase 4.2 的 `GENERATION_REASONING_EFFORT=low|high|max`：显式设置后，无 task override 的 Generation tasks 仍继承这个全局 fallback；facts 的 low 优先于 role high/max。两者都未设置时 request 完全省略 reasoning 字段，不发送 null。GLM wire 继续使用已有顶层 `reasoning_effort`，json_object/json_schema 均保留。

本地 `.env` 已注释全局 reasoning assignment，role 恢复 unset；文件未提交。默认 timeout 仍 Generation/Judge=30/30 秒，本地仍 60/30 秒，模型仍 glm-5.3-flash。Prompt 正文、版本、schema、temperature、output limits、retry、token accounting 与业务 graph 均未修改。

## Settings and trace

`GET /api/settings` 保留 models 中的 Generation role reasoning 配置，新增只读 allowlist：

```json
{"task_policies": {"profile-facts-v1": {"reasoning_effort": "low"}}}
```

只暴露 task name 与 reasoning policy；不序列化完整 PromptSpec，不返回 prompt 正文、URL、key 或 secret。前端新增只读 Task inference policies 区域，无编辑能力。

Generation success/failure/fake/replay metadata 记录最终请求配置：`reasoning_effort` 为 low/high/max/null；`reasoning_source` 为 task/role/provider_default。Provider default 表示未发送 override，无法推断服务端实际 effort。Judge/Jev 不变；不保存 hidden reasoning，不增加 reasoning_tokens/thinking_tokens。fake/replay 不模拟真实推理消耗；policy 不进入 canonical business input hash，沿用原 replay fixtures。

## Validation

全部使用 synthetic 输入。pytest 禁用 TCP，Provider 测试使用 MockTransport；前端模拟 fetch；三套 eval 强制离线 replay。测试保护唯一 task override、全部目标 Generation task 的精确 wire、task > role、role fallback、无 override 时字段 absent、json_object/json_schema、timeout/temperature/output limits，以及成功和失败的持久化 metadata。复用现有 fixture 验证 fake/replay 与有效配置一致。

执行：`uv run pytest`、Ruff check/format、frontend check/test/build/audit、Profile/Jobs/Resume eval，以及 `AI_MODE=fake` 临时空 DB 的 `scripts/check_http.py --resume`。HTTP 检查包含 Profile/Job/Resume/Settings/trace 和 task policy metadata。结果：388 backend / 25 frontend tests 通过；Ruff check/format、frontend check/build 通过，audit=0 vulnerabilities；Profile 10-case、Jobs 12/8/5-case、Resume 6-case replay eval 全通过。fake HTTP --resume 通过，facts trace=low/task，normalization/JD/matching/Resume=null/provider_default；独立端口临时服务已停止，临时空 DB 已清理。所有自动验证及 Agent live calls=0。

## Manual verification — user only

1. 确认本地 `.env` 没有启用 `GENERATION_REASONING_EFFORT=low`，进程环境也没有 role override。
2. 完整重启 backend，打开 `/api/settings`：Generation role reasoning_effort=null，task_policies.profile-facts-v1.reasoning_effort=low。
3. 在 `localhost:5173` 使用与之前相同的 Test 1，保存并执行 AI 整理。Coding Agent 不执行此步骤。
4. 查看 trace：profile-facts-v1 的 reasoning_effort=low、reasoning_source=task；profile-normalize-v1 为 null/provider_default。记录 facts、input/output/total tokens、latency、retry 与人工质量观察。
5. 后续自行生成 Resume，检查 resume-generation-v1 为 null/provider_default；前提是没有显式 role override。matching/JD 同理。

## Future work and exclusions

分别评估 profile-normalize-v1、jd-requirements-v2、matching-v1、resume-generation-v1，每个 task 必须有独立质量/性能依据后再调整。业务 timeout × 两次 attempt 的等待与 retry UX 另行处理。

本阶段不做 chunking、schema compression、删除 source_quote、prompt tuning、技能 grouping heuristics、Evidence parent/child 或 atomic skill redesign、模型替换/fallback、context trimming、streaming、async queue、retry redesign、Resume revision、Cover Letter、Application Tracker、Agent loop 或 Phase 5。
