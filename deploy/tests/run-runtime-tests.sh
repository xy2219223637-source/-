#!/usr/bin/env bash
set -Eeuo pipefail
REPO="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
IMAGE="${BACKEND_TEST_IMAGE:?Set BACKEND_TEST_IMAGE to an existing branch backend image with dependencies}"
VALIDATION=$(mktemp -d /tmp/enrui-runtime-test-XXXXXXXX)
NETWORK="enrui-runtime-test-$(basename "$VALIDATION")"
POSTGRES="$NETWORK-postgres"
cleanup() {
  docker rm -fv "$POSTGRES" >/dev/null 2>&1 || true
  docker network rm "$NETWORK" >/dev/null 2>&1 || true
}
trap cleanup EXIT
docker network create --internal "$NETWORK" >/dev/null
docker run -d --name "$POSTGRES" --network "$NETWORK" --network-alias isolation-postgres \
  -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_DB=isolation postgres:16 >/dev/null
for attempt in $(seq 1 30); do
  docker exec "$POSTGRES" pg_isready -U postgres -d isolation >/dev/null 2>&1 && break
  sleep 1
done
validate() {
  docker run --rm --network "$NETWORK" \
    -v "$REPO:/workspace:ro" -v "$REPO/backend/app:/app/app:ro" -v "$VALIDATION:/run/isolation" \
    -e DEPLOYMENT_SERVICE_ID="$1" -e ISOLATION_TEST_POSTGRES=1 \
    -e DATABASE_URL=postgresql+psycopg://postgres@isolation-postgres:5432/isolation \
    -e REQUIRE_POSTGRES=false -e REQUIRE_ONLINE_SERVICES=false -e REDIS_URL= \
    -e STORAGE_PROVIDER=local -e QIANCHUAN_ENABLED=false -e FEISHU_ENABLED=false \
    "$IMAGE" python /workspace/deploy/tests/test_task_isolation.py "$2"
}
validate production migrate
validate production seed
validate artinjecter seed
validate production recover
validate artinjecter recover
validate production select
validate artinjecter select
validate production routing
docker run --rm --network none \
  -v "$REPO:/workspace:ro" -v "$REPO/backend/app:/app/app:ro" -v "$VALIDATION:/run/isolation" \
  -e DATABASE_URL=sqlite:////run/isolation/test-workers.sqlite \
  -e REQUIRE_POSTGRES=false -e REQUIRE_ONLINE_SERVICES=false -e REDIS_URL= \
  -e STORAGE_PROVIDER=local -e QIANCHUAN_ENABLED=false -e FEISHU_ENABLED=false \
  -e VIDEO_REPAIR_V4_ENABLED=true -e VIDEO_REPAIR_ASYNC_WORKER_ENABLED=true \
  "$IMAGE" python /workspace/deploy/tests/test_test_workers.py
printf 'Runtime tests passed; isolated fixtures: %s\n' "$VALIDATION"
