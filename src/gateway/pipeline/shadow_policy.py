"""Shadow policy evaluation — observe-only mode for testing policy impact."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Supported operators (must match builtin policy engine)
_OPERATORS = {
    "equals": lambda a, b: str(a) == str(b),
    "not_equals": lambda a, b: str(a) != str(b),
    "contains": lambda a, b: str(b) in str(a),
    "greater_than": lambda a, b: float(a) > float(b),
}


async def run_shadow_policies(
    policies: list[dict[str, Any]],
    context: dict[str, Any],
) -> list[dict[str, Any]]:
    """Evaluate policies in shadow mode. Never raises -- catches per-policy errors.

    Returns list of dicts: {policy_name, version, would_block, failed_rules, error?}
    """
    results: list[dict[str, Any]] = []
    for policy in policies:
        try:
            name = policy.get("name", "unknown")
            version = policy.get("version", 0)
            rules = policy.get("rules", [])

            if not isinstance(rules, list):
                results.append({
                    "policy_name": name,
                    "version": version,
                    "would_block": False,
                    "failed_rules": [],
                    "error": f"Invalid rules type: {type(rules).__name__}",
                })
                continue

            failed_rules: list[dict] = []
            for rule in rules:
                field = rule.get("field", "")
                operator = rule.get("operator", "equals")
                expected = rule.get("value", "")
                actual = context.get(field)

                op_fn = _OPERATORS.get(operator)
                if op_fn is None:
                    failed_rules.append({"field": field, "reason": f"unknown operator: {operator}"})
                    continue

                try:
                    if not op_fn(actual, expected):
                        failed_rules.append({
                            "field": field,
                            "operator": operator,
                            "expected": str(expected),
                            "actual": str(actual),
                        })
                except (TypeError, ValueError) as e:
                    failed_rules.append({"field": field, "reason": str(e)})

            would_block = len(failed_rules) > 0
            results.append({
                "policy_name": name,
                "version": version,
                "would_block": would_block,
                "failed_rules": failed_rules,
            })
            if would_block:
                logger.info(
                    "Shadow policy '%s' v%s would BLOCK: %s",
                    name, version, failed_rules,
                )
        except Exception as e:
            results.append({
                "policy_name": policy.get("name", "unknown"),
                "version": policy.get("version", 0),
                "would_block": False,
                "failed_rules": [],
                "error": str(e),
            })
    return results
