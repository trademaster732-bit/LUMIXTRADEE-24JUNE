"""Module 2 — Multi-Timeframe Alignment (2026-01).

Replaces the simple "H4 + H1 trend agrees" check in quality_score with a
richer, fully-configurable alignment evaluator across D1, H4, H1 and M15.

For each enabled timeframe the module computes:
  • direction         — up / down / flat (EMA21 vs EMA55, 5-bp band)
  • strength (ADX)    — Wilder period 14
  • EMA angle         — basis-point slope of EMA21 over the last 5 bars
  • momentum direction— 5-bar ADX slope (rising = healthier trend)
  • `agrees` flag     — direction == signal side AND strength ≥ min_strength AND
                         ema_angle_abs ≥ min_ema_angle AND (if required) momentum

The orchestrator then computes a weighted alignment percentage and applies two
hard gates: (a) alignment_pct < `min_alignment_pct`, (b) optional
D1↔M15 strong-disagreement (both decisive but opposite).

Configuration lives at `engine_config.mtf_alignment`:

    {
      "enabled": true,
      "timeframes": {
        "D1":  { "enabled": true,  "weight": 30, "min_strength_adx": 18, "min_ema_angle_bps": 0.5 },
        "H4":  { "enabled": true,  "weight": 30, "min_strength_adx": 20, "min_ema_angle_bps": 0.4 },
        "H1":  { "enabled": true,  "weight": 25, "min_strength_adx": 22, "min_ema_angle_bps": 0.3 },
        "M15": { "enabled": true,  "weight": 15, "min_strength_adx": 25, "min_ema_angle_bps": 0.2 }
      },
      "min_alignment_pct": 60,
      "htf_ltf_disagreement_reject": true,
      "require_momentum_agreement": false,
      "min_momentum_agreement_count": 2
    }

This module does not modify quality_score weights, risk, sessions or any other
existing gate.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

from engine import ema, atr  # reuse Wilder ATR + EMA shipped with the engine
from quality_score import _adx as _adx_compute  # same Wilder ADX implementation


# Stable order — used for diagnostics + alignment denominator.
TF_ORDER: tuple = ("D1", "H4", "H1", "M15")
HTF_NAME: str = "D1"     # the "highest timeframe" used for the LTF-disagreement gate
LTF_NAME: str = "M15"    # the "lowest timeframe" used for the LTF-disagreement gate


@dataclass
class TFEval:
    """Per-timeframe evaluation snapshot."""
    timeframe: str
    enabled: bool = True
    direction: str = "flat"            # up | down | flat | unknown
    adx: float = 0.0                   # latest Wilder ADX
    adx_slope: float = 0.0             # last-5 minus prior-5 ADX
    ema_angle_bps: float = 0.0         # signed basis-point slope of EMA21
    momentum_dir_ok: bool = False      # ema21 slope direction matches signal side
    weight: int = 0
    agrees: bool = False               # direction == side AND meets all minimums
    decisive: bool = False             # direction != "flat" AND strength ≥ min_strength
    reasons: List[str] = field(default_factory=list)


@dataclass
class MTFResult:
    evaluations: Dict[str, TFEval] = field(default_factory=dict)
    alignment_pct: int = 0             # 0-100 weighted % of enabled TFs that agree
    aligned_count: int = 0
    enabled_count: int = 0
    momentum_agree_count: int = 0
    htf_dir: str = "unknown"
    ltf_dir: str = "unknown"
    htf_ltf_disagree: bool = False
    passed: bool = True
    rejection_reason: Optional[str] = None
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "evaluations": {k: asdict(v) for k, v in self.evaluations.items()},
            "alignment_pct": self.alignment_pct,
            "aligned_count": self.aligned_count,
            "enabled_count": self.enabled_count,
            "momentum_agree_count": self.momentum_agree_count,
            "htf_dir": self.htf_dir,
            "ltf_dir": self.ltf_dir,
            "htf_ltf_disagree": self.htf_ltf_disagree,
            "passed": self.passed,
            "rejection_reason": self.rejection_reason,
            "reasons": self.reasons,
        }


def _direction_from_ema(candles: List[Dict[str, float]],
                        band_bp: float = 5.0) -> str:
    """Return 'up' / 'down' / 'flat' / 'unknown'. 'unknown' when history < 60 bars
    (warm-up). `band_bp` is the basis-point dead-zone between EMA21 and EMA55."""
    if len(candles) < 60:
        return "unknown"
    closes = [c["c"] for c in candles]
    ef = ema(closes, 21)
    es = ema(closes, 55)
    if not ef or not es:
        return "unknown"
    diff = ef[-1] - es[-1]
    band = abs(closes[-1]) * (band_bp / 10_000.0)
    if diff > band:
        return "up"
    if diff < -band:
        return "down"
    return "flat"


def _ema21_slope_bps(candles: List[Dict[str, float]], lookback: int = 5) -> float:
    """Signed basis-point slope of EMA21 over the last `lookback` bars."""
    if len(candles) < 60 + lookback:
        return 0.0
    closes = [c["c"] for c in candles]
    ef = ema(closes, 21)
    if len(ef) <= lookback:
        return 0.0
    last_close = closes[-1] or 1.0
    return ((ef[-1] - ef[-1 - lookback]) / last_close) * 10_000.0


def evaluate_tf(
    timeframe: str,
    candles: List[Dict[str, float]],
    side: str,
    tf_cfg: Dict[str, Any],
) -> TFEval:
    """Evaluate a single timeframe. Always returns a TFEval (never raises)."""
    enabled = bool(tf_cfg.get("enabled", True))
    weight = int(tf_cfg.get("weight", 0))
    ev = TFEval(timeframe=timeframe, enabled=enabled, weight=weight)
    if not enabled:
        ev.reasons.append(f"{timeframe}: disabled in config")
        return ev
    if not candles or len(candles) < 60:
        ev.direction = "unknown"
        ev.reasons.append(f"{timeframe}: history < 60 bars → unknown")
        return ev

    # Direction
    ev.direction = _direction_from_ema(candles)
    # Strength (ADX)
    adx_vals = _adx_compute(candles, 14)
    ev.adx = round(adx_vals[-1], 2) if adx_vals else 0.0
    if len(adx_vals) >= 10:
        ev.adx_slope = round(
            (sum(adx_vals[-5:]) / 5.0) - (sum(adx_vals[-10:-5]) / 5.0), 3
        )
    # EMA angle
    ev.ema_angle_bps = round(_ema21_slope_bps(candles), 3)

    min_strength = float(tf_cfg.get("min_strength_adx", 18.0))
    min_angle = float(tf_cfg.get("min_ema_angle_bps", 0.3))

    # Decisive = direction is not flat AND strength clears the bar.
    ev.decisive = ev.direction in ("up", "down") and ev.adx >= min_strength

    # Side match
    want = "up" if side == "buy" else "down"
    dir_ok = ev.direction == want
    strength_ok = ev.adx >= min_strength
    angle_ok = abs(ev.ema_angle_bps) >= min_angle and (
        (ev.ema_angle_bps > 0 and want == "up") or
        (ev.ema_angle_bps < 0 and want == "down")
    )
    ev.momentum_dir_ok = (ev.adx_slope > 0) and dir_ok
    ev.agrees = dir_ok and strength_ok and angle_ok

    ev.reasons.append(
        f"{timeframe}: dir={ev.direction} adx={ev.adx:.1f}(min {min_strength:.0f}) "
        f"slope={ev.ema_angle_bps:+.2f}bp(min {min_angle:.2f}) "
        f"adx_slope={ev.adx_slope:+.2f} → agrees={ev.agrees}"
    )
    return ev


def evaluate_mtf_alignment(
    *,
    side: str,
    candles_d1: List[Dict[str, float]],
    candles_h4: List[Dict[str, float]],
    candles_h1: List[Dict[str, float]],
    candles_m15: List[Dict[str, float]],
    mtf_cfg: Dict[str, Any],
) -> MTFResult:
    """Run all four timeframes and assemble a single result with gating."""
    res = MTFResult()
    if not mtf_cfg or not mtf_cfg.get("enabled", True):
        res.reasons.append("mtf_alignment engine disabled — pass-through")
        return res

    tfs = (mtf_cfg.get("timeframes") or {})
    candle_map = {
        "D1": candles_d1,
        "H4": candles_h4,
        "H1": candles_h1,
        "M15": candles_m15,
    }

    total_weight = 0
    agreed_weight = 0
    enabled_count = 0
    aligned_count = 0
    momentum_count = 0

    for name in TF_ORDER:
        tf_cfg = tfs.get(name) or {}
        ev = evaluate_tf(name, candle_map[name] or [], side, tf_cfg)
        res.evaluations[name] = ev
        res.reasons.extend(ev.reasons)
        if not ev.enabled:
            continue
        enabled_count += 1
        total_weight += ev.weight
        if ev.agrees:
            aligned_count += 1
            agreed_weight += ev.weight
        if ev.momentum_dir_ok:
            momentum_count += 1

    res.enabled_count = enabled_count
    res.aligned_count = aligned_count
    res.momentum_agree_count = momentum_count
    res.alignment_pct = int(round((agreed_weight / total_weight) * 100)) if total_weight > 0 else 0
    res.htf_dir = res.evaluations.get(HTF_NAME, TFEval(HTF_NAME)).direction
    res.ltf_dir = res.evaluations.get(LTF_NAME, TFEval(LTF_NAME)).direction
    htf_ev = res.evaluations.get(HTF_NAME)
    ltf_ev = res.evaluations.get(LTF_NAME)
    res.htf_ltf_disagree = bool(
        htf_ev and ltf_ev and htf_ev.decisive and ltf_ev.decisive
        and htf_ev.direction != ltf_ev.direction
    )

    # ─── Gates ───────────────────────────────────────────────────────────────
    # Gate 1: alignment % below threshold
    min_pct = int(mtf_cfg.get("min_alignment_pct", 60))
    if res.alignment_pct < min_pct:
        res.passed = False
        res.rejection_reason = (
            f"alignment_pct:{res.alignment_pct}<{min_pct} "
            f"(agreed_w={agreed_weight}/{total_weight})"
        )
        return res

    # Gate 2: HTF vs LTF strong disagreement (both decisive, opposite directions)
    if mtf_cfg.get("htf_ltf_disagreement_reject", True) and res.htf_ltf_disagree:
        res.passed = False
        res.rejection_reason = (
            f"htf_ltf_disagree:{HTF_NAME}={res.htf_dir}_vs_{LTF_NAME}={res.ltf_dir}"
        )
        return res

    # Gate 3: optional momentum-agreement requirement
    if mtf_cfg.get("require_momentum_agreement", False):
        need = int(mtf_cfg.get("min_momentum_agreement_count", 2))
        if momentum_count < need:
            res.passed = False
            res.rejection_reason = (
                f"momentum_disagree:{momentum_count}<{need}"
            )
            return res

    res.passed = True
    return res
