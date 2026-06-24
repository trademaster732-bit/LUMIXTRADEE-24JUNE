"""Regression tests for the 2026-06 audit fixes (P0/P1/P2).

P0-1: daily_loss_limit interpreted as % of equity (flat-USD fallback when no equity)
P0-2: scalp activation — RSI mean-reversion scalp fires in ranging/compression under v2
P0-3: FIX #3b no longer blocks liquidity-sweep entries (pierce + close-back-inside)
P1-4: S/R proximity gate is ATR-scaled (0.5 × ATR)
P1-5: Asia session enabled — 21:00-24:00 UTC counts as asia; DEFAULT_SESSIONS has asia
P2-6: scanner HTF gate treats flat/unknown as neutral (tested via gate logic in scan)
P2-7: scalp RR floor 1.3 / max hold 30 min

Pure-function tests run without DB. The daily-loss test uses the live local Mongo
(same pattern as test_risk_gates.py).
"""
import os
import sys
import asyncio
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
load_dotenv(os.path.join(ROOT, ".env"))

from engine import current_session, rsi, atr, GeneratedSignal  # noqa: E402
from strategy_v2 import (  # noqa: E402
    conservative_config, generate_signal_v2, SignalContext,
    _setup_rsi_scalp, _sr_check, _enforce_min_rr,
)

UTC = timezone.utc


def _c(o, h, l, c, t=0):
    return {"t": t, "o": o, "h": h, "l": l, "c": c}


def _ctx(session="london", regime="ranging"):
    return SignalContext(
        regime=regime, session=session, atr=0.001, atr_ratio=1.0,
        swing_high=None, swing_low=None, bos=None, fvg=None,
        sweep=None, displacement=None, squeeze=False, htf_aligned=None,
    )


# ─────────────────────────────────────────────────────────────────────────────
# P1-5 · Session map: 21-24 UTC = asia, no daily dead zone
# ─────────────────────────────────────────────────────────────────────────────
def test_session_asia_evening_hours():
    assert current_session(datetime(2026, 6, 15, 21, 30, tzinfo=UTC)) == "asia"
    assert current_session(datetime(2026, 6, 15, 23, 0, tzinfo=UTC)) == "asia"
    assert current_session(datetime(2026, 6, 15, 2, 0, tzinfo=UTC)) == "asia"
    # unchanged sessions
    assert current_session(datetime(2026, 6, 15, 8, 0, tzinfo=UTC)) == "london"
    assert current_session(datetime(2026, 6, 15, 13, 30, tzinfo=UTC)) == "overlap"
    assert current_session(datetime(2026, 6, 15, 18, 0, tzinfo=UTC)) == "new_york"


def test_default_sessions_include_asia():
    import server as s
    assert "asia" in s.DEFAULT_SESSIONS


# ─────────────────────────────────────────────────────────────────────────────
# P2-7 · Scalp config: TP 1.3×ATR, hold 30 min, RR floor 1.3 — safety kept
# ─────────────────────────────────────────────────────────────────────────────
def test_conservative_config_scalp_values_and_safety_preserved():
    cfg = conservative_config()
    assert cfg.scalp_tp_atr == 1.8
    assert cfg.max_hold_minutes_scalp == 30
    # safety features explicitly preserved per user instruction
    assert cfg.require_displacement is True
    assert cfg.require_htf_alignment is True


def test_enforce_min_rr_scalp_floor_1_3():
    sig = GeneratedSignal(side="buy", entry=1.1000, sl=1.0990, tp=1.1005,
                          confidence=0.6, regime="ranging", session="london",
                          reason="t", mode="scalp", max_hold_minutes=30)
    out = _enforce_min_rr(sig, min_rr=1.3)
    assert abs(out.tp - 1.1013) < 1e-9  # 0.0010 SL × 1.3
    # swing keeps 2.0
    sig2 = GeneratedSignal(side="sell", entry=1.1000, sl=1.1010, tp=1.0995,
                           confidence=0.6, regime="trending_down", session="london",
                           reason="t", mode="swing", max_hold_minutes=0)
    out2 = _enforce_min_rr(sig2, min_rr=2.0)
    assert abs(out2.tp - 1.0980) < 1e-9


# ─────────────────────────────────────────────────────────────────────────────
# P1-4 · S/R gate ATR-scaled
# ─────────────────────────────────────────────────────────────────────────────
def test_sr_check_atr_scaled_allows_what_fixed_pct_blocked():
    # 22 bars, range 1.1000-1.1100, last close 1.1075 → 0.0025 from resistance.
    candles = [_c(1.1050, 1.1100, 1.1000, 1.1050) for _ in range(21)]
    candles.append(_c(1.1070, 1.1080, 1.1060, 1.1075))
    r_vals = [50.0] * len(candles)
    # old behavior (no ATR → % fallback): 0.0025/1.1075 = 0.23% < 0.3% → block
    legacy = _sr_check(candles, r_vals, "buy", last_close=1.1075, atr_v=0.0)
    assert legacy["action"] == "block"
    # new behavior: 0.5 × ATR(0.001) = 0.0005 < 0.0025 → ok
    scaled = _sr_check(candles, r_vals, "buy", last_close=1.1075, atr_v=0.001)
    assert scaled["action"] == "ok"
    # still blocks genuinely-near entries: close 1.1098, 0.0002 from resistance
    near = _sr_check(candles, r_vals, "buy", last_close=1.1098, atr_v=0.001)
    assert near["action"] == "block"


# ─────────────────────────────────────────────────────────────────────────────
# P0-2 · RSI mean-reversion scalp (unit level)
# ─────────────────────────────────────────────────────────────────────────────
def test_rsi_scalp_buy_fires():
    cfg = conservative_config()
    candles = [_c(1.1000, 1.1006, 1.0994, 1.0990), _c(1.0990, 1.1002, 1.0988, 1.1000)]
    r_vals = [50.0, 28.0]
    a_vals = [0.001, 0.001]
    out = _setup_rsi_scalp(candles, cfg, r_vals, a_vals, "ranging", "london", None, _ctx())
    assert out is not None
    sig, _ = out
    assert sig.side == "buy" and sig.mode == "scalp"
    assert sig.max_hold_minutes == 30
    assert abs((sig.entry - sig.sl) - 0.001) < 1e-9          # SL = 1.0 × ATR
    assert abs((sig.tp - sig.entry) - 0.0018) < 1e-9         # TP = 1.8 × ATR (2026-06-22 audit P0)
    assert sig.confidence >= cfg.scalp_min_confidence


def test_rsi_scalp_fires_in_asia():
    cfg = conservative_config()
    candles = [_c(1.1000, 1.1006, 1.0994, 1.0990), _c(1.0990, 1.1002, 1.0988, 1.1000)]
    out = _setup_rsi_scalp(candles, cfg, [50.0, 28.0], [0.001, 0.001],
                           "ranging", "asia", None, _ctx(session="asia"))
    assert out is not None  # asia penalty (-0.02) must not kill a clean setup


def test_rsi_scalp_blocked_against_decisive_htf():
    # SAFETY preserved: require_htf_alignment still hard-blocks contra-HTF scalps
    cfg = conservative_config()
    candles = [_c(1.1000, 1.1006, 1.0994, 1.0990), _c(1.0990, 1.1002, 1.0988, 1.1000)]
    out = _setup_rsi_scalp(candles, cfg, [50.0, 28.0], [0.001, 0.001],
                           "ranging", "london", "down", _ctx())
    assert out is None


def test_rsi_scalp_htf_flat_is_neutral():
    cfg = conservative_config()
    candles = [_c(1.1000, 1.1006, 1.0994, 1.0990), _c(1.0990, 1.1002, 1.0988, 1.1000)]
    out = _setup_rsi_scalp(candles, cfg, [50.0, 28.0], [0.001, 0.001],
                           "ranging", "london", "flat", _ctx())
    assert out is not None


def test_rsi_scalp_no_signal_midrange_rsi():
    cfg = conservative_config()
    candles = [_c(1.1000, 1.1006, 1.0994, 1.0990), _c(1.0990, 1.1002, 1.0988, 1.1000)]
    out = _setup_rsi_scalp(candles, cfg, [50.0, 50.0], [0.001, 0.001],
                           "ranging", "london", None, _ctx())
    assert out is None


# ─────────────────────────────────────────────────────────────────────────────
# P0-3 · End-to-end: a liquidity sweep survives FIX #3b (pierce + close back in)
# ─────────────────────────────────────────────────────────────────────────────
def _sweep_scenario():
    """100 ranging bars; pivot low 1.0980 at idx 90; last bar pierces to 1.0975
    and closes back at 1.0984 (> 1.0980). RSI depressed by a slow drift down."""
    candles = []
    base = 1.1000
    for i in range(90):
        d = 0.0003 if i % 2 == 0 else -0.0003
        o = base - d
        c = base + d
        candles.append(_c(o, max(o, c) + 0.0004, min(o, c) - 0.0004, c, t=i * 900000))
    # pivot low bar
    candles.append(_c(1.0995, 1.0996, 1.0980, 1.0990, t=90 * 900000))
    # slow drift down (depress RSI, keep lows above the pivot)
    closes = [1.0998, 1.0995, 1.0993, 1.0991, 1.0990, 1.0989, 1.0988, 1.0987]
    prev = 1.0990
    for j, cl in enumerate(closes):
        candles.append(_c(prev, max(prev, cl) + 0.0003, min(prev, cl) - 0.0002, cl,
                          t=(91 + j) * 900000))
        prev = cl
    # sweep bar: pierces 1.0980, closes back above it
    candles.append(_c(1.0986, 1.0988, 1.0975, 1.0984, t=99 * 900000))
    return candles


def test_sweep_signal_survives_fix3b():
    cfg = conservative_config()
    candles = _sweep_scenario()
    # sanity: RSI must be in the sweep-buy window (≤ 45)
    r_vals = rsi([c["c"] for c in candles], 14)
    assert r_vals[-1] <= 45, f"scenario RSI too high: {r_vals[-1]:.1f}"
    out = generate_signal_v2(candles, cfg, htf_trend=None, session_override="london")
    assert out is not None, "sweep setup was blocked — FIX #3b regression"
    sig, ctx = out
    assert sig.side == "buy"
    assert sig.mode == "scalp"
    assert "sweep" in sig.reason.lower()
    # RR floor: scalp ≥ 1.3
    rr = abs(sig.tp - sig.entry) / abs(sig.entry - sig.sl)
    assert rr >= 1.299


def test_true_breakdown_still_blocked():
    """SAFETY preserved: a bar that breaks support AND CLOSES below it must still
    be rejected for BUYs (the original June-8 protection)."""
    cfg = conservative_config()
    candles = _sweep_scenario()
    # mutate last bar: closes BELOW the broken level (true breakdown, not a sweep)
    candles[-1] = _c(1.0986, 1.0988, 1.0970, 1.0976, t=99 * 900000)
    out = generate_signal_v2(candles, cfg, htf_trend=None, session_override="london")
    if out is not None:
        sig, _ = out
        assert sig.side != "buy", "BUY fired on a close below broken support"


# ─────────────────────────────────────────────────────────────────────────────
# P0-1 · Daily loss limit = % of equity (integration, local Mongo)
# ─────────────────────────────────────────────────────────────────────────────
TEST_TAG = "auditfix-test-"


def test_daily_loss_limit_percent_of_equity():
    import server as s
    from motor.motor_asyncio import AsyncIOMotorClient

    async def main():
        # Rebind a fresh motor client to THIS event loop (pytest runs several
        # asyncio.run() tests per session; motor binds to the loop it first sees)
        s.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        s.db = s.client[os.environ["DB_NAME"]]
        db = s.db
        # cleanup
        for coll in (db.bots, db.trades, db.signals, db.users, db.mt5_accounts, db.risk_state):
            await coll.delete_many({"_id": {"$regex": f"^{TEST_TAG}"}})
        await db.risk_state.delete_many({"_id": {"$regex": f"^{TEST_TAG}"}})

        uid = f"{TEST_TAG}user"
        await db.users.insert_one({"_id": uid, "email": f"{uid}@t.local",
                                   "password_hash": "x", "role": "user", "disabled": False,
                                   "created_at": s.now_iso(), "updated_at": s.now_iso()})
        # equity $1,000 → 5% limit = $50
        await db.mt5_accounts.insert_one({"_id": f"{TEST_TAG}acct", "user_id": uid,
                                          "login": 1, "equity": 1000.0,
                                          "updated_at": s.now_iso()})

        def bot_doc(suffix):
            return {"_id": f"{TEST_TAG}bot-{suffix}", "user_id": uid,
                    "name": suffix, "pair": "EURUSD", "timeframe": "M15",
                    "risk_per_trade": 1.0, "max_positions": 5,
                    "daily_loss_limit": 5.0, "sessions": s.DEFAULT_SESSIONS,
                    "is_active": True, "strategy_config": {},
                    "higher_tf_confirmation": "off",
                    "created_at": s.now_iso(), "updated_at": s.now_iso()}

        def loss_trade(bot_id, pnl, n):
            return {"_id": f"{TEST_TAG}tr-{bot_id}-{n}", "user_id": uid,
                    "bot_id": bot_id, "pair": "EURUSD", "side": "buy",
                    "status": "closed", "pnl": pnl,
                    "closed_at": s.now_iso(), "opened_at": s.now_iso()}

        # Case A: -$10 on $1k (1%) — OLD flat-$5 logic would block; NEW 5%=-$50 must NOT
        bot_a = bot_doc("a")
        await db.bots.insert_one(bot_a)
        await db.trades.insert_one(loss_trade(bot_a["_id"], -10.0, 1))
        await s._scan_and_persist([bot_a])
        doc_a = await db.bots.find_one({"_id": bot_a["_id"]})
        res_a = doc_a.get("last_scan_result") or ""
        assert not res_a.startswith("daily_loss_blocked"), f"Case A wrongly blocked: {res_a}"

        # Case B: -$60 on $1k (6%) — must block, and show the % in the reason
        bot_b = bot_doc("b")
        await db.bots.insert_one(bot_b)
        await db.trades.insert_one(loss_trade(bot_b["_id"], -60.0, 1))
        await s._scan_and_persist([bot_b])
        doc_b = await db.bots.find_one({"_id": bot_b["_id"]})
        res_b = doc_b.get("last_scan_result") or ""
        assert res_b.startswith("daily_loss_blocked"), f"Case B not blocked: {res_b}"
        assert "(5%)" in res_b and "-50.00" in res_b, f"limit not 5% of equity: {res_b}"

        # Case C: no equity reported → flat-USD fallback: -$6 vs limit $5 → blocked
        uid2 = f"{TEST_TAG}user2"
        await db.users.insert_one({"_id": uid2, "email": f"{uid2}@t.local",
                                   "password_hash": "x", "role": "user", "disabled": False,
                                   "created_at": s.now_iso(), "updated_at": s.now_iso()})
        bot_c = bot_doc("c")
        bot_c["_id"] = f"{TEST_TAG}bot-c"
        bot_c["user_id"] = uid2
        await db.bots.insert_one(bot_c)
        await db.trades.insert_one({**loss_trade(bot_c["_id"], -6.0, 1), "user_id": uid2})
        await s._scan_and_persist([bot_c])
        doc_c = await db.bots.find_one({"_id": bot_c["_id"]})
        res_c = doc_c.get("last_scan_result") or ""
        assert res_c.startswith("daily_loss_blocked"), f"Case C fallback failed: {res_c}"

        # cleanup
        for coll in (db.bots, db.trades, db.signals, db.users, db.mt5_accounts):
            await coll.delete_many({"_id": {"$regex": f"^{TEST_TAG}"}})
        await db.risk_state.delete_many({"_id": uid})
        await db.risk_state.delete_many({"_id": uid2})

    asyncio.run(main())


if __name__ == "__main__":
    test_session_asia_evening_hours()
    test_conservative_config_scalp_values_and_safety_preserved()
    test_sr_check_atr_scaled_allows_what_fixed_pct_blocked()
    test_rsi_scalp_buy_fires()
    test_sweep_signal_survives_fix3b()
    test_daily_loss_limit_percent_of_equity()
    print("ALL AUDIT-FIX TESTS PASSED")
