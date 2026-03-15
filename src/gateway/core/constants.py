"""Shared constants and enums for the Walacor gateway."""

from __future__ import annotations

from enum import Enum


HASH_ALGORITHM = "SHA3-512"
SHA3_512_HEX_LENGTH = 128


class AttestationStatus(str, Enum):
    VERIFIED = "verified"
    FAILED = "failed"
    PENDING = "pending"
    REVOKED = "revoked"
    TAMPERED = "tampered"


class EnforcementLevel(str, Enum):
    BLOCKING = "blocking"
    WARNING = "warning"
    ADVISORY = "advisory"


class RuleOperator(str, Enum):
    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    CONTAINS = "contains"
    NOT_CONTAINS = "not_contains"
    REGEX = "regex"
    NOT_REGEX = "not_regex"
    GREATER_THAN = "greater_than"
    LESS_THAN = "less_than"
    IN_LIST = "in_list"
