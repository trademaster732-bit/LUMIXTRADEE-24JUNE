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
    entry_confirmation: int = 0     # Phase-2 (2026-01): pullback-completion derived
    # Penalties (subtracted from total; logged for audit)
    daily_bias_penalty: int = 0
    # Final
    total: int = 0
    threshold: int = 0
    approved: bool = False
    reasons: List[str] = field(default_factory=list)   # human-readable per-factor justifications
    # 2026-01 diagnostics — persist alongside every scan for forensic analysis
    raw_score: int = 0                                  # un-normalized sum of available factors
    available_weight: int = 100                         # sum of weights actually scored (factors not skipped)
    missing_history: Dict[str, bool] = field(default_factory=dict)  # {"h4": bool, "d1": bool}
    daily_bias_state: str = "neutral"                   # bullish | bearish | neutral | unknown

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
    """Return 'bullish' | 'bearish' | 'neutral' | 'unknown'.

    'unknown' is returned when there is not enough reconstructed D1 history
    (< 56 days) to compute EMA21/EMA55. Callers must treat 'unknown' as
    "no signal" — it MUST NOT incur the neutral-bias score penalty, because
    the cause is a warm-up gap, not market consolidation.
    Source: H1 candles aggregated into D1.
    """
    d1 = aggregate_daily(candles_h1)
    if len(d1) < 56:                       # need enough days for EMA55
        return "unknown"
    closes = [d["c"] for d in d1]
    ef = ema(closes, 21)
    es = ema(closes, 55)
    if not ef or not es:
        return "unknown"
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
    entry_confirmation_score: Optional[int] = None,  # Phase-2: 0-20 from entry_quality engine
) -> ScoreBreakdown:
    """Run the 7-factor score. Caller decides whether to honour the approval result."""
    w = cfg.get("score_weights") or {}
    b = ScoreBreakdown(threshold=int(cfg.get("min_score", 80)))
    side_want_h1 = "up" if side == "buy" else "down"
    side_want_h4 = side_want_h1

    # Track which factor weights are actually available (used for normalization).
    # If a factor cannot be evaluated (e.g. <60 H4 bars), its weight is excluded
    # from the denominator instead of silently awarded 0 — which previously made
    # the 100-point scale unreachable during HTF warm-up.
    w_h4 = int(w.get("h4_trend", 12))
    w_h1 = int(w.get("h1_trend", 13))
    w_adx = int(w.get("adx", 15))
    w_vwap = int(w.get("vwap", 10))
    w_sr = int(w.get("sr", 15))
    w_atr = int(w.get("atr_ratio", 10))
    w_spread = int(w.get("spread", 10))
    w_entry = int(w.get("entry_confirmation", 15))
    # h4_trend is added conditionally (warm-up); entry_confirmation is added when
    # the caller provided a Phase-2 score, otherwise excluded from denominator.
    available = w_h1 + w_adx + w_vwap + w_sr + w_atr + w_spread

    # 1. H4 trend — UNKNOWN when history < 60 bars (excluded from total + denominator)
    h4_short = len(candles_h4) < 60
    if h4_short:
        b.missing_history["h4"] = True
        b.reasons.append("H4 trend unknown — history < 60 bars (excluded)")
    else:
        available += w_h4
        h4_trend = _trend_from_emas(candles_h4)
        if h4_trend == side_want_h4:
            b.h4_trend = w_h4
            b.reasons.append(f"H4 trend agrees ({h4_trend})")
        else:
            b.reasons.append(f"H4 trend {h4_trend} vs side {side}")

    # 2. H1 trend
    h1_trend = _trend_from_emas(candles_h1)
    if h1_trend == side_want_h1:
        b.h1_trend = w_h1
        b.reasons.append(f"H1 trend agrees ({h1_trend})")
    else:
        b.reasons.append(f"H1 trend {h1_trend} vs side {side}")

    # 3. ADX > threshold
    adx_vals = _adx(candles, 14)
    adx_now = adx_vals[-1] if adx_vals else 0.0
    if adx_now > float(cfg.get("adx_threshold", 25.0)):
        b.adx = w_adx
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
            b.vwap = w_vwap
            b.reasons.append(f"VWAP {dist_atr:.2f}×ATR ≤ {max_dist}")
        else:
            b.reasons.append(f"VWAP {dist_atr:.2f}×ATR too far")
    else:
        b.reasons.append("VWAP/ATR unavailable")

    # 5. S/R reaction — the strategy_v2 S/R gate either passes the signal or flips it.
    #    "ok" (passed cleanly) earns the points; a flip or unknown does not.
    if sr_action == "ok":
        b.sr = w_sr
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
            b.atr_ratio = w_atr
            b.reasons.append(f"ATR ratio {ratio:.2f} in band")
        else:
            b.reasons.append(f"ATR ratio {ratio:.2f} out of band [{lo}, {hi}]")

    # 7. Spread vs SL distance
    sl_dist = abs(signal_entry - signal_sl) if (signal_entry and signal_sl) else 0.0
    if spread_at_fill is not None and sl_dist > 0:
        pct = (spread_at_fill / sl_dist) * 100.0
        # Award full points if spread is < 15% of SL distance (tight).
        if pct < 15.0:
            b.spread = w_spread
            b.reasons.append(f"Spread {pct:.1f}% of SL")
        else:
            b.reasons.append(f"Spread {pct:.1f}% of SL too wide")
    else:
        # No live spread info — be neutral (no points, no penalty) and note it.
        b.reasons.append("Spread unknown — neutral")

    # ───── Phase-2 (2026-01): Entry-Confirmation factor (0-w_entry) ─────
    # Caller passes the 0-20 score from the entry_quality engine. Linear scale
    # to the configured weight (default 15). Excluded from numerator+denominator
    # when None (engine disabled or upstream skipped it) so the 0-100 scale
    # remains meaningful — same backward-compat pattern as the H4 warm-up gate.
    if entry_confirmation_score is not None:
        available += w_entry
        ec = max(0, min(20, int(entry_confirmation_score)))
        b.entry_confirmation = int(round((ec / 20.0) * w_entry))
        b.reasons.append(
            f"Entry-confirmation {ec}/20 → {b.entry_confirmation}/{w_entry}"
        )
    else:
        b.reasons.append("Entry-confirmation skipped (engine disabled)")

    # ───── Daily-bias penalty (Feature 4 — Option B: subtract points when neutral) ─────
    # 'unknown' (D1 history < 56 days) is treated as "no information" — it does
    # NOT incur the neutral penalty.
    b.daily_bias_state = daily_bias_value or "unknown"
    if daily_bias_value == "neutral":
        b.daily_bias_penalty = int(cfg.get("daily_bias_neutral_penalty", 5))
        b.reasons.append(f"Daily bias neutral — penalty {b.daily_bias_penalty}")
    elif daily_bias_value == "unknown" or daily_bias_value is None:
        b.missing_history["d1"] = True
        b.reasons.append("Daily bias unknown — D1 history short (no penalty)")

    # Raw sum of awarded factor points (pre-penalty, pre-normalization).
    raw = (b.h4_trend + b.h1_trend + b.adx + b.vwap + b.sr + b.atr_ratio
           + b.spread + b.entry_confirmation)
    b.raw_score = max(0, raw - b.daily_bias_penalty)
    b.available_weight = max(1, available)
    # Normalize so the 0-100 scale (and the configured min_score) remain meaningful
    # when a factor is excluded due to warm-up.
    if available == 100:
        b.total = b.raw_score
    else:
        b.total = int(round(b.raw_score * 100.0 / available))
        b.reasons.append(
            f"Normalized {b.raw_score}/{available} → {b.total}/100 (warm-up adjusted)"
        )
    b.approved = b.total >= b.threshold
    return b
