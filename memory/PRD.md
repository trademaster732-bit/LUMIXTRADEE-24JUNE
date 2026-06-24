# LumixTrade — PRD

## Original Problem Statement
User uploaded a complete, working GitHub project ZIP (LUMIXTRADE-LIVE-main). Task evolved over the session into a commercial-grade re-tuning of the trading engine after diagnostics showed it had become over-restrictive (zero trades / day instead of the 20–25 target).

## Architecture
- **Backend**: FastAPI (`/app/backend/server.py`) + engine.py, strategy_v2.py, risk_engine.py, quality_score.py, marketdata.py, notifications.py, backtest*.py, engine_config.py, data_validation.py. Bridge v1.9.1. APScheduler scans every 3 min.
- **Frontend**: React 18 + TypeScript + craco + Tailwind
- **DB**: MongoDB (`lumixtrade_db`) at mongodb://localhost:27017
- **Auth**: JWT, seeded admin admin@bot.com / password

## Environment Variables (from problem statement, applied verbatim)
Backend `.env`: MONGO_URL, DB_NAME=lumixtrade_db, CORS_ORIGINS, JWT_SECRET, ADMIN_EMAIL, ADMIN_PASSWORD, AURUM_API_URL, STRATEGY_VERSION=v2
Frontend `.env`: REACT_APP_BACKEND_URL=https://lumix-bridge.preview.emergentagent.com

## Completed Work

### 2026-01 — Initial deploy (as-is)
- Adopted ZIP into `/app` without code modifications
- Created `.env` files with exact values from problem statement
- Verified `/api/health`, admin login, `POST /api/bridge-poll` 401 without key

### 2026-01 — Diagnostic round (no code changes)
- Identified `_merge_defaults` bug — global `min_score` changes were silently ignored for XAU/XAG/EUR/GBP/CAD because hard-coded defaults were re-injected on every read.
- Identified silent history-warmup tax — pairs with <60 H4 bars lost 20 pts and <56 D1 days lost another 15 pts, making the 85-threshold pairs mathematically unreachable during warm-up.
- Identified setup over-restriction — `require_displacement=True` was the dominant cause of `no_setup (trending_*)` rejections.

### 2026-01 — Commercial-grade re-tuning (THIS SESSION)
Files modified: `engine_config.py`, `quality_score.py`, `strategy_v2.py`, `server.py`.

1. **`_merge_defaults` bug fix** — `symbol_overrides` from the DB doc is now authoritative when the key is explicitly present. Admins can now actually *remove* an override (e.g. drop XAUUSD's old 85 floor). `score_weights` and `session_windows` keep deep-merge for partial-patch ergonomics.
2. **`save_engine_config` semantics** — `symbol_overrides` patch now does REPLACEMENT (matches the read path).
3. **New default thresholds**:
   - Global `min_score = 70` (was 80)
   - `XAUUSD=75, XAGUSD=75, EURUSD=75, GBPUSD=75, USDCAD=72`
   - `USDJPY, AUDUSD, NZDUSD` fall through to global = 70 ✓
4. **`daily_bias_neutral_penalty: 15 → 5`**
5. **History warm-up handling** (`quality_score.py`):
   - H4 < 60 bars → `h4_trend` excluded from BOTH numerator AND denominator; `missing_history["h4"]=true`.
   - D1 < 56 days → `daily_bias_value="unknown"`, zero penalty, `missing_history["d1"]=true`.
   - Score is normalized: `total = round(raw * 100 / available_weight)` so the 0-100 scale (and configured `min_score`) remain meaningful during warm-up.
6. **`strategy_v2.conservative_config()`** — `require_displacement=False`. BOS-retest still hard-requires HTF + BOS + pullback-to-broken-level (3 confirmations by construction); displacement remains a confidence bonus. Net policy: HTF + at-least-2-of {pullback, BOS, displacement}.
7. **Per-scan diagnostics** persisted on EVERY outcome (approved or rejected):
   - On `bots`: `last_raw_score, last_normalized_score, last_available_weight, last_missing_history, last_effective_min_score, last_regime, last_daily_bias, last_quality_score`.
   - On `filter_rejections` (quality_score gate): all of the above + `score{...full ScoreBreakdown}`.

### Explicitly NOT changed (per user instruction)
- Metals session filter (`metals_blocked_sessions=["asia"]`)
- ATR band `[0.80, 2.00]`
- Instrument cooldown (2 SL → 60 min pause)
- SL-cluster lockout / pair-direction cooldown / opposite-open guard
- Authentication, bridge endpoints, frontend

## Verification (preview env)
- `GET /api/admin/engine-config` returns global=70, neutral_penalty=5, ATR=[0.8,2.0], the 5 new overrides.
- PUT with empty `symbol_overrides` now actually empties (round-trip test passed).
- PUT with full commercial overrides round-trips correctly.
- Unit-test of `score_trade` shows normalization works: H4-short scan with raw 70/80 → normalized 88/100 (vs old silent 70 → blocked).
- Backend log confirms `require_displacement=False` is the live setting.

## Production Migration (action required from user)
After deploying preview → production, the existing prod `engine_config` doc (if any) will still contain the old XAU=85 / XAG=85 overrides. The fix is backward-compatible (no crash) but the stale values will persist until overwritten. Run ONCE after deploy:

```bash
HOST=https://lumix-bridge.emergent.host
TOKEN=$(curl -s -X POST $HOST/api/auth/login -H "Content-Type: application/json" \
  -d '{"email":"admin@bot.com","password":"password"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
curl -X PUT $HOST/api/admin/engine-config \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{
    "min_score": 70,
    "daily_bias_neutral_penalty": 5,
    "near_miss_lower": 65,
    "symbol_overrides": {
      "XAUUSD":{"min_score":75,"cooldown_min":60},
      "XAGUSD":{"min_score":75,"cooldown_min":60},
      "EURUSD":{"min_score":75,"cooldown_min":45},
      "GBPUSD":{"min_score":75,"cooldown_min":45},
      "USDCAD":{"min_score":72,"cooldown_min":30}
    }
  }'
```

## Backlog
- P1: Live validation under real market hours — confirm the target ~20–25 trades/day, 65–75% WR.
- P2: Expose new diagnostic fields in admin UI (currently visible via Mongo / `/api/admin/filter-stats`).
- P3: Auto-migration job to rewrite legacy engine_config docs at startup (would remove the manual PUT step above).
