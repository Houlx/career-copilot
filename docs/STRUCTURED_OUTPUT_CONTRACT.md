# Phase 4.4 — Pydantic-Driven Structured Output Contract

```text
Pydantic response model
    ↓ deterministic compact prompt contract
PromptSpec.runtime_system (business instructions + contract)
    ↓ Provider response_format={"type":"json_object"}
JSON response
    ↓ authoritative Pydantic validation
Business grounding / mapping validation
```

Pydantic is the single source of truth for output fields, types, enum values, required fields and constraints. Prompt runtime contract is now Pydantic-derived. Business prompts retain extraction rules, evidence meaning, grounding, and input-as-data instructions; they no longer duplicate structural field lists, enum lists or example output shapes.

## Bound tasks

| Task | Existing response model |
| --- | --- |
| profile-facts-v1 | ProfileFacts |
| profile-normalize-v1 | NormalizedProfile |
| jd-requirements-v2 | JobRequirements (nested ExtractedRequirement) |
| matching-v1 | Matching (nested Match) |
| resume-generation-v2 | ResumeDraft |
| generation-diagnostic-v1 | DiagnosticResult |

`PromptSpec.response_model=None` preserves the original system prompt for other tasks. Judge/Jev native Choice and Noul protocols and historical JD extraction are unchanged. Router rejects a supplied validation model that differs from a task's bound response model before executing the task.

## Generator and provider boundary

`models/contracts.py::build_prompt_contract` calls the existing `model_json_schema()` and inlines local `#/$defs/Name` references. It preserves `type`, `properties`, `required`, `items`, `enum`, `const`, `additionalProperties`, numerical bounds and multiples, string length/pattern, and array length/uniqueness constraints when present. It strips `title`, `description`, `examples`, and `default` metadata without changing requiredness. Property names named `title` or `description` remain intact.

The helper handles only this project's schema forms. External/unresolved refs, recursive refs, constraint-bearing ref siblings, and unsupported keywords (including `anyOf`) raise `ValueError`. It never silently drops an unknown constraint. Reference expansion uses a per-branch reference stack, so repeated nonrecursive models such as Resume FactItem are supported. No network or LLM is used to generate schemas; this is not a general JSON Schema compiler.

Compact serialization uses sorted keys, UTF-8 text and no whitespace separators. This avoids irrelevant title/description/$defs noise and manual maintenance. Inline expansion can repeat nested objects: the Resume contract is larger than the original schema with shared refs. Compact does not guarantee fewer bytes for every model; it makes the model-facing structure explicit while retaining all supported constraints.

`json_object` only guarantees JSON syntax. It does **not** guarantee Pydantic schema compliance. The prompt contract helps generation; Pydantic remains authoritative validation, followed by existing business validators. Strict config, Literal values and Field constraints remain unchanged. Invalid fields, enum values or types still fail; there are no aliases, category normalization, fallback, or results→matches / result→status / explanation→reason repairs.

This phase leaves provider configuration untouched: local Generation stays `json_object`, and the existing explicitly configured `json_schema` capability remains available for compatibility. No capability discovery or server-side schema enforcement assumption was added.

## Trace, versions and replay

`schema_contract_hash` is the first 16 hexadecimal characters of SHA256 over stable compact JSON. Success, failure, fake and replay metadata include the current bound contract hash. Production traces store this hash, not the full contract. Final prompt content is available only through the existing development + `AI_DEBUG_PAYLOADS=1` payload policy.

Prompt versions remain unchanged. The repository replay key is task + prompt_version + SHA256(canonical **business input**); it does not include system prompt bytes. Contract injection therefore does not invalidate or migrate existing fixtures. Replay still authoritatively validates saved outputs against the current Pydantic model. A replay hash describes the current runtime contract, not proof that a historical fixture was originally generated with it.

Future field/enum/constraint changes must start in Pydantic and flow automatically to the prompt. PromptSpec must bind the actual workflow response model. Business meaning stays in the business prompt or business validators, including constraints implemented by Pydantic validators that JSON Schema cannot express.

## Offline size observation

No provider tokenizer or live request was used. Exact provider input-token deltas are unavailable offline. The following records measured character overhead of contract injection relative to each **new business-only** prompt, with a rough characters/4 token proxy (not GLM token counts, and not net change from the former hand-written prompt):

| Task | Contract characters | Injection characters | Approximate injection tokens |
| --- | ---: | ---: | ---: |
| profile-facts-v1 | 566 | 740 | ~185 |
| profile-normalize-v1 | 535 | 709 | ~177 |
| jd-requirements-v2 | 581 | 755 | ~189 |
| matching-v1 | 504 | 678 | ~170 |
| resume-generation-v1 | 1526 | 1700 | ~425 |
| generation-diagnostic-v1 | 112 | 286 | ~72 |

No inference tuning followed this observation. The existing workspace policies (`profile-facts-v1=low`, `jd-requirements-v2=high`, `matching-v1=high`, historical `jd-extract-v1=low`), timeout, retry, output limits (including matching=6000), `.env` and fixtures are unchanged. Regression expectations now match those already-existing policies.

## Validation and manual follow-up

Offline validation: 420 backend tests; Ruff check/format; frontend check, 25 tests, build, audit (0 vulnerabilities); Profile 10-case, Jobs 12/8/5-case and Resume 6-case replay evals. Fake HTTP `scripts/check_http.py --resume` used a temporary empty DB and isolated backend/Vite ports; Profile, Job, Matching, Resume, Settings policy, contract hashes, trace and standalone replay passed. Temporary services/config/DB were removed. Agent live requests = **0**; no browser interaction, GLM or Jev calls.

The user can manually restart the backend, confirm Generation remains json_object, and run Job Analysis with the same point-cloud JD. Check `extract_job_requirements` and `matching-v1` for successful Pydantic validation and absent `failure_stage`; note `schema_contract_hash`, token usage, latency and retry count. Then generate a Resume and check its existing schema/grounding validation. Offline tests do not establish live model compliance. No Phase 5 work is included.

Phase 5：nullable `ResumeRevisionProposal.updated_resume` 通过递归 anyOf compact 保留各分支及 nested constraints。未知关键词仍拒绝；原五个业务 contract 不变。

## Resume generation v2 update

The active task remains resume-generation-v2. The document refactor removes headline from
ResumeDraft; the derived contract hash changes. ResumeDraft contains semantic language/summary/
sections only. Summary and sections may be empty individually; the full content must not be empty.
Stable section kinds remain skills/experience/projects/education/additional. No identity/contact
fields enter provider contracts. Python combines the validated content with a confirmed Profile
ResumeHeader snapshot into ResumeDocument. The same semantic contract is reused by the existing
revision flow; Header cannot be revised. No structured-output architecture or task policy changed.
The size table above records the historical v1 observation.

LLM decides what to say. Renderer decides how the resume is organized and displayed.
Header is deterministic. Global section order is deterministic. Visual layout is deterministic.
Python supplies localized labels and omits empty sections. No PDF renderer was implemented.

Workflow, trace, fake mode, debug replay API/UI, reasoning tests and eval runner use v2. New v2 replay files reuse synthetic v1 outputs for contract and grounding regression only; they are not live captures. Historical v1 files/results remain unchanged. Exact task/version lookup has no cross-version fallback. Offline replay does not evaluate compliance with the new prose instructions.
