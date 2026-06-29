# LumixTrade — PRD

## Phases completed
- Phase 0: Deploy as-is from user ZIP
- Phase 1: Diagnostic + commercial tuning (merge-bug fix, neutral penalty 15→5, warm-up handling)
- Phase 2: Entry Quality Engine (4 modules + diagnostics + symbol profiles)
- Phase 3 / Module 1: Market Regime Detection (6 regimes + symbol preferences)
- **Phase 3 / Module 2: Multi-Timeframe Alignment** ← this session

## Module 2 — Multi-Timeframe Alignment (NEW, 2026-01)

**New file:** `/app/backend/mtf_alignment.py` (~230 LOC, fully self-contained).

**Four timeframes evaluated in parallel:** D1, H4, H1, M15. Each TF returns:
- `direction` — up / down / flat / unknown (EMA21 vs EMA55, 5-bp band, warm-up aware)
- `adx` — Wilder ADX-14 latest value
- `adx_slope` — 5-bar slope (rising = momentum present)
- `ema_angle_bps` — signed basis-point slope of EMA21 over last 5 bars
- `decisive` — direction ≠ flat AND ADX ≥ min_strength
- `agrees` — direction matches signal side AND strength ≥ min AND angle ≥ min

**Per-TF configurable** (`engine_config.mtf_alignment.timeframes.<TF>`):
- `enabled`, `weight`, `min_strength_adx`, `min_ema_angle_bps`

**Default weights:** D1=30, H4=30, H1=25, M15=15 (sums to 100; admin can re-balance freely).

**Three hard gates:**
1. **Weighted alignment %** — `agreed_weight / enabled_weight × 100` must clear `min_alignment_pct` (default 60). Below → reject with `alignment_pct:X<Y`.
2. **HTF/LTF strong disagreement** — when D1 AND M15 are both decisive but opposite (e.g. D1=up, M15=down) → reject. Toggleable via `htf_ltf_disagreement_reject`.
3. **Momentum agreement (optional)** — `require_momentum_agreement=true` requires ADX rising on ≥ `min_momentum_agreement_count` timeframes. Default OFF.

**Wired into `server.py` between `market_regime` and `entry_quality`** — gates fire BEFORE the score gate. Order is now:

`signal → SR-cluster → market_regime → mtf_alignment → entry_quality → quality_score`

**D1 candles** reconstructed via existing `aggregate_daily(H1)` — no new data sources, no bridge changes.
**M15 candles** fetched fresh per scan (200 bars).
**H1/H4** reuse existing fetches.

**Diagnostics persisted on EVERY scan** (in `bots.last_*` + `filter_rejections`):
`mtf_alignment_pct, mtf_aligned_count, mtf_enabled_count, mtf_momentum_agree_count, mtf_htf_dir, mtf_ltf_dir, mtf_htf_ltf_disagree, mtf_passed, mtf_rejection_reason, mtf_evaluations (full per-TF breakdown)`

### Explicitly NOT touched
- Risk management, lot sizing, SL, TP
- Session logic / `metals_blocked_sessions=["asia"]`
- ATR band `[0.80, 2.00]`
- Instrument cooldown (2 SL → 60 min)
- Daily bias logic (penalty 5)
- Quality-score architecture (score_weights sum still = 100)
- Bridge endpoints, auth, frontend
- Module-1 market_regime and Phase-2 entry_quality (untouched)

## Verification (preview env)
- Lint clean across all changed files.
- Five unit tests:
  - Full uptrend → 3/4 TFs agree (D1 warm-up), 70% alignment → PASS.
  - HTF/LTF disagreement on partial uptrend → rejected via alignment_pct gate.
  - Buy signal vs full-down market → 0% alignment → reject.
  - Empty candles → 0% alignment → reject (no crash).
  - `enabled: false` → pass-through.
- Recursive admin patch: PUT `{timeframes:{M15:{min_strength_adx:30}}, min_alignment_pct:75}` — M15 strength + global threshold updated; ALL untouched fields (M15.weight, D1.*, H4.*, H1.*) preserved.
- Reset-defaults works.
- Regression confirmed: ATR `[0.8, 2.0]`, cooldown 60, metals session, daily-bias penalty 5, score_weights sum 100, market_regime 6 regimes + 8 prefs, entry_quality enabled.

## Production deploy notes
Old engine_config docs lack `mtf_alignment` → defaults auto-apply. To tune, single PUT:
```bash
curl -X PUT https://lumixtrade.live/api/admin/engine-config \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"mtf_alignment":{"timeframes":{"D1":{"weight":40}},"min_alignment_pct":65}}'
```

## Backlog
- Module 3+: pending user spec
- P1: Admin UI surfaces for the new MTF diagnostics + per-scan TF breakdown
- P2: Per-regime + per-symbol auto-calibration from rolling win-rate using the rich diagnostic fields now persisted on every scan
