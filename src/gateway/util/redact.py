"""Redacting wrapper for sensitive values. __repr__/__str__ return [REDACTED] to avoid log leakage."""

from __future__ import annotations


class RedactedString:
    """Holds a value but masks it in __str__ and __repr__."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    @property
    def value(self) -> str:
        return self._value

    def __str__(self) -> str:
        return "[REDACTED]"

    def __repr__(self) -> str:
        return "RedactedString([REDACTED])"

    def __len__(self) -> int:
        return len(self._value)
