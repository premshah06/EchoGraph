#!/usr/bin/env bash
set -euo pipefail

required=(HOST PORT CHROMADB_PERSIST_DIR ALLOWED_ORIGINS)
missing=0

for key in "${required[@]}"; do
  if [[ -z "${!key:-}" ]]; then
    echo "[ERROR] Missing env var: $key"
    missing=1
  else
    echo "[OK] $key=${!key}"
  fi
done

if [[ "${DEMO_MODE:-false}" != "true" && -z "${OPENAI_API_KEY:-}" ]]; then
  echo "[WARN] OPENAI_API_KEY not set; app will run in demo mode behavior"
fi

if [[ "$missing" -ne 0 ]]; then
  exit 1
fi

echo "Environment validation passed."
