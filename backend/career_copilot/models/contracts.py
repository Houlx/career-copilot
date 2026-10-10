"""Deterministic prompt contracts for the project's Pydantic response models."""

import hashlib
import json

from pydantic import BaseModel

_METADATA = {"title", "description", "examples", "default"}
_SCALARS = {
    "type",
    "required",
    "enum",
    "const",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "minLength",
    "maxLength",
    "pattern",
    "minItems",
    "maxItems",
    "uniqueItems",
}


def build_prompt_contract(response_model: type[BaseModel]) -> dict:
    schema = response_model.model_json_schema()
    definitions = schema.get("$defs", {})

    def compact(node: dict, stack: tuple[str, ...] = ()) -> dict:
        if not isinstance(node, dict):
            raise ValueError("Unsupported non-object schema")
        if "$ref" in node:
            ref = node["$ref"]
            if not isinstance(ref, str) or not ref.startswith("#/$defs/"):
                raise ValueError(f"Unsupported schema reference: {ref}")
            name = ref.removeprefix("#/$defs/")
            if "/" in name or "~" in name or name not in definitions:
                raise ValueError(f"Unresolved schema reference: {ref}")
            if ref in stack:
                raise ValueError(f"Recursive schema reference: {ref}")
            siblings = set(node) - {"$ref"} - _METADATA
            if siblings:
                raise ValueError(f"Unsupported reference constraints: {sorted(siblings)}")
            return compact(definitions[name], (*stack, ref))
        result = {}
        for key, value in node.items():
            if key in _METADATA:
                continue
            if key in _SCALARS:
                result[key] = value
            elif key == "properties":
                result[key] = {name: compact(child, stack) for name, child in value.items()}
            elif key == "anyOf":
                result[key] = [compact(child, stack) for child in value]
            elif key == "items":
                result[key] = compact(value, stack)
            elif key == "additionalProperties":
                result[key] = value if isinstance(value, bool) else compact(value, stack)
            else:
                raise ValueError(f"Unsupported schema keyword: {key}")
        return result

    return compact({key: value for key, value in schema.items() if key != "$defs"})


def serialize_contract(contract: dict) -> str:
    return json.dumps(contract, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def contract_hash(contract: dict) -> str:
    return hashlib.sha256(serialize_contract(contract).encode()).hexdigest()[:16]
