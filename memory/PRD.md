# LumixTrade — PRD

## Phases completed
- Phase 0: Deploy as-is
- Phase 1: Diagnostic + commercial tuning
- Phase 2: Entry Quality Engine
- Phase 3 / Module 1: Market Regime Detection
- Phase 3 / Module 2: Multi-Timeframe Alignment
- Phase 3 / Module 3: Adaptive Take Profit Engine
- **Phase 3 / Admin Control Center (UI parity)** ← this session

## Admin Control Center (NEW, 2026-01)

**File touched:** `/app/frontend/src/pages/app/EngineConfig.tsx`. **New backend endpoints:** `/api/admin/recent-rejections` and `/api/admin/recent-signals`.

### Tabs in the Engine Configuration page (now 9 total):
| Tab | Module / Phase | Status |
|---|---|---|
| SCORE | Phase-1 weights + thresholds (now 8 weights incl. `entry_confirmation`) | extended |
| COOLDOWN | Phase-1 cooldown + active sessions | unchanged |
| FILTERS | Phase-1 sessions + daily-bias + ATR ratio | unchanged |
| PER-SYMBOL | Phase-1 per-symbol overrides | unchanged |
| **ENTRY-Q** | **Phase-2 — Entry Quality Engine** | NEW |
| **REGIME** | **Module 1 — Market Regime Detection** | NEW |
| **MTF** | **Module 2 — Multi-Timeframe Alignment** | NEW |
| **ADAPTIVE-TP** | **Module 3 — Adaptive Take Profit Engine** | NEW |
| TELEMETRY | Cross-module rejection stats | unchanged |

### Each new tab includes:
- **Master enable/disable toggle**
- **Hero stat cards** (engine status, key thresholds, counts)
- **Configuration controls** — toggles, number inputs, selects, list inputs
- **Per-symbol overrides editor** (Adaptive TP, Market Regime symbol_preferences)
- **Save button** — sends a recursive partial patch via existing `PUT /api/admin/engine-config`
- **Live diagnostic widget** — counts + per-pair breakdown + last-10 example rejections (via new `/api/admin/recent-rejections?filter=<module>`)
- **Adaptive-TP tab** additionally shows per-strategy usage from last 30 signals (`/api/admin/recent-signals`)

### Shared widgets:
- `<LiveRejections filter="..." />` — pulls `filter-stats` + `recent-rejections` for any module
- `<SymbolOverridesEditor>` — generic per-symbol matrix editor (used by Adaptive TP; designed to be reusable for Modules 4-7)

### Verified live on preview:
- Lint clean (only pre-existing useEffect warnings, no new ones)
- Webpack compiled successfully
- 5 tabs screenshotted end-to-end:
  - SCORE shows new `ENTRY_CONFIRMATION=15` weight; sum=100 balanced
  - ENTRY-Q shows master toggle, all 4 sub-modules, metals + forex profiles
  - REGIME shows 6 regimes (5/6 on, low_volatility default OFF), all 9 symbols in preference editor
  - MTF shows D1/H4/H1/M15 rows + 3 global gates + live monitor
  - ADAPTIVE-TP shows priority editor, 8 params, partial-TP table, trailing controls, symbol overrides matrix, live strategy-usage widget
- Login via admin@bot.com/password works through the rendered shell

## Pattern established for future modules (4–7)
Every new module from this point forward will ship with:
1. Self-contained backend file (`<module>.py`)
2. Recursive-mergeable config block in `engine_config.py`
3. Persisted diagnostics on every scan
4. **Dedicated Admin tab** in `EngineConfig.tsx` with: master toggle → config controls → per-symbol overrides (if applicable) → save button → live monitor section (using `<LiveRejections filter="<module>" />`).

## Production deploy notes
- Frontend bundle now includes 4 new tabs (no other route changes).
- 2 new backend endpoints (`/api/admin/recent-rejections`, `/api/admin/recent-signals`) — both admin-only, read-only, safe to deploy.
- Backward compatibility: all new tabs work even when their config block is missing from the DB (defaults from `DEFAULT_CONFIG` auto-apply).

## Backlog
- Module 4+: pending user spec — UI will be added in the same shape automatically
- P2: A unified "Pipeline" overview tab showing the full funnel (signal → regime → mtf → entry_quality → score → adaptive_tp) for any single bot's last scan
