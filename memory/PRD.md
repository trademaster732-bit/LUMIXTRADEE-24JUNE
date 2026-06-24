# LumixTrade — PRD

## Original Problem Statement
User uploaded a complete, working GitHub project ZIP (LUMIXTRADE-LIVE-main). Task: extract and adopt the codebase exactly as is — preserve all bot logic, fixes, schemas, endpoints, env values; deploy backend then frontend; confirm `/api/health`, admin login, and bridge endpoint behavior. Do NOT modify code or recreate anything.

## Architecture
- **Backend**: FastAPI (`/app/backend/server.py`) with engine.py, strategy_v2.py, risk_engine.py, quality_score.py, marketdata.py, notifications.py, backtest.py, backtest_v2.py, engine_config.py, data_validation.py. Bridge v1.9.1 referenced inside server. APScheduler scans every 3 min.
- **Frontend**: React 18 + TypeScript + craco + Tailwind
- **DB**: MongoDB (`lumixtrade_db`) at mongodb://localhost:27017
- **Auth**: JWT with seeded admin (admin@bot.com / password)

## Environment Variables (from problem statement, applied verbatim)
Backend `.env`: MONGO_URL, DB_NAME=lumixtrade_db, CORS_ORIGINS, JWT_SECRET, ADMIN_EMAIL, ADMIN_PASSWORD, AURUM_API_URL, STRATEGY_VERSION=v2
Frontend `.env`: REACT_APP_BACKEND_URL=https://lumix-bridge.preview.emergentagent.com

## What's Been Implemented (2026-01)
- Extracted user-provided ZIP and adopted full codebase into `/app` (no code modifications)
- Installed Python deps (requirements.txt) and frontend deps (yarn)
- Created `.env` files with exact values from the problem statement
- Restarted backend & frontend via supervisor
- Verified:
  - `GET /api/health` → `{"status":"ok","service":"lumixtrade-api","version":"1.8.1"}`
  - Admin login (admin@bot.com / password) returns valid JWT
  - `POST /api/bridge-poll` without key → 401
  - Admin auto-seeded on startup
  - APScheduler started; strategy_v2 active (conservative=True, min_confidence=0.62)

## Preserved (unchanged)
- All bot logic (strategy_v2.py, engine.py, risk_engine.py)
- All fixes (RR 1:2, S/R detection, lot cap, daily loss limit, trade scoring, session filters, cooldown)
- All DB schemas/collections, API endpoints, auth
- Bridge v1.9.1, frontend components/pages

## Preview URL
https://lumix-bridge.preview.emergentagent.com

## Next Action Items
- User to log in and verify bots/strategies render as expected
- Optionally seed/restore production data (out of scope unless requested)
