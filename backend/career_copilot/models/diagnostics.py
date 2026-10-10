"""Internal, value-free output diagnostics; never include provider text or reasoning."""

import re
from contextvars import ContextVar

from pydantic import ValidationError

DIAGNOSTICS_ENABLED: ContextVar[bool] = ContextVar("diagnostics_enabled", default=False)


def field_name(value):
    if isinstance(value, int):
        return value
    if re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]{0,63}", str(value)) and not re.search(
        r"key|authorization|secret|password|token|reasoning", str(value), re.I
    ):
        return value
    return "[redacted_field]"


def json_shape(value, depth=0):
    if depth >= 8:
        return {"type": type(value).__name__}
    if isinstance(value, dict):
        return {field_name(k): json_shape(v, depth + 1) for k, v in list(value.items())[:50]}
    if isinstance(value, list):
        return {
            "type": "array",
            "count": len(value),
            "items": [json_shape(v, depth + 1) for v in value[:3]],
        }
    return {"type": type(value).__name__}


def diagnostic(stage, *, exc=None, loc=(), rule=None, shape=None):
    result = {"failure_stage": stage}
    if not DIAGNOSTICS_ENABLED.get():
        return result
    errors = []
    if isinstance(exc, ValidationError):
        for error in exc.errors(include_input=False, include_context=False, include_url=False)[:50]:
            kind = error["type"]
            # Pydantic messages can interpolate input, enum values or custom validator text.
            message = {"missing": "Field required", "extra_forbidden": "Extra field forbidden"}.get(
                kind, "Value does not satisfy schema constraint"
            )
            errors.append(
                {"loc": [field_name(v) for v in error["loc"]], "type": kind, "msg": message}
            )
    else:
        errors.append(
            {
                "loc": list(loc),
                "type": rule or type(exc).__name__,
                "msg": rule or "Response does not satisfy provider contract",
            }
        )
    result["errors"] = errors
    if shape is not None:
        result["structured_output_shape"] = shape
    return result
