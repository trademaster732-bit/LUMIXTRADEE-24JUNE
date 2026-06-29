"""Phase-2 Entry Quality Engine (2026-01).

Four post-strategy, pre-execution modules that improve ENTRY TIMING without
altering direction prediction or any risk/sizing/SL/TP/cooldown logic:

  1. PullbackCompletion   — score 0-20: has the retracement actually ended?
  2. SRDistanceFilter     — ATR-normalized headroom to nearest resistance/support
  3. TrendMaturity        — fresh / developing / extended / exhausted
  4. CandleConfirmation   — last closed bar is a valid confirmation pattern

The orchestrator `evaluate_entry_quality()` returns an `EntryQualityResult`
with hard-gate flags, scores, and human-readable reasons for diagnostics.

Configuration is read from engine_config under the "entry_quality" key with
optional per-symbol-profile overrides (metals vs forex). All weights, gates
and thresholds are toggleable + tunable; defaults are conservative and the
module is fully backward-compatible (callers that don't read the result
behave identically to pre-Phase-2).
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

# Reuse the same Wilder ADX as quality_score to keep telemetry consistent.
from quality_score import _adx as _adx_compute


# ─────────────────────────────────────────────────────────────────────────────
# Symbol-profile dispatcher
# ─────────────────────────────────────────────────────────────────────────────
def symbol_profile(symbol: str) -> str:
    """Return 'metals' for XAU/XAG, else 'forex'."""
    s = (symbol or "").upper()
    if s.startswith("XAU") or s.startswith("XAG"):
        return "metals"
    return "forex"


def _profile_cfg(eq_cfg: Dict[str, Any], profile: str) -> Dict[str, Any]:
    """Merge profile-specific overrides on top of the base entry_quality config."""
    base = {k: v for k, v in (eq_cfg or {}).items() if k != "profiles"}
    prof_overrides = ((eq_cfg or {}).get("profiles") or {}).get(profile) or {}
    return {**base, **prof_overrides}


# ─────────────────────────────────────────────────────────────────────────────
# Local helpers (kept inside this module to avoid coupling with strategy_v2)
# ─────────────────────────────────────────────────────────────────────────────
def _swings(candles: List[Dict[str, float]], left: int = 3, right: int = 3
            ) -> Tuple[List[int], List[int]]:
    """Pivot-based swing-high / swing-low indexes."""
    highs, lows = [], []
    n = len(candles)
    for i in range(left, n - right):
        h, l = candles[i]["h"], candles[i]["l"]
        if (all(candles[j]["h"] <= h for j in range(i - left, i)) and
                all(candles[j]["h"] < h for j in range(i + 1, i + right + 1))):
            highs.append(i)
        if (all(candles[j]["l"] >= l for j in range(i - left, i)) and
                all(candles[j]["l"] > l for j in range(i + 1, i + right + 1))):
            lows.append(i)
    return highs, lows


def _body_pct(c: Dict[str, float]) -> float:
    rng = max(c["h"] - c["l"], 1e-9)
    return abs(c["c"] - c["o"]) / rng


# ─────────────────────────────────────────────────────────────────────────────
# Result dataclass
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class EntryQualityResult:
    # Module 1
    pullback_score: int = 0                     # 0-20
    pullback_passed: bool = True                # ≥ min_pullback_score
    # Module 2
    sr_distance_atr: Optional[float] = None     # None when no level detected
    sr_passed: bool = True
    nearest_level: Optional[float] = None
    # Module 3
    trend_stage: str = "fresh"                  # fresh|developing|extended|exhausted
    momentum_accel: bool = False
    trend_passed: bool = True
    # Module 4
    candle_confirmed: bool = True
    candle_pattern: Optional[str] = None        # engulfing|pin_bar|momentum|break_high|...
    candle_passed: bool = True
    # Combined
    entry_confirmation_score: int = 0           # alias of pullback_score (0-20) for score integration
    momentum_score: int = 0                     # 0-100 momentum gauge (diagnostics)
    passed: bool = True                         # overall hard gate
    rejection_reason: Optional[str] = None      # first hard-gate failure
    reasons: List[str] = field(default_factory=list)
    profile: str = "forex"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ─────────────────────────────────────────────────────────────────────────────
# Module 1 — Pullback Completion (score 0-20)
# ─────────────────────────────────────────────────────────────────────────────
def evaluate_pullback_completion(
    candles: List[Dict[str, float]], side: str,
) -> Tuple[int, List[str]]:
    """Return (score 0-20, reasons[]).

    Sub-factors:
      • Higher-low (buy) / lower-high (sell) formed AFTER pullback's deepest point  (+8)
      • Close back across the pullback's micro-structure (break of prior bar's
        high for buy / low for sell)                                                (+6)
      • Pullback length is "normal" (2–6 bars of opposite-direction candles)        (+3)
      • Last closed bar's body is bullish (buy) / bearish (sell)                    (+3)
    """
    reasons: List[str] = []
    score = 0
    if len(candles) < 10:
        reasons.append("pullback: insufficient candles (<10) → score 0")
        return 0, reasons

    last = candles[-1]
    prev = candles[-2]

    # 1) Higher-low / lower-high formation (using last ~12 bars)
    window = candles[-12:]
    if side == "buy":
        # Find local minimum within first 8 bars of window, then look for a higher low after
        lows = [c["l"] for c in window]
        idx_min = lows.index(min(lows[:-2]))
        later_min = min(lows[idx_min + 1:]) if idx_min + 1 < len(lows) else lows[-1]
        if later_min > lows[idx_min]:
            score += 8
            reasons.append(f"HL formed: {later_min:.5f} > swing-low {lows[idx_min]:.5f} (+8)")
        else:
            reasons.append("no higher-low yet")
    else:  # sell
        highs = [c["h"] for c in window]
        idx_max = highs.index(max(highs[:-2]))
        later_max = max(highs[idx_max + 1:]) if idx_max + 1 < len(highs) else highs[-1]
        if later_max < highs[idx_max]:
            score += 8
            reasons.append(f"LH formed: {later_max:.5f} < swing-high {highs[idx_max]:.5f} (+8)")
        else:
            reasons.append("no lower-high yet")

    # 2) Break of pullback micro-structure (last close crosses previous bar's high/low)
    if side == "buy" and last["c"] > prev["h"]:
        score += 6
        reasons.append(f"close {last['c']:.5f} > prev high {prev['h']:.5f} (+6)")
    elif side == "sell" and last["c"] < prev["l"]:
        score += 6
        reasons.append(f"close {last['c']:.5f} < prev low {prev['l']:.5f} (+6)")
    else:
        reasons.append("no break of pullback micro-structure")

    # 3) Pullback length normality (2–6 opposite-direction bars before last)
    opp_run = 0
    for c in reversed(candles[-8:-1]):
        is_opp = (c["c"] < c["o"]) if side == "buy" else (c["c"] > c["o"])
        if is_opp:
            opp_run += 1
        else:
            break
    if 2 <= opp_run <= 6:
        score += 3
        reasons.append(f"pullback length {opp_run} bars in [2,6] (+3)")
    else:
        reasons.append(f"pullback length {opp_run} bars out of [2,6]")

    # 4) Last closed bar direction agrees with signal side
    last_bull = last["c"] > last["o"]
    if (side == "buy" and last_bull) or (side == "sell" and not last_bull):
        score += 3
        reasons.append("last bar direction agrees (+3)")
    else:
        reasons.append("last bar direction disagrees")

    return min(20, score), reasons


# ─────────────────────────────────────────────────────────────────────────────
# Module 2 — Dynamic SR Distance Filter
# ─────────────────────────────────────────────────────────────────────────────
def evaluate_sr_distance(
    candles: List[Dict[str, float]], side: str,
    atr_now: float, min_distance_atr: float,
) -> Tuple[bool, Optional[float], Optional[float], List[str]]:
    """Return (passed, distance_atr, nearest_level, reasons[]).

    For BUY: nearest_level = closest swing-high ABOVE current close.
    For SELL: nearest_level = closest swing-low BELOW current close.
    """
    reasons: List[str] = []
    if not candles or atr_now <= 0:
        reasons.append("SR: atr or candles missing — pass-through")
        return True, None, None, reasons

    last_close = candles[-1]["c"]
    highs, lows = _swings(candles[-120:], left=3, right=3)
    # Translate indexes from sub-window back to absolute prices
    sub = candles[-120:]
    if side == "buy":
        levels_above = [sub[i]["h"] for i in highs if sub[i]["h"] > last_close]
        if not levels_above:
            reasons.append("no resistance above close in last 120 bars — pass-through")
            return True, None, None, reasons
        nearest = min(levels_above)
        dist_atr = (nearest - last_close) / atr_now
    else:
        levels_below = [sub[i]["l"] for i in lows if sub[i]["l"] < last_close]
        if not levels_below:
            reasons.append("no support below close in last 120 bars — pass-through")
            return True, None, None, reasons
        nearest = max(levels_below)
        dist_atr = (last_close - nearest) / atr_now

    passed = dist_atr >= min_distance_atr
    reasons.append(
        f"SR distance {dist_atr:.2f}×ATR vs min {min_distance_atr:.2f} → "
        f"{'PASS' if passed else 'FAIL'}"
    )
    return passed, round(dist_atr, 3), nearest, reasons


# ─────────────────────────────────────────────────────────────────────────────
# Module 3 — Trend Maturity
# ─────────────────────────────────────────────────────────────────────────────
def evaluate_trend_maturity(
    candles: List[Dict[str, float]], side: str,
    ema_fast: List[float], ema_slow: List[float],
    adx_vals: List[float], atr_now: float,
    *, fresh_threshold: float = 1.0,
    exhaustion_distance_atr: float = 3.5,
    momentum_threshold: float = 0.0,
) -> Tuple[str, bool, int, List[str]]:
    """Return (stage, momentum_accel, momentum_score 0-100, reasons[]).

    Stage rules:
      • fresh       — EMA crossover within last `fresh_threshold` × 20 bars AND
                      ADX rising AND price ≤ 1×ATR from EMA fast
      • developing  — trend established 10-40 bars, ADX healthy, price ≤ 2.5×ATR from EMA
      • extended    — trend > 40 bars OR price > 2.5×ATR from EMA AND ADX flat/falling
      • exhausted   — price > `exhaustion_distance_atr`×ATR from EMA OR ADX falling
                      while trend > 50 bars

    Momentum acceleration: last 5-bar ADX slope > momentum_threshold AND
    last 5-bar EMA-fast slope same direction as `side`.
    """
    reasons: List[str] = []
    n = len(candles)
    if n < 60 or not ema_fast or not ema_slow:
        reasons.append("maturity: insufficient history — defaulting to 'developing'")
        return "developing", False, 50, reasons

    # Find last EMA cross matching trend direction
    cross_idx = None
    for i in range(n - 2, max(0, n - 100), -1):
        if side == "buy":
            crossed_up = ema_fast[i - 1] <= ema_slow[i - 1] and ema_fast[i] > ema_slow[i]
            if crossed_up:
                cross_idx = i
                break
        else:
            crossed_dn = ema_fast[i - 1] >= ema_slow[i - 1] and ema_fast[i] < ema_slow[i]
            if crossed_dn:
                cross_idx = i
                break
    trend_age = (n - 1 - cross_idx) if cross_idx is not None else 100  # default "old"

    # Distance of price from fast EMA in ATR units
    last_close = candles[-1]["c"]
    ema_f_now = ema_fast[-1]
    dist_atr = abs(last_close - ema_f_now) / atr_now if atr_now > 0 else 0.0

    # ADX trajectory (last 5 vs prior 5)
    if len(adx_vals) >= 10:
        adx_last5 = sum(adx_vals[-5:]) / 5.0
        adx_prev5 = sum(adx_vals[-10:-5]) / 5.0
        adx_slope = adx_last5 - adx_prev5
    else:
        adx_slope = 0.0

    # EMA-fast slope (last 5 bars)
    if len(ema_fast) >= 6:
        ema_slope = ema_fast[-1] - ema_fast[-6]
    else:
        ema_slope = 0.0

    momentum_dir_ok = (ema_slope > 0) if side == "buy" else (ema_slope < 0)
    momentum_accel = adx_slope > momentum_threshold and momentum_dir_ok
    # Momentum score (0-100): mix of ADX level, ADX slope direction, distance penalty
    adx_now = adx_vals[-1] if adx_vals else 0.0
    raw_mom = (
        min(60.0, adx_now * 2.0)                # ADX 0-30 → 0-60
        + (20.0 if adx_slope > 0 else 0.0)      # rising ADX bonus
        + (20.0 if momentum_dir_ok else 0.0)    # EMA slope direction bonus
    )
    momentum_score = int(round(min(100.0, max(0.0, raw_mom))))

    fresh_window = max(5, int(fresh_threshold * 20))
    if trend_age <= fresh_window and adx_slope >= 0 and dist_atr <= 1.5:
        stage = "fresh"
    elif trend_age <= 40 and dist_atr <= 2.5 and adx_now > 18.0:
        stage = "developing"
    elif dist_atr > exhaustion_distance_atr or (trend_age > 50 and adx_slope < 0):
        stage = "exhausted"
    else:
        stage = "extended"

    reasons.append(
        f"maturity: age={trend_age}b dist={dist_atr:.2f}×ATR adx={adx_now:.1f} "
        f"slope={adx_slope:+.2f} ema_slope={ema_slope:+.5f} → {stage} "
        f"(momentum_accel={momentum_accel}, score={momentum_score})"
    )
    return stage, momentum_accel, momentum_score, reasons


# ─────────────────────────────────────────────────────────────────────────────
# Module 4 — Candle Confirmation Engine
# ─────────────────────────────────────────────────────────────────────────────
def evaluate_candle_confirmation(
    candles: List[Dict[str, float]], side: str,
    *, min_body_pct: float = 0.55,
) -> Tuple[bool, Optional[str], List[str]]:
    """Return (confirmed, pattern_name, reasons[]).

    Patterns checked (any one passes the gate):
      • Bullish/bearish engulfing
      • Pin bar (long opposite wick, small body, close in confirming half)
      • Strong-close momentum bar (body ≥ min_body_pct of range AND close in confirming quartile)
      • Break of previous bar's high (buy) / low (sell)
    """
    reasons: List[str] = []
    if len(candles) < 2:
        reasons.append("candle: <2 bars — cannot confirm")
        return False, None, reasons
    last = candles[-1]
    prev = candles[-2]
    rng = max(last["h"] - last["l"], 1e-9)
    body = abs(last["c"] - last["o"])
    body_pct = body / rng

    if side == "buy":
        # Engulfing
        if last["c"] > last["o"] and prev["c"] < prev["o"] and \
                last["c"] >= prev["o"] and last["o"] <= prev["c"]:
            reasons.append("bullish engulfing")
            return True, "bullish_engulfing", reasons
        # Pin bar (long lower wick)
        lower_wick = min(last["o"], last["c"]) - last["l"]
        upper_wick = last["h"] - max(last["o"], last["c"])
        if last["c"] > last["o"] and lower_wick >= 2 * body and lower_wick > upper_wick:
            reasons.append(f"bullish pin bar (lower_wick={lower_wick:.5f})")
            return True, "bullish_pin", reasons
        # Strong-close momentum
        pos_in_range = (last["c"] - last["l"]) / rng
        if last["c"] > last["o"] and body_pct >= min_body_pct and pos_in_range > 0.75:
            reasons.append(f"bullish momentum (body={body_pct:.0%})")
            return True, "bullish_momentum", reasons
        # Break of previous high
        if last["c"] > prev["h"]:
            reasons.append(f"break above prev high {prev['h']:.5f}")
            return True, "break_high", reasons
    else:  # sell
        if last["c"] < last["o"] and prev["c"] > prev["o"] and \
                last["c"] <= prev["o"] and last["o"] >= prev["c"]:
            reasons.append("bearish engulfing")
            return True, "bearish_engulfing", reasons
        lower_wick = min(last["o"], last["c"]) - last["l"]
        upper_wick = last["h"] - max(last["o"], last["c"])
        if last["c"] < last["o"] and upper_wick >= 2 * body and upper_wick > lower_wick:
            reasons.append(f"bearish pin bar (upper_wick={upper_wick:.5f})")
            return True, "bearish_pin", reasons
        pos_in_range = (last["c"] - last["l"]) / rng
        if last["c"] < last["o"] and body_pct >= min_body_pct and pos_in_range < 0.25:
            reasons.append(f"bearish momentum (body={body_pct:.0%})")
            return True, "bearish_momentum", reasons
        if last["c"] < prev["l"]:
            reasons.append(f"break below prev low {prev['l']:.5f}")
            return True, "break_low", reasons

    reasons.append(
        f"no confirmation pattern (body_pct={body_pct:.0%}, "
        f"min={min_body_pct:.0%})"
    )
    return False, None, reasons


# ─────────────────────────────────────────────────────────────────────────────
# Orchestrator
# ─────────────────────────────────────────────────────────────────────────────
def evaluate_entry_quality(
    *,
    side: str,
    symbol: str,
    candles: List[Dict[str, float]],
    ema_fast: List[float],
    ema_slow: List[float],
    atr_now: float,
    eq_cfg: Dict[str, Any],
    adx_vals: Optional[List[float]] = None,
) -> EntryQualityResult:
    """Run all four modules and assemble a single result.

    The orchestrator decides hard-gate pass/fail; the caller is responsible for
    rejection persistence + funnel telemetry. Returning even on failure (rather
    than raising) keeps the call site flat. If `adx_vals` is omitted, ADX is
    computed internally from the same candle series (Wilder, period 14).
    """
    profile = symbol_profile(symbol)
    cfg = _profile_cfg(eq_cfg, profile)
    res = EntryQualityResult(profile=profile)

    # If the engine is globally disabled, short-circuit with a pass.
    if not cfg.get("enabled", True):
        res.reasons.append("entry_quality disabled — pass-through")
        return res

    # Compute ADX once if the caller didn't supply it.
    if adx_vals is None:
        adx_vals = _adx_compute(candles, 14) if candles else []

    # ── Module 1 ─────────────────────────────────────────────
    pb_score, pb_reasons = evaluate_pullback_completion(candles, side)
    res.pullback_score = pb_score
    res.entry_confirmation_score = pb_score
    res.reasons.extend(pb_reasons)
    min_pb = int(cfg.get("min_entry_confirmation_score", 10))  # of 20
    res.pullback_passed = pb_score >= min_pb

    # ── Module 2 ─────────────────────────────────────────────
    sr_ok, sr_dist, sr_level, sr_reasons = evaluate_sr_distance(
        candles, side, atr_now,
        min_distance_atr=float(cfg.get("min_sr_distance_atr", 0.30)),
    )
    res.sr_distance_atr = sr_dist
    res.nearest_level = sr_level
    res.sr_passed = sr_ok
    res.reasons.extend(sr_reasons)

    # ── Module 3 ─────────────────────────────────────────────
    stage, accel, mom_score, tm_reasons = evaluate_trend_maturity(
        candles, side, ema_fast, ema_slow, adx_vals, atr_now,
        fresh_threshold=float(cfg.get("fresh_trend_threshold", 1.0)),
        exhaustion_distance_atr=float(cfg.get("trend_exhaustion_threshold", 3.5)),
        momentum_threshold=float(cfg.get("momentum_threshold", 0.0)),
    )
    res.trend_stage = stage
    res.momentum_accel = accel
    res.momentum_score = mom_score
    res.reasons.extend(tm_reasons)
    # Gate: extended/exhausted blocked UNLESS momentum is accelerating
    if stage in ("extended", "exhausted") and not accel:
        res.trend_passed = False

    # ── Module 4 ─────────────────────────────────────────────
    if cfg.get("confirmation_candle_required", True):
        ok, pattern, cc_reasons = evaluate_candle_confirmation(
            candles, side,
            min_body_pct=float(cfg.get("min_candle_body_pct", 0.55)),
        )
        res.candle_confirmed = ok
        res.candle_pattern = pattern
        res.candle_passed = ok
        res.reasons.extend(cc_reasons)
    else:
        res.candle_confirmed = True
        res.candle_passed = True
        res.reasons.append("candle confirmation disabled — pass-through")

    # ── Compose final gate (order matters: most-specific first) ──
    if not res.pullback_passed:
        res.passed = False
        res.rejection_reason = f"pullback_not_complete:{pb_score}<{min_pb}"
    elif not res.sr_passed:
        res.passed = False
        d = res.sr_distance_atr if res.sr_distance_atr is not None else 0.0
        res.rejection_reason = (
            f"sr_too_close:{d:.2f}<{cfg.get('min_sr_distance_atr', 0.30):.2f}"
        )
    elif not res.trend_passed:
        res.passed = False
        res.rejection_reason = f"trend_{res.trend_stage}:no_momentum_accel"
    elif not res.candle_passed:
        res.passed = False
        res.rejection_reason = "no_candle_confirmation"
    else:
        res.passed = True

    return res
