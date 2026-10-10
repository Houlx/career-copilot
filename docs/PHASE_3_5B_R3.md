# Phase 3.5B-R3 — Step 2–4 Live Acceptance

日期：2026-10-10（Asia/Shanghai）。

**Phase 3.5B-R3 Step 2–4 Live Acceptance 完成，已停止，未开始 Phase 4。**

R2 已验证 GLM json_object Profile contract。本轮 GLM `openai-compatible / glm-5.3-flash / json_object` 的 JD jd-01 通过 HTTP 200、JSON parsing、严格 Pydantic 及原 JD validation：title=`Synthetic role 1`，1 个 `Python / required / skill` requirement。exact metrics：requirement_missing=0、unsupported_addition=0、importance_correct/total=1/1、category_correct/total=1/1，两个 accuracy 均 1.0。title 与 reference 相同，但现有 metrics 不评估 title。

Jev `jev-1.13.0` native Choice 单例 score-0（身体健康）raw/guarded/reference 均 NON_SCORABLE。8-case reviewed benchmark 合并为一次请求，raw=8/8、guarded=8/8（均 100%）；SCORED=2/2、NEEDS_CONFIRMATION=2/2、NON_SCORABLE=4/4（raw/guarded 相同）。guard corrected=0、harmed=0，全部 guard_adjusted=false。

JD / Jev single / Jev batch latency=7952.355 / 550.898 / 527.180 ms；input/output/total tokens=151/547/698、495/51/546、2122/389/2511。实际请求数 **3**，retry 全为 0；合计 input/output/total tokens=2768/987/3755，调用 latency 合计 9030.433 ms。所有 request_success/schema_mapping_success=true。

## 执行与合同

用户本轮明确授权最多 3 次 live，JD → Jev single → Jev batch 顺序执行，失败即停止。本轮均成功，没有自动补发。每步发送前 harness 打印 provider/model/case_count/planned_request_count；第三步打印 jev / jev-1.13.0 / 8 / 1，响应 question_count=8，未拆成 8 次请求。单例响应 question_count=1。Jev 原生 answers/criteria/probability、ScoreabilityResult 及 Python 完整唯一 index mapping 均通过。

```bash
AI_DEBUG_PAYLOADS=0 AI_MODE=live uv run python scripts/eval_jobs.py --live --suite jd_extraction --case jd-01 --output evals/results/phase_3_5b_r/jd-live-r3.json
AI_DEBUG_PAYLOADS=0 AI_MODE=live uv run python scripts/eval_jobs.py --live --suite scoreability --case score-0 --output evals/results/phase_3_5b_r/scoreability-single-live-r3.json
AI_DEBUG_PAYLOADS=0 AI_MODE=live uv run python scripts/eval_jobs.py --live --suite scoreability --output evals/results/phase_3_5b_r/scoreability-suite-live-r3.json
```

R2 历史合同见 [PHASE_3_5B_R2.md](PHASE_3_5B_R2.md)。本轮 structured_output_mode=json_object 不变，Jev 仍为 native Choice。未修改代码、配置、schema、criteria、validators、四词 guard、coverage、prompts 或 fixtures，没有 tuning/repair/strip fence。

JD 的现有业务 validate_requirements 使用同一个 JobRequirements.model_validate，没有 Profile 式独立 grounding validator。保存结果在本地通过该原 validator 再核验；未运行业务 graph。harness 不输出独立 business_validation_success 字段，因此不伪造该字段。合同成功不等于语义完美。

## 逐 case 与 Python guard

| case_id | requirement_text | reference | raw Jev | guarded | raw / guarded correct? |
| --- | --- | --- | --- | --- | --- |
| score-0 | 身体健康 | NON_SCORABLE | NON_SCORABLE | NON_SCORABLE | True / True |
| score-1 | 吃苦耐劳 | NON_SCORABLE | NON_SCORABLE | NON_SCORABLE | True / True |
| score-2 | 爱岗敬业 | NON_SCORABLE | NON_SCORABLE | NON_SCORABLE | True / True |
| score-3 | 道德品质 | NON_SCORABLE | NON_SCORABLE | NON_SCORABLE | True / True |
| score-4 | 接受出差 | NEEDS_CONFIRMATION | NEEDS_CONFIRMATION | NEEDS_CONFIRMATION | True / True |
| score-5 | 接受搬迁 | NEEDS_CONFIRMATION | NEEDS_CONFIRMATION | NEEDS_CONFIRMATION | True / True |
| score-6 | Python | SCORED | SCORED | SCORED | True / True |
| score-7 | 与产品、测试、算法团队协作交付 | SCORED | SCORED | SCORED | True / True |

raw correct count=8；guarded correct count=8；guard corrected cases=0（无）；guard harmed cases=0（无）。单例 score-0 raw/guarded=1/1，NON_SCORABLE=1/1，其余两类没有样本（accuracy=null）。单例与 batch 中 score-0 重复，不算第九个独立样本。

| Class | Raw accuracy | Guarded accuracy |
| --- | --- | --- |
| SCORED | 2/2（100%） | 2/2（100%） |
| NEEDS_CONFIRMATION | 2/2（100%） | 2/2（100%） |
| NON_SCORABLE | 4/4（100%） | 4/4（100%） |
| Overall | 8/8（100%） | 8/8（100%） |

## 成本与结果文件

| Step | Latency ms | Input tokens | Output tokens | Total tokens | Requests | Retry |
| --- | --- | --- | --- | --- | --- | --- |
| [JD jd-01](../evals/results/phase_3_5b_r/jd-live-r3.json) | 7952.355 | 151 | 547 | 698 | 1 | 0 |
| [Jev score-0](../evals/results/phase_3_5b_r/scoreability-single-live-r3.json) | 550.898 | 495 | 51 | 546 | 1 | 0 |
| [Jev 8-case batch](../evals/results/phase_3_5b_r/scoreability-suite-live-r3.json) | 527.180 | 2122 | 389 | 2511 | 1 | 0 |
| 合计 | 9030.433 | 2768 | 987 | 3755 | 3 | 0 |

## 验证、限制与 STOP

只改文档及 3 个 result JSON，没有代码变化，按本轮指令无需重跑完整 regression。进行了无网络结果一致性核验、原 JD validator 核验和 git diff whitespace 检查，自动验证 0 live。R2 后端 237 / 前端 16 及完整 regression 只作历史记录，不冒充本轮重跑结果。

所有输入均为现有 reviewed synthetic fixtures。独立 eval 没有业务 graph/SQLite 访问，没有真实用户数据、normalization/matching live。AI_DEBUG_PAYLOADS=0；没有开启全文 debug，没有保存原始 provider envelope、headers、reasoning 或密钥。

已满足 R3 完成条件：JD 单例、Jev 单例、8-case 单请求批量成功，raw/guarded、各类、逐 case、guard corrected/harmed、latency/tokens 和请求计数完整。本次 100% 仅说明当前 8 个 reviewed synthetic cases 表现，不能声称总体准确率；单 JD 也不能证明其他输入。零 guard 调整不能证明其在其他输入上的效果。

历史 PHASE_3_5B / R / R1 / R2 原样保留。本轮提交 `Phase 3.5B-R3`，hash 通过 git log -1 获取，确认 working tree clean 后 STOP。不自动增加 live 请求、不开始 Phase 4；后续工作需用户新指令。
