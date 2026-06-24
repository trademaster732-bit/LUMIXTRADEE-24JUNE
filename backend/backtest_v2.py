"""
Aurum FX — Backtest Engine v2 (P0, 2026-06)
--------------------------------------------
Replays the LIVE v2 strategy (strategy_v2.generate_signal_v2) over stored broker
candles so every constant (RSI bands, S/R zone width, RR floors) can be PROVEN
before going live. The legacy backtest.py (v1 engine, Dukascopy data) remains
untouched and continues to power /api/bots/{id}/backtest.

Fidelity to production:
  • 200-bar rolling window — exactly what the live scanner feeds the strategy
  • bar-close-only — signals evaluated on completed bars
  • session derived from each bar's UTC timestamp (same current_session map)
  • HTF trend replicated from server._htf_trend (EMA21/55 + 5bp flat band)
  • fills at NEXT bar open, SL/TP re-anchored to fill (mirrors bridge execute())
  • trade management mirrors bridge v1.8.1+: SL→BE at +0.5R, 50% partial at +1R
    with BE on remainder, max-hold timeout close
  • conservative same-bar resolution: SL checked BEFORE TP
  • full round-trip spread charged once per trade (in R terms)
  • 1-bar signal cooldown, max 2 concurrent positions (max_positions default)
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from engine import ema, current_session
from strategy_v2 import StrategyV2Config, generate_signal_v2

# Typical broker spreads in PRICE units (override per request)
SPREAD_DEFAULTS: Dict[str, float] = {
    "XAUUSD": 0.30, "XAGUSD": 0.030,
    "USDJPY": 0.015, "EURJPY": 0.020, "GBPJPY": 0.025, "AUDJPY": 0.020,
    "EURUSD": 0.00012, "GBPUSD": 0.00015, "AUDUSD": 0.00014,
    "USDCAD": 0.00016, "USDCHF": 0.00016, "NZDUSD": 0.00018,
}
TF_MIN = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}

WINDOW = 200                 # live scanner fetch_candles limit
WARMUP = 80                  # v2 minimum bars
MAX_CONCURRENT = 2           # mirrors bot max_positions default
DEFAULT_SWING_HOLD_MIN = 480


def _setup_tag(reason: str) -> str:
    rl = (reason or "").lower()
    if "bos" in rl:
        return "bos_retest"
    if "breakout" in rl or "squeeze" in rl:
        return "range_breakout"
    if "sweep" in rl:
        return "liquidity_sweep"
    if "rsi-scalp" in rl:
        return "rsi_scalp"
    if "pullback" in rl:
        return "trend_pullback"
    return "other"


def _htf_trend_series(htf_candles: List[dict]) -> List[tuple]:
    """[(bar_t_ms, 'up'|'down'|'flat'|None)] per HTF closed bar.
    Replicates server._htf_trend: EMA21 vs EMA55, ±5bp flat band, needs ≥60 bars."""
    out: List[tuple] = []
    closes: List[float] = []
    for c in htf_candles:
        closes.append(c["c"])
        if len(closes) < 60:
            out.append((c["t"], None))
            continue
        ef = ema(closes, 21)
        es = ema(closes, 55)
        diff = ef[-1] - es[-1]
        band = abs(closes[-1]) * 0.0005
        trend = "up" if diff > band else ("down" if diff < -band else "flat")
        out.append((c["t"], trend))
    return out


def _trade_r(side: str, entry: float, price: float, sl_dist: float) -> float:
    move = (price - entry) if side == "buy" else (entry - price)
    return move / sl_dist


def _manage_trade(tr: dict, bar: dict, tf_min: int) -> Optional[dict]:
    """Apply bridge-style management for one completed bar. Returns a closed-trade
    dict when the position fully exits on this bar, else None (still open)."""
    side, entry, sl_dist = tr["side"], tr["entry"], tr["sl_dist"]
    worst = bar["l"] if side == "buy" else bar["h"]
    best = bar["h"] if side == "buy" else bar["l"]
    worst_r = _trade_r(side, entry, worst, sl_dist)
    best_r = _trade_r(side, entry, best, sl_dist)
    sl_r = _trade_r(side, entry, tr["sl"], sl_dist)

    # 1) SL first — conservative same-bar ordering
    if worst_r <= sl_r + 1e-12:
        return _close(tr, bar, exit_r=sl_r,
                      reason="be_stop" if tr["be_done"] and abs(sl_r) < 0.05 else "sl_hit")
    # 2) TP on the remainder
    tp_r = _trade_r(side, entry, tr["tp"], sl_dist)
    if best_r >= tp_r - 1e-12:
        return _close(tr, bar, exit_r=tp_r, reason="tp_hit")
    # 3) Max-hold timeout (compare bar CLOSE time vs entry)
    bar_close_t = bar["t"] + tf_min * 60_000
    elapsed_min = (bar_close_t - tr["entry_t"]) / 60_000.0
    if tr["max_hold_min"] > 0 and elapsed_min >= tr["max_hold_min"]:
        return _close(tr, bar, exit_r=_trade_r(side, entry, bar["c"], sl_dist),
                      reason="max_hold")
    # 4) Management flags — effective from the NEXT bar (intra-bar order unknowable)
    if not tr["partial_done"] and best_r >= 1.0:
        tr["realized_r"] += 1.0 * 0.5          # book +1R on 50% of the position
        tr["remaining"] = 0.5
        tr["partial_done"] = True
        tr["be_done"] = True
        tr["sl"] = entry                       # BE on the remainder
    elif not tr["be_done"] and best_r >= 0.5:
        tr["be_done"] = True
        tr["sl"] = entry                       # SL → entry at +0.5R
    return None


def _close(tr: dict, bar: dict, *, exit_r: float, reason: str) -> dict:
    spread_r = tr["spread"] / tr["sl_dist"] if tr["sl_dist"] > 0 else 0.0
    total_r = tr["realized_r"] + exit_r * tr["remaining"] - spread_r
    return {
        "side": tr["side"], "mode": tr["mode"], "setup": tr["setup"],
        "session": tr["session"], "confidence": round(tr["confidence"], 3),
        "entry_t": tr["entry_t"], "exit_t": bar["t"],
        "entry": round(tr["entry"], 6),
        "r": round(total_r, 3),
        "exit_reason": reason,
        "partial_taken": tr["partial_done"],
    }


def _agg(trades: List[dict]) -> Dict[str, Any]:
    n = len(trades)
    if n == 0:
        return {"trades": 0, "wins": 0, "win_rate": None, "avg_r": None,
                "expectancy_r": None, "profit_factor": None, "total_r": 0.0}
    rs = [t["r"] for t in trades]
    wins = sum(1 for x in rs if x > 0)
    pos = sum(x for x in rs if x > 0)
    neg = abs(sum(x for x in rs if x < 0))
    return {
        "trades": n, "wins": wins,
        "win_rate": round(wins / n, 3),
        "avg_r": round(sum(rs) / n, 3),
        "expectancy_r": round(sum(rs) / n, 3),
        "profit_factor": round(pos / neg, 2) if neg > 0 else None,
        "total_r": round(sum(rs), 3),
    }


def simulate_backtest(
    candles: List[dict],
    cfg: StrategyV2Config,
    *,
    timeframe: str,
    pair: str,
    spread: Optional[float] = None,
    htf_candles: Optional[List[dict]] = None,
) -> Dict[str, Any]:
    """Pure, deterministic replay. `candles` must be ascending closed bars."""
    tf_min = TF_MIN.get((timeframe or "M15").upper(), 15)
    sp = SPREAD_DEFAULTS.get(pair.upper(), 0.0002) if spread is None else float(spread)
    htf_series = _htf_trend_series(htf_candles or [])
    hp = 0
    cur_htf: Optional[str] = None

    open_trades: List[dict] = []
    closed: List[dict] = []
    last_signal_i = -10

    for i in range(WARMUP, len(candles)):
        bar = candles[i]
        t_ms = int(bar["t"])
        # advance HTF pointer to the last HTF bar that closed at/before this bar
        while hp < len(htf_series) and htf_series[hp][0] <= t_ms:
            cur_htf = htf_series[hp][1]
            hp += 1
        # 1) manage open positions with this completed bar
        for tr in list(open_trades):
            if i < tr["entry_i"]:
                continue
            done = _manage_trade(tr, bar, tf_min)
            if done:
                closed.append(done)
                open_trades.remove(tr)
        # 2) evaluate a new signal on the close of this bar (needs a fill bar after)
        if i >= len(candles) - 1:
            continue
        if len(open_trades) >= MAX_CONCURRENT:
            continue
        if i - last_signal_i < 1:                       # 1-bar cooldown
            continue
        window = candles[max(0, i - WINDOW + 1): i + 1]
        dt = datetime.fromtimestamp(t_ms / 1000, tz=timezone.utc)
        session = current_session(dt)
        out = generate_signal_v2(window, cfg, htf_trend=cur_htf, session_override=session)
        if not out:
            continue
        sig, _ctx = out
        sl_dist = abs(sig.entry - sig.sl)
        tp_dist = abs(sig.tp - sig.entry)
        if sl_dist <= 0 or tp_dist <= 0:
            continue
        nxt = candles[i + 1]
        entry = float(nxt["o"])                          # market fill at next bar open
        sl = entry - sl_dist if sig.side == "buy" else entry + sl_dist
        tp = entry + tp_dist if sig.side == "buy" else entry - tp_dist
        open_trades.append({
            "side": sig.side, "mode": sig.mode, "setup": _setup_tag(sig.reason),
            "session": sig.session, "confidence": sig.confidence,
            "entry": entry, "sl": sl, "tp": tp, "sl_dist": sl_dist,
            "entry_t": int(nxt["t"]), "entry_i": i + 1,
            "max_hold_min": int(sig.max_hold_minutes or DEFAULT_SWING_HOLD_MIN),
            "be_done": False, "partial_done": False,
            "realized_r": 0.0, "remaining": 1.0, "spread": sp,
        })
        last_signal_i = i

    # force-close anything still open at the last bar (mark-to-market)
    if candles and open_trades:
        last_bar = candles[-1]
        for tr in open_trades:
            exit_r = _trade_r(tr["side"], tr["entry"], last_bar["c"], tr["sl_dist"])
            closed.append(_close(tr, last_bar, exit_r=exit_r, reason="end_of_data"))
        open_trades.clear()

    # ---- aggregates ----
    summary = _agg(closed)
    # max drawdown + max consecutive losses on the R equity curve
    peak = run = max_dd = 0.0
    consec = max_consec = 0
    for t in closed:
        run += t["r"]
        peak = max(peak, run)
        max_dd = max(max_dd, peak - run)
        if t["r"] < 0:
            consec += 1
            max_consec = max(max_consec, consec)
        else:
            consec = 0
    summary["max_drawdown_r"] = round(max_dd, 3)
    summary["max_consecutive_losses"] = max_consec
    exit_reasons: Dict[str, int] = {}
    for t in closed:
        exit_reasons[t["exit_reason"]] = exit_reasons.get(t["exit_reason"], 0) + 1
    summary["exit_reasons"] = exit_reasons

    def _group(key: str) -> Dict[str, Any]:
        groups: Dict[str, List[dict]] = {}
        for t in closed:
            groups.setdefault(t[key], []).append(t)
        return {k: _agg(v) for k, v in sorted(groups.items())}

    bars = len(candles)
    period_days = ((candles[-1]["t"] - candles[0]["t"]) / 86_400_000) if bars >= 2 else 0
    return {
        "params": {
            "pair": pair.upper(), "timeframe": timeframe.upper(),
            "spread": sp, "window": WINDOW, "bars": bars,
            "period_days": round(period_days, 2),
            "from": datetime.fromtimestamp(candles[0]["t"] / 1000, tz=timezone.utc).isoformat() if bars else None,
            "to": datetime.fromtimestamp(candles[-1]["t"] / 1000, tz=timezone.utc).isoformat() if bars else None,
            "htf_bars": len(htf_candles or []),
        },
        "summary": summary,
        "by_setup": _group("setup"),
        "by_session": _group("session"),
        "by_mode": _group("mode"),
        "trades": closed[-300:],
    }
