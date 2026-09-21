import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.ai_market_brain import MarketIntelligenceBrain, ai_entry_gate
from core.ai_memory import append, verify


def make_df(n=120):
    x = np.arange(n, dtype=float)
    close = 100 + x * 0.08 + np.sin(x / 5.0) * 0.4
    open_ = close - 0.03
    high = close + 0.25
    low = close - 0.25
    volume = 1000 + (x % 12) * 40
    volume[-1] = 2400
    return pd.DataFrame({
        "timestamp": np.arange(n) * 900000,
        "open": open_, "high": high, "low": low,
        "close": close, "volume": volume,
    })


def test_brain_schema_and_bounds():
    brain = MarketIntelligenceBrain(mode="SHADOW")
    result = brain.evaluate("TEST/USDT", "BUY", make_df(), {
        "bids": [[100.0, 30.0], [99.9, 20.0]],
        "asks": [[100.1, 10.0], [100.2, 10.0]],
    }, legacy_analysis={"score": 8.2, "narrative_score": 8.0},
       trade_intelligence={"score": 78, "behaviour": "ACCUMULATION"})
    assert result["schema_version"] == "1.0"
    assert 0 <= result["score"] <= 100
    assert 0 <= result["confidence"] <= 100
    assert result["preferred_zone"]["low"] < result["preferred_zone"]["high"]
    assert "liquidity" in result["agents"]
    assert "institutional" in result["agents"]
    assert isinstance(result["scenarios"], list)


def test_shadow_gate_never_blocks():
    ok, reason = ai_entry_gate({"score": 1, "confidence": 1, "action": "WAIT"}, False, mode="SHADOW")
    assert ok is True
    assert reason == "AI_SHADOW"


def test_assisted_gate_is_bounded():
    ok, reason = ai_entry_gate({"score": 60, "confidence": 90, "action": "APPROVE_CANDIDATE"}, True, mode="ASSISTED")
    assert not ok
    assert "AI_SCORE_BELOW" in reason


def test_ai_memory_hash_chain(tmp_path, monkeypatch):
    path = tmp_path / "ai.jsonl"
    monkeypatch.setenv("AI_MEMORY_PATH", str(path))
    # module-level state is reset for deterministic isolated test behavior.
    import core.ai_memory as mem
    mem._INITIALIZED = False
    mem._LAST_HASH = "0" * 64
    mem._LAST_WRITE.clear()
    append("TEST", {"value": 1})
    append("TEST", {"value": 2})
    ok, count, reason = verify(str(path))
    assert ok and count == 2 and reason == "ok"
