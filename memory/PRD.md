# LumixTrade — PRD

## Phases completed
- Phase 0: Deploy as-is from user ZIP
- Phase 1: Diagnostic + commercial tuning (merge-bug fix, neutral penalty 15→5, warm-up handling)
- Phase 2: Entry Quality Engine (4 modules + diagnostics + symbol profiles)
- **Phase 3 / Module 1: Market Regime Detection** ← this session

## Module 1 — Market Regime Detection (NEW, 2026-01)

**New file:** `/app/backend/market_regime.py` (~250 LOC, fully self-contained).

**Six regimes** (priority-ordered classifier):
1. `breakout` — 20-bar range break + ATR ratio > 1.20
2. `high_volatility` — ATR ratio > 1.50 (no clean range break)
3. `low_volatility` — ATR ratio < 0.70
4. `strong_trend` — ADX ≥ 30 + EMA-sep ≥ 1.5×ATR + healthy slope
5. `weak_trend` — ADX 18–30 + EMA-sep ≥ 0.5×ATR
6. `range` — default fallback

**Per-regime config** (`engine_config.market_regime.regimes.<name>`):
- `enabled` (default values: only `low_volatility` off)
- `min_score` — OVERRIDES the per-pair quality-score threshold when this regime is active
- `entry_aggressiveness` — `high`/`medium`/`low` → 0.7×/1.0×/1.3× multiplier on Phase-2 `min_entry_confirmation_score`
- `preferred_confirmation` — whitelist of allowed Module-4 candle pattern families (`engulfing`, `pin`, `momentum`, `break`, or `any`)

**Symbol preferences** (`engine_config.market_regime.symbol_preferences.<symbol>`):
Whitelist of regimes admissible for that symbol. Empty list / missing key = all enabled regimes allowed.
Defaults:
- Forex (EUR/GBP/USDJPY/USDCAD): `strong_trend, weak_trend, breakout`
- AUD/NZD: `strong_trend, weak_trend`
- XAUUSD: `breakout, strong_trend, high_volatility`
- XAGUSD: `breakout, strong_trend, range`

**Wired into `server.py` between strategy signal generation and entry_quality.** Order:
`signal → SR-cluster → market_regime gate → entry_quality (regime-tuned) → quality_score (regime-min_score)`

**Diagnostics persisted on EVERY scan** (added to `bots.last_*` and `filter_rejections`):
`market_regime, regime_confidence, regime_passed, regime_enabled, regime_min_score, regime_aggressiveness, regime_preferred_confirmation, regime_symbol_preferred, regime_adx, regime_adx_slope, regime_atr_ratio, regime_ema_sep_atr, regime_rejection_reason`

**Recursive config merge** (`_deep_update`) so admins can patch a single regime or single symbol's preferences without resending the entire block. Applies to `entry_quality` and `market_regime`.

**Score-threshold priority chain** (most specific wins, when configured):
`regime.min_score → symbol_overrides[pair].min_score → global min_score`

**Candle-pattern filter**: if the regime declares `preferred_confirmation` and Module-4 detected a pattern outside that list → reject with `wrong_confirmation_pattern:<pattern>_not_in_<list>`.

### Explicitly NOT touched
- Risk management, lot sizing, SL, TP
- Session logic / `metals_blocked_sessions=["asia"]`
- ATR band `[0.80, 2.00]`
- Instrument cooldown (2 SL → 60 min)
- Daily bias logic (penalty 5)
- Quality-score architecture (only adds the existing `entry_confirmation` factor; weights still sum to 100)
- Bridge endpoints, auth, frontend

## Verification (preview env)
- Lint clean on `market_regime.py`, `engine_config.py`, `server.py`.
- Classifier unit-tested on synthetic strong_trend / range / breakout / low_vol / high_vol markets — all categorized as expected (modulo idealized-test-data caveats).
- Orchestrator: EURUSD on a range → rejected (`regime_not_preferred`); XAGUSD on same range → passes with min_score=80.
- `regime_allows_candle_pattern`: family mapping + `"any"` wildcard work.
- **Recursive admin patch**: PUT `{regimes:{range:{enabled:false}}, symbol_preferences:{XAUUSD:[...]}}` modifies only the targeted entries; all 5 other regimes + 7 other symbol preferences preserved.
- Reset-defaults works.
- All Phase-1/Phase-2 settings + untouched gates verified intact via `/api/admin/engine-config`.

## Production deploy notes
Old engine_config docs lack `market_regime` → defaults auto-apply (backward compatible). To tune any value:
```bash
curl -X PUT https://lumixtrade.live/api/admin/engine-config \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"market_regime":{"regimes":{"breakout":{"min_score":70}}}}'
```

## Backlog (remaining adaptive-engine modules)
- P1: Module 2 — (user to spec next)
- P1: Admin UI surfaces for the new regime diagnostics
- P2: Side-by-side trade comparison (pre/post Module 1) once ~1 week of live data exists
- P2: Per-regime + per-symbol auto-calibration from rolling win-rate
