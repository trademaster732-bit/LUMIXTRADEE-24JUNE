"""Module 3 — Adaptive Take Profit Engine (2026-01).

Replaces the static R:R-based TP with a priority-ordered chooser that picks
the most appropriate TP for the current market structure, then enforces
min-RR/max-RR sanity bounds. Stop loss is NEVER modified.

Seven strategies (admin orders them via `adaptive_tp.priority`):
  • static_rr   — TP = entry ± rr × SL distance
  • atr         — TP = entry ± atr_multiplier × ATR
  • swing       — TP = nearest swing-high (buy) / swing-low (sell)
  • sr          — TP = nearest major S/R level (clusters of swing pivots)
  • structure   — TP = projected next structural level (HH/HL/LH/LL targeting)
  • partial     — generates a tp_levels[] array with allocation %s (not a
                  single price — persisted alongside the primary TP)
  • trailing    — generates a trailing config payload that the bridge can
                  consume to trail the stop after price moves favorably
                  (persisted as `trailing` on the signal, NOT a TP price)

The first strategy from `priority` that returns a valid price-level TP wins
and becomes the primary `tp`. `partial` and `trailing` are auxiliary — they
add tp_levels[] / trailing{} regardless of the chosen primary, so admins can
combine "swing-TP" with "partial closes" and "trailing" simultaneously.

Configuration:
    engine_config.adaptive_tp = {
      "enabled": false,                  # default OFF — opt-in via admin
      "priority": ["structure","swing","sr","atr","static_rr"],
      "static_rr": 2.0,
      "atr_multiplier": 2.5,
      "swing_lookback": 50,
      "sr_lookback": 120,
      "sr_cluster_atr": 0.5,
      "structure_lookback": 40,
      "min_rr_floor": 1.5,
      "max_rr_cap": 6.0,
      "partial_tp": {
        "enabled": false,
        "levels": [
          {"rr": 1.0, "close_pct": 50},
          {"rr": 2.0, "close_pct": 30},
          {"rr": 3.0, "close_pct": 20}
        ]
      },
      "trailing": {
        "enabled": false,
        "activate_at_rr": 1.0,
        "trail_distance_atr": 0.8
      },
      "symbol_overrides": {
        "XAUUSD": { "priority": ["structure","swing","atr"], "atr_multiplier": 3.0 }
      }
    }
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple


# All recognised strategy names. New strategies must be added here AND to the
# `_LEVEL_STRATEGIES` dispatch table below.
KNOWN_STRATEGIES: Tuple[str, ...] = (
    "static_rr", "atr", "swing", "sr", "structure",
)


# ─────────────────────────────────────────────────────────────────────────────
# Result container
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class AdaptiveTPResult:
    enabled: bool = True
    primary_tp: Optional[float] = None              # final TP price (None ⇒ caller keeps existing sig.tp)
    primary_strategy: Optional[str] = None          # strategy that produced primary_tp
    rr_realized: Optional[float] = None             # actual RR = tp_dist / sl_dist
    rr_floor_applied: bool = False                  # min_rr_floor widened the picked level
    rr_cap_applied: bool = False                    # max_rr_cap clipped the picked level
    candidates: Dict[str, Optional[float]] = field(default_factory=dict)
    # Auxiliary outputs (persisted alongside primary_tp on the signal doc)
    tp_levels: Optional[List[Dict[str, float]]] = None        # partial TP plan
    trailing: Optional[Dict[str, float]] = None               # trailing payload for bridge
    reasons: List[str] = field(default_factory=list)
    symbol_override_used: Optional[str] = None      # which symbol_overrides key was applied

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ─────────────────────────────────────────────────────────────────────────────
# Local helpers
# ─────────────────────────────────────────────────────────────────────────────
def _swings(candles: List[Dict[str, float]], left: int = 3, right: int = 3
            ) -> Tuple[List[int], List[int]]:
    """Pivot-based swing-high / swing-low indexes (same scheme as entry_quality)."""
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
    """Merge symbol-specific overrides on top of the base config. Returns
    (merged_cfg, applied_symbol_key | None)."""
    sym = (symbol or "").upper()
    overrides = (cfg.get("symbol_overrides") or {}).get(sym) or {}
    if not overrides:
        return cfg, None
    merged = {k: v for k, v in cfg.items() if k != "symbol_overrides"}
    merged.update(overrides)
    return merged, sym


# ─────────────────────────────────────────────────────────────────────────────
# Strategy: static R:R
# ─────────────────────────────────────────────────────────────────────────────
def tp_static_rr(*, side: str, entry: float, sl: float, rr: float) -> Optional[float]:
    sl_dist = abs(entry - sl)
    if sl_dist <= 0 or rr <= 0:
        return None
    if side == "buy":
        return entry + rr * sl_dist
    return entry - rr * sl_dist


# ─────────────────────────────────────────────────────────────────────────────
# Strategy: ATR multiple
# ─────────────────────────────────────────────────────────────────────────────
def tp_atr(*, side: str, entry: float, atr_now: float, mult: float) -> Optional[float]:
    if atr_now <= 0 or mult <= 0:
        return None
    if side == "buy":
        return entry + mult * atr_now
    return entry - mult * atr_now


# ─────────────────────────────────────────────────────────────────────────────
# Strategy: nearest swing high/low
# ─────────────────────────────────────────────────────────────────────────────
def tp_swing(*, side: str, entry: float,
             candles: List[Dict[str, float]], lookback: int) -> Optional[float]:
    if not candles:
        return None
    win = candles[-lookback:] if len(candles) > lookback else candles
    highs, lows = _swings(win, left=3, right=3)
    if side == "buy":
        # nearest swing high ABOVE entry
        levels = [win[i]["h"] for i in highs if win[i]["h"] > entry]
        if not levels:
            return None
        return min(levels)
    # sell: nearest swing low BELOW entry
    levels = [win[i]["l"] for i in lows if win[i]["l"] < entry]
    if not levels:
        return None
    return max(levels)


# ─────────────────────────────────────────────────────────────────────────────
# Strategy: nearest major S/R (cluster of swing pivots within `sr_cluster_atr`)
# ─────────────────────────────────────────────────────────────────────────────
def tp_sr(*, side: str, entry: float,
          candles: List[Dict[str, float]], lookback: int,
          atr_now: float, cluster_atr: float) -> Optional[float]:
    if not candles or atr_now <= 0:
        return None
    win = candles[-lookback:] if len(candles) > lookback else candles
    highs, lows = _swings(win, left=3, right=3)
    raw_levels = [win[i]["h"] for i in highs] + [win[i]["l"] for i in lows]
    raw_levels = [lvl for lvl in raw_levels
                  if (lvl > entry if side == "buy" else lvl < entry)]
    if not raw_levels:
        return None
    # Cluster levels within `cluster_atr * atr_now`
    raw_levels.sort()
    clusters: List[List[float]] = []
    bucket: List[float] = []
    threshold = cluster_atr * atr_now
    for lvl in raw_levels:
        if not bucket or (lvl - bucket[-1]) <= threshold:
            bucket.append(lvl)
        else:
            clusters.append(bucket)
            bucket = [lvl]
    if bucket:
        clusters.append(bucket)
    # Score clusters by member count; nearest cluster with ≥ 2 members wins.
    qualified = [c for c in clusters if len(c) >= 2]
    if not qualified:
        # Fall back to the single nearest level if no clusters formed.
        return raw_levels[0] if side == "buy" else raw_levels[-1]
    # Cluster center (median of its members)
    if side == "buy":
        cluster = qualified[0]                  # nearest above
    else:
        cluster = qualified[-1]                 # nearest below (sorted ascending)
    cluster.sort()
    return cluster[len(cluster) // 2]


# ─────────────────────────────────────────────────────────────────────────────
# Strategy: market structure projection
#
# Project the next structural target by extrapolating the last impulse leg.
# For BUY: distance from the most recent swing-low to the current price is
# projected by the same magnitude (1:1 measured-move). For SELL: mirrored.
# ─────────────────────────────────────────────────────────────────────────────
def tp_structure(*, side: str, entry: float,
                 candles: List[Dict[str, float]], lookback: int) -> Optional[float]:
    if len(candles) < 10:
        return None
    win = candles[-lookback:] if len(candles) > lookback else candles
    highs, lows = _swings(win, left=3, right=3)
    if side == "buy":
        if not lows:
            return None
        last_low = win[lows[-1]]["l"]
        # Look for the swing-high that produced the impulse leg INTO this low.
        relevant_highs = [h for h in highs if h < lows[-1]]
        if relevant_highs:
            leg_high = win[relevant_highs[-1]]["h"]
            leg = abs(leg_high - last_low)
        else:
            leg = abs(entry - last_low)
        return entry + leg
    # sell
    if not highs:
        return None
    last_high = win[highs[-1]]["h"]
    relevant_lows = [l for l in lows if l < highs[-1]]
    if relevant_lows:
        leg_low = win[relevant_lows[-1]]["l"]
        leg = abs(last_high - leg_low)
    else:
        leg = abs(entry - last_high)
    return entry - leg


# Dispatch table — keep KNOWN_STRATEGIES in sync.
_LEVEL_STRATEGIES = {
    "static_rr": "static_rr",
    "atr": "atr",
    "swing": "swing",
    "sr": "sr",
    "structure": "structure",
}


# ─────────────────────────────────────────────────────────────────────────────
# Sanity bounds + side check
# ─────────────────────────────────────────────────────────────────────────────
def _is_valid_tp(tp: Optional[float], *, side: str, entry: float, sl: float) -> bool:
    """A TP is valid iff it sits on the correct side of `entry` and beyond `sl`."""
    if tp is None or not isinstance(tp, (int, float)):
        return False
    if side == "buy":
        return tp > entry and tp > sl
    return tp < entry and tp < sl


def _enforce_rr_bounds(
    *, side: str, entry: float, sl: float, tp: float,
    min_rr: float, max_rr: float,
) -> Tuple[float, bool, bool]:
    """Widen TP if RR < min_rr, clip TP if RR > max_rr. Returns
    (adjusted_tp, floor_applied, cap_applied)."""
    sl_dist = abs(entry - sl)
    if sl_dist <= 0:
        return tp, False, False
    tp_dist = abs(tp - entry)
    rr = tp_dist / sl_dist
    floor_applied = False
    cap_applied = False
    if rr < min_rr:
        tp_dist = sl_dist * min_rr
        floor_applied = True
    elif rr > max_rr:
        tp_dist = sl_dist * max_rr
        cap_applied = True
    new_tp = entry + tp_dist if side == "buy" else entry - tp_dist
    return new_tp, floor_applied, cap_applied


# ─────────────────────────────────────────────────────────────────────────────
# Orchestrator
# ─────────────────────────────────────────────────────────────────────────────
def compute_adaptive_tp(
    *,
    side: str,
    symbol: str,
    entry: float,
    sl: float,
    atr_now: float,
    candles: List[Dict[str, float]],        # signal-TF candles
    candles_h1: Optional[List[Dict[str, float]]] = None,
    candles_h4: Optional[List[Dict[str, float]]] = None,
    cfg: Dict[str, Any],
) -> AdaptiveTPResult:
    """Compute the adaptive TP, partial-TP plan, and trailing payload.

    The caller is responsible for replacing `sig.tp` and persisting auxiliary
    fields. Never mutates SL.
    """
    res = AdaptiveTPResult(enabled=bool(cfg.get("enabled", False)))
    if not res.enabled:
        res.reasons.append("adaptive_tp disabled — keeping existing sig.tp")
        return res

    # Resolve symbol overrides (e.g. XAUUSD-specific priority / atr_multiplier)
    rcfg, applied_sym = _resolve_symbol_overrides(cfg, symbol)
    if applied_sym:
        res.symbol_override_used = applied_sym
        res.reasons.append(f"symbol overrides applied for {applied_sym}")

    priority: List[str] = list(rcfg.get("priority") or KNOWN_STRATEGIES)
    static_rr = float(rcfg.get("static_rr", 2.0))
    atr_mult = float(rcfg.get("atr_multiplier", 2.5))
    swing_lb = int(rcfg.get("swing_lookback", 50))
    sr_lb = int(rcfg.get("sr_lookback", 120))
    sr_cluster = float(rcfg.get("sr_cluster_atr", 0.5))
    structure_lb = int(rcfg.get("structure_lookback", 40))
    min_rr_floor = float(rcfg.get("min_rr_floor", 1.5))
    max_rr_cap = float(rcfg.get("max_rr_cap", 6.0))

    # Compute every strategy's candidate (for diagnostics).
    res.candidates = {
        "static_rr": tp_static_rr(side=side, entry=entry, sl=sl, rr=static_rr),
        "atr":       tp_atr(side=side, entry=entry, atr_now=atr_now, mult=atr_mult),
        "swing":     tp_swing(side=side, entry=entry, candles=candles, lookback=swing_lb),
        "sr":        tp_sr(side=side, entry=entry, candles=candles, lookback=sr_lb,
                           atr_now=atr_now, cluster_atr=sr_cluster),
        "structure": tp_structure(side=side, entry=entry, candles=candles,
                                  lookback=structure_lb),
    }

    # Walk priority; pick the first VALID candidate.
    picked_tp: Optional[float] = None
    picked_name: Optional[str] = None
    for name in priority:
        if name not in _LEVEL_STRATEGIES:
            res.reasons.append(f"skip unknown strategy '{name}'")
            continue
        cand = res.candidates.get(name)
        if _is_valid_tp(cand, side=side, entry=entry, sl=sl):
            picked_tp = cand
            picked_name = name
            res.reasons.append(f"picked '{name}' → tp={cand:.5f}")
            break
        res.reasons.append(f"'{name}' candidate invalid or wrong side ({cand})")

    # Fallback: if NOTHING produced a valid TP, hold the line with static_rr=min_rr_floor.
    if picked_tp is None:
        picked_tp = tp_static_rr(side=side, entry=entry, sl=sl, rr=min_rr_floor)
        picked_name = "fallback_static_rr"
        res.reasons.append(f"all strategies failed → fallback static_rr@{min_rr_floor:.2f}")
        if picked_tp is None:
            # Degenerate input — caller will keep its original sig.tp.
            res.reasons.append("fallback also failed (sl_dist=0) — leaving sig.tp untouched")
            return res

    # Enforce RR bounds
    adjusted, floor_applied, cap_applied = _enforce_rr_bounds(
        side=side, entry=entry, sl=sl, tp=picked_tp,
        min_rr=min_rr_floor, max_rr=max_rr_cap,
    )
    res.primary_tp = adjusted
    res.primary_strategy = picked_name
    res.rr_floor_applied = floor_applied
    res.rr_cap_applied = cap_applied
    sl_dist = abs(entry - sl)
    if sl_dist > 0:
        res.rr_realized = round(abs(adjusted - entry) / sl_dist, 3)
    if floor_applied:
        res.reasons.append(f"RR floor enforced (min={min_rr_floor})")
    if cap_applied:
        res.reasons.append(f"RR cap enforced (max={max_rr_cap})")

    # ── Partial TP plan ─────────────────────────────────────────────────────
    ptp_cfg = rcfg.get("partial_tp") or {}
    if ptp_cfg.get("enabled", False):
        levels_in = ptp_cfg.get("levels") or []
        out_levels: List[Dict[str, float]] = []
        for lvl in levels_in:
            try:
                rr = float(lvl.get("rr", 0))
                pct = float(lvl.get("close_pct", 0))
            except (TypeError, ValueError):
                continue
            if rr <= 0 or pct <= 0:
                continue
            tp_price = tp_static_rr(side=side, entry=entry, sl=sl, rr=rr)
            if not _is_valid_tp(tp_price, side=side, entry=entry, sl=sl):
                continue
            out_levels.append({"rr": rr, "close_pct": pct, "tp": tp_price})
        if out_levels:
            # Normalize close_pct so the sum doesn't exceed 100 (excess clipped on last).
            running = 0.0
            for lvl in out_levels:
                room = max(0.0, 100.0 - running)
                lvl["close_pct"] = min(lvl["close_pct"], room)
                running += lvl["close_pct"]
            res.tp_levels = out_levels
            res.reasons.append(f"partial_tp: {len(out_levels)} levels")

    # ── Trailing payload (bridge consumes; not a TP price) ─────────────────
    tr_cfg = rcfg.get("trailing") or {}
    if tr_cfg.get("enabled", False):
        res.trailing = {
            "enabled": True,
            "activate_at_rr": float(tr_cfg.get("activate_at_rr", 1.0)),
            "trail_distance_atr": float(tr_cfg.get("trail_distance_atr", 0.8)),
        }
        res.reasons.append(
            f"trailing: activate@{res.trailing['activate_at_rr']}RR "
            f"trail={res.trailing['trail_distance_atr']}×ATR"
        )

    return res
