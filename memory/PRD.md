# LumixTrade — PRD

## Original Problem Statement
User uploaded a complete, working GitHub project ZIP (LUMIXTRADE-LIVE-main). Goal: deploy as-is, then commercial-grade tuning, then a Phase-2 Entry Quality Engine to reduce avoidable stop losses without changing direction prediction or risk/SL/TP/sizing.

## Architecture
- Backend: FastAPI + engine.py, strategy_v2.py, risk_engine.py, quality_score.py, **entry_quality.py (NEW)**, marketdata.py, engine_config.py, notifications.py, backtest*.py
- Frontend: React 18 + TypeScript + craco + Tailwind
- DB: MongoDB (`lumixtrade_db`)
- Auth: JWT, admin admin@bot.com / password

## Completed Work

### 2026-01 — Phase 0: Initial deploy (as-is)
- Adopted ZIP into `/app`. `.env` files set from problem statement.
- Verified `/api/health`, admin login, bridge-poll auth.

### 2026-01 — Phase 1: Diagnostic + Commercial Tuning
- Fixed `_merge_defaults` so `symbol_overrides` from DB doc is authoritative.
- Effective `min_score`: XAU/XAG/EUR/GBP=75, USDCAD=72, others=70.
- `daily_bias_neutral_penalty`: 15 → 5.
- History warm-up handling: H4 < 60 → factor excluded from numerator AND denominator; D1 < 56 → bias = "unknown", zero penalty. Normalized to /100.
- `strategy_v2.conservative_config()` → `require_displacement=False`. BOS-retest now HTF + BOS + pullback-implicit; displacement is a confidence bonus.
- Per-scan diagnostics persisted on every outcome.

### 2026-01 — Phase 2: Entry Quality Engine (THIS SESSION)
New file: `/app/backend/entry_quality.py` (~330 LOC, fully self-contained).

**Four new modules:**
1. **PullbackCompletion** (Module 1) — outputs 0-20 score from 4 sub-factors:
   - Higher-low (buy) / lower-high (sell) formed (+8)
   - Break of pullback micro-structure (close > prev high for buy) (+6)
   - Pullback length 2–6 bars (+3)
   - Last bar direction agrees (+3)
   Hard gate: `pullback_score < min_entry_confirmation_score` → reject.
2. **SRDistance** (Module 2) — ATR-normalized distance to nearest swing-high (buy) / swing-low (sell) above/below close. Default min = 0.30 ATR. Configurable.
3. **TrendMaturity** (Module 3) — classifies `fresh / developing / extended / exhausted` from EMA crossover age, ADX trajectory, price-to-EMA distance. Rejects `extended`/`exhausted` unless momentum accelerating. Exposes `momentum_score 0-100`.
4. **CandleConfirmation** (Module 4) — last closed bar must match one of: engulfing, pin bar, strong-close momentum, or break of prior high/low. Configurable on/off (default ON), min body % configurable.

**Symbol profiles** built-in: metals (stronger momentum confirmation, body ≥ 65%) vs forex (stronger pullback, score ≥ 12/20). Tunable via admin endpoint.

**Score integration** (backward-compatible):
- New factor `entry_confirmation` weight 15 (scaled linearly from 0-20 pullback score).
- Rebalanced weights: H4 20→12, H1 20→13, all others unchanged. Total = 100 exactly.
- When `entry_quality.enabled=false`, factor is excluded from BOTH numerator AND denominator (normalization preserves the 0-100 scale).

**Admin configuration** (all toggleable / tunable via `PUT /api/admin/engine-config`):
- `entry_quality.enabled`
- `min_entry_confirmation_score` (default 10/20)
- `min_sr_distance_atr` (default 0.30)
- `min_candle_body_pct` (default 0.55)
- `fresh_trend_threshold` (default 1.0)
- `trend_exhaustion_threshold` (default 3.5 ATR)
- `momentum_threshold` (default 0.0)
- `confirmation_candle_required` (default true)
- `profiles.metals` and `profiles.forex` blocks override base values per symbol class.

**Diagnostics persisted on EVERY scan** (in `bots.last_*` AND `filter_rejections`):
- `pullback_score`, `entry_confirmation_score`, `entry_quality_passed`
- `trend_stage`, `momentum_accel`, `momentum_score`
- `sr_distance_atr`, `sr_nearest_level`
- `candle_pattern`, `candle_confirmed`
- `eq_profile`, `eq_rejection_reason`
- Plus all Phase-1 fields: `last_raw_score, last_normalized_score, last_available_weight, last_missing_history, last_effective_min_score, last_regime, last_daily_bias`.

### Explicitly NOT touched (per user instruction)
- Risk management, lot sizing, SL, TP — `risk_engine.py`, `adaptive_lot` calls untouched
- Session logic / `metals_blocked_sessions=["asia"]`
- ATR band `[0.80, 2.00]`
- Instrument cooldown (2 SL → 60 min)
- Daily bias logic (penalty value already at 5 from Phase-1)
- Quality-score architecture (only ADDED the new factor; existing 7 factors unchanged in behavior)
- Bridge endpoints, auth, frontend

## Verification (preview env)
- Lint clean on all 3 changed files + new `entry_quality.py`.
- Unit tests pass: PullbackCompletion=20/20 on synthetic clean pullback, sell on uptrend correctly rejected with `pullback_not_complete:8<12`, engulfing pattern detected.
- Integrated `score_trade` with `entry_confirmation_score=18` → entry factor contributes 14/15. With `None` (engine off) → cleanly excluded.
- Admin endpoint round-trip: deep-merge patch of `entry_quality` works; reset-defaults works.
- Live config sum verified: `score_weights` total = 100.

## Production Migration (one-time after deploy)
Existing prod `engine_config` doc (if any) won't have the new `entry_quality` block — Phase-2 will fall back to defaults automatically (backward-compatible). To tune any setting, use:
```bash
curl -X PUT https://lumixtrade.live/api/admin/engine-config \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"entry_quality":{"min_sr_distance_atr":0.35,"min_entry_confirmation_score":11}}'
```

## Backlog
- P1: Expose new diagnostic fields in the admin UI (currently visible via Mongo / `/api/admin/filter-stats`).
- P1: Side-by-side trade comparison (pre/post Phase-2) once 1 week of live data exists.
- P2: Auto-tune `min_entry_confirmation_score` per symbol from rolling win-rate.
- P3: Add a "soft" mode where Module-3 (trend maturity) reduces position size instead of rejecting (mirrors the existing noisy-hour 0.5× sizing pattern).
