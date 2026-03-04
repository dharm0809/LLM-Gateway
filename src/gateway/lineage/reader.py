"""Read-only SQLite reader for the WAL database. Separate connection from WALWriter."""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from walacor_core import compute_sha3_512_string

logger = logging.getLogger(__name__)

_GENESIS_HASH = "0" * 128


class LineageReader:
    """Read-only access to the WAL SQLite database for lineage queries.

    Opens with `?mode=ro` and `PRAGMA query_only=ON` so the write-path
    WALWriter is never blocked or corrupted by lineage reads.
    """

    def __init__(self, db_path: str) -> None:
        self._path = db_path
        self._conn: sqlite3.Connection | None = None

    def _ensure_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            if not Path(self._path).exists():
                raise FileNotFoundError(f"WAL database not found: {self._path}")
            uri = f"file:{self._path}?mode=ro"
            self._conn = sqlite3.connect(uri, uri=True)
            self._conn.execute("PRAGMA query_only=ON")
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def list_sessions(self, limit: int = 50, offset: int = 0) -> list[dict]:
        """List distinct sessions with record count and latest timestamp."""
        conn = self._ensure_conn()
        cur = conn.execute(
            """
            SELECT
                json_extract(record_json, '$.session_id') AS session_id,
                COUNT(*) AS record_count,
                MAX(json_extract(record_json, '$.timestamp')) AS last_activity,
                COALESCE(json_extract(record_json, '$.model_id'),
                         json_extract(record_json, '$.model_attestation_id')) AS model
            FROM wal_records
            WHERE json_extract(record_json, '$.session_id') IS NOT NULL
              AND json_extract(record_json, '$.event_type') IS NULL
            GROUP BY session_id
            ORDER BY last_activity DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        return [dict(row) for row in cur.fetchall()]

    def get_session_timeline(self, session_id: str) -> list[dict]:
        """Return all execution records for a session, ordered by sequence_number."""
        conn = self._ensure_conn()
        cur = conn.execute(
            """
            SELECT execution_id, record_json, created_at
            FROM wal_records
            WHERE json_extract(record_json, '$.session_id') = ?
              AND json_extract(record_json, '$.event_type') IS NULL
            ORDER BY json_extract(record_json, '$.sequence_number') ASC,
                     created_at ASC
            """,
            (session_id,),
        )
        results = []
        for row in cur.fetchall():
            record = json.loads(row["record_json"])
            record["_wal_created_at"] = row["created_at"]
            results.append(record)
        return results

    def get_execution(self, execution_id: str) -> dict[str, Any] | None:
        """Return full execution record by execution_id."""
        conn = self._ensure_conn()
        cur = conn.execute(
            "SELECT record_json FROM wal_records WHERE execution_id = ?",
            (execution_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return json.loads(row["record_json"])

    def get_tool_events(self, execution_id: str) -> list[dict]:
        """Return tool event records linked to an execution."""
        conn = self._ensure_conn()
        cur = conn.execute(
            """
            SELECT record_json
            FROM wal_records
            WHERE json_extract(record_json, '$.execution_id') = ?
              AND json_extract(record_json, '$.event_type') = 'tool_call'
            ORDER BY json_extract(record_json, '$.timestamp') ASC
            """,
            (execution_id,),
        )
        return [json.loads(row["record_json"]) for row in cur.fetchall()]

    def get_attempts(self, limit: int = 100, offset: int = 0) -> dict:
        """Return recent attempt records and disposition stats."""
        conn = self._ensure_conn()
        cur = conn.execute(
            """
            SELECT request_id, timestamp, tenant_id, provider, model_id,
                   path, disposition, execution_id, status_code
            FROM gateway_attempts
            ORDER BY timestamp DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        items = [dict(row) for row in cur.fetchall()]

        cur2 = conn.execute(
            "SELECT disposition, COUNT(*) AS count FROM gateway_attempts GROUP BY disposition"
        )
        stats = {row["disposition"]: row["count"] for row in cur2.fetchall()}

        total_cur = conn.execute("SELECT COUNT(*) AS total FROM gateway_attempts")
        total = total_cur.fetchone()["total"]

        return {"items": items, "stats": stats, "total": total}

    def verify_chain(self, session_id: str) -> dict:
        """Verify Merkle chain integrity for a session.

        Recomputes record_hash for each record and checks previous_record_hash linkage.
        Returns {valid: bool, record_count: int, errors: list[str]}.
        """
        records = self.get_session_timeline(session_id)
        if not records:
            return {"valid": True, "record_count": 0, "errors": [], "session_id": session_id}

        errors: list[str] = []
        prev_hash = _GENESIS_HASH

        for i, rec in enumerate(records):
            seq = rec.get("sequence_number")
            rec_hash = rec.get("record_hash")
            rec_prev = rec.get("previous_record_hash")
            execution_id = rec.get("execution_id", "")

            # Check sequence_number ordering
            if seq is not None and seq != i:
                errors.append(
                    f"Record {i}: expected sequence_number={i}, got {seq} (execution_id={execution_id})"
                )

            # Check previous_record_hash linkage
            if rec_prev is not None and rec_prev != prev_hash:
                errors.append(
                    f"Record {i}: previous_record_hash mismatch "
                    f"(expected={prev_hash[:16]}..., got={rec_prev[:16]}..., execution_id={execution_id})"
                )

            # Recompute record_hash
            if rec_hash is not None:
                computed = compute_sha3_512_string("|".join([
                    execution_id,
                    str(rec.get("policy_version", "")),
                    str(rec.get("policy_result", "")),
                    str(rec.get("previous_record_hash", "")),
                    str(seq if seq is not None else ""),
                    str(rec.get("timestamp", "")),
                ]))
                if computed != rec_hash:
                    errors.append(
                        f"Record {i}: record_hash mismatch "
                        f"(computed={computed[:16]}..., stored={rec_hash[:16]}..., execution_id={execution_id})"
                    )
                prev_hash = rec_hash
            else:
                # Record has no chain fields (e.g. unchained legacy record)
                errors.append(
                    f"Record {i}: missing record_hash (execution_id={execution_id})"
                )

        return {
            "valid": len(errors) == 0,
            "record_count": len(records),
            "errors": errors,
            "session_id": session_id,
        }
