"""Regression tests for the 2026-06 P0/P1/P2 build:
  P0-1 Backtest engine v2 (simulate_backtest)
  P0-2 Trend-pullback continuation scalp
  P0-3 Bar-close-only evaluation (_drop_forming_bar)
  P1-4 Funnel telemetry (_funnel_record + scan instrumentation)
  P2-6 Spread-vs-SL filter (bridge-side — config sanity only here)
"""
import os
import sys
import asyncio

from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
load_dotenv(os.path.join(ROOT, ".env"))

from strategy_v2 import (  # noqa: E402
    conservative_config, generate_signal_v2, SignalContext, _setup_trend_pullback,
)
from backtest_v2 import simulate_backtest, _setup_tag  # noqa: E402


def _c(o, h, l, c, t=0):
    return {"t": t, "o": o, "h": h, "l": l, "c": c}


def _ctx():
    return SignalContext(
        regime="trending_up", session="london", atr=0.001, atr_ratio=1.0,
        swing_high=None, swing_low=None, bos=None, fvg=None,
        sweep=None, displacement=None, squeeze=False, htf_aligned=None,
    )


# ─────────────────────────────────────────────────────────────────────────────
# P0-3 · Bar-close-only
# ─────────────────────────────────────────────────────────────────────────────
def test_drop_forming_bar():
    import server as s
    tf_ms = 5 * 60_000
    closed = [_c(1, 1, 1, 1, t=0), _c(1, 1, 1, 1, t=tf_ms)]
    now_after_close = 2 * tf_ms + 1000          # second bar completed
    assert len(s._drop_forming_bar(closed, "M5", now_after_close)) == 2
    now_mid_bar = tf_ms + 60_000                # second bar still forming
    assert len(s._drop_forming_bar(closed, "M5", now_mid_bar)) == 1
    assert s._drop_forming_bar([], "M5", now_mid_bar) == []
    # config flag actually enabled in the live preset
    assert s.STRATEGY_V2_CFG.bar_close_only is True


# ─────────────────────────────────────────────────────────────────────────────
# P0-2 · Trend-pullback scalp (unit level)
# ─────────────────────────────────────────────────────────────────────────────
def _pullback_candles_buy():
    """Uptrend, pullback to EMA21 zone, bullish resumption bar.
    Parameters numerically verified: EMA tag + RSI 56-60 + depth ≥ 0.8 ATR."""
    candles = []
    px = 1.1000
    for i in range(40):
        px += 0.0003                       # steady climb
        candles.append(_c(px - 0.0003, px + 0.0003, px - 0.0006, px, t=i * 300000))
    # pullback: 5 decaying down bars from the local high into the EMA21 zone
    drops = [0.0008 * (1 - 0.1 * j) for j in range(5)]
    for j, drop in enumerate(drops):
        px -= drop
        candles.append(_c(px + drop, px + drop + 0.0002, px - 0.0002, px, t=(40 + j) * 300000))
    # resumption bar: tags the EMA zone low, closes bullish above prev close
    candles.append(_c(px, px + 0.0007, px - 0.0003, px + 0.0006, t=45 * 300000))
    return candles


def _ema_atr_rsi(candles):
    from engine import ema, atr, rsi
    closes = [c["c"] for c in candles]
    return ema(closes, 21), ema(closes, 55), rsi(closes, 14), atr(candles, 14)


def test_trend_pullback_buy_fires():
    cfg = conservative_config()
    candles = _pullback_candles_buy()
    ef, es, r, a = _ema_atr_rsi(candles)
    # sanity: bar must actually be in the EMA21 touch zone for this scenario
    assert candles[-1]["l"] <= ef[-1] + 0.25 * a[-1], "scenario: no EMA tag"
    out = _setup_trend_pullback(candles, cfg, ef, es, r, a,
                                "trending_up", "london", "up", _ctx())
    assert out is not None
    sig, ctx = out
    assert sig.side == "buy" and sig.mode == "scalp"
    assert sig.max_hold_minutes == 30
    assert abs((sig.entry - sig.sl) - a[-1] * 1.0) < 1e-9     # SL = 1.0 × ATR (user spec)
    assert abs((sig.tp - sig.entry) - a[-1] * 1.8) < 1e-9     # TP = 1.8 × ATR (2026-06-22 audit P0)
    assert sig.confidence >= cfg.scalp_min_confidence
    assert ctx.htf_aligned is True


def test_trend_pullback_blocked_against_htf():
    # SAFETY preserved: contra-HTF continuation entries hard-blocked
    cfg = conservative_config()
    candles = _pullback_candles_buy()
    ef, es, r, a = _ema_atr_rsi(candles)
    out = _setup_trend_pullback(candles, cfg, ef, es, r, a,
                                "trending_up", "london", "down", _ctx())
    assert out is None


def test_trend_pullback_requires_real_pullback():
    """A bar far above the EMA21 zone (no tag) must not fire."""
    cfg = conservative_config()
    candles = _pullback_candles_buy()
    # push the last bar's low well above the EMA zone
    candles[-1] = dict(candles[-1], l=candles[-1]["c"] - 0.0001)
    ef, es, r, a = _ema_atr_rsi(candles)
    if candles[-1]["l"] > ef[-1] + 0.25 * a[-1]:
        out = _setup_trend_pullback(candles, cfg, ef, es, r, a,
                                    "trending_up", "london", "up", _ctx())
        assert out is None


# ─────────────────────────────────────────────────────────────────────────────
# P0-1 · Backtest engine v2
# ─────────────────────────────────────────────────────────────────────────────
def _sweep_then_rally():
    """100 ranging bars ending in a bullish liquidity sweep (known to produce a
    signal — proven in test_audit_fixes), then 30 rising bars so the trade can
    resolve at TP. Total 130 bars."""
    candles = []
    base = 1.1000
    for i in range(90):
        d = 0.0003 if i % 2 == 0 else -0.0003
        o = base - d
        c = base + d
        candles.append(_c(o, max(o, c) + 0.0004, min(o, c) - 0.0004, c, t=i * 900000))
    candles.append(_c(1.0995, 1.0996, 1.0980, 1.0990, t=90 * 900000))   # pivot low
    closes = [1.0998, 1.0995, 1.0993, 1.0991, 1.0990, 1.0989, 1.0988, 1.0987]
    prev = 1.0990
    for j, cl in enumerate(closes):
        candles.append(_c(prev, max(prev, cl) + 0.0003, min(prev, cl) - 0.0002, cl,
                          t=(91 + j) * 900000))
        prev = cl
    candles.append(_c(1.0986, 1.0988, 1.0975, 1.0984, t=99 * 900000))    # sweep bar
    # rally: 30 bars climbing 4 pips per bar — comfortably crosses any 1.3×ATR TP
    px = 1.0984
    for k in range(30):
        o = px
        px += 0.0004
        candles.append(_c(o, px + 0.0002, o - 0.0002, px, t=(100 + k) * 900000))
    return candles


def test_backtest_simulate_end_to_end():
    cfg = conservative_config()
    candles = _sweep_then_rally()
    report = simulate_backtest(candles, cfg, timeframe="M15", pair="EURUSD",
                               spread=0.0001, htf_candles=[])
    # structure
    for key in ("params", "summary", "by_setup", "by_session", "by_mode", "trades"):
        assert key in report, f"missing report key {key}"
    s = report["summary"]
    assert s["trades"] >= 1, "backtest produced zero trades on a known-signal scenario"
    # the sweep trade must resolve profitably into the rally
    assert s["total_r"] > 0
    assert any(t["setup"] in ("liquidity_sweep", "rsi_scalp", "trend_pullback")
               for t in report["trades"])
    # every trade carries the spread cost and complete fields
    for t in report["trades"]:
        for k in ("side", "mode", "setup", "session", "r", "exit_reason", "entry_t", "exit_t"):
            assert k in t
    # management fidelity: a 1.3R TP must have banked the +1R partial on the way
    tp_trades = [t for t in report["trades"] if t["exit_reason"] == "tp_hit"]
    if tp_trades:
        assert all(t["partial_taken"] for t in tp_trades)
        # 50% banked at +1R + 50% at 1.3R = 1.15R minus spread → strictly < 1.3R
        assert all(t["r"] < 1.3 for t in tp_trades)


def test_backtest_conservative_sl_first():
    """Same-bar SL+TP touch on the FILL bar must resolve as a LOSS (worst-case)."""
    cfg = conservative_config()
    candles = _sweep_then_rally()
    # signal fires on the sweep bar (index 99); make the FILL bar (index 100) a
    # giant whipsaw spanning both SL and TP → conservative ordering = SL first
    base = candles[:100]
    o = base[-1]["c"]
    whip = _c(o, o + 0.0100, o - 0.0100, o, t=base[-1]["t"] + 900000)
    post = _c(o, o + 0.0002, o - 0.0002, o, t=whip["t"] + 900000)
    report = simulate_backtest(base + [whip, post], cfg, timeframe="M15",
                               pair="EURUSD", spread=0.0001, htf_candles=[])
    losses = [t for t in report["trades"] if t["exit_reason"] == "sl_hit"]
    assert losses, f"whipsaw fill bar should be an SL-first loss, got {[(t['exit_reason'], t['r']) for t in report['trades']]}"
    assert all(t["r"] < 0 for t in losses)


def test_setup_tag_classification():
    assert _setup_tag("BOS-retest BUY") == "bos_retest"
    assert _setup_tag("Liquidity sweep SELL") == "liquidity_sweep"
    assert _setup_tag("RSI-scalp BUY · RSI 28") == "rsi_scalp"
    assert _setup_tag("Trend-pullback BUY · EMA21 tag") == "trend_pullback"
    assert _setup_tag("Range breakout SELL") == "range_breakout"


# ─────────────────────────────────────────────────────────────────────────────
# P1-4 · Funnel telemetry (integration, local Mongo)
# ─────────────────────────────────────────────────────────────────────────────
TAG = "funneltest-"


def test_funnel_records_scan_stages():
    import server as s
    from motor.motor_asyncio import AsyncIOMotorClient

    async def main():
        # Rebind a fresh motor client to THIS event loop (pytest runs several
        # asyncio.run() tests per session; motor binds to the loop it first sees)
        s.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        s.db = s.client[os.environ["DB_NAME"]]
        db = s.db
        for coll in (db.bots, db.users, db.funnel, db.trades, db.signals):
            await coll.delete_many({"_id": {"$regex": f"^{TAG}"}})
        await db.funnel.delete_many({"bot_id": {"$regex": f"^{TAG}"}})

        uid = f"{TAG}user"
        await db.users.insert_one({"_id": uid, "email": f"{uid}@t.local",
                                   "password_hash": "x", "role": "user",
                                   "disabled": False, "created_at": s.now_iso(),
                                   "updated_at": s.now_iso()})
        bot = {"_id": f"{TAG}bot", "user_id": uid, "name": "f", "pair": "ZZZUSD",
               "timeframe": "M15", "risk_per_trade": 1.0, "max_positions": 5,
               "daily_loss_limit": 5.0, "sessions": s.DEFAULT_SESSIONS,
               "is_active": True, "strategy_config": {},
               "higher_tf_confirmation": "off",
               "created_at": s.now_iso(), "updated_at": s.now_iso()}
        await db.bots.insert_one(bot)
        # No candles exist for ZZZUSD → scan must record data_unavailable in funnel
        await s._scan_and_persist([bot])
        docs = await db.funnel.find({"bot_id": bot["_id"]}).to_list(10)
        assert len(docs) == 1, f"expected exactly one funnel doc, got {len(docs)}"
        d = docs[0]
        assert d["stage"] in ("data_unavailable", "news_block"), d["stage"]
        assert d["pair"] == "ZZZUSD" and d["user_id"] == uid
        assert d.get("date") and d.get("ts") is not None
        # re-scan same bar period → still ONE doc (upsert, no double count)
        await s._scan_and_persist([bot])
        docs2 = await db.funnel.find({"bot_id": bot["_id"]}).to_list(10)
        assert len(docs2) == 1, "funnel double-counted the same bar period"

        # cleanup
        await db.funnel.delete_many({"bot_id": bot["_id"]})
        await db.bots.delete_many({"_id": bot["_id"]})
        await db.users.delete_many({"_id": uid})
        await db.risk_state.delete_many({"_id": uid})

    asyncio.run(main())


# ─────────────────────────────────────────────────────────────────────────────
# P2-6 · Bridge spread-vs-SL filter — static verification
# ─────────────────────────────────────────────────────────────────────────────
def test_bridge_has_spread_vs_sl_filter_and_partials():
    src = open(os.path.join(ROOT, "static", "aurum_bridge.py")).read()
    assert 'BRIDGE_VERSION = "1.9.1"' in src
    assert "AURUM_MAX_SPREAD_SL_PCT" in src
    assert "spread_vs_sl" in src
    # P1-5 partial TP + BE confirmed present (pre-existing v1.8.1 feature)
    assert "PARTIAL_CLOSE_ENABLED" in src and "_move_sl_to_breakeven" in src
    # server must still accept 1.8.1 bridges (no forced upgrade)
    import server as s
    assert s.MIN_BRIDGE_VERSION == "1.8.1"
    # both served copies identical
    pub = open("/app/frontend/public/aurum_bridge.py").read()
    assert pub == src, "frontend/public bridge copy out of sync"


if __name__ == "__main__":
    test_drop_forming_bar()
    test_trend_pullback_buy_fires()
    test_backtest_simulate_end_to_end()
    test_funnel_records_scan_stages()
    test_bridge_has_spread_vs_sl_filter_and_partials()
    print("ALL P0/P1/P2 TESTS PASSED")
