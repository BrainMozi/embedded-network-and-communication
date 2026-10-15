"""Shared paths and append-only structured event logging."""
import json
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVENT_LOG = Path(os.environ.get("CANDEMO_EVENT_LOG", ROOT / "data" / "events.jsonl"))


def mono_ms() -> int:
    return time.monotonic_ns() // 1_000_000


def emit_event(source: str, kind: str, message: str, **fields) -> dict:
    EVENT_LOG.parent.mkdir(parents=True, exist_ok=True)
    item = {"ms": mono_ms(), "source": source, "kind": kind,
            "message": message, **fields}
    blob = (json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    fd = os.open(EVENT_LOG, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, blob)
    finally:
        os.close(fd)
    return item
