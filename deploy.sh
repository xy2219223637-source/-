#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="/opt/enrui-ai-commerce-agent"
REPO_DIR="$APP_DIR/repository"
SHARED_DIR="$APP_DIR/shared"
LOG_DIR="$SHARED_DIR/logs"
ENV_FILE="$SHARED_DIR/.env"
LOCK_DIR="$SHARED_DIR/.deploy.lock"
REPO_URL="https://github.com/Dreamsmama/enrui-ai-commerce-agent.git"
BRANCH="${DEPLOY_BRANCH:-main}"
FRONTEND_BIND="${FRONTEND_BIND:-127.0.0.1}"
FRONTEND_PORT="${FRONTEND_PORT:-8080}"
HEALTHCHECK_URL="${HEALTHCHECK_URL:-http://127.0.0.1:${FRONTEND_PORT}/api/health}"

export FRONTEND_BIND FRONTEND_PORT

if [[ "$(id -u)" -ne 0 ]]; then
  echo "请使用 root 执行：sudo bash deploy.sh"
  exit 1
fi

mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/deploy-$(date +%Y%m%d-%H%M%S).log"
exec > >(tee -a "$LOG_FILE") 2>&1

log() { printf '[%s] %s\n' "$(date '+%F %T%z')" "$*"; }
fail() { log "部署失败：$*"; exit 1; }

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  fail "已有部署正在执行（锁目录：$LOCK_DIR）"
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT

install_runtime() {
  if ! command -v git >/dev/null || ! command -v curl >/dev/null; then
    apt-get update
    apt-get install -y git ca-certificates curl
  fi
  if ! command -v docker >/dev/null; then
    apt-get update
    apt-get install -y docker.io
    apt-get install -y docker-compose-v2 || apt-get install -y docker-compose
    systemctl enable --now docker
  fi
  if ! docker info >/dev/null 2>&1; then
    systemctl start docker
  fi
}

compose() {
  if docker compose version >/dev/null 2>&1; then docker compose "$@"; else docker-compose "$@"; fi
}

wait_for_health() {
  local health
  for _ in $(seq 1 45); do
    if health="$(curl -fsS --max-time 5 "$HEALTHCHECK_URL" 2>/dev/null)" \
      && [[ "$health" == *'"status":"ok"'* ]] \
      && [[ "$health" == *'"database":"postgresql"'* ]] \
      && [[ "$health" == *'"redis":"ok"'* ]] \
      && [[ "$health" == *'"storage":"aliyun_oss"'* ]]; then
      printf '%s\n' "$health"
      return 0
    fi
    sleep 2
  done
  return 1
}

install_runtime
mkdir -p "$SHARED_DIR"

PREVIOUS_COMMIT=""
if [[ -d "$REPO_DIR/.git" ]]; then
  if [[ -n "$(git -C "$REPO_DIR" status --porcelain)" ]]; then
    fail "仓库存在未提交改动，请先清理：$REPO_DIR"
  fi
  PREVIOUS_COMMIT="$(git -C "$REPO_DIR" rev-parse HEAD)"
fi

if [[ ! -d "$REPO_DIR/.git" ]]; then
  log "首次拉取仓库：$REPO_URL ($BRANCH)"
  git clone --branch "$BRANCH" --single-branch "$REPO_URL" "$REPO_DIR"
else
  log "拉取最新代码：$BRANCH"
  git -C "$REPO_DIR" fetch --prune origin \
    "+refs/heads/$BRANCH:refs/remotes/origin/$BRANCH"
  git -C "$REPO_DIR" checkout --quiet -B "$BRANCH" "origin/$BRANCH"
fi

COMMIT="$(git -C "$REPO_DIR" rev-parse HEAD)"
SHORT_COMMIT="$(git -C "$REPO_DIR" rev-parse --short HEAD)"
export DEPLOY_IMAGE_TAG="$SHORT_COMMIT"
log "目标 Git 提交：$COMMIT"

if [[ ! -f "$ENV_FILE" ]]; then
  cp "$REPO_DIR/deploy/.env.production.example" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  log "已创建 $ENV_FILE，请填写全部 FILL_ 配置后重新执行本脚本"
  exit 2
fi

if grep -Eq 'FILL_[A-Z0-9_]+' "$ENV_FILE"; then
  fail "$ENV_FILE 仍包含 FILL_ 占位符，拒绝部署"
fi

cd "$REPO_DIR"
export APP_ENV_FILE="$ENV_FILE"
PRIVATE_VIDEO_S3_HOST_FILE="$SHARED_DIR/hz.env"
if grep -Eq '^PRIVATE_VIDEO_S3_ENV_FILE=/run/secrets/dlr-hz\.env[[:space:]]*$' "$ENV_FILE" \
  && [[ ! -f "$PRIVATE_VIDEO_S3_HOST_FILE" ]]; then
  fail "缺少 DLR 私有存储配置文件：$PRIVATE_VIDEO_S3_HOST_FILE"
fi
if [[ -f "$PRIVATE_VIDEO_S3_HOST_FILE" ]]; then
  chmod 600 "$PRIVATE_VIDEO_S3_HOST_FILE"
  export PRIVATE_VIDEO_S3_HOST_FILE
fi
OLD_BACKEND="$(compose ps -q backend 2>/dev/null || true)"
OLD_FRONTEND="$(compose ps -q frontend 2>/dev/null || true)"
OLD_BACKEND_IMAGE=""
OLD_FRONTEND_IMAGE=""
if [[ -n "$OLD_BACKEND" ]]; then OLD_BACKEND_IMAGE="$(docker inspect --format '{{.Config.Image}}' "$OLD_BACKEND")"; fi
if [[ -n "$OLD_FRONTEND" ]]; then OLD_FRONTEND_IMAGE="$(docker inspect --format '{{.Config.Image}}' "$OLD_FRONTEND")"; fi

log "构建提交 $SHORT_COMMIT 的前后端镜像"
compose build --pull
log "执行 PostgreSQL/Redis/OSS 幂等检查（不迁移、不清空业务数据）"
compose run --rm backend python scripts/init_deployment.py
log "启动新版本容器"
compose up -d --remove-orphans

if wait_for_health; then
  log "部署成功：$(hostname -I | awk '{print $1}')"
  log "Git 提交：$COMMIT"
  log "日志文件：$LOG_FILE"
  compose ps
  docker image prune -f >/dev/null || true
  exit 0
fi

compose ps
compose logs --tail=120 backend frontend || true
if [[ -z "$OLD_BACKEND_IMAGE" || -z "$OLD_FRONTEND_IMAGE" ]]; then
  fail "新版本健康检查失败，未发现可回退的旧版本"
fi

ROLLBACK_FILE="$SHARED_DIR/docker-compose.rollback.yml"
cat > "$ROLLBACK_FILE" <<EOF
services:
  backend:
    image: $OLD_BACKEND_IMAGE
    build: null
  frontend:
    image: $OLD_FRONTEND_IMAGE
    build: null
EOF
log "新版本健康检查失败，回退到旧版本镜像"
compose down --remove-orphans || true
COMPOSE_FILE="$REPO_DIR/docker-compose.yml:$ROLLBACK_FILE" compose up -d --remove-orphans
if wait_for_health; then
  log "已回退到旧版本，原提交：${PREVIOUS_COMMIT:-unknown}"
else
  compose ps
  compose logs --tail=120 backend frontend || true
  fail "新旧版本健康检查均失败，请人工处理"
fi
exit 1
