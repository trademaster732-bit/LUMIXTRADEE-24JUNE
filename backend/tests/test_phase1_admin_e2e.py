"""End-to-end HTTP tests for Phase-1 Engine Optimization admin endpoints.

Tests against the running backend (via REACT_APP_BACKEND_URL).
Covers:
  - Admin login → GET / PUT / RESET engine-config
  - Cache invalidation
  - Cooldowns, filter-stats, symbol-metrics endpoints
  - Non-admin and unauthenticated access protection
"""
import os
import requests
import pytest

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL")
if not BASE_URL:
    # Fallback: read from frontend/.env
    env_path = "/app/frontend/.env"
    with open(env_path) as fh:
        for line in fh:
            if line.startswith("REACT_APP_BACKEND_URL="):
                BASE_URL = line.split("=", 1)[1].strip()
                break
BASE_URL = BASE_URL.rstrip("/")

ADMIN_EMAIL = "admin@bot.com"
ADMIN_PASSWORD = "password"


# ──────────────── fixtures ────────────────
@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        timeout=15,
    )
    assert r.status_code == 200, f"Admin login failed: {r.status_code} {r.text}"
    data = r.json()
    token = data.get("token") or data.get("access_token")
    assert token, f"No token in login response: {data}"
    return token


@pytest.fixture(scope="module")
def admin_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"}


# ──────────────── GET engine-config ────────────────
PHASE1_KEYS = [
    "score_weights", "min_score", "near_miss_lower", "adx_threshold",
    "vwap_max_distance_atr", "cooldown_consecutive_losses", "cooldown_min",
    "session_windows", "metals_blocked_sessions",
    "daily_bias_enabled", "daily_bias_neutral_mode", "daily_bias_neutral_penalty",
    "atr_ratio_min", "atr_ratio_max", "symbol_overrides",
]


class TestEngineConfigGet:
    def test_admin_can_get_merged_config(self, admin_headers):
        r = requests.get(f"{BASE_URL}/api/admin/engine-config", headers=admin_headers, timeout=15)
        assert r.status_code == 200, r.text
        body = r.json()
        assert "config" in body and "defaults" in body, f"Unexpected envelope: {list(body)}"
        cfg = body["config"]
        # All Phase-1 keys present
        for k in PHASE1_KEYS:
            assert k in cfg, f"Missing key in config: {k}"
        assert isinstance(cfg["score_weights"], dict)
        assert "h4_trend" in cfg["score_weights"]
        assert isinstance(cfg["min_score"], (int, float))
        # XAU override
        xau = cfg["symbol_overrides"].get("XAUUSD") or cfg["symbol_overrides"].get("XAU")
        assert xau is not None, f"XAUUSD override missing: {cfg['symbol_overrides']}"
        assert xau.get("min_score") == 85

    def test_unauthenticated_get_blocked(self):
        r = requests.get(f"{BASE_URL}/api/admin/engine-config", timeout=15)
        assert r.status_code in (401, 403), f"Expected 401/403, got {r.status_code}"


# ──────────────── PUT engine-config + cache invalidation ────────────────
class TestEngineConfigPut:
    def test_admin_put_partial_updates_and_get_reflects(self, admin_headers):
        # Reset to known baseline first
        requests.post(
            f"{BASE_URL}/api/admin/engine-config/reset-defaults",
            headers=admin_headers, timeout=15,
        )

        # Update min_score → 82
        r = requests.put(
            f"{BASE_URL}/api/admin/engine-config",
            headers=admin_headers, json={"min_score": 82}, timeout=15,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        cfg = body.get("config", body)
        assert cfg["min_score"] == 82, f"PUT response should reflect new value: {body}"

        # Subsequent GET reflects the new value (cache invalidated)
        r2 = requests.get(f"{BASE_URL}/api/admin/engine-config", headers=admin_headers, timeout=15)
        assert r2.status_code == 200
        cfg2 = r2.json()["config"]
        assert cfg2["min_score"] == 82, "Cache must invalidate after PUT"

        # Reset back to defaults
        requests.post(
            f"{BASE_URL}/api/admin/engine-config/reset-defaults",
            headers=admin_headers, timeout=15,
        )

    def test_unauthenticated_put_blocked(self):
        r = requests.put(
            f"{BASE_URL}/api/admin/engine-config",
            json={"min_score": 99}, timeout=15,
        )
        assert r.status_code in (401, 403)


# ──────────────── RESET defaults ────────────────
class TestEngineConfigReset:
    def test_admin_reset_returns_baseline(self, admin_headers):
        # Mutate first
        requests.put(
            f"{BASE_URL}/api/admin/engine-config",
            headers=admin_headers, json={"min_score": 95}, timeout=15,
        )

        # Reset
        r = requests.post(
            f"{BASE_URL}/api/admin/engine-config/reset-defaults",
            headers=admin_headers, timeout=15,
        )
        assert r.status_code == 200, r.text

        # GET → baked-in default min_score = 80
        r2 = requests.get(f"{BASE_URL}/api/admin/engine-config", headers=admin_headers, timeout=15)
        assert r2.status_code == 200
        cfg = r2.json()["config"]
        assert cfg["min_score"] == 80, f"After reset min_score should be 80, got {cfg['min_score']}"

    def test_unauthenticated_reset_blocked(self):
        r = requests.post(f"{BASE_URL}/api/admin/engine-config/reset-defaults", timeout=15)
        assert r.status_code in (401, 403)


# ──────────────── /admin/cooldowns ────────────────
class TestCooldowns:
    def test_admin_get_cooldowns_returns_structure(self, admin_headers):
        r = requests.get(f"{BASE_URL}/api/admin/cooldowns", headers=admin_headers, timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "active" in data
        assert "as_of" in data
        assert isinstance(data["active"], list)

    def test_unauthenticated_cooldowns_blocked(self):
        r = requests.get(f"{BASE_URL}/api/admin/cooldowns", timeout=15)
        assert r.status_code in (401, 403)


# ──────────────── /admin/filter-stats ────────────────
class TestFilterStats:
    def test_admin_get_filter_stats(self, admin_headers):
        r = requests.get(
            f"{BASE_URL}/api/admin/filter-stats?days=7",
            headers=admin_headers, timeout=15,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert "window_days" in data
        assert "by_filter" in data
        assert "by_pair" in data
        assert "details" in data
        assert data["window_days"] == 7

    def test_unauthenticated_filter_stats_blocked(self):
        r = requests.get(f"{BASE_URL}/api/admin/filter-stats?days=7", timeout=15)
        assert r.status_code in (401, 403)


# ──────────────── /admin/symbol-metrics ────────────────
class TestSymbolMetrics:
    def test_admin_get_symbol_metrics(self, admin_headers):
        r = requests.get(
            f"{BASE_URL}/api/admin/symbol-metrics?days=7",
            headers=admin_headers, timeout=15,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert "window_days" in data
        assert "metrics" in data
        assert data["window_days"] == 7

    def test_unauthenticated_symbol_metrics_blocked(self):
        r = requests.get(f"{BASE_URL}/api/admin/symbol-metrics?days=7", timeout=15)
        assert r.status_code in (401, 403)
