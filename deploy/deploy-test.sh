#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="/opt/enrui-ai-commerce-agent-test"
REPO_DIR="$APP_DIR/repository"
SHARED_DIR="$APP_DIR/shared"
LOG_DIR="$SHARED_DIR/logs"
ENV_FILE="$SHARED_DIR/.env"
COMPOSE_ENV_FILE="$SHARED_DIR/compose.env"
BASE_IMAGES_FILE="$SHARED_DIR/base-images.env"
LOCK_DIR="$SHARED_DIR/.deploy.lock"
ROLLBACK_FILE="$SHARED_DIR/docker-compose.rollback.yml"
RESOURCE_FILE="$SHARED_DIR/test-resources.json"
MIGRATION_ENV_FILE="$SHARED_DIR/migration.env"
RUNTIME_DIR="$APP_DIR/runtime"
REPO_URL="https://gitee.com/enrui_1/enrui-ai-commerce-agent.git"
BUILD_PIP_INDEX_URL="${BUILD_PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
COMPOSE_PROJECT_NAME="enrui-test"
SELECTED_BRANCH="${DEPLOY_BRANCH:-${TEST_DEPLOY_BRANCH:-feat/domain-management}}"
HEALTHCHECK_URL="${HEALTHCHECK_URL:-http://127.0.0.1:8180/api/health}"
export COMPOSE_PARALLEL_LIMIT=1

if [[ $# -gt 1 ]]; then
  echo "用法: bash /opt/enrui-ai-commerce-agent-test/deploy.sh [branch]"
  exit 2
fi
case "${1:-}" in
  --help|-h)
    echo "用法: bash /opt/enrui-ai-commerce-agent-test/deploy.sh [branch]"
    echo "默认拉取 feat/domain-management; 支持 DEPLOY_BRANCH 或 TEST_DEPLOY_BRANCH 覆盖."
    echo "仅支持远端分支, 不支持 commit 或 tag; 交互选择请使用统一入口."
    echo "基础镜像固定配置: shared/base-images.env; 升级基础镜像需主动修改该文件."
    exit 0
    ;;
  -*) echo "未知参数: $1"; exit 2 ;;
  '') ;;
  *) SELECTED_BRANCH="$1" ;;
esac

if [[ -n "${TEST_GIT_REF:-}" ]]; then
  echo "不再支持 TEST_GIT_REF, 请使用分支参数或 DEPLOY_BRANCH"
  exit 2
fi

if [[ "$(id -u)" -ne 0 ]]; then
  echo "请使用 root 执行: sudo bash deploy/deploy-test.sh <branch>"
  exit 1
fi

if [[ -z "$SELECTED_BRANCH" || "$SELECTED_BRANCH" == -* ]] \
  || ! git check-ref-format "refs/heads/$SELECTED_BRANCH" >/dev/null 2>&1; then
  echo "无效的测试分支名: $SELECTED_BRANCH"
  exit 1
fi

# Run from a stable snapshot: checking out another branch may replace this file.
if [[ "${ENRUI_TEST_SCRIPT_SNAPSHOT:-}" != "${BASH_SOURCE[0]}" ]]; then
  SNAPSHOT="$(mktemp /tmp/enrui-deploy-test.XXXXXX)"
  trap 'rm -f -- "$SNAPSHOT"' EXIT
  cp -- "${BASH_SOURCE[0]}" "$SNAPSHOT"
  ENRUI_TEST_SCRIPT_SNAPSHOT="$SNAPSHOT" bash "$SNAPSHOT" "$SELECTED_BRANCH"
  exit $?
fi

mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/deploy-$(date +%Y%m%d-%H%M%S).log"
exec > >(tee -a "$LOG_FILE") 2>&1

log() { printf '[%s] %s\n' "$(date '+%F %T%z')" "$*"; }
fail() { log "测试环境部署失败: $*"; exit 1; }

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  fail "已有测试环境部署正在执行, 锁目录: $LOCK_DIR"
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT

install_runtime() {
  for tool in git curl docker python3; do
    command -v "$tool" >/dev/null || fail "缺少 $tool, 请先配置运行环境"
  done
  docker info >/dev/null 2>&1 || fail "Docker 不可用"
  docker compose version >/dev/null 2>&1 || fail "需要 Docker Compose v2 或更新版本"
}

compose() {
  docker compose --project-name "$COMPOSE_PROJECT_NAME" --env-file "$COMPOSE_ENV_FILE" \
    "${COMPOSE_FILES[@]}" "$@"
}

compose_rollback() {
  compose -f "$ROLLBACK_FILE" "$@"
}

compose_migration() {
  compose -f "$RUNTIME_DIR/migration.yml" "$@"
}

backup_test_database() {
  local pg backup
  pg="$(docker ps -q --filter label=com.docker.compose.project=enrui-test-infra \
    --filter label=com.docker.compose.service=postgres-test)"
  [[ -n "$pg" && "$pg" != *$'\n'* ]] || fail "未找到唯一测试 PostgreSQL 容器"
  mkdir -p "$SHARED_DIR/backups"
  chmod 700 "$SHARED_DIR/backups"
  backup="$SHARED_DIR/backups/database-$(date +%Y%m%d-%H%M%S)-$SHORT_COMMIT.dump"
  (umask 077; docker exec -u postgres "$pg" pg_dump -U postgres -d enrui_ai_commerce_agent_test -Fc > "$backup") \
    || fail "测试数据库备份失败, 禁止迁移"
  [[ -s "$backup" ]] || fail "测试数据库备份为空"
  docker exec -i "$pg" pg_restore --list < "$backup" >/dev/null || fail "测试数据库备份不可读取"
  log "测试数据库备份: $backup"
}

env_value() {
  local file="$1"
  local key="$2"
  local value
  value="$( (grep -E "^${key}=" "$file" || true) | tail -n 1 | cut -d= -f2-)"
  case "$value" in
    \"*\"|\'*\') value="${value:1:${#value}-2}" ;;
  esac
  printf '%s\n' "$value"
}

require_env_value() {
  local file="$1"
  local key="$2"
  local expected="$3"
  local actual
  actual="$(env_value "$file" "$key")"
  [[ "$actual" == "$expected" ]] || fail "$file 中 $key 必须为 $expected"
}

wait_for_health() {
  local health
  for _ in $(seq 1 45); do
    if health="$(curl -fsS --max-time 5 "$HEALTHCHECK_URL" 2>/dev/null)" \
      && [[ "$health" == *'"status":"ok"'* ]] \
      && [[ "$health" == *'"database":"postgresql"'* ]] \
      && [[ "$health" == *'"redis":"ok"'* ]] \
      && [[ "$health" == *'"storage":"aliyun_oss"'* ]] \
      && [[ "$health" == *'"deployment_environment":"test"'* ]] \
      && [[ "$health" == *'"background_workers_enabled":true'* ]] \
      && [[ "$health" == *'"production_queue":true'* ]] \
      && [[ "$health" == *'"video_analysis":true'* ]]; then
      printf '%s\n' "$health"
      return 0
    fi
    sleep 2
  done
  return 1
}

install_runtime
mkdir -p "$SHARED_DIR"

if [[ -d "$REPO_DIR/.git" && -n "$(git -C "$REPO_DIR" status --porcelain)" ]]; then
  fail "测试仓库存在未提交改动, 请先处理: $REPO_DIR"
fi

if [[ ! -d "$REPO_DIR/.git" ]]; then
  log "首次创建测试仓库: $REPO_URL"
  git clone --no-checkout "$REPO_URL" "$REPO_DIR"
fi

log "拉取测试分支: $SELECTED_BRANCH"
git -C "$REPO_DIR" fetch --no-tags origin "refs/heads/$SELECTED_BRANCH" \
  || fail "无法拉取远端分支: $SELECTED_BRANCH"
COMMIT="$(git -C "$REPO_DIR" rev-parse 'FETCH_HEAD^{commit}')"
git -C "$REPO_DIR" checkout --quiet --detach "$COMMIT"
SHORT_COMMIT="$(git -C "$REPO_DIR" rev-parse --short "$COMMIT")"
export DEPLOY_IMAGE_TAG="test-$SHORT_COMMIT"
log "目标 Git 提交: $COMMIT"

[[ -f "$ENV_FILE" && -f "$COMPOSE_ENV_FILE" ]] \
  || fail "缺少 shared/.env 或 compose.env, 请先初始化测试环境"
for file in compose.yml migration.yml runtime.py isolation_guard.py; do
  [[ -f "$RUNTIME_DIR/$file" ]] || fail "服务器测试部署组件缺失: $RUNTIME_DIR/$file"
done
[[ -f "$REPO_DIR/backend/Dockerfile" && -f "$REPO_DIR/frontend/Dockerfile" \
   && -f "$REPO_DIR/backend/app/main.py" ]] || fail "目标分支缺少应用源码或 Dockerfile"
export TEST_REPOSITORY_DIR="$REPO_DIR" TEST_DEPLOY_RUNTIME_DIR="$RUNTIME_DIR" TEST_SHARED_DIR="$SHARED_DIR"
COMPOSE_FILES=(-f "$RUNTIME_DIR/compose.yml")
[[ -f "$SHARED_DIR/prepare-build-cache.py" ]] || fail "服务器构建缓存组件缺失: $SHARED_DIR/prepare-build-cache.py"
[[ -f "$BASE_IMAGES_FILE" ]] || fail "缺少基础镜像固定配置: $BASE_IMAGES_FILE"
python3 "$SHARED_DIR/prepare-build-cache.py" "$REPO_DIR" "$SHARED_DIR" "$BUILD_PIP_INDEX_URL" "$BASE_IMAGES_FILE"
COMPOSE_FILES+=(-f "$SHARED_DIR/docker-compose.build-cache.yml")

chmod 600 "$ENV_FILE" "$COMPOSE_ENV_FILE"
if grep -Eq 'FILL_[A-Z0-9_]+' "$ENV_FILE"; then
  fail "$ENV_FILE 仍包含 FILL_ 占位符, 拒绝部署"
fi

require_env_value "$ENV_FILE" RUN_DATABASE_INIT false
require_env_value "$ENV_FILE" RUN_STARTUP_RECOVERY true
require_env_value "$ENV_FILE" RUN_BACKGROUND_WORKERS true
[[ -f "$RESOURCE_FILE" && -f "$MIGRATION_ENV_FILE" ]] || fail "请先创建独立测试基础服务和资源清单"
chmod 600 "$RESOURCE_FILE" "$MIGRATION_ENV_FILE"
require_env_value "$ENV_FILE" REDIS_KEY_PREFIX 'enrui-ai-commerce-agent:test:'
require_env_value "$ENV_FILE" ALIYUN_OSS_PREFIX 'enrui-ai-commerce-agent/test/'
require_env_value "$ENV_FILE" APP_PUBLIC_BASE_URL 'https://test.waynepartners.xyz'
require_env_value "$COMPOSE_ENV_FILE" COMPOSE_PROJECT_NAME "$COMPOSE_PROJECT_NAME"
require_env_value "$COMPOSE_ENV_FILE" APP_ENV_FILE "$ENV_FILE"
require_env_value "$COMPOSE_ENV_FILE" FRONTEND_BIND '127.0.0.1'
require_env_value "$COMPOSE_ENV_FILE" FRONTEND_PORT '8180'
require_env_value "$COMPOSE_ENV_FILE" BACKEND_BIND '127.0.0.1'
require_env_value "$COMPOSE_ENV_FILE" BACKEND_PORT '8100'
# Shell variables take precedence over --env-file. Pin the isolation boundary.
export APP_ENV_FILE="$ENV_FILE" COMPOSE_PROJECT_NAME
export FRONTEND_BIND=127.0.0.1 FRONTEND_PORT=8180 BACKEND_BIND=127.0.0.1 BACKEND_PORT=8100
if [[ -n "$(env_value "$ENV_FILE" PRIVATE_VIDEO_TENANTS)" ]]; then
  export PRIVATE_VIDEO_S3_HOST_FILE="$SHARED_DIR/private-video-test.env"
  [[ -f "$PRIVATE_VIDEO_S3_HOST_FILE" ]] || fail "缺少测试专用 S3 配置文件"
  COMPOSE_FILES+=(-f "$RUNTIME_DIR/private-video.yml")
fi
compose config --quiet

OLD_BACKEND="$(compose ps -q backend 2>/dev/null || true)"
OLD_FRONTEND="$(compose ps -q frontend 2>/dev/null || true)"
OLD_BACKEND_IMAGE=""
OLD_FRONTEND_IMAGE=""
OLD_BACKEND_ID=""
OLD_FRONTEND_ID=""
if [[ -n "$OLD_BACKEND" ]]; then
  OLD_BACKEND_ID="$(docker inspect --format '{{.Image}}' "$OLD_BACKEND")"
  OLD_BACKEND_IMAGE="enrui-ai-commerce-agent-backend:rollback-${OLD_BACKEND_ID#sha256:}"
  docker image tag "$OLD_BACKEND_ID" "$OLD_BACKEND_IMAGE"
fi
if [[ -n "$OLD_FRONTEND" ]]; then
  OLD_FRONTEND_ID="$(docker inspect --format '{{.Image}}' "$OLD_FRONTEND")"
  OLD_FRONTEND_IMAGE="enrui-ai-commerce-agent-frontend:rollback-${OLD_FRONTEND_ID#sha256:}"
  docker image tag "$OLD_FRONTEND_ID" "$OLD_FRONTEND_IMAGE"
fi

log "串行构建测试镜像: $DEPLOY_IMAGE_TAG"
compose build backend
compose build frontend
log "在迁移前校验独立 PostgreSQL, Redis 和 OSS 目标"
compose run --rm --no-deps backend check --before-migration
log "使用独立迁移账号显式初始化或迁移测试库"
compose stop backend frontend
backup_test_database
if ! compose_migration run --rm --no-deps backend migrate; then
  fail "测试数据库迁移失败, 测试应用保持停止, 请检查备份和结构后重新部署"
fi
compose run --rm --no-deps backend check \
  || fail "迁移后的结构或运行账号权限验证失败, 测试应用保持停止"
log "仅更新 $COMPOSE_PROJECT_NAME Compose 项目"
if compose up -d --no-build --remove-orphans && wait_for_health; then
  sed -i '/^DEPLOY_IMAGE_TAG=/d' "$COMPOSE_ENV_FILE"
  printf 'DEPLOY_IMAGE_TAG=%s\n' "$DEPLOY_IMAGE_TAG" >> "$COMPOSE_ENV_FILE"
  printf '%s\n' "$COMMIT" > "$SHARED_DIR/deployed-commit"
  printf '%s\n%s\n' "$(docker inspect --format '{{.Image}}' "$(compose ps -q backend)")" \
    "$(docker inspect --format '{{.Image}}' "$(compose ps -q frontend)")" > "$SHARED_DIR/isolated-images"
  log "测试环境部署成功"
  log "Git 提交: $COMMIT"
  log "健康检查: $HEALTHCHECK_URL"
  log "日志文件: $LOG_FILE"
  compose ps
  exit 0
fi

compose ps
compose logs --tail=120 backend frontend || true
if [[ -z "$OLD_BACKEND_IMAGE" || -z "$OLD_FRONTEND_IMAGE" ]]; then
  compose stop backend frontend || true
  fail "新版本健康检查失败, 未发现可回退的旧测试镜像"
fi

if [[ ! -f "$SHARED_DIR/isolated-images" ]] \
  || ! grep -Fxq "$OLD_BACKEND_ID" "$SHARED_DIR/isolated-images" \
  || ! grep -Fxq "$OLD_FRONTEND_ID" "$SHARED_DIR/isolated-images"; then
  compose stop backend frontend || true
  fail "旧镜像未通过独立资源验收, 测试应用保持停止, 禁止恢复共享生产连接"
fi

cat > "$ROLLBACK_FILE" <<EOF
services:
  backend:
    image: $OLD_BACKEND_IMAGE
    build: null
  frontend:
    image: $OLD_FRONTEND_IMAGE
    build: null
EOF
chmod 600 "$ROLLBACK_FILE"
compose_rollback run --rm --no-deps backend check \
  || { compose stop backend frontend; fail "旧镜像与当前测试结构不兼容, 停止自动回退"; }
log "新版本健康检查失败, 仅回退 $COMPOSE_PROJECT_NAME 到旧测试镜像"
if compose_rollback up -d --no-build --remove-orphans && wait_for_health; then
  log "测试环境已回退到旧镜像"
else
  compose_rollback ps
  compose_rollback logs --tail=120 backend frontend || true
  compose_rollback stop backend frontend || true
  fail "新旧测试版本健康检查均失败, 请人工处理"
fi
exit 1
