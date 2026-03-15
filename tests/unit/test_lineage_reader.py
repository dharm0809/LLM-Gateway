"""Unit tests for the LineageReader (read-only WAL SQLite queries + chain verification)."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile

import pytest
from gateway.core import compute_sha3_512_string

from gateway.lineage.reader import LineageReader


_GENESIS_HASH = "0" * 128


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _create_wal_db(db_path: str):
    """Create a WAL DB with the same schema as WALWriter and populate it with test data."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS wal_records (
            execution_id  TEXT    PRIMARY KEY,
            record_json   TEXT    NOT NULL,
            created_at    TEXT    NOT NULL,
            delivered     INTEGER NOT NULL DEFAULT 0,
            delivered_at  TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS gateway_attempts (
            request_id    TEXT    PRIMARY KEY,
            timestamp     TEXT    NOT NULL,
            tenant_id     TEXT    NOT NULL,
            provider      TEXT,
            model_id      TEXT,
            path          TEXT    NOT NULL,
            disposition   TEXT    NOT NULL,
            execution_id  TEXT,
            status_code   INTEGER NOT NULL,
            user          TEXT
        )"""
    )
    conn.commit()
    return conn


def _compute_hash(execution_id, policy_version, policy_result, prev_hash, seq, timestamp):
    canonical = "|".join([
        execution_id,
        str(policy_version),
        policy_result,
        prev_hash,
        str(seq),
        timestamp,
    ])
    return compute_sha3_512_string(canonical)


def _insert_chained_records(conn, session_id: str, count: int = 3):
    """Insert a Merkle-chained series of execution records into wal_records."""
    prev_hash = _GENESIS_HASH
    records = []
    for i in range(count):
        eid = f"exec-{session_id}-{i}"
        ts = f"2026-03-03T10:00:{i:02d}+00:00"
        record_hash = _compute_hash(eid, 1, "pass", prev_hash, i, ts)
        record = {
            "execution_id": eid,
            "session_id": session_id,
            "model_attestation_id": "test-model",
            "policy_version": 1,
            "policy_result": "pass",
            "tenant_id": "test-tenant",
            "gateway_id": "gw-test",
            "timestamp": ts,
            "prompt_text": f"prompt {i}",
            "response_content": f"response {i}",
            "sequence_number": i,
            "record_hash": record_hash,
            "previous_record_hash": prev_hash,
        }
        conn.execute(
            "INSERT INTO wal_records (execution_id, record_json, created_at) VALUES (?, ?, ?)",
            (eid, json.dumps(record), ts),
        )
        prev_hash = record_hash
        records.append(record)
    conn.commit()
    return records


def _insert_tool_event(conn, execution_id: str, event_id: str):
    """Insert a tool event record linked to an execution."""
    record = {
        "event_id": event_id,
        "execution_id": execution_id,
        "event_type": "tool_call",
        "tool_name": "web_search",
        "input_hash": "a" * 128,
        "output_hash": "b" * 128,
        "duration_ms": 150,
        "timestamp": "2026-03-03T10:00:05+00:00",
    }
    conn.execute(
        "INSERT INTO wal_records (execution_id, record_json, created_at) VALUES (?, ?, ?)",
        (event_id, json.dumps(record), "2026-03-03T10:00:05+00:00"),
    )
    conn.commit()
    return record


def _insert_attempts(conn, count: int = 5):
    """Insert gateway_attempts rows."""
    for i in range(count):
        disp = "forwarded" if i < 3 else "denied_auth"
        conn.execute(
            """INSERT INTO gateway_attempts
               (request_id, timestamp, tenant_id, provider, model_id, path, disposition, execution_id, status_code)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (f"req-{i}", f"2026-03-03T10:00:{i:02d}+00:00", "test-tenant",
             "ollama", "qwen3:4b", "/v1/chat/completions", disp, f"exec-{i}" if i < 3 else None, 200 if i < 3 else 403),
        )
    conn.commit()


@pytest.fixture
def wal_db():
    """Create a temp WAL database, yield (db_path, conn), then cleanup."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "wal.db")
        conn = _create_wal_db(db_path)
        yield db_path, conn
        conn.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_list_sessions_returns_distinct_sessions(wal_db):
    db_path, conn = wal_db
    _insert_chained_records(conn, "session-a", count=3)
    _insert_chained_records(conn, "session-b", count=2)

    reader = LineageReader(db_path)
    sessions = reader.list_sessions()
    reader.close()

    assert len(sessions) == 2
    ids = {s["session_id"] for s in sessions}
    assert "session-a" in ids
    assert "session-b" in ids
    a = next(s for s in sessions if s["session_id"] == "session-a")
    assert a["record_count"] == 3
    b = next(s for s in sessions if s["session_id"] == "session-b")
    assert b["record_count"] == 2


def test_list_sessions_excludes_tool_events(wal_db):
    db_path, conn = wal_db
    _insert_chained_records(conn, "session-c", count=2)
    _insert_tool_event(conn, "exec-session-c-0", "tool-evt-1")

    reader = LineageReader(db_path)
    sessions = reader.list_sessions()
    reader.close()

    assert len(sessions) == 1
    assert sessions[0]["record_count"] == 2  # tool event not counted


def test_session_timeline_ordered_by_sequence(wal_db):
    db_path, conn = wal_db
    _insert_chained_records(conn, "session-d", count=4)

    reader = LineageReader(db_path)
    timeline = reader.get_session_timeline("session-d")
    reader.close()

    assert len(timeline) == 4
    for i, rec in enumerate(timeline):
        assert rec["sequence_number"] == i
        assert rec["execution_id"] == f"exec-session-d-{i}"


def test_get_execution_returns_full_record(wal_db):
    db_path, conn = wal_db
    _insert_chained_records(conn, "session-e", count=1)

    reader = LineageReader(db_path)
    rec = reader.get_execution("exec-session-e-0")
    reader.close()

    assert rec is not None
    assert rec["execution_id"] == "exec-session-e-0"
    assert rec["prompt_text"] == "prompt 0"
    assert rec["session_id"] == "session-e"


def test_get_execution_not_found(wal_db):
    db_path, conn = wal_db

    reader = LineageReader(db_path)
    rec = reader.get_execution("nonexistent-id")
    reader.close()

    assert rec is None


def test_get_tool_events_for_execution(wal_db):
    db_path, conn = wal_db
    _insert_chained_records(conn, "session-f", count=1)
    _insert_tool_event(conn, "exec-session-f-0", "tool-evt-f1")
    _insert_tool_event(conn, "exec-session-f-0", "tool-evt-f2")

    reader = LineageReader(db_path)
    events = reader.get_tool_events("exec-session-f-0")
    reader.close()

    assert len(events) == 2
    assert all(e["event_type"] == "tool_call" for e in events)
    assert all(e["execution_id"] == "exec-session-f-0" for e in events)


def test_get_attempts_with_stats(wal_db):
    db_path, conn = wal_db
    _insert_attempts(conn, count=5)

    reader = LineageReader(db_path)
    result = reader.get_attempts(limit=10)
    reader.close()

    assert len(result["items"]) == 5
    assert result["total"] == 5
    assert result["stats"]["forwarded"] == 3
    assert result["stats"]["denied_auth"] == 2


def test_verify_chain_valid(wal_db):
    db_path, conn = wal_db
    _insert_chained_records(conn, "session-g", count=5)

    reader = LineageReader(db_path)
    result = reader.verify_chain("session-g")
    reader.close()

    assert result["valid"] is True
    assert result["record_count"] == 5
    assert result["errors"] == []
    assert result["session_id"] == "session-g"


def test_verify_chain_detects_tamper(wal_db):
    db_path, conn = wal_db
    records = _insert_chained_records(conn, "session-h", count=3)

    # Tamper with the second record's hash
    tampered = json.loads(conn.execute(
        "SELECT record_json FROM wal_records WHERE execution_id = ?",
        ("exec-session-h-1",),
    ).fetchone()[0])
    tampered["record_hash"] = "f" * 128  # bogus hash
    conn.execute(
        "UPDATE wal_records SET record_json = ? WHERE execution_id = ?",
        (json.dumps(tampered), "exec-session-h-1"),
    )
    conn.commit()

    reader = LineageReader(db_path)
    result = reader.verify_chain("session-h")
    reader.close()

    assert result["valid"] is False
    assert result["record_count"] == 3
    assert len(result["errors"]) > 0


def test_verify_chain_empty_session(wal_db):
    db_path, conn = wal_db

    reader = LineageReader(db_path)
    result = reader.verify_chain("nonexistent-session")
    reader.close()

    assert result["valid"] is True
    assert result["record_count"] == 0


def test_list_sessions_pagination(wal_db):
    db_path, conn = wal_db
    for i in range(5):
        _insert_chained_records(conn, f"session-page-{i}", count=1)

    reader = LineageReader(db_path)
    page1 = reader.list_sessions(limit=2, offset=0)
    page2 = reader.list_sessions(limit=2, offset=2)
    page3 = reader.list_sessions(limit=2, offset=4)
    reader.close()

    assert len(page1) == 2
    assert len(page2) == 2
    assert len(page3) == 1


def _insert_recent_attempts(conn, count: int = 5):
    """Insert gateway_attempts with current timestamps so datetime('now', ...) filters work."""
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    for i in range(count):
        ts = (now - timedelta(minutes=i)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        disp = "forwarded" if i < 3 else "denied_auth"
        conn.execute(
            """INSERT INTO gateway_attempts
               (request_id, timestamp, tenant_id, provider, model_id, path, disposition, execution_id, status_code)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (f"recent-{i}", ts, "test-tenant", "ollama", "qwen3:4b",
             "/v1/chat/completions", disp, f"exec-recent-{i}" if i < 3 else None, 200 if i < 3 else 403),
        )
    conn.commit()


def test_get_metrics_history_returns_buckets(wal_db):
    db_path, conn = wal_db
    _insert_recent_attempts(conn, count=5)

    reader = LineageReader(db_path)
    result = reader.get_metrics_history("1h")
    reader.close()

    assert result["range"] == "1h"
    assert isinstance(result["buckets"], list)
    total = sum(b["total"] for b in result["buckets"])
    assert total == 5
    total_allowed = sum(b["allowed"] for b in result["buckets"])
    assert total_allowed == 3  # 3 forwarded
    total_blocked = sum(b["blocked"] for b in result["buckets"])
    assert total_blocked == 2  # 2 denied_auth


def test_get_metrics_history_invalid_range_defaults_to_1h(wal_db):
    db_path, conn = wal_db
    _insert_recent_attempts(conn, count=3)

    reader = LineageReader(db_path)
    result = reader.get_metrics_history("invalid")
    reader.close()

    assert result["range"] == "1h"


def test_get_metrics_history_empty(wal_db):
    db_path, conn = wal_db

    reader = LineageReader(db_path)
    result = reader.get_metrics_history("7d")
    reader.close()

    assert result["range"] == "7d"
    assert result["buckets"] == []


# ---------------------------------------------------------------------------
# Token + Latency History Tests
# ---------------------------------------------------------------------------

def _insert_recent_execution_records(conn, count: int = 4):
    """Insert wal_records with token + latency fields and current timestamps."""
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    for i in range(count):
        eid = f"exec-tl-{i}"
        ts = (now - timedelta(minutes=i)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        record = {
            "execution_id": eid,
            "session_id": "session-tl",
            "model_attestation_id": "test-model",
            "model_id": "qwen3:4b",
            "timestamp": ts,
            "prompt_tokens": 100 + i * 10,
            "completion_tokens": 50 + i * 5,
            "total_tokens": 150 + i * 15,
            "latency_ms": 200.0 + i * 50,
        }
        conn.execute(
            "INSERT INTO wal_records (execution_id, record_json, created_at) VALUES (?, ?, ?)",
            (eid, json.dumps(record), ts),
        )
    conn.commit()


def test_get_token_latency_history_returns_buckets(wal_db):
    db_path, conn = wal_db
    _insert_recent_execution_records(conn, count=4)

    reader = LineageReader(db_path)
    result = reader.get_token_latency_history("1h")
    reader.close()

    assert result["range"] == "1h"
    assert isinstance(result["buckets"], list)
    assert len(result["buckets"]) > 0

    total_prompt = sum(b["prompt_tokens"] for b in result["buckets"])
    assert total_prompt == 100 + 110 + 120 + 130  # 460
    total_completion = sum(b["completion_tokens"] for b in result["buckets"])
    assert total_completion == 50 + 55 + 60 + 65  # 230
    total_requests = sum(b["request_count"] for b in result["buckets"])
    assert total_requests == 4


def test_get_token_latency_history_empty(wal_db):
    db_path, conn = wal_db

    reader = LineageReader(db_path)
    result = reader.get_token_latency_history("24h")
    reader.close()

    assert result["range"] == "24h"
    assert result["buckets"] == []


def test_get_token_latency_history_invalid_range(wal_db):
    db_path, conn = wal_db
    _insert_recent_execution_records(conn, count=2)

    reader = LineageReader(db_path)
    result = reader.get_token_latency_history("bogus")
    reader.close()

    assert result["range"] == "1h"
    assert isinstance(result["buckets"], list)
