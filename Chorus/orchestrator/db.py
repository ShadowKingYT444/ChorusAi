from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING, Any

import aiosqlite

if TYPE_CHECKING:
    from orchestrator.models import JobRecord, PeerEntry

logger = logging.getLogger("orchestrator.db")


def _json_default(o: Any) -> Any:
    if hasattr(o, "model_dump"):
        return o.model_dump()
    if hasattr(o, "value"):
        return o.value
    return str(o)


def _dumps(obj: Any) -> str:
    return json.dumps(obj, default=_json_default)


class ChorusDB:
    """aiosqlite wrapper mirroring in-memory state for orchestrator durability."""

    def __init__(self) -> None:
        self._conn: aiosqlite.Connection | None = None
        self._lock: asyncio.Lock | None = None
        self._path: str | None = None

    async def connect(self, path: str) -> None:
        # Normalize to forward-slashes (Windows-friendly, sqlite accepts both).
        self._path = path.replace("\\", "/")
        self._lock = asyncio.Lock()
        self._conn = await aiosqlite.connect(self._path)
        await self._conn.execute("PRAGMA journal_mode=WAL;")
        await self._conn.execute("PRAGMA synchronous=NORMAL;")
        await self.init_schema()

    async def init_schema(self) -> None:
        assert self._conn is not None
        await self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                workspace_id TEXT,
                spec_json TEXT NOT NULL,
                status TEXT NOT NULL,
                current_round INTEGER NOT NULL DEFAULT 0,
                shadow_credit_cost INTEGER NOT NULL DEFAULT 0,
                routing_mode TEXT,
                created_at REAL NOT NULL,
                completed_at REAL,
                final_answer TEXT,
                citations_json TEXT,
                settlement_json TEXT,
                error TEXT
            );
            CREATE TABLE IF NOT EXISTS peers (
                peer_id TEXT PRIMARY KEY,
                model TEXT,
                address TEXT,
                status TEXT,
                protocol_version TEXT,
                joined_at REAL,
                last_seen REAL,
                pubkey TEXT
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                ts REAL NOT NULL,
                type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                UNIQUE(job_id, seq)
            );
            CREATE INDEX IF NOT EXISTS idx_events_job_seq ON events(job_id, seq);
            CREATE TABLE IF NOT EXISTS _health (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                ts REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS workspace_usage (
                workspace_id TEXT PRIMARY KEY,
                jobs_created INTEGER NOT NULL DEFAULT 0,
                shadow_credits_reserved INTEGER NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL
            );
            """
        )
        await self._ensure_job_column("workspace_id", "TEXT")
        await self._ensure_job_column("shadow_credit_cost", "INTEGER NOT NULL DEFAULT 0")
        await self._ensure_job_column("routing_mode", "TEXT")
        await self._conn.commit()

    async def _ensure_job_column(self, column_name: str, ddl: str) -> None:
        assert self._conn is not None
        async with self._conn.execute("PRAGMA table_info(jobs)") as cur:
            rows = await cur.fetchall()
        existing = {str(row[1]) for row in rows}
        if column_name in existing:
            return
        await self._conn.execute(f"ALTER TABLE jobs ADD COLUMN {column_name} {ddl}")

    async def mirror_job(self, record: "JobRecord") -> None:
        if self._conn is None or self._lock is None:
            return
        try:
            data = record.model_dump()
            job_id = data["job_id"]
            spec = data.get("spec") or {}
            status = data.get("status")
            if hasattr(status, "value"):
                status = status.value
            current_round = data.get("current_round") or 0
            workspace_id = data.get("workspace_id")
            shadow_credit_cost = int(data.get("shadow_credit_cost") or 0)
            routing_mode = data.get("routing_mode")
            settlement = data.get("settlement_preview")
            error = data.get("error")
            final_answer = data.get("final_answer")
            citations = data.get("citations")
            now = time.time()

            async with self._lock:
                # Preserve existing created_at on upsert; set completed_at on terminal states.
                async with self._conn.execute(
                    "SELECT created_at, completed_at FROM jobs WHERE job_id=?",
                    (job_id,),
                ) as cur:
                    row = await cur.fetchone()
                created_at = row[0] if row and row[0] is not None else now
                prev_completed = row[1] if row else None
                completed_at = prev_completed
                if status in ("completed", "failed") and completed_at is None:
                    completed_at = now

                await self._conn.execute(
                    """
                    INSERT OR REPLACE INTO jobs
                      (job_id, workspace_id, spec_json, status, current_round, shadow_credit_cost, routing_mode,
                       created_at, completed_at,
                       final_answer, citations_json, settlement_json, error)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        job_id,
                        workspace_id,
                        _dumps(spec),
                        str(status),
                        int(current_round or 0),
                        shadow_credit_cost,
                        routing_mode,
                        float(created_at),
                        float(completed_at) if completed_at is not None else None,
                        final_answer,
                        _dumps(citations) if citations is not None else None,
                        _dumps(settlement) if settlement is not None else None,
                        error,
                    ),
                )
                await self._conn.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("mirror_job failed: %s", exc)

    async def mirror_peer(self, entry: "PeerEntry") -> None:
        if self._conn is None or self._lock is None:
            return
        try:
            data = entry.model_dump()
            status = data.get("status")
            if hasattr(status, "value"):
                status = status.value
            pubkey = data.get("pubkey")
            async with self._lock:
                await self._conn.execute(
                    """
                    INSERT OR REPLACE INTO peers
                      (peer_id, model, address, status, protocol_version, joined_at, last_seen, pubkey)
                    VALUES (?,?,?,?,?,?,?,?)
                    """,
                    (
                        data["peer_id"],
                        data.get("model"),
                        data.get("address"),
                        str(status) if status is not None else None,
                        data.get("protocol_version"),
                        data.get("joined_at"),
                        data.get("last_seen"),
                        pubkey,
                    ),
                )
                await self._conn.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("mirror_peer failed: %s", exc)

    async def remove_peer(self, peer_id: str) -> None:
        if self._conn is None or self._lock is None:
            return
        try:
            async with self._lock:
                await self._conn.execute("DELETE FROM peers WHERE peer_id=?", (peer_id,))
                await self._conn.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("remove_peer failed: %s", exc)

    async def append_event(
        self,
        job_id: str,
        seq: int,
        ts: float,
        etype: str,
        payload: dict[str, Any],
    ) -> None:
        if self._conn is None or self._lock is None:
            return
        try:
            async with self._lock:
                await self._conn.execute(
                    """
                    INSERT OR IGNORE INTO events (job_id, seq, ts, type, payload_json)
                    VALUES (?,?,?,?,?)
                    """,
                    (job_id, int(seq), float(ts), etype, _dumps(payload)),
                )
                await self._conn.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("append_event failed: %s", exc)

    async def list_chats(
        self,
        limit: int = 20,
        offset: int = 0,
        workspace_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if self._conn is None:
            return []
        try:
            if workspace_id is None:
                query = """
                    SELECT job_id, workspace_id, spec_json, status, shadow_credit_cost, routing_mode,
                           created_at, completed_at, final_answer
                      FROM jobs
                     ORDER BY created_at DESC
                     LIMIT ? OFFSET ?
                """
                params = (int(limit), int(offset))
            else:
                query = """
                    SELECT job_id, workspace_id, spec_json, status, shadow_credit_cost, routing_mode,
                           created_at, completed_at, final_answer
                      FROM jobs
                     WHERE workspace_id=?
                     ORDER BY created_at DESC
                     LIMIT ? OFFSET ?
                """
                params = (workspace_id, int(limit), int(offset))
            async with self._conn.execute(query, params) as cur:
                rows = await cur.fetchall()
            chats: list[dict[str, Any]] = []
            for row in rows:
                (
                    job_id,
                    chat_workspace_id,
                    spec_json,
                    status,
                    shadow_credit_cost,
                    routing_mode,
                    created_at,
                    completed_at,
                    final_answer,
                ) = row
                try:
                    spec = json.loads(spec_json) if spec_json else {}
                except Exception:  # noqa: BLE001
                    spec = {}
                chats.append(
                    {
                        "job_id": job_id,
                        "workspace_id": chat_workspace_id,
                        "status": status,
                        "shadow_credit_cost": shadow_credit_cost,
                        "routing_mode": routing_mode,
                        "created_at": created_at,
                        "completed_at": completed_at,
                        "final_answer": final_answer,
                        "prompt": spec.get("prompt"),
                        "context": spec.get("context"),
                    }
                )
            return chats
        except Exception as exc:  # noqa: BLE001
            logger.warning("list_chats failed: %s", exc)
            return []

    async def get_chat(self, job_id: str, workspace_id: str | None = None) -> dict[str, Any] | None:
        if self._conn is None:
            return None
        try:
            if workspace_id is None:
                query = """
                    SELECT job_id, workspace_id, spec_json, status, current_round, shadow_credit_cost,
                           routing_mode, created_at, completed_at, final_answer, citations_json,
                           settlement_json, error
                      FROM jobs WHERE job_id=?
                """
                params = (job_id,)
            else:
                query = """
                    SELECT job_id, workspace_id, spec_json, status, current_round, shadow_credit_cost,
                           routing_mode, created_at, completed_at, final_answer, citations_json,
                           settlement_json, error
                      FROM jobs WHERE job_id=? AND workspace_id=?
                """
                params = (job_id, workspace_id)
            async with self._conn.execute(query, params) as cur:
                row = await cur.fetchone()
            if row is None:
                return None
            (
                jid,
                chat_workspace_id,
                spec_json,
                status,
                current_round,
                shadow_credit_cost,
                routing_mode,
                created_at,
                completed_at,
                final_answer,
                citations_json,
                settlement_json,
                error,
            ) = row
            try:
                spec = json.loads(spec_json) if spec_json else {}
            except Exception:  # noqa: BLE001
                spec = {}
            try:
                citations = json.loads(citations_json) if citations_json else None
            except Exception:  # noqa: BLE001
                citations = None
            try:
                settlement = json.loads(settlement_json) if settlement_json else None
            except Exception:  # noqa: BLE001
                settlement = None

            events: list[dict[str, Any]] = []
            async with self._conn.execute(
                """
                SELECT seq, ts, type, payload_json FROM events
                 WHERE job_id=? ORDER BY seq ASC LIMIT 500
                """,
                (job_id,),
            ) as cur:
                ev_rows = await cur.fetchall()
            for seq, ts, etype, payload_json in ev_rows:
                try:
                    payload = json.loads(payload_json) if payload_json else {}
                except Exception:  # noqa: BLE001
                    payload = {}
                events.append({"seq": seq, "ts": ts, "type": etype, "payload": payload})

            return {
                "job_id": jid,
                "workspace_id": chat_workspace_id,
                "status": status,
                "current_round": current_round,
                "shadow_credit_cost": shadow_credit_cost,
                "routing_mode": routing_mode,
                "created_at": created_at,
                "completed_at": completed_at,
                "final_answer": final_answer,
                "citations": citations,
                "settlement": settlement,
                "error": error,
                "spec": spec,
                "prompt": spec.get("prompt"),
                "context": spec.get("context"),
                "events": events,
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_chat failed: %s", exc)
            return None

    async def ready_check(self) -> bool:
        if self._conn is None or self._lock is None:
            return False
        try:
            now = time.time()
            async with self._lock:
                await self._conn.execute(
                    "INSERT OR REPLACE INTO _health (key, value, ts) VALUES (?,?,?)",
                    ("sentinel", "ok", now),
                )
                await self._conn.commit()
                async with self._conn.execute(
                    "SELECT value FROM _health WHERE key=?", ("sentinel",)
                ) as cur:
                    row = await cur.fetchone()
            return bool(row and row[0] == "ok")
        except Exception as exc:  # noqa: BLE001
            logger.warning("ready_check failed: %s", exc)
            return False

    async def close(self) -> None:
        try:
            if self._conn is not None:
                await self._conn.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("close failed: %s", exc)
        finally:
            self._conn = None
