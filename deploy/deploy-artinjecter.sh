#!/usr/bin/env bash
set -Eeuo pipefail
export ENRUI_DEPLOY_APP_DIR="/opt/enrui-ai-commerce-agent-artinjecter"
export ENRUI_DEPLOY_DATABASE_MODE=shared
export COMPOSE_PROJECT_NAME=enrui-artinjecter
export COMPOSE_PARALLEL_LIMIT=1
export FRONTEND_BIND=127.0.0.1 FRONTEND_PORT=8280
export BACKEND_BIND=127.0.0.1 BACKEND_PORT=8200
export HEALTHCHECK_URL=http://127.0.0.1:8280/api/health
exec bash /opt/enrui-ai-commerce-agent/deploy-production.sh "$@"
