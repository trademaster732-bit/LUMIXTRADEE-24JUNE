# LumixTrade — PRD

## Phases completed
- Phase 0: Deploy as-is from user ZIP
- Phase 1: Diagnostic + commercial tuning
- Phase 2: Entry Quality Engine
- Phase 3 / Module 1: Market Regime Detection (6 regimes + per-symbol whitelists)
- Phase 3 / Module 2: Multi-Timeframe Alignment (D1/H4/H1/M15)
- **Phase 3 / Module 3: Adaptive Take Profit Engine** ← this session

## Module 3 — Adaptive Take Profit Engine (NEW, 2026-01)

**New file:** `/app/backend/adaptive_tp.py` (~330 LOC, self-contained). **Stop loss is NEVER modified.**

### 7 strategies, priority-ordered:
1. **`static_rr`** — TP = entry ± `static_rr` × SL distance.
2. **`atr`** — TP = entry ± `atr_multiplier` × ATR.
3. **`swing`** — TP = nearest swing-high (buy) / swing-low (sell) from `_swings()` pivots, `swing_lookback` bars.
4. **`sr`** — TP = nearest clustered S/R level (≥2 pivots within `sr_cluster_atr` × ATR), `sr_lookback` bars.
5. **`structure`** — 1:1 measured-move projection from the last impulse leg's swing-low (buy) / swing-high (sell).
6. **`partial_tp`** — generates `tp_levels[]` (rr + close_pct + tp price) — persisted as **auxiliary** on signal doc for the bridge to consume; does NOT replace primary TP.
7. **`trailing`** — generates `trailing{}` payload (activate_at_rr, trail_distance_atr) — persisted as **auxiliary** on signal doc.

### Orchestrator behavior:
- Walks `priority` list — FIRST strategy that returns a valid TP wins.
- All 5 level-strategy candidates are recorded in diagnostics (`tp_candidates`) for forensics.
- Sanity bounds: TP is widened to `min_rr_floor` if too tight, clipped to `max_rr_cap` if too far.
- Fallback: if every strategy fails, falls back to `static_rr@min_rr_floor`.

### Per-symbol overrides (`adaptive_tp.symbol_overrides.<symbol>`):
Default shipped:
- **XAUUSD / XAGUSD** → `priority=[structure, swing, atr]`, `atr_multiplier=3.0`, `min_rr_floor=1.8`
- **EURUSD / GBPUSD** → `priority=[sr, swing, structure, atr]`

### Default config: `enabled: false`
Backward-compatible. Existing `sig.tp` (with strategy_v2's own min-RR enforcement) is used until admin opts in.

### Wired into `server.py` immediately BEFORE `db.signals.insert_one`:
- `sig.tp` is replaced with `_atp.primary_tp` when enabled.
- New signal-doc fields persisted: `adaptive_tp{diag}`, `tp_levels[]`, `trailing{}`.
- `notify_svc.notify(..., tp=sig.tp)` automatically gets the new TP (sig.tp is mutated).

### Diagnostics persisted on EVERY approved signal:
`adaptive_tp_enabled, tp_strategy (picked), tp_candidates (all 5), tp_rr_realized, tp_rr_floor_applied, tp_rr_cap_applied, tp_symbol_override_used, tp_reasons (full reasoning chain)`

### Explicitly NOT touched
- **Stop loss** (every gate, every code path)
- Risk management, lot sizing, sessions, ATR band, cooldown, daily-bias, quality-score weights
- Bridge endpoints (the bridge just sees new optional fields on signal docs — `tp_levels` and `trailing` — and can ignore them safely)
- Auth, frontend, all Phase-1/Phase-2/Module-1/Module-2 logic

## Verification (preview env)
- Lint clean.
- 8 unit tests covering: individual strategies, orchestrator default, engine-disabled passthrough, XAUUSD symbol override (min_rr_floor 1.5→1.8), partial-TP plan generation, trailing payload, SELL-on-uptrend fallback, min-RR floor enforcement.
- Recursive admin patch: enabled `adaptive_tp.enabled=true` + tightened ONLY `XAUUSD.min_rr_floor` — every untouched field preserved (XAUUSD.priority, XAUUSD.atr_multiplier, XAGUSD entirely, partial_tp.levels).
- Reset-defaults works.
- Full regression of all prior subsystems passed.

## Production deploy notes
Old engine_config docs lack `adaptive_tp` → defaults auto-apply with `enabled=false`. To opt in:
```bash
curl -X PUT https://lumixtrade.live/api/admin/engine-config \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"adaptive_tp":{"enabled":true}}'
```

To enable partial closes + trailing on top:
```bash
curl -X PUT https://lumixtrade.live/api/admin/engine-config \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"adaptive_tp":{"enabled":true,"partial_tp":{"enabled":true},"trailing":{"enabled":true}}}'
```

## Bridge compatibility note
- `tp_levels` and `trailing` are optional fields on the signal doc. Bridges built before Module 3 will simply ignore them and execute the primary `tp` (which already incorporates the adaptive choice). **No bridge update is required** to start benefiting from Module 3; partial-closes and trailing only activate once the bridge is updated to read those fields.

## Backlog
- Module 4+: pending user spec
- P1: Admin UI surfaces for adaptive_tp config + per-signal TP audit trail
- P2: Walk-forward backtest of `priority` orderings per symbol from the persisted candidate diagnostics — admins could auto-select the best priority per pair.
