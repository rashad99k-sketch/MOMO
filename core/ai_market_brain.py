"""BARON Market Intelligence Brain.

A deterministic, multi-agent evidence layer inspired by the three reference
projects studied for BARON:

* Web-Check: many independent checks -> one evidence view.
* MiroFish: specialist agents + memory/scenario thinking.
* Loop Engineering: explicit verification/receipts and bounded actions.

This module is deliberately execution-free.  It never sends an order and never
mutates exchange state.  The legacy institutional strategy remains the source
of trading intent; this brain validates, scores, explains and proposes zones.

AI modes are consumed by the portfolio gate, not by this module:
    SHADOW     = observe only
    ASSISTED   = may veto a weak AI/strategy conflict when explicitly enabled
    AUTONOMOUS = may approve only when every hard safety/strategy condition is
                 already satisfied; it still cannot bypass the execution layer.
"""
from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

SCHEMA_VERSION = "1.0"
SIDES = {"BUY", "SELL"}


def _f(v: Any, default: float = 0.0) -> float:
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, _f(v)))


def _side(side: str) -> str:
    return str(side or "").upper().strip()


def _safe_df(df: pd.DataFrame) -> bool:
    return isinstance(df, pd.DataFrame) and len(df) >= 30 and all(
        c in df.columns for c in ("open", "high", "low", "close", "volume")
    )


def _atr(df: pd.DataFrame, period: int = 14) -> float:
    h, l, c = df["high"], df["low"], df["close"]
    prev = c.shift(1)
    tr = pd.concat([(h-l).abs(), (h-prev).abs(), (l-prev).abs()], axis=1).max(axis=1)
    value = tr.rolling(period, min_periods=period).mean().iloc[-1]
    return max(_f(value), _f(c.iloc[-1]) * 0.001)


def _volume_ratio(df: pd.DataFrame) -> float:
    base = _f(df["volume"].iloc[-21:-1].mean(), 1.0)
    return _f(df["volume"].iloc[-1]) / max(base, 1e-12)


def _orderbook_imbalance(orderbook: Optional[dict], depth: int = 10) -> float:
    if not isinstance(orderbook, dict):
        return 0.0
    try:
        bids = orderbook.get("bids", [])[:depth]
        asks = orderbook.get("asks", [])[:depth]
        b = sum(_f(x[1]) for x in bids if len(x) >= 2)
        a = sum(_f(x[1]) for x in asks if len(x) >= 2)
        return (b-a) / (b+a) if b+a > 0 else 0.0
    except Exception:
        return 0.0


def _swing_levels(df: pd.DataFrame, window: int = 3) -> Tuple[List[float], List[float]]:
    highs: List[float] = []
    lows: List[float] = []
    start = window
    end = len(df) - window
    for i in range(start, end):
        h = _f(df["high"].iloc[i]); l = _f(df["low"].iloc[i])
        if h >= _f(df["high"].iloc[i-window:i+window+1].max()):
            highs.append(h)
        if l <= _f(df["low"].iloc[i-window:i+window+1].min()):
            lows.append(l)
    return highs, lows


def _cluster(values: List[float], tolerance: float) -> List[float]:
    if not values:
        return []
    vals = sorted(v for v in values if v > 0)
    if not vals:
        return []
    out: List[float] = []
    group = [vals[0]]
    for v in vals[1:]:
        if abs(v-group[-1]) / max(abs(v), 1e-12) <= tolerance:
            group.append(v)
        else:
            out.append(sum(group)/len(group))
            group = [v]
    out.append(sum(group)/len(group))
    return out


def _liquidity_map(df: pd.DataFrame, side: str, atr: float) -> Dict[str, Any]:
    highs, lows = _swing_levels(df, 3)
    price = _f(df["close"].iloc[-1])
    tol = max(0.001, min(0.004, (atr/max(price, 1e-12))*0.8))
    hi_pools = _cluster(highs[-12:], tol)
    lo_pools = _cluster(lows[-12:], tol)
    target = "SELL_SIDE" if side == "BUY" else "BUY_SIDE"
    pools = lo_pools if side == "BUY" else hi_pools
    nearest = None
    if pools:
        if side == "BUY":
            below = [x for x in pools if x <= price]
            nearest = max(below) if below else min(pools, key=lambda x: abs(x-price))
        else:
            above = [x for x in pools if x >= price]
            nearest = min(above) if above else min(pools, key=lambda x: abs(x-price))
    last = df.iloc[-1]
    sweep = False
    sweep_level = nearest
    if nearest:
        if side == "BUY":
            sweep = _f(last["low"]) < nearest and _f(last["close"]) > nearest
        else:
            sweep = _f(last["high"]) > nearest and _f(last["close"]) < nearest
    eq_count = 0
    source = []
    for pool in pools:
        if sum(1 for x in pools if abs(x-pool)/max(abs(pool),1e-12) <= tol) >= 2:
            eq_count += 1
    if hi_pools:
        source.append("SWING_HIGH_POOL")
    if lo_pools:
        source.append("SWING_LOW_POOL")
    if eq_count:
        source.append("EQUAL_LEVEL_CLUSTER")
    if sweep:
        source.append("LIQUIDITY_SWEEP")
    distance_atr = abs(price-nearest)/max(atr,1e-12) if nearest else 999.0
    score = 30.0
    score += 30.0 if nearest else 0.0
    score += 25.0 if sweep else 0.0
    score += min(15.0, eq_count * 5.0)
    return {
        "target_side": target,
        "high_pools": hi_pools[-8:],
        "low_pools": lo_pools[-8:],
        "nearest_pool": nearest,
        "sweep": bool(sweep),
        "sweep_level": sweep_level,
        "distance_atr": round(distance_atr, 3),
        "score": round(_clamp(score), 1),
        "evidence": source,
    }


def _structure_agent(df: pd.DataFrame, side: str) -> Dict[str, Any]:
    highs, lows = _swing_levels(df, 3)
    price = _f(df["close"].iloc[-1])
    bull = len(highs) >= 2 and len(lows) >= 2 and highs[-1] > highs[-2] and lows[-1] > lows[-2]
    bear = len(highs) >= 2 and len(lows) >= 2 and highs[-1] < highs[-2] and lows[-1] < lows[-2]
    bos = False
    level = 0.0
    if side == "BUY" and highs:
        level = highs[-1]
        bos = price > level
    elif side == "SELL" and lows:
        level = lows[-1]
        bos = price < level
    aligned = (side == "BUY" and (bull or bos)) or (side == "SELL" and (bear or bos))
    score = 45.0 + (25.0 if aligned else 0.0) + (15.0 if bos else 0.0)
    return {"direction": "BULLISH" if bull else "BEARISH" if bear else "NEUTRAL",
            "bos": bool(bos), "aligned": bool(aligned), "level": level,
            "score": round(_clamp(score), 1)}


def _flow_agent(df: pd.DataFrame, orderbook: Optional[dict], side: str) -> Dict[str, Any]:
    last = df.iloc[-1]
    body = _f(last["close"])-_f(last["open"])
    vr = _volume_ratio(df)
    obi = _orderbook_imbalance(orderbook)
    directional = (body > 0 and side == "BUY") or (body < 0 and side == "SELL")
    ob_aligned = (obi >= 0.12 and side == "BUY") or (obi <= -0.12 and side == "SELL")
    absorption = vr >= 1.15 and abs(body) <= max(_atr(df)*0.45, 1e-12)
    score = 45.0 + (20 if directional else 0) + (20 if ob_aligned else 0) + (15 if absorption else 0)
    return {"volume_ratio": round(vr, 3), "orderbook_imbalance": round(obi, 4),
            "directional": bool(directional), "orderbook_aligned": bool(ob_aligned),
            "absorption": bool(absorption), "score": round(_clamp(score), 1)}


def _timing_agent(df: pd.DataFrame, side: str, zone: Dict[str, Any], atr: float) -> Dict[str, Any]:
    price = _f(df["close"].iloc[-1])
    distance = _f(zone.get("distance_atr"), 999)
    recent_move = abs(price-_f(df["close"].iloc[-6]))/max(_f(df["close"].iloc[-6]),1e-12) if len(df)>=6 else 0
    late = distance > 2.5 or recent_move > 0.025
    score = 95.0 if distance <= 0.75 else 85.0 if distance <= 1.5 else 68.0 if distance <= 2.5 else 35.0
    if late:
        score -= 25
    phase = "EARLY" if score >= 80 else "DEVELOPING" if score >= 60 else "LATE"
    return {"distance_atr": round(distance,3), "recent_move_pct": round(recent_move*100,3),
            "late": bool(late), "phase": phase, "score": round(_clamp(score),1)}


def _volume_agent(df: pd.DataFrame, side: str) -> Dict[str, Any]:
    vr = _volume_ratio(df)
    atr = _atr(df)
    body = abs(_f(df["close"].iloc[-1])-_f(df["open"].iloc[-1]))
    expansion = vr >= 1.5 and body >= atr*0.6
    absorption = vr >= 1.2 and body <= atr*0.45
    score = 50 + (30 if expansion else 0) + (15 if absorption else 0) - (15 if vr < 0.7 else 0)
    return {"volume_ratio": round(vr,3), "expansion": bool(expansion),
            "absorption": bool(absorption), "score": round(_clamp(score),1)}


def _regime_agent(df: pd.DataFrame) -> Dict[str, Any]:
    atr = _atr(df)
    price = _f(df["close"].iloc[-1])
    adx = 0.0
    if len(df) >= 30:
        h,l,c = df["high"],df["low"],df["close"]
        tr = pd.concat([(h-l).abs(),(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
        atrs = tr.rolling(14).mean()
        up = h.diff(); dn = -l.diff()
        p = up.where((up>dn)&(up>0),0.0).rolling(14).sum()
        m = dn.where((dn>up)&(dn>0),0.0).rolling(14).sum()
        pdi = 100*p/(atrs*14+1e-12); mdi = 100*m/(atrs*14+1e-12)
        dx = 100*(pdi-mdi).abs()/(pdi+mdi+1e-12)
        adx = _f(dx.rolling(14).mean().iloc[-1])
    atr_pct = atr/max(price,1e-12)*100
    if adx >= 35 and atr_pct >= 1.0:
        regime = "EXPANSION"
    elif adx >= 25:
        regime = "TREND"
    elif atr_pct < 0.5:
        regime = "COMPRESSION"
    else:
        regime = "RANGE"
    score = 85 if regime in ("TREND","EXPANSION") else 65 if regime == "COMPRESSION" else 55
    return {"regime": regime, "adx": round(adx,2), "atr_pct": round(atr_pct,3), "score": float(score)}


def _strategy_score(legacy: Optional[dict], trade_intel: Optional[dict]) -> float:
    vals = []
    if isinstance(legacy, dict):
        vals.append(_f(legacy.get("score"))*10.0)
        vals.append(_f(legacy.get("narrative_score"))*10.0)
    if isinstance(trade_intel, dict):
        vals.append(_f(trade_intel.get("score")))
    return _clamp(max(vals) if vals else 0.0)


def _zone_from_context(df: pd.DataFrame, side: str, atr: float, liquidity: Dict[str, Any], trade_intel: Optional[dict]) -> Dict[str, Any]:
    ev = (trade_intel or {}).get("evidence") if isinstance(trade_intel, dict) else {}
    z = ev.get("zone") if isinstance(ev, dict) else None
    if isinstance(z, dict) and z.get("low") is not None:
        low = _f(z.get("low")); high = _f(z.get("high"))
        if high <= 0: high = low
        return {"low": low, "high": high, "type": "TRADE_INTELLIGENCE_ZONE", "score": _f(z.get("score"), 50)}
    pool = _f(liquidity.get("nearest_pool"))
    if pool > 0:
        half = max(atr*0.35, pool*0.0005)
        return {"low": pool-half, "high": pool+half, "type": "LIQUIDITY_POOL", "score": _f(liquidity.get("score"),50)}
    price = _f(df["close"].iloc[-1])
    half = atr*0.5
    return {"low": price-half, "high": price+half, "type": "FALLBACK_VALUE_ZONE", "score": 30.0}


def _scenario(side: str, liq: Dict[str, Any], struct: Dict[str, Any], timing: Dict[str, Any], flow: Dict[str, Any]) -> List[dict]:
    scenarios = []
    if liq.get("sweep") and struct.get("aligned"):
        scenarios.append({"name":"SWEEP_RECLAIM_EXPANSION","support":82,"invalidated_by":"zone_loss"})
    if flow.get("absorption") and timing.get("phase") in ("EARLY","DEVELOPING"):
        scenarios.append({"name":"ABSORPTION_CONTINUATION","support":74,"invalidated_by":"flow_flip"})
    scenarios.append({"name":"FAILED_RECLAIM","support":35,"invalidated_by":"structure_failure"})
    return scenarios


@dataclass
class MarketDecision:
    schema_version: str
    timestamp: float
    symbol: str
    side: str
    action: str
    score: float
    confidence: float
    strategy_score: float
    preferred_zone: Dict[str, Any]
    invalidation: Dict[str, Any]
    agents: Dict[str, Any]
    scenarios: List[dict]
    reasons: List[str]
    data_quality: str
    mode: str

    def to_dict(self) -> dict:
        return asdict(self)


class MarketIntelligenceBrain:
    """Multi-check market brain; execution-free by design."""

    def __init__(self, mode: Optional[str] = None):
        self.mode = str(mode or os.getenv("AI_MARKET_MODE", "SHADOW")).upper()
        if self.mode not in {"SHADOW", "ASSISTED", "AUTONOMOUS"}:
            self.mode = "SHADOW"

    def evaluate(self, symbol: str, side: str, df: pd.DataFrame,
                 orderbook: Optional[dict] = None,
                 legacy_analysis: Optional[dict] = None,
                 trade_intelligence: Optional[dict] = None) -> Dict[str, Any]:
        d = _side(side)
        if d not in SIDES or not _safe_df(df):
            return MarketDecision(SCHEMA_VERSION,time.time(),str(symbol),d,"WAIT",0,0,0,{}, {},{},[],["INSUFFICIENT_DATA"],"INVALID","SHADOW").to_dict()
        atr = _atr(df)
        liq = _liquidity_map(df,d,atr)
        struct = _structure_agent(df,d)
        flow = _flow_agent(df,orderbook,d)
        volume = _volume_agent(df,d)
        timing = _timing_agent(df,d,liq,atr)
        regime = _regime_agent(df)
        strategy_score = _strategy_score(legacy_analysis, trade_intelligence)
        ti = trade_intelligence or {}
        behaviour = str(ti.get("behaviour","NEUTRAL"))
        inst_score = strategy_score
        if behaviour in ("ACCUMULATION", "DISTRIBUTION"):
            inst_score = min(100.0, inst_score + 8.0)
        if liq.get("sweep"):
            inst_score = min(100.0, inst_score + 7.0)
        if flow.get("orderbook_aligned"):
            inst_score = min(100.0, inst_score + 5.0)
        institutional = {"score": round(inst_score,1), "behaviour": behaviour,
                         "sweep": bool(liq.get("sweep")),
                         "evidence": [x for x in ("LIQUIDITY_SWEEP" if liq.get("sweep") else "",
                                                     "ABSORPTION" if flow.get("absorption") else "",
                                                     "ORDERBOOK_ALIGNMENT" if flow.get("orderbook_aligned") else "") if x]}
        agents = {"liquidity":liq,"structure":struct,"flow":flow,"volume":volume,
                  "timing":timing,"regime":regime,"institutional":institutional}
        weights = {"strategy":0.24,"liquidity":0.18,"structure":0.14,"flow":0.12,
                   "volume":0.10,"timing":0.12,"institutional":0.06,"regime":0.04}
        final = (strategy_score*weights["strategy"] + liq["score"]*weights["liquidity"] +
                 struct["score"]*weights["structure"] + flow["score"]*weights["flow"] +
                 volume["score"]*weights["volume"] + timing["score"]*weights["timing"] +
                 institutional["score"]*weights["institutional"] + regime["score"]*weights["regime"])
        # Hard quality penalties: they reduce confidence/score but do not create
        # an execution path. Strategy gates remain authoritative downstream.
        if timing["late"]:
            final -= 12
        if d == "BUY" and institutional["behaviour"] == "DISTRIBUTION_RISK":
            final -= 18
        if d == "SELL" and institutional["behaviour"] == "ACCUMULATION_RISK":
            final -= 18
        final = _clamp(final)
        conflict = (d == "BUY" and struct["direction"] == "BEARISH") or (d == "SELL" and struct["direction"] == "BULLISH")
        confidence = final
        if conflict:
            confidence = max(0.0, confidence-15)
        if orderbook is None:
            confidence = max(0.0, confidence-5)
        zone = _zone_from_context(df,d,atr,liq,trade_intelligence)
        invalidation = {"price": (zone["low"]-atr*0.75 if d=="BUY" else zone["high"]+atr*0.75),
                        "reason":"liquidity_zone_failure_or_structure_invalidation"}
        scenarios = _scenario(d,liq,struct,timing,flow)
        reasons = []
        if liq.get("sweep"): reasons.append("liquidity_sweep")
        if flow.get("absorption"): reasons.append("absorption")
        if flow.get("orderbook_aligned"): reasons.append("orderbook_aligned")
        if struct.get("aligned"): reasons.append("structure_aligned")
        if timing.get("phase") == "EARLY": reasons.append("early_location")
        if institutional["behaviour"] != "NEUTRAL": reasons.append(institutional["behaviour"].lower())
        if timing["late"]: reasons.append("late_entry_risk")
        if conflict: reasons.append("structure_conflict")
        if final >= 82 and not timing["late"] and not conflict:
            action = "APPROVE_CANDIDATE"
        elif final >= 68 and not timing["late"]:
            action = "VALIDATE_CANDIDATE"
        else:
            action = "WAIT"
        dq = "OK" if orderbook is not None else "DEGRADED_NO_ORDERBOOK"
        return MarketDecision(SCHEMA_VERSION,time.time(),str(symbol),d,action,round(final,2),round(confidence,2),
                              round(strategy_score,2),zone,invalidation,agents,scenarios,reasons,dq,self.mode).to_dict()


def ai_entry_gate(ai: Optional[dict], strategy_ready: bool, *, mode: Optional[str] = None) -> Tuple[bool, str]:
    """Optional bounded gate. Default SHADOW never blocks the strategy."""
    mode = str(mode or os.getenv("AI_MARKET_MODE", "SHADOW")).upper()
    if mode == "SHADOW" or not isinstance(ai, dict):
        return True, "AI_SHADOW"
    score = _f(ai.get("score"))
    confidence = _f(ai.get("confidence"))
    if not strategy_ready:
        return False, "STRATEGY_NOT_READY"
    min_score = _f(os.getenv("AI_MIN_ENTRY_SCORE", "78"), 78)
    min_conf = _f(os.getenv("AI_MIN_CONFIDENCE", "70"), 70)
    if score < min_score:
        return False, f"AI_SCORE_BELOW_{min_score:.0f}"
    if confidence < min_conf:
        return False, f"AI_CONFIDENCE_BELOW_{min_conf:.0f}"
    if ai.get("action") == "WAIT":
        return False, "AI_WAIT"
    return True, "AI_CONFIRMED"
