#!/usr/bin/env bash
set -euo pipefail

docker compose up --build -d

echo "Waiting for service startup..."
sleep 5

curl -fsS http://localhost:8000/health | cat
curl -fsS http://localhost:8000/graph/stats | cat

echo "Local launch checks completed."
