# BARON × Legacy Institutional Bot — Integration Audit

## Scope

Reviewed the supplied `test cv new(20260921-202650).py` as the legacy/reference
strategy and compared its functional ideas with the BARON production tree.
The legacy file is 7,117 lines and contains its own Smart Money, liquidity,
MSS/CHoCH, ADX/DI, thesis-failure, regime, scoring, scanner and execution
logic.

## What the legacy strategy actually contributes

### 1. Smart-money pressure

The supplied strategy calculates banker/retailer pressure, accumulation,
distribution risk, institutional bias and flow alignment. This is retained as
strategy evidence, not treated as proof of a named institution entering.

### 2. Liquidity and sweep logic

It builds swing/equal-level liquidity pools, detects sweeps, classifies rejection
and uses those events in the setup narrative.

### 3. Institutional setup sequence

The legacy scanner combines liquidity narrative, smart-money pressure, momentum,
zone quality and a ranked opportunity selection before execution.

### 4. Structure and regime

MSS/CHoCH, displacement, ADX/DI, volume and market-regime classification are
used to distinguish emerging/strong trends, compression, expansion and
transition conditions.

## What BARON already had before this integration

The BARON tree already contained a substantially more modular version of these
ideas:

`DeepScanner -> Institutional Intent -> Institutional Zone Analysis -> Atom /
MSB-OB evidence -> Execution Queue -> PortfolioManager -> Execution Kernel`

It also already had Trade Intelligence with liquidity sweep, structure,
zone/retest, volume, phase, accumulation/distribution behaviour, timing and a
Trade Management Board.

Therefore the correct engineering decision was **not** to paste the legacy
monolith into BARON. That would create duplicate RF/scanner/execution/management
authorities.

## New integration

### `core/ai_market_brain.py`

Adds a deterministic multi-agent evidence layer:

- Liquidity Agent
- Structure Agent
- Flow Agent
- Volume Agent
- Timing Agent
- Regime Agent
- Institutional Evidence Agent

It returns:

- AI score
- confidence
- preferred liquidity/value zone
- invalidation
- evidence by agent
- scenario candidates
- reasons
- data-quality state
- AI mode

It does not execute orders.

### `core/ai_memory.py`

Stores the exact AI decision snapshots in a hash-chained JSONL audit stream.
Market observations are rate-limited per symbol/side. Trade entry and outcome
receipts are not silently dropped by the market-observation throttle.

### Strategy integration

`strategy/engine.py` now calls the AI brain after the preserved strategy and
Trade Intelligence calculations. The legacy score remains separate. The AI
score is not silently added to the old strategy score.

### Scanner integration

Deep Scanner carries the AI snapshot into the watchlist and Institutional Zone
Analysis. The AI preferred zone, score, confidence and action are therefore
visible before execution.

### Queue / portfolio integration

Execution candidates transport `ai_market` evidence. The PortfolioManager has
an explicit AI gate:

- `SHADOW`: no trading effect.
- `ASSISTED`: explicit score/confidence gate can veto a weak candidate.
- `AUTONOMOUS`: AI confirmation is required for candidates carrying AI evidence.

The existing strategy, portfolio risk and execution gates remain mandatory in
all modes.

### Execution / forensics integration

At entry, the exact AI snapshot, strategy score, thesis and entry reason are
persisted as `TRADE_ENTRY_AI_RECEIPT`.

At close, the system records `TRADE_OUTCOME` with:

- realized PnL
- realized PnL percentage
- MFE (peak ROE)
- MAE (minimum ROE)
- duration
- close reason
- AI snapshot
- Trade Intelligence snapshot
- thesis

## Important legacy discrepancy handled safely

The supplied standalone legacy file contains a different configuration boundary:
its `PAPER_MODE` expression is inverted relative to the intended semantic name,
and it uses `LEVERAGE = 5`. BARON's canonical kernel is not replaced by those
lines. The current BARON kernel uses its own explicit paper/live parsing and
10x leverage configuration.

This is exactly why the legacy file is treated as a **strategy reference**, not
as a drop-in runtime.

## Final authority map

```text
Legacy Strategy
    -> strategy evidence

Market Intelligence Brain
    -> liquidity / flow / structure / timing / regime evidence

AI Memory
    -> immutable-ish audit trail / receipts

Institutional Zone Analysis
    -> candidate preparation

Execution Queue
    -> trigger / zone / quality / risk validation

PortfolioManager
    -> capacity / portfolio risk

Execution Kernel
    -> BingX order authority / SL / TP / position lifecycle
```

No second execution engine, second RF engine or second trade-management brain
was introduced.
