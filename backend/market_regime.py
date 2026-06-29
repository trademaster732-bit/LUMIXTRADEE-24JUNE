"""Module 1 — Market Regime Detection (2026-01).

Classifies the current market into one of six regimes so that per-regime
configuration can adjust gates downstream:

    strong_trend   weak_trend   range   breakout   high_volatility   low_volatility

The classifier is deterministic, priority-ordered, and operates entirely on
data already computed upstream (candles, EMAs, ADX-via-quality_score, ATR
series). No new market-data fetches.

Configuration lives under engine_config.market_regime:

    {
      "enabled": true,
      "regimes": {
        "<regime_name>": {
          "enabled": bool,
          "min_score": int,              # overrides quality_score threshold when active
          "entry_aggressiveness": "high" | "medium" | "low",
          "preferred_confirmation": ["engulfing","pin","momentum","break","any"]
        },
        ...
      },
      "symbol_preferences": {            # whitelist; empty = all enabled regimes allowed
        "EURUSD": ["strong_trend","weak_trend","breakout"],
        "XAUUSD": ["breakout","strong_trend","high_volatility"],
        ...
      }
    }

The orchestrator returns a `RegimeDecision` that the caller uses to:
  • Gate (reject when regime disabled or not in symbol's preferred list)
  • Override the effective `min_score` for the active regime
  • Translate `entry_aggressiveness` into a multiplier for Phase-2 Module 1
  • Filter Phase-2 Module 4 candle patterns against `preferred_confirmation`

This module DOES NOT touch SL/TP/sizing/cooldowns/session logic.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple


# All recognised regime names — keep this list in sync with the default config.
REGIME_NAMES: Tuple[str, ...] = (
    "strong_trend",
    "weak_trend",
    "range",
    "breakout",
    "high_volatility",
    "low_volatility",
)

# Aggressiveness multiplier applied to Phase-2 `min_entry_confirmation_score`.
# >1.0 = stricter (raises the bar); <1.0 = more permissive (lowers the bar).
AGGRESSIVENESS_MULTIPLIER: Dict[str, float] = {
    "high": 0.7,        # high aggressiveness ⇒ easier to take entries
    "medium": 1.0,
    "low": 1.3,         # low aggressiveness  ⇒ stricter entries
}


# ─────────────────────────────────────────────────────────────────────────────
# Result container
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class RegimeDecision:
    regime: str = "range"               # primary classification
    confidence: int = 0                 # 0-100 (classifier conviction)
    reasons: List[str] = field(default_factory=list)
    # Effective config snapshot for this regime + symbol (resolved at orchestrator time)
    regime_enabled: bool = True
    regime_min_score: Optional[int] = None
    entry_aggressiveness: str = "medium"
    confirmation_multiplier: float = 1.0
    preferred_confirmation: List[str] = field(default_factory=list)
    symbol_preferred: bool = True       # symbol's preference whitelist passed (or no whitelist)
    # Hard-gate result + reason
    passed: bool = True
    rejection_reason: Optional[str] = None
    # Indicators captured for diagnostics
    adx: float = 0.0
    adx_slope: float = 0.0
    atr_ratio: float = 1.0
    ema_separation_atr: float = 0.0     # |EMAf - EMAs| / ATR

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ─────────────────────────────────────────────────────────────────────────────
# Classifier
# ─────────────────────────────────────────────────────────────────────────────
def classify_market_regime(
    candles: List[Dict[str, float]],
    ema_fast: List[float],
    ema_slow: List[float],
    adx_vals: List[float],
    atr_arr: List[float],
) -> RegimeDecision:
    """Pure classifier — no config, no gating. Returns regime + confidence."""
    d = RegimeDecision()
    if not candles or not atr_arr or not adx_vals or not ema_fast or not ema_slow:
        d.regime = "range"
        d.confidence = 0
        d.reasons.append("insufficient data — defaulting to 'range'")
        return d

    adx_now = adx_vals[-1]
    d.adx = round(adx_now, 2)
    atr_now = atr_arr[-1]
    # Median ATR over last 50 bars for ratio calc (same approach as quality_score)
    med_list = sorted([x for x in atr_arr[-50:] if x > 0])
    atr_med = med_list[len(med_list) // 2] if med_list else atr_now
    atr_ratio = (atr_now / atr_med) if atr_med > 0 else 1.0
    d.atr_ratio = round(atr_ratio, 3)

    # 5-bar ADX slope (rising trend = healthier)
    if len(adx_vals) >= 10:
        adx_last5 = sum(adx_vals[-5:]) / 5.0
        adx_prev5 = sum(adx_vals[-10:-5]) / 5.0
        d.adx_slope = round(adx_last5 - adx_prev5, 3)

    # EMA fast/slow separation in ATR units
    ema_sep = abs(ema_fast[-1] - ema_slow[-1]) / atr_now if atr_now > 0 else 0.0
    d.ema_separation_atr = round(ema_sep, 3)

    # 20-bar range break detection
    last_close = candles[-1]["c"]
    win = candles[-20:] if len(candles) >= 20 else candles
    high20 = max(c["h"] for c in win)
    low20 = min(c["l"] for c in win)

    # ─── Priority order ─────────────────────────────────────────────────────
    # 1. Breakout — recent ATR expansion + close beyond the 20-bar range.
    breakout_up = last_close > high20 * 0.9995 and atr_ratio > 1.20
    breakout_dn = last_close < low20 * 1.0005 and atr_ratio > 1.20
    if breakout_up or breakout_dn:
        d.regime = "breakout"
        d.confidence = int(min(100, 40 + atr_ratio * 30))
        d.reasons.append(
            f"close {last_close:.5f} {'>' if breakout_up else '<'} "
            f"20b {'high' if breakout_up else 'low'} "
            f"{(high20 if breakout_up else low20):.5f} · "
            f"atr_ratio {atr_ratio:.2f} > 1.20"
        )
        return d

    # 2. High volatility — wide ATR but no clean breakout
    if atr_ratio > 1.50:
        d.regime = "high_volatility"
        d.confidence = int(min(100, atr_ratio * 50))
        d.reasons.append(f"atr_ratio {atr_ratio:.2f} > 1.50 (no clean breakout)")
        return d

    # 3. Low volatility — compressed market
    if atr_ratio < 0.70:
        d.regime = "low_volatility"
        d.confidence = int(min(100, (1.0 - atr_ratio) * 200))
        d.reasons.append(f"atr_ratio {atr_ratio:.2f} < 0.70 (compressed)")
        return d

    # 4. Strong trend — high ADX, EMAs separated, healthy slope
    if adx_now >= 30 and ema_sep >= 1.5 and d.adx_slope >= -1.0:
        d.regime = "strong_trend"
        d.confidence = int(min(100, adx_now * 2))
        d.reasons.append(
            f"ADX {adx_now:.1f} ≥ 30 · EMA-sep {ema_sep:.2f}×ATR ≥ 1.5 · "
            f"adx_slope {d.adx_slope:+.2f}"
        )
        return d

    # 5. Weak trend — moderate ADX, some EMA separation
    if 18.0 <= adx_now < 30.0 and ema_sep >= 0.5:
        d.regime = "weak_trend"
        d.confidence = int(min(100, adx_now * 3))
        d.reasons.append(
            f"ADX {adx_now:.1f} ∈ [18,30) · EMA-sep {ema_sep:.2f}×ATR ≥ 0.5"
        )
        return d

    # 6. Default = range
    d.regime = "range"
    d.confidence = int(min(100, 100 - adx_now * 3))
    d.reasons.append(
        f"ADX {adx_now:.1f} < 18 or EMA-sep {ema_sep:.2f}×ATR < 0.5 — default range"
    )
    return d


# ─────────────────────────────────────────────────────────────────────────────
# Orchestrator — applies symbol-aware config + gating
# ─────────────────────────────────────────────────────────────────────────────
def evaluate_market_regime(
    *,
    symbol: str,
    candles: List[Dict[str, float]],
    ema_fast: List[float],
    ema_slow: List[float],
    adx_vals: List[float],
    atr_arr: List[float],
    mr_cfg: Dict[str, Any],
) -> RegimeDecision:
    """Classify + apply config + decide gate. Always returns a RegimeDecision
    (even on hard-gate failure) so the caller can persist diagnostics.

    If `mr_cfg.enabled` is False, the classifier still runs (for telemetry) but
    no gate is applied. `passed=True` is returned in that case.
    """
    d = classify_market_regime(candles, ema_fast, ema_slow, adx_vals, atr_arr)

    # Engine globally disabled → diagnostics-only pass-through.
    if not mr_cfg or not mr_cfg.get("enabled", True):
        d.reasons.append("market_regime engine disabled — pass-through")
        return d

    regimes_cfg = mr_cfg.get("regimes") or {}
    reg_cfg = regimes_cfg.get(d.regime) or {}

    # Resolve per-regime settings
    d.regime_enabled = bool(reg_cfg.get("enabled", True))
    if "min_score" in reg_cfg:
        try:
            d.regime_min_score = int(reg_cfg["min_score"])
        except (TypeError, ValueError):
            d.regime_min_score = None
    d.entry_aggressiveness = str(reg_cfg.get("entry_aggressiveness", "medium")).lower()
    d.confirmation_multiplier = AGGRESSIVENESS_MULTIPLIER.get(d.entry_aggressiveness, 1.0)
    d.preferred_confirmation = list(reg_cfg.get("preferred_confirmation") or [])

    # Symbol preference whitelist (empty list / missing key = no restriction)
    sym_prefs = (mr_cfg.get("symbol_preferences") or {}).get((symbol or "").upper()) or []
    if sym_prefs and d.regime not in sym_prefs:
        d.symbol_preferred = False
        d.passed = False
        d.rejection_reason = (
            f"regime_not_preferred:{symbol.upper()}:{d.regime} "
            f"(prefers {','.join(sym_prefs)})"
        )
        return d

    # Hard gate: regime disabled in config
    if not d.regime_enabled:
        d.passed = False
        d.rejection_reason = f"regime_disabled:{d.regime}"
        return d

    d.passed = True
    return d


def regime_allows_candle_pattern(
    decision: RegimeDecision, candle_pattern: Optional[str]
) -> bool:
    """True if regime's preferred_confirmation list allows the detected pattern.

    Empty / missing list ⇒ no restriction. "any" in the list ⇒ no restriction.
    Otherwise the pattern's family must be in the list. Pattern family is the
    suffix after the side prefix, e.g. "bullish_engulfing" → "engulfing".
    """
    prefs = [p.lower() for p in (decision.preferred_confirmation or [])]
    if not prefs or "any" in prefs:
        return True
    if not candle_pattern:
        return False
    # Map full pattern names → families
    family_map = {
        "bullish_engulfing": "engulfing", "bearish_engulfing": "engulfing",
        "bullish_pin": "pin", "bearish_pin": "pin",
        "bullish_momentum": "momentum", "bearish_momentum": "momentum",
        "break_high": "break", "break_low": "break",
    }
    fam = family_map.get(candle_pattern.lower(), candle_pattern.lower())
    return fam in prefs
