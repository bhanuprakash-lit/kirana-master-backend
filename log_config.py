"""
Shared logging primitives — imported by main.py (to register handlers)
and by routers (to read the buffer). Kept separate to avoid circular imports.
"""
from __future__ import annotations

import collections
import json
import logging
import threading
from contextvars import ContextVar
from datetime import datetime, timezone


# ── Per-request context var ────────────────────────────────────────────────────
# Set by the HTTP middleware in main.py; every log line emitted during that
# request automatically carries the same request_id in its JSON output.
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


# ── JSON formatter ─────────────────────────────────────────────────────────────
class JsonFormatter(logging.Formatter):
    """One JSON object per line — Azure Log Analytics parses this natively.
    Any extra= fields passed to logger.xxx() are included verbatim."""

    _SKIP = frozenset({
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "message", "taskName",
    })

    def format(self, record: logging.LogRecord) -> str:
        record.message = record.getMessage()
        ts = (
            datetime.fromtimestamp(record.created, tz=timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%S.")
            + f"{record.msecs:03.0f}Z"
        )
        doc: dict = {
            "ts":         ts,
            "level":      record.levelname,
            "logger":     record.name,
            "msg":        record.message,
            "request_id": request_id_var.get("-"),
        }
        for k, v in record.__dict__.items():
            if k not in self._SKIP and not k.startswith("_"):
                doc[k] = v
        if record.exc_info:
            doc["exc"] = self.formatException(record.exc_info)
        return json.dumps(doc, default=str)


# ── In-process ring buffer ─────────────────────────────────────────────────────
class MemoryLogHandler(logging.Handler):
    """Keeps the last `maxlen` log records in memory.

    Serves two purposes:
      - /admin/logs        → tail(n, level) for the log viewer page
      - /admin/logs/stream → SSE live tail via cursor polling

    Thread-safe: logs are emitted from the event loop AND from executor
    threads (run_in_executor, DB/FCM work), so the buffer is guarded by a
    plain threading.Lock. The SSE reader polls by sequence cursor rather than
    receiving pushes — this avoids touching loop-bound asyncio primitives from
    a non-loop thread.

    IMPORTANT: each process has its OWN buffer. With uvicorn workers>1 or
    multiple Azure replicas, /admin/logs only reflects the worker that served
    the request. Azure Log Analytics (stdout JSON) is the system of record;
    this buffer is a best-effort live convenience.
    """

    def __init__(self, maxlen: int = 2000) -> None:
        super().__init__()
        self._buf: collections.deque[dict] = collections.deque(maxlen=maxlen)
        self._seq = 0
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            raw = self.format(record)
            with self._lock:
                self._seq += 1
                self._buf.append({"seq": self._seq, "raw": raw, "level": record.levelname})
        except Exception:
            self.handleError(record)

    @staticmethod
    def _public(e: dict) -> dict:
        return {"raw": e["raw"], "level": e["level"]}

    def tail(self, n: int, level: str = "") -> list[dict]:
        with self._lock:
            entries = list(self._buf)
        if level:
            entries = [e for e in entries if e["level"] == level.upper()]
        return [self._public(e) for e in entries[-n:]]

    def seed(self, n: int, level: str = "") -> tuple[list[dict], int]:
        """Return the last n entries AND the current cursor, captured atomically
        so the SSE reader can stream from exactly where the seed ended."""
        with self._lock:
            entries = list(self._buf)
            cursor = self._seq
        if level:
            entries = [e for e in entries if e["level"] == level.upper()]
        return [self._public(e) for e in entries[-n:]], cursor

    def read_since(self, cursor: int) -> tuple[list[dict], int]:
        """Return entries newer than `cursor` plus the new cursor. Safe to call
        from a coroutine — it only touches the lock-guarded deque."""
        with self._lock:
            new = [self._public(e) for e in self._buf if e["seq"] > cursor]
            cursor = self._seq
        return new, cursor


# Singleton — registered as a handler in main.py, read by routers
log_memory = MemoryLogHandler(maxlen=2000)
log_memory.setFormatter(JsonFormatter())
