"""Module 4 — Professional Stop Loss Engine (2026-01).

Replaces the strategy-computed `sig.sl` with a priority-ordered SL chooser
plus volatility-aware modifiers, and emits a post-trade management payload
(break-even + trailing) for the bridge to consume.

Eight sub-features (admin toggles each independently):
  • ATR SL          — entry ± atr_multiplier × ATR
  • Swing SL        — beyond the nearest swing low/high + buffer
  • Structure SL    — beyond the last structural swing on the signal TF
  • Volatility Buffer — extra cushion proportional to ATR ratio
  • Dynamic SL Expansion — widen SL during high-volatility regimes
  • SL Tightening   — tighten SL during low-volatility regimes (or per regime)
  • Break-even      — move SL to entry once X×RR is reached (bridge-side exec)
  • Trailing Stop   — trail SL at Y×ATR once Z×RR reached (bridge-side exec)

The orchestrator returns an `AdaptiveSLResult` with the primary SL, all
modifier impacts, and an `sl_management{}` payload. SL distance is bounded
to [min_sl_atr, max_sl_atr] × ATR to prevent degenerate SLs.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple


KNOWN_LEVEL_STRATEGIES: Tuple[str, ...] = ("structure", "swing", "atr")


@dataclass
class AdaptiveSLResult:
    enabled: bool = True
    primary_sl: Optional[float] = None              # final SL price (None ⇒ keep existing sig.sl)
    primary_strategy: Optional[str] = None
    raw_sl_before_modifiers: Optional[float] = None
    sl_distance_atr: Optional[float] = None         # final |entry - sl| / ATR
    bounds_clamped: bool = False                    # min/max ATR bound clipped the SL
    candidates: Dict[str, Optional[float]] = field(default_factory=dict)
    # Modifier diagnostics
    volatility_buffer_atr: float = 0.0
    dynamic_expansion_factor: float = 1.0
    tightening_factor: float = 1.0
    # Bridge payload — break-even + trailing (persisted on signal doc as sl_management)
    sl_management: Optional[Dict[str, Any]] = None
    symbol_override_used: Optional[str] = None
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _swings(candles: List[Dict[str, float]], left: int = 3, right: int = 3
            ) -> Tuple[List[int], List[int]]:
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


def _resolve_symbol_overrides(cfg: Dict[str, Any], symbol: str) -> Tuple[Dict[str, Any], Optional[str]]:
    sym = (symbol or "").upper()
    ovs = (cfg.get("symbol_overrides") or {}).get(sym) or {}
    if not ovs:
        return cfg, None
    merged = {k: v for k, v in cfg.items() if k != "symbol_overrides"}
    merged.update(ovs)
    return merged, sym


def _is_valid_sl(sl: Optional[float], *, side: str, entry: float) -> bool:
    """A SL is valid iff it sits on the protective side of `entry`."""
    if sl is None or not isinstance(sl, (int, float)):
        return False
    if side == "buy":
        return sl < entry
    return sl > entry


# ─────────────────────────────────────────────────────────────────────────────
# Level strategies
# ─────────────────────────────────────────────────────────────────────────────
def sl_atr(*, side: str, entry: float, atr_now: float, mult: float) -> Optional[float]:
    if atr_now <= 0 or mult <= 0:
        return None
    return entry - mult * atr_now if side == "buy" else entry + mult * atr_now


def sl_swing(*, side: str, entry: float, candles: List[Dict[str, float]],
             lookback: int, buffer_atr: float, atr_now: float) -> Optional[float]:
    if not candles:
        return None
    win = candles[-lookback:] if len(candles) > lookback else candles
    highs, lows = _swings(win, left=3, right=3)
    buf = max(0.0, buffer_atr) * max(0.0, atr_now)
    if side == "buy":
        levels = [win[i]["l"] for i in lows if win[i]["l"] < entry]
        if not levels:
            return None
        return max(levels) - buf
    levels = [win[i]["h"] for i in highs if win[i]["h"] > entry]
    if not levels:
        return None
    return min(levels) + buf


def sl_structure(*, side: str, entry: float, candles: List[Dict[str, float]],
                 lookback: int, buffer_atr: float, atr_now: float) -> Optional[float]:
    """Beyond the most-recent structural swing (last swing low for buy, last
    swing high for sell). More forgiving than `swing` (uses LAST not nearest)
    so a deep recent swing protects the trade."""
    if not candles:
        return None
    win = candles[-lookback:] if len(candles) > lookback else candles
    highs, lows = _swings(win, left=3, right=3)
    buf = max(0.0, buffer_atr) * max(0.0, atr_now)
    if side == "buy":
        if not lows:
            return None
        return win[lows[-1]]["l"] - buf
    if not highs:
        return None
    return win[highs[-1]]["h"] + buf


# ─────────────────────────────────────────────────────────────────────────────
# Modifiers
# ─────────────────────────────────────────────────────────────────────────────
def apply_volatility_buffer(*, side: str, entry: float, sl: float, atr_now: float,
                            buffer_atr: float) -> Tuple[float, float]:
    """Push SL further from entry by `buffer_atr × ATR`. Returns (new_sl, applied_buffer_units)."""
    if atr_now <= 0 or buffer_atr <= 0:
        return sl, 0.0
    add = buffer_atr * atr_now
    new_sl = sl - add if side == "buy" else sl + add
    return new_sl, buffer_atr


def apply_expansion(*, side: str, entry: float, sl: float, factor: float) -> float:
    """Multiply SL distance from entry by `factor` (>1 widens, <1 tightens)."""
    if factor <= 0 or factor == 1.0:
        return sl
    dist = abs(entry - sl)
    new_dist = dist * factor
    return entry - new_dist if side == "buy" else entry + new_dist


def clamp_sl_distance(*, side: str, entry: float, sl: float, atr_now: float,
                      min_atr: float, max_atr: float) -> Tuple[float, bool]:
    """Clip SL distance to [min_atr, max_atr] × ATR. Returns (new_sl, was_clamped)."""
    if atr_now <= 0:
        return sl, False
    dist_atr = abs(entry - sl) / atr_now
    target_atr = dist_atr
    if dist_atr < min_atr:
        target_atr = min_atr
    elif dist_atr > max_atr:
        target_atr = max_atr
    if target_atr == dist_atr:
        return sl, False
    new_dist = target_atr * atr_now
    return (entry - new_dist if side == "buy" else entry + new_dist), True


# ─────────────────────────────────────────────────────────────────────────────
# Orchestrator
# ─────────────────────────────────────────────────────────────────────────────
def compute_adaptive_sl(
    *,
    side: str,
    symbol: str,
    entry: float,
    existing_sl: float,                              # strategy-emitted SL (fallback)
    atr_now: float,
    candles: List[Dict[str, float]],
    cfg: Dict[str, Any],
    market_regime: Optional[str] = None,             # from Module 1 (used by modifiers)
) -> AdaptiveSLResult:
    """Compute adaptive SL + modifiers + bridge management payload.

    `market_regime` enables regime-aware dynamic expansion / tightening.
    Caller keeps `existing_sl` when engine is disabled. SL is NEVER moved
    closer to entry than `min_sl_atr × ATR` to prevent broker SL-too-tight
    rejections.
    """
    res = AdaptiveSLResult(enabled=bool(cfg.get("enabled", False)))
    if not res.enabled:
        res.reasons.append("adaptive_sl disabled — keeping existing sig.sl")
        return res

    rcfg, applied_sym = _resolve_symbol_overrides(cfg, symbol)
    if applied_sym:
        res.symbol_override_used = applied_sym
        res.reasons.append(f"symbol overrides applied for {applied_sym}")

    priority: List[str] = list(rcfg.get("priority") or KNOWN_LEVEL_STRATEGIES)
    atr_mult = float(rcfg.get("atr_multiplier", 1.5))
    swing_lb = int(rcfg.get("swing_lookback", 50))
    structure_lb = int(rcfg.get("structure_lookback", 40))
    buf = float(rcfg.get("swing_buffer_atr", 0.20))
    vol_buf = float(rcfg.get("volatility_buffer_atr", 0.0))
    min_atr = float(rcfg.get("min_sl_atr", 0.50))
    max_atr = float(rcfg.get("max_sl_atr", 4.00))

    # Dynamic expansion / tightening per regime
    expand_cfg = rcfg.get("dynamic_expansion") or {}
    tighten_cfg = rcfg.get("tightening") or {}
    expansion_per_regime = (expand_cfg.get("per_regime") or {})
    tightening_per_regime = (tighten_cfg.get("per_regime") or {})

    # Compute every candidate (diagnostics)
    res.candidates = {
        "atr":       sl_atr(side=side, entry=entry, atr_now=atr_now, mult=atr_mult),
        "swing":     sl_swing(side=side, entry=entry, candles=candles,
                              lookback=swing_lb, buffer_atr=buf, atr_now=atr_now),
        "structure": sl_structure(side=side, entry=entry, candles=candles,
                                  lookback=structure_lb, buffer_atr=buf, atr_now=atr_now),
    }

    picked_sl: Optional[float] = None
    picked_name: Optional[str] = None
    for name in priority:
        if name not in KNOWN_LEVEL_STRATEGIES:
            res.reasons.append(f"skip unknown SL strategy '{name}'")
            continue
        cand = res.candidates.get(name)
        if _is_valid_sl(cand, side=side, entry=entry):
            picked_sl = cand
            picked_name = name
            res.reasons.append(f"picked '{name}' → sl={cand:.5f}")
            break
        res.reasons.append(f"'{name}' candidate invalid ({cand})")

    # Fallback to existing strategy SL
    if picked_sl is None:
        picked_sl = existing_sl
        picked_name = "fallback_strategy_sl"
        res.reasons.append("all strategies failed → fallback to strategy_v2 sl")

    res.raw_sl_before_modifiers = picked_sl

    # Volatility buffer modifier
    if vol_buf > 0:
        picked_sl, vb_units = apply_volatility_buffer(
            side=side, entry=entry, sl=picked_sl, atr_now=atr_now, buffer_atr=vol_buf,
        )
        res.volatility_buffer_atr = vb_units
        res.reasons.append(f"volatility_buffer +{vb_units:.2f}×ATR applied")

    # Dynamic expansion (regime-aware)
    if expand_cfg.get("enabled", False) and market_regime:
        factor = float(expansion_per_regime.get(market_regime, 1.0))
        if factor != 1.0:
            picked_sl = apply_expansion(side=side, entry=entry, sl=picked_sl, factor=factor)
            res.dynamic_expansion_factor = factor
            res.reasons.append(f"expansion ×{factor:.2f} for regime={market_regime}")

    # Tightening (regime-aware) — applied AFTER expansion (so net = expansion × tighten)
    if tighten_cfg.get("enabled", False) and market_regime:
        factor = float(tightening_per_regime.get(market_regime, 1.0))
        if factor != 1.0 and factor > 0:
            picked_sl = apply_expansion(side=side, entry=entry, sl=picked_sl, factor=factor)
            res.tightening_factor = factor
            res.reasons.append(f"tightening ×{factor:.2f} for regime={market_regime}")

    # Distance clamp
    picked_sl, clamped = clamp_sl_distance(
        side=side, entry=entry, sl=picked_sl, atr_now=atr_now,
        min_atr=min_atr, max_atr=max_atr,
    )
    res.bounds_clamped = clamped
    if clamped:
        res.reasons.append(f"SL distance clamped to [{min_atr}, {max_atr}] × ATR")

    res.primary_sl = picked_sl
    res.primary_strategy = picked_name
    if atr_now > 0:
        res.sl_distance_atr = round(abs(entry - picked_sl) / atr_now, 3)

    # ── Bridge management payload: break-even + trailing ───────────────────
    be_cfg = rcfg.get("break_even") or {}
    tr_cfg = rcfg.get("trailing") or {}
    mgmt: Dict[str, Any] = {}
    if be_cfg.get("enabled", False):
        mgmt["break_even"] = {
            "enabled": True,
            "activate_at_rr": float(be_cfg.get("activate_at_rr", 1.0)),
            "lock_pips_atr": float(be_cfg.get("lock_pips_atr", 0.1)),
        }
        res.reasons.append(
            f"break_even activate@{mgmt['break_even']['activate_at_rr']}RR "
            f"lock={mgmt['break_even']['lock_pips_atr']}×ATR"
        )
    if tr_cfg.get("enabled", False):
        mgmt["trailing"] = {
            "enabled": True,
            "activate_at_rr": float(tr_cfg.get("activate_at_rr", 1.5)),
            "trail_distance_atr": float(tr_cfg.get("trail_distance_atr", 1.0)),
        }
        res.reasons.append(
            f"trailing activate@{mgmt['trailing']['activate_at_rr']}RR "
            f"trail={mgmt['trailing']['trail_distance_atr']}×ATR"
        )
    if mgmt:
        res.sl_management = mgmt

    return res
