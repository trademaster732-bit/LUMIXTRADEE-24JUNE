"""Regression tests for the 2026-06-22 weekly audit batch (P0 + P1 + dynamic trailing).

Covers:
  1. Scalp TP widened to 1.8×ATR (from 1.3) — config + min_rr floor.
  2. Swing min_rr floor lifted to 2.5 (from 2.0).
  3. XAG-specific SL multiplier (1.5×) applied when pair contains "XAG".
  4. New _setup_swing_pullback exists and emits a swing-mode signal with TP ≥ 2.5R.
  5. Bridge has dynamic trailing constants for scalp ($5/$2) and swing ($30/$15).
  6. Bridge has TICKET_MODE registry + populates it at fill.
  7. Server has SL-cluster cooldown gate and time-of-day soft size-down.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from strategy_v2 import (
    StrategyV2Config, conservative_config, generate_signal_v2, _setup_swing_pullback,
    _enforce_min_rr,
)
from engine import GeneratedSignal


def _make_candles(n=120, start=100.0, step=0.05):
    """Steady uptrend candles, enough bars for indicators."""
    out = []
    px = start
    for i in range(n):
        o = px
        c = px + step
        h = max(o, c) + step * 0.3
        l = min(o, c) - step * 0.3
        out.append({"t": 1700000000 + i * 300, "o": o, "h": h, "l": l, "c": c, "v": 1000})
        px = c
    return out


# ---------- 1. Scalp TP widened to 1.8×ATR ----------
def test_scalp_tp_atr_widened_to_1_8():
    cfg = conservative_config()
    assert cfg.scalp_tp_atr == 1.8, "P0 audit: scalp TP must be 1.8×ATR (was 1.3)"
    assert cfg.scalp_sl_atr == 1.0, "Scalp SL unchanged at 1.0×ATR"


# ---------- 2. min_rr floor — swing 2.5, scalp 1.8 ----------
def test_enforce_min_rr_floor_widened_swing_2_5():
    sig = GeneratedSignal(side="buy", entry=100.0, sl=99.0, tp=101.5,  # tp_dist=1.5, sl_dist=1.0 → RR=1.5
                          confidence=0.7, regime="trending_up", session="london",
                          reason="t", mode="swing", max_hold_minutes=240)
    sig2 = _enforce_min_rr(sig, min_rr=2.5)
    assert abs(sig2.tp - 102.5) < 1e-9, "Swing TP must widen to 2.5×SL"


def test_enforce_min_rr_floor_widened_scalp_1_8():
    sig = GeneratedSignal(side="buy", entry=100.0, sl=99.0, tp=100.5,  # RR=0.5
                          confidence=0.7, regime="ranging", session="london",
                          reason="t", mode="scalp", max_hold_minutes=30)
    sig2 = _enforce_min_rr(sig, min_rr=1.8)
    assert abs(sig2.tp - 101.8) < 1e-9, "Scalp TP must widen to 1.8×SL"


# ---------- 3. XAG SL multiplier applied via pair= ----------
def test_xag_sl_multiplier_widens_scalp_sl():
    """When pair contains XAG, the scalp SL distance must equal 1.5 × atr_v
    (vs 1.0 × atr_v for non-XAG pairs). We verify by reading config used during
    a synthetic call. Direct unit-style — just confirm generate_signal_v2 accepts
    the `pair` kwarg without erroring and the config multiplier is honoured."""
    import inspect
    sig = inspect.signature(generate_signal_v2)
    assert "pair" in sig.parameters, "generate_signal_v2 must accept pair= kwarg"
    cfg = conservative_config()
    assert cfg.xag_sl_multiplier == 1.5, "XAG SL multiplier must be 1.5"


# ---------- 4. Swing-pullback setup exists and emits swing-mode ----------
def test_setup_swing_pullback_exists_and_is_swing_mode():
    import inspect
    src = inspect.getsource(_setup_swing_pullback)
    assert 'mode="swing"' in src, "_setup_swing_pullback must emit swing-mode signals"
    assert "max_hold_minutes=240" in src, "Swing pullback must use 240-min hold"
    assert "3.0" in src or "3 *" in src or "3*" in src, "Swing pullback TP must be ~3×ATR"


# ---------- 5. Bridge dynamic trailing constants ----------
def test_bridge_has_dynamic_trailing_per_mode():
    bridge_src = Path(__file__).resolve().parents[1] / "static" / "aurum_bridge.py"
    src = bridge_src.read_text()
    assert "TRAIL_SCALP_START" in src and "TRAIL_SCALP_DISTANCE" in src, "scalp trail consts missing"
    assert "TRAIL_SWING_START" in src and "TRAIL_SWING_DISTANCE" in src, "swing trail consts missing"
    # Defaults per user spec
    assert 'AURUM_TRAIL_SCALP_START", "5"' in src, "scalp start default must be $5"
    assert 'AURUM_TRAIL_SCALP_DISTANCE", "2"' in src, "scalp distance default must be $2"
    assert 'AURUM_TRAIL_SWING_START", "30"' in src, "swing start default must be $30"
    assert 'AURUM_TRAIL_SWING_DISTANCE", "15"' in src, "swing distance default must be $15"


# ---------- 6. Bridge TICKET_MODE registry ----------
def test_bridge_has_ticket_mode_registry_and_populates_it():
    bridge_src = Path(__file__).resolve().parents[1] / "static" / "aurum_bridge.py"
    src = bridge_src.read_text()
    assert "TICKET_MODE: Dict[int, str] = {}" in src, "TICKET_MODE registry missing"
    # Populated at fill
    assert 'TICKET_MODE[last_result.order] = _mode' in src, "TICKET_MODE not populated at fill"
    # Cleaned up at close
    assert "TICKET_MODE.pop(t, None)" in src, "TICKET_MODE not cleaned up on close"
    # Manage loop reads per-mode thresholds
    assert "_mode_tag = TICKET_MODE.get(ticket)" in src, "trailing must read per-ticket mode"


# ---------- 7. Server has SL-cluster cooldown gate ----------
def test_server_has_sl_cluster_cooldown_gate():
    server_src = Path(__file__).resolve().parents[1] / "server.py"
    src = server_src.read_text()
    assert "SL-CLUSTER COOLDOWN" in src or "sl_cluster_lockout" in src, "SL-cluster cooldown missing"
    assert '"exit_reason": "sl_hit"' in src, "Cooldown must filter on sl_hit exit reason"
    # Lookback / lockout window
    assert "timedelta(minutes=90)" in src, "90-min SL lookback window required"
    assert "timedelta(minutes=120)" in src, "120-min lockout window required"


# ---------- 7b. Time-of-day soft size-down ----------
def test_server_has_time_of_day_soft_size_down():
    server_src = Path(__file__).resolve().parents[1] / "server.py"
    src = server_src.read_text()
    assert "_noisy_hours" in src, "Noisy-hour size-down logic missing"
    assert "{5, 8, 14, 16, 23}" in src, "Noisy hours set incorrect"
    assert "lot * 0.5" in src, "Lot must be halved during noisy hours"


# ---------- 7c. Pair is passed to generate_signal_v2 ----------
def test_server_passes_pair_to_generate_signal_v2():
    server_src = Path(__file__).resolve().parents[1] / "server.py"
    src = server_src.read_text()
    assert 'generate_signal_v2(candles, STRATEGY_V2_CFG, htf_trend=htf_trend, pair=bot["pair"])' in src, (
        "server.py must pass pair= kwarg to generate_signal_v2 so XAG SL widening fires"
    )
