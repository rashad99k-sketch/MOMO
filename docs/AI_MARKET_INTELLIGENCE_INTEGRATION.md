# BARON AI Market Intelligence — Engineering Integration

## Purpose

BARON now contains an execution-free Market Intelligence Brain that sits beside
(the not instead of) the existing RF/institutional strategy.

The design uses three reference patterns:

- **Web-Check pattern:** independent evidence checks are collected before a
  conclusion is displayed.
- **MiroFish pattern:** specialist reasoning agents, scenario analysis and
  persistent memory are represented as structured data.
- **Loop Engineering pattern:** decisions are bounded, verifiable and recorded
  as receipts.

No code from those projects is required by BARON at runtime.

## Authority boundaries

1. Legacy RF/institutional strategy remains the trading-strategy baseline.
2. `core/ai_market_brain.py` only reads market evidence and returns a structured
   proposal. It cannot place, modify or close an order.
3. `PortfolioManager` remains the portfolio/risk entry boundary.
4. `core.engine.execute_entry` remains the preserved exchange execution
   authority.
5. AI intervention is opt-in through `AI_MARKET_MODE`.

### Modes

- `SHADOW` (default): AI observes, scores, proposes zones and records evidence.
  It cannot block or create trades.
- `ASSISTED`: when an AI snapshot is attached to a technical candidate, the AI
  score/confidence gate may block a weak candidate. Existing strategy, risk and
  execution gates still apply.
- `AUTONOMOUS`: AI confirmation is required for candidates that carry AI
  evidence, but it still cannot bypass the legacy strategy, portfolio risk or
  execution authority.

## Market agents

The brain emits independent evidence from:

- Liquidity: swing/equal-level pools, sweep and distance.
- Structure: BOS/market-direction alignment.
- Flow: candle direction, order-book imbalance and absorption.
- Volume: expansion/absorption/exhaustion context.
- Timing: distance from the preferred zone and late-move detection.
- Regime: trend/expansion/compression/range context.
- Institutional: strategy/trade-intelligence evidence plus liquidity/flow
  confirmation.

The final score is a weighted evidence score, not a claim that a named
institution entered the market. Institutional participation is represented as
inferred footprint/evidence.

## Zone recommendation

The brain prefers the existing Trade Intelligence zone when available. If no
zone exists, it derives a bounded zone around the nearest directional liquidity
pool. A fallback ATR value zone is explicitly labelled as a fallback.

Each decision contains:

- `preferred_zone`
- `invalidation`
- `score`
- `confidence`
- `action`
- `reasons`
- per-agent evidence
- scenario candidates
- data-quality state

## Persistence

`core/ai_memory.py` writes a hash-chained JSONL audit stream to:

`logs/ai_market_memory.jsonl`

Market decisions are rate-limited per symbol/side. Trade-entry AI receipts are
written at the exact entry event and include the AI snapshot, trade-intelligence
snapshot, thesis and entry reason.

The chain can be verified with `core.ai_memory.verify()`.

## Dashboard

The digital command center exposes:

- `/ai` — ranked AI market candidates and active-trade AI state.
- `/data` — includes `ai_market` for the existing dashboard.

The dashboard panel displays AI mode, score, confidence, preferred zone,
invalidation and evidence reasons. It does not expose a second order-control
path.

## Validation strategy

The AI layer is intentionally deterministic and dependency-light so it can be
unit tested with synthetic OHLCV/order-book fixtures. Live market accuracy is
not claimed by static tests; paper runtime and then controlled live validation
remain mandatory before enabling an intervention mode.
