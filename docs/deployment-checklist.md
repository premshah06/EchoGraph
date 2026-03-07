# Deployment Checklist

## Pre-Deploy

- [x] Verify `.env` values for production host, CORS, and API key
- [x] Validate `docker-compose.yml` and volume persistence settings
- [x] Confirm FastAPI health endpoint responds (`/health`)
- [x] Confirm WebSocket endpoint responds (`/stream/{session_id}`)

## Build and Release

- [x] Build image: `docker compose build`
- [x] Start stack: `docker compose up -d`
- [x] Verify logs: `docker compose logs -f echosystem`
- [x] Run smoke requests (`/graph/stats`, `/query`)

## Post-Deploy

- [x] Verify Chroma persistence survives restart
- [x] Check request-rate limiting and response headers
- [x] Check event streaming in browser
- [x] Confirm error surfaces are user-friendly
