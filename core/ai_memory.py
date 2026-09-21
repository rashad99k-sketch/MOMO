"""Persistent AI market memory with a hash chain.

The memory is an audit trail, not a model-training oracle.  It stores the exact
AI decision/evidence snapshot that existed at the time of observation.
"""
from __future__ import annotations
import hashlib, json, os, threading, time
from pathlib import Path
from typing import Any

_LOCK = threading.RLock()
_LAST_HASH = "0" * 64
_INITIALIZED = False
_LAST_WRITE: dict[str, float] = {}


def _path() -> Path:
    return Path(os.getenv("AI_MEMORY_PATH", "logs/ai_market_memory.jsonl"))


def _canon(obj: dict) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _init(path: Path) -> None:
    global _INITIALIZED, _LAST_HASH
    if _INITIALIZED:
        return
    _INITIALIZED = True
    try:
        if path.exists():
            for line in reversed(path.read_text(encoding="utf-8").splitlines()):
                if line.strip():
                    _LAST_HASH = str(json.loads(line).get("hash") or ("0"*64))
                    break
    except Exception:
        _LAST_HASH = "0" * 64


def append(event_type: str, payload: dict[str, Any], *, key: str = "", min_interval: float = 0.0) -> dict:
    global _LAST_HASH
    now = time.time()
    if key and min_interval > 0:
        last = _LAST_WRITE.get(key, 0.0)
        if now-last < min_interval:
            return {"skipped": True, "event_type": event_type, "key": key}
    path = _path()
    with _LOCK:
        _init(path)
        record = {"ts": round(now,6), "event_type": str(event_type), "payload": payload or {}, "prev_hash": _LAST_HASH}
        record["hash"] = hashlib.sha256(_canon(record)).hexdigest()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            _LAST_HASH = record["hash"]
            if key:
                _LAST_WRITE[key] = now
        except Exception:
            pass
        return record


def record_market(decision: dict) -> dict:
    symbol = decision.get("symbol", "")
    side = decision.get("side", "")
    return append("MARKET_DECISION", decision, key=f"{symbol}:{side}", min_interval=float(os.getenv("AI_MARKET_MEMORY_INTERVAL", "15")))


def record_trade_event(event_type: str, payload: dict) -> dict:
    return append(event_type, payload)


def verify(path: str | None = None) -> tuple[bool, int, str]:
    p = Path(path) if path else _path()
    if not p.exists():
        return True, 0, "empty"
    prev = "0" * 64; count = 0
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip(): continue
            rec = json.loads(line); stored = rec.pop("hash")
            if rec.get("prev_hash") != prev: return False, count, "prev_hash_mismatch"
            expected = hashlib.sha256(_canon(rec)).hexdigest()
            if expected != stored: return False, count, "hash_mismatch"
            prev = stored; count += 1
        return True, count, "ok"
    except Exception as exc:
        return False, count, f"verification_error:{type(exc).__name__}"
