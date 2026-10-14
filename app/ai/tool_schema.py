"""One schema validator and value-free repair diagnostics for every tool boundary."""
from __future__ import annotations

import json
from functools import lru_cache
from typing import Any


@lru_cache(maxsize=256)
def schema_validator(serialized: str):
    from jsonschema import Draft202012Validator, validators
    from referencing import Registry
    from referencing.exceptions import NoSuchResource

    def no_network(uri):
        raise NoSuchResource(ref=uri)

    schema = json.loads(serialized)
    cls = validators.validator_for(schema, default=Draft202012Validator)
    cls.check_schema(schema)
    return cls(schema, registry=Registry(retrieve=no_network))


def argument_issues(schema: dict, arguments: Any, *, validator=None) -> list[dict]:
    validator = validator or schema_validator(json.dumps(schema, sort_keys=True))
    try:
        json.dumps(arguments, allow_nan=False)
    except (TypeError, ValueError, OverflowError):
        return [{"argument_path": "$", "validation_rule": "json", "expected_type": "JSON with finite numbers"}]
    # Paths may include caller-controlled map keys. Only echo names declared by
    # the schema; rejected values and unknown property names never leave here.
    names = set()
    def collect(node):
        if isinstance(node, dict):
            names.update((node.get("properties") or {}).keys())
            for key, child in node.items():
                if key not in {"enum", "const", "default", "examples"}:
                    collect(child)
        elif isinstance(node, list):
            for child in node:
                collect(child)
    collect(schema)
    issues = []
    def errors():
        for error in validator.iter_errors(arguments):
            yield error
            # Keep alternative/conditional branch diagnoses actionable too.
            yield from error.context
    for error in errors():
        path = "$" + "".join(f"[{part}]" if isinstance(part, int) else
            "." + (str(part) if part in names else "*") for part in error.absolute_path)
        rule = str(error.validator)
        detail = {"argument_path": path, "validation_rule": rule}
        if rule == "required" and isinstance(error.instance, dict):
            missing = [name for name in error.validator_value if name not in error.instance]
            detail["required_fields"] = missing[:8]
            if missing:
                detail["argument_path"] += "." + str(missing[0])
        elif rule == "type":
            detail["expected_type"] = error.validator_value
        issues.append(detail)
        if len(issues) >= 4:
            break
    return issues
