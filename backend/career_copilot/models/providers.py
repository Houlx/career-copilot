"""Live wire protocols. Adapters never retry or access career persistence."""

import json
from typing import Annotated, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..config import ModelConfig
from ..errors import CopilotError, ErrorCode
from .diagnostics import diagnostic, json_shape
from .jobs import Scoreability, ScoreabilityInput, ScoreabilityResult, apply_classifications
from .prompts import JudgeDiagnosticResult, PromptSpec


class TokenUsage(BaseModel):
    prompt_tokens: int = Field(default=0, ge=0, strict=True)
    completion_tokens: int = Field(default=0, ge=0, strict=True)


class JevUsage(BaseModel):
    input_tokens: int | None = Field(default=None, ge=0, strict=True)
    output_tokens: int | None = Field(default=None, ge=0, strict=True)


class NoulAnswer(BaseModel):
    model_config = ConfigDict(strict=True)
    type: Literal["noul"]
    noul: float = Field(ge=0, le=1, allow_inf_nan=False)


class JevResponse(BaseModel):
    model_config = ConfigDict(strict=True)
    model: str
    answers: dict[str, NoulAnswer]
    usage: JevUsage | None = None


SCOREABILITY_CRITERIA = {
    "SCORED": "Career facts can reasonably assess education, major, technology stack, "
    "programming languages, frameworks, databases, work experience, explicit project "
    "capability, language ability or observable work capability.",
    "NEEDS_CONFIRMATION": "Requires explicit user confirmation of real-world conditions: "
    "accepting travel, relocation, shifts, driving licence or availability.",
    "NON_SCORABLE": "Unsuitable for automatic resume/evidence scoring: physical health, "
    "hardship tolerance, devotion to work, moral character or vague personality traits.",
}


class ChoiceAnswer(BaseModel):
    model_config = ConfigDict(strict=True)
    type: Literal["choice"]
    choice: Scoreability
    probabilities: dict[Scoreability, Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]]
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)


class JevChoiceResponse(BaseModel):
    model_config = ConfigDict(strict=True)
    model: str
    answers: dict[str, ChoiceAnswer]
    usage: JevUsage | None = None


def unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def add_usage(meta: dict, input_tokens: int, output_tokens: int):
    meta["input_tokens"] += input_tokens
    meta["output_tokens"] += output_tokens
    meta["total_tokens"] = meta["input_tokens"] + meta["output_tokens"]


class HTTPProvider:
    def __init__(self, config: ModelConfig, *, timeout_seconds: float):
        self.config = config
        self.timeout_seconds = timeout_seconds

    def validate_task(self, spec: PromptSpec, schema: type[BaseModel]):
        pass

    def post(self, endpoint: str, body: dict) -> httpx.Response:
        with httpx.Client(timeout=self.timeout_seconds, follow_redirects=False) as client:
            response = client.post(
                f"{self.config.base_url.rstrip('/')}/{endpoint}",
                headers={"Authorization": f"Bearer {self.config.api_key.get_secret_value()}"},
                json=body,
            )
            response.raise_for_status()
            return response


class OpenAICompatibleProvider(HTTPProvider):
    def invoke[T: BaseModel](
        self, spec: PromptSpec, payload: dict, schema: type[T], meta: dict
    ) -> T:
        response_format = {"type": self.config.structured_output_mode}
        if self.config.structured_output_mode == "json_schema":
            response_format["json_schema"] = {
                "name": schema.__name__,
                "strict": True,
                "schema": schema.model_json_schema(),
            }
        response = self.post(
            "chat/completions",
            {
                "model": self.config.model,
                "messages": [
                    {"role": "system", "content": spec.system},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                "temperature": spec.temperature,
                "max_tokens": spec.max_output_tokens,
                "response_format": response_format,
            },
        )
        meta["http_status"] = response.status_code
        try:
            body = response.json()
        except ValueError as exc:
            meta.update(diagnostic("RESPONSE_ENVELOPE_INVALID", exc=exc, rule="provider_json"))
            raise
        path = ("usage",)
        try:
            if not isinstance(body, dict):
                raise TypeError("Expected response object")
            usage = TokenUsage.model_validate(body.get("usage") or {})
            add_usage(meta, usage.prompt_tokens, usage.completion_tokens)
            path = ("choices", 0, "message", "content")
            content = body["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise TypeError("Expected assistant string content")
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            meta.update(diagnostic("RESPONSE_ENVELOPE_INVALID", exc=exc, loc=path))
            raise
        try:
            parsed = json.loads(content)
        except ValueError as exc:
            meta.update(diagnostic("STRUCTURED_JSON_INVALID", exc=exc, loc=path))
            meta["assistant_content_kind"] = (
                "markdown_fence" if content.lstrip().startswith("```") else "invalid_json"
            )
            raise
        try:
            return schema.model_validate_json(content)
        except ValidationError as exc:
            stage = (
                "STRUCTURED_JSON_INVALID"
                if any(e["type"] == "json_invalid" for e in exc.errors())
                else "PYDANTIC_VALIDATION_FAILED"
            )
            meta.update(diagnostic(stage, exc=exc, shape=json_shape(parsed)))
            raise


class JevProvider(HTTPProvider):
    """Native Noul diagnostic and batched finite Choice scoreability."""

    def validate_task(self, spec: PromptSpec, schema: type[BaseModel]):
        if (spec.version, schema) not in (
            ("judge-diagnostic-v1", JudgeDiagnosticResult),
            ("scoreability-v1", ScoreabilityResult),
        ):
            # Business mappings are deliberately deferred; reject before sending any data.
            raise CopilotError(ErrorCode.MODEL_OUTPUT_INVALID)

    def invoke[T: BaseModel](
        self, spec: PromptSpec, payload: dict, schema: type[T], meta: dict
    ) -> T:
        self.validate_task(spec, schema)
        if spec.version == "scoreability-v1":
            requirements = ScoreabilityInput.model_validate(payload).model_dump()["requirements"]
            if not requirements:
                return schema.model_validate({"classifications": []})
            state = {"requirements": requirements}
            questions = {
                str(i): {
                    "type": "choice",
                    "instructions": f"{spec.system} Evaluate only state.requirements[{i}].",
                    "criteria": SCOREABILITY_CRITERIA,
                }
                for i in range(len(requirements))
            }
        else:
            state = payload["state"]
            questions = {"has_python": {"type": "noul", "instructions": spec.system}}
        response = self.post(
            "systemone",
            {
                "model": self.config.model,
                "state": state,
                "questions": questions,
            },
        )
        body = json.loads(response.text, object_pairs_hook=unique_json_object)
        if not isinstance(body, dict):
            raise ValueError("Expected a decision response")
        # Capture reported usage even if subsequent answer validation fails.
        usage = JevUsage.model_validate(body.get("usage") or {})
        add_usage(meta, usage.input_tokens or 0, usage.output_tokens or 0)
        if spec.version == "scoreability-v1":
            parsed = JevChoiceResponse.model_validate(body)
            if set(parsed.answers) != set(questions):
                raise ValueError("Incomplete or extra Choice answers")
            for answer in parsed.answers.values():
                if set(answer.probabilities) != set(SCOREABILITY_CRITERIA):
                    raise ValueError("Invalid Choice criteria")
                if abs(sum(answer.probabilities.values()) - 1) > 0.001:
                    raise ValueError("Invalid probability distribution")
                if answer.probabilities[answer.choice] < max(answer.probabilities.values()):
                    raise ValueError("Choice disagrees with probabilities")
            output = schema.model_validate(
                {
                    "classifications": [
                        {"requirement_index": i, "scoreability": parsed.answers[str(i)].choice}
                        for i in range(len(requirements))
                    ]
                }
            )
            apply_classifications(requirements, output)
            meta["provider_metadata"] = {
                "response_model": parsed.model,
                "question_count": len(questions),
            }
            request_id = response.headers.get("x-typesafe-request-id")
            if request_id:
                meta["provider_metadata"]["request_id"] = request_id[:256]
            return output
        parsed = JevResponse.model_validate(body)
        probability = parsed.answers["has_python"].noul
        # Explicit allowlist: never persist response headers, arbitrary body or reasoning.
        meta["provider_metadata"] = {
            "response_model": parsed.model,
            "probability": probability,
        }
        request_id = response.headers.get("x-typesafe-request-id")
        if request_id:
            meta["provider_metadata"]["request_id"] = request_id[:256]
        return schema.model_validate({"decision": probability >= 0.5})
