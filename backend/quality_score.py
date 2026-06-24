"""Phase-1 Trade-Quality Scoring Engine (2026-06-22).

Computes a 0-100 quality score for a candidate trade BEFORE it is fired.
Score = sum of 7 weighted factors, each contributing 0 or its full weight:

    1. h4_trend  — H4 EMA21 vs EMA55 agrees with signal side    (default +20)
    2. h1_trend  — H1 EMA21 vs EMA55 agrees with signal side    (default +20)
    3. adx       — ADX(14) on the signal timeframe > threshold  (default +15)
    4. vwap      — price within configured ATR-distance of VWAP (default +10)
    5. sr        — clean S/R reaction confirmed by signal context (default +15)
    6. atr_ratio — ATR ratio in [min, max] band                 (default +10)
    7. spread    — broker spread below threshold pct of SL dist (default +10)

Returns a `ScoreBreakdown` with the total + each component (for telemetry).

The function is pure / side-effect-free. Pulls weights and thresholds from the
engine_config dict (loaded by the caller).
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

from engine import ema, atr


@dataclass
class ScoreBreakdown:
    h4_trend: int = 0
    h1_trend: int = 0
    adx: int = 0
    vwap: int = 0
    sr: int = 0
    atr_ratio: int = 0
    spread: int = 0
    # Penalties (subtracted from total; logged for audit)
    daily_bias_penalty: int = 0
    # Final
    total: int = 0
    threshold: int = 0
    approved: bool = False
    reasons: List[str] = field(default_factory=list)   # human-readable per-factor justifications

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _adx(candles: List[Dict[str, float]], period: int = 14) -> List[float]:
    """Wilder's ADX. Returns list aligned with candles (NaN/0.0 for warm-up bars)."""
    n = len(candles)
    if n < period * 2:
        return [0.0] * n
    tr: List[float] = [0.0]
    plus_dm: List[float] = [0.0]
    minus_dm: List[float] = [0.0]
    for i in range(1, n):
        h, l, pc = candles[i]["h"], candles[i]["l"], candles[i - 1]["c"]
        ph, pl = candles[i - 1]["h"], candles[i - 1]["l"]
        tr.append(max(h - l, abs(h - pc), abs(l - pc)))
        up = h - ph
        dn = pl - l
        plus_dm.append(up if (up > dn and up > 0) else 0.0)
        minus_dm.append(dn if (dn > up and dn > 0) else 0.0)
    # Wilder smoothing
    def _smooth(arr: List[float]) -> List[float]:
        out = [0.0] * len(arr)
        s = sum(arr[1:period + 1])
        out[period] = s
        for i in range(period + 1, len(arr)):
            out[i] = out[i - 1] - (out[i - 1] / period) + arr[i]
        return out
    tr_s = _smooth(tr)
    pdm_s = _smooth(plus_dm)
    mdm_s = _smooth(minus_dm)
    adx_out = [0.0] * n
    dx_buf: List[float] = []
    for i in range(period, n):
        if tr_s[i] <= 0:
            continue
        pdi = 100 * pdm_s[i] / tr_s[i]
        mdi = 100 * mdm_s[i] / tr_s[i]
        if (pdi + mdi) <= 0:
            continue
        dx = 100 * abs(pdi - mdi) / (pdi + mdi)
        dx_buf.append(dx)
        if len(dx_buf) > period:
            adx_out[i] = sum(dx_buf[-period:]) / period
    return adx_out


def _vwap(candles: List[Dict[str, float]], window: int = 80) -> Optional[float]:
    """Rolling VWAP over the last `window` bars. Uses (h+l+c)/3 as typical price.
    Returns None if window cannot be filled."""
    if len(candles) < min(20, window):
        return None
    sample = candles[-window:]
    num = 0.0
    den = 0.0
    for c in sample:
        tp = (float(c["h"]) + float(c["l"]) + float(c["c"])) / 3.0
        v = float(c.get("v") or 1.0)
        num += tp * v
        den += v
    return num / den if den > 0 else None


def _trend_from_emas(candles: List[Dict[str, float]]) -> str:
    """Return 'up' / 'down' / 'flat' from EMA21 vs EMA55."""
    if len(candles) < 60:
        return "flat"
    closes = [c["c"] for c in candles]
    ef = ema(closes, 21)
    es = ema(closes, 55)
    if not ef or not es:
        return "flat"
    diff = ef[-1] - es[-1]
    band = abs(closes[-1]) * 0.0005
    if diff > band:
        return "up"
    if diff < -band:
        return "down"
    return "flat"


def aggregate_daily(candles_h1: List[Dict[str, float]]) -> List[Dict[str, float]]:
    """Roll up H1 candles into D1 OHLC bars by UTC day. Used for the daily-bias filter
    when the bridge isn't streaming D1 directly."""
    if not candles_h1:
        return []
    out: List[Dict[str, float]] = []
    bucket: Optional[Dict[str, float]] = None
    bucket_day: Optional[int] = None
    for c in candles_h1:
        ts = int(c["t"])
        day = ts // 86_400_000  # ms → day index
        if bucket_day != day:
            if bucket is not None:
                out.append(bucket)
            bucket = {"t": day * 86_400_000, "o": c["o"], "h": c["h"], "l": c["l"],
                      "c": c["c"], "v": float(c.get("v") or 0)}
            bucket_day = day
        else:
            bucket["h"] = max(bucket["h"], c["h"])
            bucket["l"] = min(bucket["l"], c["l"])
            bucket["c"] = c["c"]
            bucket["v"] = bucket.get("v", 0) + float(c.get("v") or 0)
    if bucket is not None:
        out.append(bucket)
    return out


def daily_bias(candles_h1: List[Dict[str, float]]) -> str:
    """Return 'bullish' | 'bearish' | 'neutral' computed from D1 EMA21 vs EMA55.
    Source: H1 candles aggregated into D1."""
    d1 = aggregate_daily(candles_h1)
    if len(d1) < 56:                       # need enough days for EMA55
        return "neutral"
    closes = [d["c"] for d in d1]
    ef = ema(closes, 21)
    es = ema(closes, 55)
    if not ef or not es:
        return "neutral"
    diff = ef[-1] - es[-1]
    # 0.05% band — neutral when EMAs are within 5 bp
    band = abs(closes[-1]) * 0.0005
    if diff > band:
        return "bullish"
    if diff < -band:
        return "bearish"
    return "neutral"


def score_trade(
    *,
    side: str,                              # "buy" | "sell"
    symbol: str,
    candles: List[Dict[str, float]],        # signal-TF candles (e.g. M15)
    candles_h1: List[Dict[str, float]],     # H1 candles for h1_trend + D1 aggregation
    candles_h4: List[Dict[str, float]],     # H4 candles for h4_trend
    cfg: Dict[str, Any],                    # full engine_config dict
    signal_sl: float,
    signal_entry: float,
    spread_at_fill: Optional[float] = None, # broker-reported live spread in price units
    sr_action: Optional[str] = None,        # "ok"|"flip"|None — from strategy_v2 _sr_check
    daily_bias_value: Optional[str] = None, # "bullish"|"bearish"|"neutral"|None
) -> ScoreBreakdown:
    """Run the 7-factor score. Caller decides whether to honour the approval result."""
    w = cfg.get("score_weights") or {}
    b = ScoreBreakdown(threshold=int(cfg.get("min_score", 80)))
    side_want_h1 = "up" if side == "buy" else "down"
    side_want_h4 = side_want_h1

    # 1. H4 trend
    h4_trend = _trend_from_emas(candles_h4)
    if h4_trend == side_want_h4:
        b.h4_trend = int(w.get("h4_trend", 20))
        b.reasons.append(f"H4 trend agrees ({h4_trend})")
    else:
        b.reasons.append(f"H4 trend {h4_trend} vs side {side}")

    # 2. H1 trend
    h1_trend = _trend_from_emas(candles_h1)
    if h1_trend == side_want_h1:
        b.h1_trend = int(w.get("h1_trend", 20))
        b.reasons.append(f"H1 trend agrees ({h1_trend})")
    else:
        b.reasons.append(f"H1 trend {h1_trend} vs side {side}")

    # 3. ADX > threshold
    adx_vals = _adx(candles, 14)
    adx_now = adx_vals[-1] if adx_vals else 0.0
    if adx_now > float(cfg.get("adx_threshold", 25.0)):
        b.adx = int(w.get("adx", 15))
        b.reasons.append(f"ADX {adx_now:.1f} > 25")
    else:
        b.reasons.append(f"ADX {adx_now:.1f} weak")

    # 4. VWAP distance
    vwap_now = _vwap(candles, 80)
    a_arr = atr(candles, 14)
    atr_now = a_arr[-1] if a_arr else 0.0
    last_close = candles[-1]["c"] if candles else 0.0
    if vwap_now and atr_now > 0:
        dist_atr = abs(last_close - vwap_now) / atr_now
        max_dist = float(cfg.get("vwap_max_distance_atr", 1.5))
        if dist_atr <= max_dist:
            b.vwap = int(w.get("vwap", 10))
            b.reasons.append(f"VWAP {dist_atr:.2f}×ATR ≤ {max_dist}")
        else:
            b.reasons.append(f"VWAP {dist_atr:.2f}×ATR too far")
    else:
        b.reasons.append("VWAP/ATR unavailable")

    # 5. S/R reaction — the strategy_v2 S/R gate either passes the signal or flips it.
    #    "ok" (passed cleanly) earns the points; a flip or unknown does not.
    if sr_action == "ok":
        b.sr = int(w.get("sr", 15))
        b.reasons.append("S/R clean")
    else:
        b.reasons.append(f"S/R action: {sr_action or 'unknown'}")

    # 6. ATR ratio in band
    if a_arr:
        med = sorted([x for x in a_arr[-50:] if x > 0])
        med = med[len(med) // 2] if med else atr_now
        ratio = (atr_now / med) if med > 0 else 1.0
        lo = float(cfg.get("atr_ratio_min", 0.80))
        hi = float(cfg.get("atr_ratio_max", 2.00))
        if lo <= ratio <= hi:
            b.atr_ratio = int(w.get("atr_ratio", 10))
            b.reasons.append(f"ATR ratio {ratio:.2f} in band")
        else:
            b.reasons.append(f"ATR ratio {ratio:.2f} out of band [{lo}, {hi}]")

    # 7. Spread vs SL distance
    sl_dist = abs(signal_entry - signal_sl) if (signal_entry and signal_sl) else 0.0
    if spread_at_fill is not None and sl_dist > 0:
        pct = (spread_at_fill / sl_dist) * 100.0
        # Award full points if spread is < 15% of SL distance (tight).
        if pct < 15.0:
            b.spread = int(w.get("spread", 10))
            b.reasons.append(f"Spread {pct:.1f}% of SL")
        else:
            b.reasons.append(f"Spread {pct:.1f}% of SL too wide")
    else:
        # No live spread info — be neutral (no points, no penalty) and note it.
        b.reasons.append("Spread unknown — neutral")

    # ───── Daily-bias penalty (Feature 4 — Option B: subtract points when neutral) ─────
    if daily_bias_value == "neutral":
        b.daily_bias_penalty = int(cfg.get("daily_bias_neutral_penalty", 15))
        b.reasons.append(f"Daily bias neutral — penalty {b.daily_bias_penalty}")

    raw = (b.h4_trend + b.h1_trend + b.adx + b.vwap + b.sr + b.atr_ratio + b.spread)
    b.total = max(0, raw - b.daily_bias_penalty)
    b.approved = b.total >= b.threshold
    return b
