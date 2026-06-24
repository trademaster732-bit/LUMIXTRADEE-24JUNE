"""Regression tests for Phase-1 Engine Optimization (2026-06-22).

Covers:
  1. engine_config: defaults, merge, per-symbol get_symbol_setting helper.
  2. quality_score: score_trade math, ADX computation, daily-bias aggregation.
  3. Server: admin endpoints exist + filter pipeline strings are present.
  4. Frontend: EngineConfig.tsx page exists with all 5 tabs.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine_config import (
    DEFAULT_CONFIG, get_symbol_setting, is_metal, current_session_name,
    _merge_defaults,
)
from quality_score import (
    score_trade, ScoreBreakdown, _adx, _vwap, _trend_from_emas,
    aggregate_daily, daily_bias,
)


# ───────── 1. engine_config ─────────
def test_default_config_has_all_phase1_keys():
    needed = [
        "score_weights", "min_score", "near_miss_lower", "adx_threshold",
        "vwap_max_distance_atr", "cooldown_consecutive_losses", "cooldown_min",
        "session_windows", "metals_blocked_sessions",
        "daily_bias_enabled", "daily_bias_neutral_mode", "daily_bias_neutral_penalty",
        "atr_ratio_min", "atr_ratio_max", "symbol_overrides",
    ]
    for k in needed:
        assert k in DEFAULT_CONFIG, f"DEFAULT_CONFIG missing key: {k}"


def test_score_weights_sum_to_100():
    total = sum(DEFAULT_CONFIG["score_weights"].values())
    assert total == 100, f"weights must sum to 100, got {total}"


def test_per_symbol_overrides_resolve_correctly():
    cfg = _merge_defaults({})
    # XAU defaults to min_score=85
    assert get_symbol_setting(cfg, "XAUUSD", "min_score") == 85
    # Unknown pair falls back to global min_score (80)
    assert get_symbol_setting(cfg, "EURJPY", "min_score") == 80
    # USDCAD is custom (75)
    assert get_symbol_setting(cfg, "USDCAD", "min_score") == 75
    # Cooldown per symbol
    assert get_symbol_setting(cfg, "XAGUSD", "cooldown_min") == 60
    assert get_symbol_setting(cfg, "EURUSD", "cooldown_min") == 45


def test_is_metal_classification():
    assert is_metal("XAUUSD") is True
    assert is_metal("xauusdm") is True
    assert is_metal("XAGUSD") is True
    assert is_metal("EURUSD") is False
    assert is_metal("GBPJPY") is False


def test_session_name_resolves_overlap():
    cfg = _merge_defaults({})
    assert current_session_name(cfg, 3) == "asia"
    assert current_session_name(cfg, 9) == "london"
    assert current_session_name(cfg, 14) == "overlap"   # 13-15 = both london + ny
    assert current_session_name(cfg, 18) == "new_york"
    assert current_session_name(cfg, 22) == "off"


def test_merge_defaults_fills_missing_keys():
    partial = {"min_score": 90}                          # only one key in DB
    merged = _merge_defaults(partial)
    assert merged["min_score"] == 90                     # respect override
    assert "score_weights" in merged                     # filled from defaults
    assert merged["score_weights"]["h4_trend"] == 20     # default weight present
    assert merged["cooldown_min"] == 60                  # default carried forward


# ───────── 2. quality_score ─────────
def _candles(n=120, start=100.0, step=0.05, down=False):
    out, px = [], start
    for i in range(n):
        o = px
        c = px - step if down else px + step
        h = max(o, c) + step * 0.3
        l = min(o, c) - step * 0.3
        out.append({"t": 1700000000 + i * 300, "o": o, "h": h, "l": l, "c": c, "v": 1000})
        px = c
    return out


def test_adx_returns_positive_during_trend():
    candles = _candles(150)                              # strong steady uptrend
    adx_arr = _adx(candles, 14)
    assert max(adx_arr[-10:]) > 25, "ADX should clear 25 in a steady trend"


def test_trend_detector_emas():
    assert _trend_from_emas(_candles(100, down=False)) == "up"
    assert _trend_from_emas(_candles(100, down=True))  == "down"


def test_aggregate_daily_buckets_correctly():
    """24 H1 bars (1 day) → 1 D1 bar; OHLC math correct."""
    h1 = []
    base_t = 1_700_000_000_000   # ms — arbitrary epoch
    base_t = (base_t // 86_400_000) * 86_400_000   # snap to UTC day boundary
    for i in range(24):
        h1.append({"t": base_t + i * 3_600_000,
                   "o": 100 + i * 0.1, "h": 100.5 + i * 0.1,
                   "l": 99.8 + i * 0.1, "c": 100.2 + i * 0.1, "v": 1.0})
    d1 = aggregate_daily(h1)
    assert len(d1) == 1
    assert d1[0]["o"] == 100.0
    assert d1[0]["c"] == 100.2 + 23 * 0.1
    assert d1[0]["h"] == 0.5 + 100 + 23 * 0.1
    assert d1[0]["l"] == 99.8


def test_score_trade_perfect_setup_approves():
    """Strong trending candles, signal long, H1 + H4 also up → high score."""
    candles = _candles(150)
    h1 = _candles(200)
    h4 = _candles(200)
    cfg = _merge_defaults({})
    b = score_trade(
        side="buy", symbol="XAUUSD",
        candles=candles, candles_h1=h1, candles_h4=h4, cfg=cfg,
        signal_sl=candles[-1]["c"] - 1.0, signal_entry=candles[-1]["c"],
        spread_at_fill=0.05,                              # tiny spread
        sr_action="ok", daily_bias_value="bullish",
    )
    assert b.h4_trend == 20 and b.h1_trend == 20
    assert b.sr == 15
    assert b.adx == 15                                    # trending
    assert b.total >= 80
    assert b.approved is True


def test_score_trade_countertrend_rejects():
    """Signal SELL into an uptrend → h1/h4 zero out, total below 80."""
    candles = _candles(150)
    h1 = _candles(200)
    h4 = _candles(200)
    cfg = _merge_defaults({})
    b = score_trade(
        side="sell", symbol="XAUUSD",
        candles=candles, candles_h1=h1, candles_h4=h4, cfg=cfg,
        signal_sl=candles[-1]["c"] + 1.0, signal_entry=candles[-1]["c"],
        spread_at_fill=0.05, sr_action="ok", daily_bias_value="bullish",
    )
    assert b.h4_trend == 0 and b.h1_trend == 0
    assert b.total < 80
    assert b.approved is False


def test_score_trade_neutral_bias_applies_penalty():
    candles = _candles(150)
    cfg = _merge_defaults({})
    b = score_trade(
        side="buy", symbol="XAUUSD",
        candles=candles, candles_h1=_candles(200), candles_h4=_candles(200),
        cfg=cfg, signal_sl=candles[-1]["c"] - 1.0, signal_entry=candles[-1]["c"],
        spread_at_fill=0.05, sr_action="ok", daily_bias_value="neutral",
    )
    assert b.daily_bias_penalty == 15
    # Total should be raw_sum - 15
    raw = b.h4_trend + b.h1_trend + b.adx + b.vwap + b.sr + b.atr_ratio + b.spread
    assert b.total == max(0, raw - 15)


# ───────── 3. server.py — filter pipeline integration ─────────
def test_server_has_phase1_filter_pipeline_blocks():
    src = (Path(__file__).resolve().parents[1] / "server.py").read_text()
    # F3 — Session Filter for metals
    assert "metals_session_blocked" in src
    # F5 — ATR Ratio Filter
    assert 'atr_ratio_{_state}' in src or "atr_ratio_dead" in src
    # F4 — Daily Bias countertrend block
    assert "daily_bias_countertrend" in src
    # F2 — Instrument Cooldown
    assert "instrument_cooldown" in src
    # F1 — Quality Score
    assert "QUALITY-SCORE" in src
    # Cooldown trigger on SL_HIT
    assert "consecutive losses" in src
    # Filter rejections collection
    assert "db.filter_rejections.insert_one" in src


def test_server_has_admin_engine_endpoints():
    src = (Path(__file__).resolve().parents[1] / "server.py").read_text()
    assert '"/admin/engine-config"' in src
    assert '"/admin/cooldowns"' in src
    assert '"/admin/filter-stats"' in src
    assert '"/admin/symbol-metrics"' in src
    assert '"/admin/engine-config/reset-defaults"' in src


def test_server_creates_phase1_indexes():
    src = (Path(__file__).resolve().parents[1] / "server.py").read_text()
    assert "db.cooldowns.create_index" in src
    assert "db.filter_rejections.create_index" in src
    assert "db.engine_config.create_index" in src


# ───────── 4. frontend ─────────
def test_engine_config_page_exists_with_5_tabs():
    page = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "pages" / "app" / "EngineConfig.tsx")
    assert page.exists(), "EngineConfig.tsx missing"
    src = page.read_text()
    for tab in ["SCORE", "COOLDOWN", "FILTERS", "PER-SYMBOL", "TELEMETRY"]:
        assert tab in src, f"Tab {tab} missing from EngineConfig.tsx"
    # API wiring
    assert "/admin/engine-config" in src
    assert "/admin/cooldowns" in src
    assert "/admin/filter-stats" in src
    assert "/admin/symbol-metrics" in src
    # data-testid present
    assert "data-testid=\"engine-config-page\"" in src
    assert "data-testid=\"save-scoring\"" in src


def test_app_tsx_has_engine_config_route():
    app = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "App.tsx")
    src = app.read_text()
    assert "/app/admin/engine-config" in src
    assert "EngineConfig" in src
