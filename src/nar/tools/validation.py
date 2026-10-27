"""Validation of tool arguments against a JSON Schema.

Messages name the offending argument so the model can correct its next call.
No type coercion is done: ``"10"`` is not accepted for an integer.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

_JSON_TYPES = (
    (bool, "boolean"),
    (int, "integer"),
    (float, "number"),
    (str, "string"),
    (list, "array"),
    (dict, "object"),
    (type(None), "null"),
)


def validate_arguments(schema: Mapping[str, Any], arguments: Mapping[str, Any]) -> list[str]:
    """Return one message per problem; an empty list means the arguments are valid."""
    validator = Draft202012Validator(schema)
    messages: list[str] = []
    reported: set[tuple[Any, ...]] = set()  # required/additionalProperties list every name once
    for error in validator.iter_errors(arguments):
        key = (error.validator, tuple(error.absolute_path))
        if error.validator in ("required", "additionalProperties"):
            if key in reported:
                continue
            reported.add(key)
        messages.extend(_describe(error))
    return messages


def _describe(error: ValidationError) -> list[str]:
    where = _location(error)
    if error.validator == "required":
        missing = [name for name in error.validator_value if name not in error.instance]
        return [f"missing required argument '{_join(where, name)}'" for name in missing]
    if error.validator == "additionalProperties":
        known = error.schema.get("properties", {})
        patterns = error.schema.get("patternProperties", {})
        extra = [
            name
            for name in error.instance
            if name not in known and not any(re.search(p, name) for p in patterns)
        ]
        return [f"unknown argument '{_join(where, name)}'" for name in extra]
    if error.validator == "type":
        expected = error.validator_value
        if not isinstance(expected, str):
            expected = " or ".join(expected)
        return [f"argument '{where}' must be of type {expected}, got {_json_type(error.instance)}"]
    return [f"argument '{where}': {error.message}"]


def _location(error: ValidationError) -> str:
    return ".".join(str(part) for part in error.absolute_path)


def _join(where: str, name: str) -> str:
    return f"{where}.{name}" if where else name


def _json_type(value: Any) -> str:
    for python_type, json_type in _JSON_TYPES:
        if isinstance(value, python_type):
            return json_type
    return type(value).__name__
