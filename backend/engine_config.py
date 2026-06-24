"""Phase-1 Engine Configuration (2026-06-22).

Single source of truth for the trade-quality scoring engine, instrument cooldowns,
session restrictions, daily-bias filter, ATR-ratio filter, and per-symbol overrides.

Storage:
  • MongoDB collection `engine_config` — a single document with _id="global".
  • Defaults baked in here are used when no DB document exists.
  • In-memory cache with 60-second TTL; PUT endpoint invalidates cache.

Per-symbol overrides live under `symbol_overrides`, e.g.:
    "symbol_overrides": {
        "XAUUSD": {"min_score": 85, "cooldown_min": 60},
        "EURUSD": {"min_score": 80, "cooldown_min": 45},
    }

Lookup helper `get_symbol_setting(cfg, symbol, key)` returns the symbol-specific
value when present, falling back to the global default.
"""
from __future__ import annotations
import time
from typing import Any, Dict, Optional

# ─────────────────────────────────────────────────────────────────────────────
# Default config (baseline). Edit via the admin UI; no redeploy needed.
# ─────────────────────────────────────────────────────────────────────────────
DEFAULT_CONFIG: Dict[str, Any] = {
    # ───── FEATURE 1: Trade-Quality Score (7 factors, max 100) ─────
    "score_weights": {
        "h4_trend": 20,
        "h1_trend": 20,
        "adx": 15,
        "vwap": 10,
        "sr": 15,
        "atr_ratio": 10,
        "spread": 10,
    },
    "min_score": 70,                     # global threshold (per-symbol override allowed)
    "near_miss_lower": 65,               # log scores in [near_miss_lower, min_score) as near-misses
    "adx_threshold": 25.0,               # ADX > this → award the 15 points
    "vwap_max_distance_atr": 1.5,        # price within 1.5×ATR of VWAP earns the 10 points

    # ───── FEATURE 2: Instrument Cooldown ─────
    "cooldown_consecutive_losses": 2,    # N consecutive losses → trigger
    "cooldown_min": 60,                  # default lockout minutes (per-symbol override allowed)

    # ───── FEATURE 3: Session Filter ─────
    # Hours are UTC. "metals" = XAUUSD/XAGUSD; FX pairs unrestricted by default.
    "session_windows": {
        "asia":     {"start": 0,  "end": 8},      # 00:00 - 07:59 UTC
        "london":   {"start": 8,  "end": 16},     # 08:00 - 15:59 UTC
        "new_york": {"start": 13, "end": 21},     # 13:00 - 20:59 UTC
    },
    "metals_blocked_sessions": ["asia"],          # XAU/XAG cannot trade these sessions

    # ───── FEATURE 4: Daily-Bias Filter ─────
    "daily_bias_enabled": True,
    "daily_bias_neutral_mode": "score_penalty",   # "score_penalty" | "block" | "carry_forward"
    "daily_bias_neutral_penalty": 5,              # subtract from score when neutral (mode B)

    # ───── FEATURE 5: ATR-Ratio Filter ─────
    "atr_ratio_min": 0.80,
    "atr_ratio_max": 2.00,

    # ───── Symbol overrides — admin can add any of these keys per symbol ─────
    # NOTE (2026-01 commercial tuning): re-calibrated after diagnostic showed the
    # pre-fix metals threshold of 85 was mathematically unreachable during the
    # H4/D1 history warm-up window. New floors target ≈20–25 trades/day across
    # the basket while keeping the structural filters (HTF, ATR band, cooldowns)
    # intact.
    "symbol_overrides": {
        "XAUUSD": {"min_score": 75, "cooldown_min": 60},
        "XAGUSD": {"min_score": 75, "cooldown_min": 60},
        "EURUSD": {"min_score": 75, "cooldown_min": 45},
        "GBPUSD": {"min_score": 75, "cooldown_min": 45},
        "USDCAD": {"min_score": 72, "cooldown_min": 30},
    },
}

# Single-process in-memory cache (the scheduler + the API share this process)
_CACHE: Dict[str, Any] = {"doc": None, "expires_at": 0.0}
_CACHE_TTL_SEC = 60.0


async def load_engine_config(db) -> Dict[str, Any]:
    """Load the live engine config. Cached for 60 s; falls back to DEFAULT_CONFIG.
    The DB doc may be partial — missing keys are filled from DEFAULT_CONFIG."""
    now = time.time()
    if _CACHE["doc"] is not None and _CACHE["expires_at"] > now:
        return _CACHE["doc"]
    try:
        doc = await db.engine_config.find_one({"_id": "global"})
    except Exception:
        doc = None
    merged = _merge_defaults(doc or {})
    _CACHE["doc"] = merged
    _CACHE["expires_at"] = now + _CACHE_TTL_SEC
    return merged


def invalidate_cache() -> None:
    """Call after any admin PUT so the next read picks up the new value."""
    _CACHE["doc"] = None
    _CACHE["expires_at"] = 0.0


async def save_engine_config(db, patch: Dict[str, Any], *, admin_id: Optional[str] = None) -> Dict[str, Any]:
    """Merge-update the config doc. Returns the new merged doc.

    Merge semantics:
      • ``score_weights`` and ``session_windows`` → deep-merged (one level) with
        the existing doc so admins can patch individual keys.
      • ``symbol_overrides`` → REPLACEMENT semantics. The full dict in the patch
        becomes the new ``symbol_overrides`` (allowing removal of any entry by
        omitting it). To keep an entry, callers must include it explicitly.
      • Everything else → shallow override.
    """
    existing = await db.engine_config.find_one({"_id": "global"}) or {}
    merged = dict(existing)
    for k, v in patch.items():
        if k == "symbol_overrides":
            # Authoritative replacement (allows removing entries).
            merged[k] = dict(v) if isinstance(v, dict) else {}
        elif k in ("score_weights", "session_windows") and isinstance(v, dict):
            base = dict(existing.get(k) or {})
            base.update(v)
            merged[k] = base
        else:
            merged[k] = v
    merged["_id"] = "global"
    merged["updated_at"] = int(time.time())
    if admin_id:
        merged["updated_by"] = admin_id
    await db.engine_config.replace_one({"_id": "global"}, merged, upsert=True)
    invalidate_cache()
    return _merge_defaults(merged)


def _merge_defaults(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Fill missing keys from DEFAULT_CONFIG so callers can read every key safely.

    2026-01 fix: ``symbol_overrides`` is now AUTHORITATIVE from the DB doc when
    that key is explicitly present (even if empty/partial). Previously the
    defaults dict was re-injected on every read, which made it impossible for
    an admin to *remove* a per-symbol override (e.g. drop XAUUSD's hard-coded
    85 floor). ``score_weights`` and ``session_windows`` keep the deep-merge
    behavior because they are structurally tied to the scoring formula.
    """
    out = dict(DEFAULT_CONFIG)
    has_overrides_key = isinstance(doc, dict) and ("symbol_overrides" in doc)
    for k, v in (doc or {}).items():
        if k == "symbol_overrides":
            # Authoritative from DB doc — no re-injection from defaults.
            out[k] = dict(v) if isinstance(v, dict) else {}
        elif k in ("score_weights", "session_windows") and isinstance(v, dict):
            merged = dict(DEFAULT_CONFIG.get(k) or {})
            merged.update(v)
            out[k] = merged
        else:
            out[k] = v
    # When the DB doc has no symbol_overrides key at all, fall back to defaults
    # (covers fresh installs and pre-fix legacy docs).
    if not has_overrides_key:
        out["symbol_overrides"] = dict(DEFAULT_CONFIG.get("symbol_overrides") or {})
    return out


def get_symbol_setting(cfg: Dict[str, Any], symbol: str, key: str, default: Any = None) -> Any:
    """Return symbol_overrides[SYMBOL][key] if set, else cfg[key], else default."""
    sym = (symbol or "").upper()
    overrides = (cfg.get("symbol_overrides") or {}).get(sym) or {}
    if key in overrides:
        return overrides[key]
    return cfg.get(key, default)


def is_metal(symbol: str) -> bool:
    s = (symbol or "").upper()
    return s.startswith("XAU") or s.startswith("XAG")


def current_session_name(cfg: Dict[str, Any], utc_hour: int) -> str:
    """Return the active session name for the given UTC hour using configured windows.
    Returns one of: 'asia' | 'london' | 'new_york' | 'overlap' | 'off'.
    Overlap = both London and NY are active.
    """
    windows = cfg.get("session_windows") or DEFAULT_CONFIG["session_windows"]
    def _in(w):  # noqa: E306
        s, e = int(w["start"]), int(w["end"])
        return s <= utc_hour < e
    in_asia = _in(windows.get("asia") or {"start": 0, "end": 8})
    in_lon  = _in(windows.get("london") or {"start": 8, "end": 16})
    in_ny   = _in(windows.get("new_york") or {"start": 13, "end": 21})
    if in_lon and in_ny:
        return "overlap"
    if in_lon:
        return "london"
    if in_ny:
        return "new_york"
    if in_asia:
        return "asia"
    return "off"
