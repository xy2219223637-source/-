#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${ENRUI_DEPLOY_APP_DIR:-/opt/enrui-ai-commerce-agent}"
REPO_DIR="$APP_DIR/repository"
SHARED_DIR="$APP_DIR/shared"
LOG_DIR="$SHARED_DIR/logs"
ENV_FILE="$SHARED_DIR/.env"
LOCK_DIR="$SHARED_DIR/.deploy.lock"
SERVER_COMPOSE_FILE="$SHARED_DIR/docker-compose.server.yml"
BASE_IMAGES_FILE="$SHARED_DIR/base-images.env"
DEFAULT_REPO_URL="https://github.com/Dreamsmama/enrui-ai-commerce-agent.git"
BUILD_PIP_INDEX_URL="${BUILD_PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
FRONTEND_BIND="${FRONTEND_BIND:-127.0.0.1}"
FRONTEND_PORT="${FRONTEND_PORT:-8080}"
HEALTHCHECK_URL="${HEALTHCHECK_URL:-http://127.0.0.1:${FRONTEND_PORT}/api/health}"

export FRONTEND_BIND FRONTEND_PORT
export DOCKER_BUILDKIT=1

DEPLOY_DATABASE_MODE="${ENRUI_DEPLOY_DATABASE_MODE:-initialize}"
[[ "$DEPLOY_DATABASE_MODE" == initialize || "$DEPLOY_DATABASE_MODE" == shared ]] || exit 2
case "$APP_DIR" in
  /opt/enrui-ai-commerce-agent) export DEPLOYMENT_SERVICE_ID=production ;;
  /opt/enrui-ai-commerce-agent-artinjecter) export DEPLOYMENT_SERVICE_ID=artinjecter ;;
  *) printf 'Unsupported service directory: %s\n' "$APP_DIR" >&2; exit 2 ;;
esac
DEPLOY_MODE=code
PULL_BASE_IMAGES=0

usage() {
  cat <<'EOF'
用法: bash /opt/enrui-ai-commerce-agent/deploy.sh [--config-only | --pull]

默认: 选择分支并部署代码, 使用 shared/base-images.env 中固定的基础镜像, 复用构建缓存.
--config-only: 保留当前镜像, 仅重新创建容器以应用配置和挂载, 不拉代码或构建.
--pull: 重新拉取已固定 digest 的基础镜像, 不自动升级; 升级需主动修改 shared/base-images.env.
--help: 显示说明.
非交互代码部署继续使用 DEPLOY_BRANCH 指定分支.
EOF
}

for argument in "$@"; do
  case "$argument" in
    --config-only) DEPLOY_MODE=config ;;
    --pull) PULL_BASE_IMAGES=1 ;;
    --help|-h) usage; exit 0 ;;
    *) printf '未知参数: %s\n' "$argument" >&2; usage; exit 2 ;;
  esac
done
if [[ "$DEPLOY_MODE" == config && "$PULL_BASE_IMAGES" == 1 ]]; then
  printf '%s\n' '--config-only 不能与 --pull 同时使用.' >&2
  exit 2
fi

log() {
  printf '[%s] %s\n' "$(date '+%F %T%z')" "$*"
}

fail() {
  log "部署失败: $*"
  exit 1
}

if [[ "$(id -u)" -ne 0 ]]; then
  fail "请使用 root 执行: sudo bash $APP_DIR/deploy.sh"
fi

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

select_branch() {
  local remote_source
  local branch
  local index
  local other_index
  local selection
  local selection_number
  local confirmation
  local -a remote_branches
  local -a ordered_branches

  if [[ -d "$REPO_DIR/.git" ]]; then
    remote_source="$(git -C "$REPO_DIR" remote get-url origin)"
  else
    remote_source="$DEFAULT_REPO_URL"
  fi

  mapfile -t remote_branches < <(
    git ls-remote --heads "$remote_source" \
      | awk '{sub("^refs/heads/", "", $2); print $2}' \
      | LC_ALL=C sort -u
  )
  [[ "${#remote_branches[@]}" -gt 0 ]] \
    || fail "无法从仓库读取远端分支: $remote_source"

  branch_exists() {
    local candidate="$1"
    local remote_branch

    for remote_branch in "${remote_branches[@]}"; do
      if [[ "$remote_branch" == "$candidate" ]]; then
        return 0
      fi
    done
    return 1
  }

  validate_branch() {
    local candidate="$1"

    [[ -n "$candidate" ]] || fail "分支名不能为空"
    git check-ref-format --branch "$candidate" >/dev/null 2>&1 \
      || fail "无效的分支名: $candidate"
    branch_exists "$candidate" \
      || fail "远端仓库不存在分支: $candidate"
  }

  if [[ -n "${DEPLOY_BRANCH:-}" ]]; then
    SELECTED_BRANCH="$DEPLOY_BRANCH"
    validate_branch "$SELECTED_BRANCH"
    printf '使用 DEPLOY_BRANCH 部署分支: %s\n' "$SELECTED_BRANCH"
    return
  fi

  [[ -t 0 ]] || fail "非交互执行时必须设置 DEPLOY_BRANCH"

  ordered_branches=()
  for branch in "${remote_branches[@]}"; do
    if [[ "$branch" == "main" ]]; then
      ordered_branches+=("$branch")
    fi
  done
  for branch in "${remote_branches[@]}"; do
    if [[ "$branch" != "main" ]]; then
      ordered_branches+=("$branch")
    fi
  done

  printf '请选择需要部署的分支:\n\n'
  for index in "${!ordered_branches[@]}"; do
    printf '%d. %s\n' "$((index + 1))" "${ordered_branches[$index]}"
  done
  other_index=$((${#ordered_branches[@]} + 1))
  printf '%d. 其他分支\n\n' "$other_index"
  printf '请输入序号或分支名:\n> '
  read -r selection

  if [[ "$selection" =~ ^[0-9]+$ ]]; then
    selection_number=$((10#$selection))
    if ((selection_number >= 1 && selection_number <= ${#ordered_branches[@]})); then
      SELECTED_BRANCH="${ordered_branches[$((selection_number - 1))]}"
    elif ((selection_number == other_index)); then
      printf '请输入分支名:\n> '
      read -r SELECTED_BRANCH
    else
      fail "无效的分支序号: $selection"
    fi
  else
    SELECTED_BRANCH="$selection"
  fi

  validate_branch "$SELECTED_BRANCH"

  printf '\n当前选择: %s\n' "$SELECTED_BRANCH"
  printf '确认部署? (y/n)\n> '
  read -r confirmation
  if [[ ! "$confirmation" =~ ^[Yy]$ ]]; then
    printf '已取消部署.\n'
    exit 0
  fi
}

configure_compose() {
  if docker compose version >/dev/null 2>&1; then
    COMPOSE_COMMAND=(docker compose)
  elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE_COMMAND=(docker-compose)
  else
    fail "未找到 Docker Compose"
  fi
}

compose() {
  local -a compose_files

  compose_files=(-f "$REPO_DIR/docker-compose.yml" -f "$SERVER_COMPOSE_FILE")
  if [[ -n "${BUILD_CACHE_COMPOSE_FILE:-}" ]]; then
    compose_files+=(-f "$BUILD_CACHE_COMPOSE_FILE")
  fi
  if [[ -n "${ROLLBACK_COMPOSE_FILE:-}" ]]; then
    compose_files+=(-f "$ROLLBACK_COMPOSE_FILE")
  fi
  "${COMPOSE_COMMAND[@]}" \
    --project-directory "$REPO_DIR" \
    "${compose_files[@]}" \
    "$@"
}

wait_for_health() {
  local health

  for _ in $(seq 1 45); do
    if health="$(curl -fsS --max-time 5 "$HEALTHCHECK_URL" 2>/dev/null)" \
      && [[ "$health" == *'"status":"ok"'* ]] \
      && [[ "$health" == *'"database":"postgresql"'* ]] \
      && [[ "$health" == *'"redis":"ok"'* ]] \
      && [[ "$health" == *'"storage":"aliyun_oss"'* ]] \
      && [[ "$health" == *"\"deployment_service\":\"$DEPLOYMENT_SERVICE_ID\""* ]] \
      && [[ "$health" == *'"production_queue":true'* ]] \
      && [[ "$health" == *'"video_analysis":true'* ]]; then
      printf '%s\n' "$health"
      return 0
    fi
    sleep 2
  done
  return 1
}

configure_storage() {
  export APP_ENV_FILE="$ENV_FILE"
  export PRIVATE_VIDEO_S3_HOST_FILE="$SHARED_DIR/hz.env"
  [[ -f "$PRIVATE_VIDEO_S3_HOST_FILE" && -r "$PRIVATE_VIDEO_S3_HOST_FILE" ]] \
    || fail "私有视频存储配置文件不存在或不可读: $PRIVATE_VIDEO_S3_HOST_FILE"
}

capture_current_images() {
  OLD_BACKEND="$(compose ps -q backend 2>/dev/null || true)"
  OLD_FRONTEND="$(compose ps -q frontend 2>/dev/null || true)"
  OLD_BACKEND_IMAGE=""
  OLD_FRONTEND_IMAGE=""
  if [[ -n "$OLD_BACKEND" ]]; then
    OLD_BACKEND_IMAGE="$(docker inspect --format '{{.Image}}' "$OLD_BACKEND")"
  fi
  if [[ -n "$OLD_FRONTEND" ]]; then
    OLD_FRONTEND_IMAGE="$(docker inspect --format '{{.Image}}' "$OLD_FRONTEND")"
  fi
}

deploy_config_only() {
  [[ -f "$ENV_FILE" ]] || fail "缺少应用配置: $ENV_FILE"
  if grep -Eq 'FILL_[A-Z0-9_]+' "$ENV_FILE"; then
    fail "$ENV_FILE 仍包含 FILL_ 占位符, 拒绝部署"
  fi
  configure_storage
  configure_compose
  compose config >/dev/null
  capture_current_images
  [[ -n "$OLD_BACKEND_IMAGE" && -n "$OLD_FRONTEND_IMAGE" ]] \
    || fail "配置部署需要已有的前后端容器; 首次部署请运行默认模式"

  ROLLBACK_COMPOSE_FILE="$SHARED_DIR/docker-compose.current-images.yml"
  cat > "$ROLLBACK_COMPOSE_FILE" <<EOF
services:
  backend:
    image: "$OLD_BACKEND_IMAGE"
    build: null
  frontend:
    image: "$OLD_FRONTEND_IMAGE"
    build: null
EOF
  log "仅更新配置和挂载, 使用当前容器的镜像, 跳过代码拉取和镜像构建"
  compose run --rm --no-deps --pull never backend python /run/enrui-shared-database-runtime.py check
  compose up -d --no-build --pull never --force-recreate
  if ! wait_for_health; then
    compose ps
    fail "配置部署健康检查失败, 请检查新配置; 当前镜像未更新"
  fi
  log "配置部署成功, 日志文件: $LOG_FILE"
  compose ps
}

install_runtime
if [[ "$DEPLOY_MODE" == code ]]; then
  select_branch
fi

mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/deploy-$(date +%Y%m%d-%H%M%S).log"
exec > >(tee -a "$LOG_FILE") 2>&1

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  fail "已有部署正在执行, 锁目录: $LOCK_DIR"
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT

[[ -f "$SERVER_COMPOSE_FILE" ]] \
  || fail "缺少服务器 Compose 配置: $SERVER_COMPOSE_FILE"

if [[ "$DEPLOY_MODE" == config ]]; then
  deploy_config_only
  exit 0
fi

PREVIOUS_COMMIT=""
if [[ -d "$REPO_DIR/.git" ]]; then
  if [[ -n "$(git -C "$REPO_DIR" status --porcelain)" ]]; then
    fail "仓库存在未提交改动, 请先清理: $REPO_DIR"
  fi
  PREVIOUS_COMMIT="$(git -C "$REPO_DIR" rev-parse HEAD)"
fi

if [[ ! -d "$REPO_DIR/.git" ]]; then
  log "首次拉取仓库: $DEFAULT_REPO_URL ($SELECTED_BRANCH)"
  git clone --branch "$SELECTED_BRANCH" --single-branch "$DEFAULT_REPO_URL" "$REPO_DIR"
else
  log "拉取最新代码: $SELECTED_BRANCH"
  git -C "$REPO_DIR" fetch --prune origin \
    "+refs/heads/$SELECTED_BRANCH:refs/remotes/origin/$SELECTED_BRANCH"
  git -C "$REPO_DIR" checkout --quiet -B "$SELECTED_BRANCH" "origin/$SELECTED_BRANCH"
fi

COMMIT="$(git -C "$REPO_DIR" rev-parse HEAD)"
SHORT_COMMIT="$(git -C "$REPO_DIR" rev-parse --short HEAD)"
export DEPLOY_IMAGE_TAG="$SHORT_COMMIT"
log "目标分支: $SELECTED_BRANCH"
log "目标 Git 提交: $COMMIT"

if [[ ! -f "$ENV_FILE" ]]; then
  cp "$REPO_DIR/deploy/.env.production.example" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  log "已创建 $ENV_FILE, 请填写全部 FILL_ 配置后重新执行本脚本"
  exit 2
fi

if grep -Eq 'FILL_[A-Z0-9_]+' "$ENV_FILE"; then
  fail "$ENV_FILE 仍包含 FILL_ 占位符, 拒绝部署"
fi

configure_storage
configure_compose
command -v python3 >/dev/null || fail "构建缓存配置需要 python3"
[[ -f "$BASE_IMAGES_FILE" ]] || fail "缺少基础镜像固定配置: $BASE_IMAGES_FILE"
python3 "$SHARED_DIR/prepare-build-cache.py" "$REPO_DIR" "$SHARED_DIR" "$BUILD_PIP_INDEX_URL" "$BASE_IMAGES_FILE"
BUILD_CACHE_COMPOSE_FILE="$SHARED_DIR/docker-compose.build-cache.yml"
compose config >/dev/null
capture_current_images

BUILD_ARGS=()
if [[ "$PULL_BASE_IMAGES" == 1 ]]; then
  BUILD_ARGS+=(--pull)
  log "重新拉取固定 digest 的基础镜像, 并构建提交 $SHORT_COMMIT 的前后端镜像"
else
  log "使用固定基础镜像和构建缓存部署提交 $SHORT_COMMIT"
fi
compose build "${BUILD_ARGS[@]}"
log "检查共享数据库兼容性与本服务队列隔离, 不执行全局任务恢复"
compose run --rm --no-deps --pull never backend python /run/enrui-shared-database-runtime.py check
log "启动新版本容器"
compose up -d --no-build --pull never --remove-orphans

if wait_for_health; then
  log "部署成功: $(hostname -I | awk '{print $1}')"
  log "部署分支: $SELECTED_BRANCH"
  log "Git 提交: $COMMIT"
  log "日志文件: $LOG_FILE"
  compose ps
  exit 0
fi

compose ps
compose logs --tail=120 backend frontend || true
if [[ -z "$OLD_BACKEND_IMAGE" || -z "$OLD_FRONTEND_IMAGE" ]]; then
  fail "新版本健康检查失败, 未发现可回退的旧版本"
fi

ROLLBACK_COMPOSE_FILE="$SHARED_DIR/docker-compose.rollback.yml"
cat > "$ROLLBACK_COMPOSE_FILE" <<EOF
services:
  backend:
    image: $OLD_BACKEND_IMAGE
    build: null
  frontend:
    image: $OLD_FRONTEND_IMAGE
    build: null
EOF
log "新版本健康检查失败, 回退到旧版本镜像"
compose up -d --no-build --pull never --remove-orphans
if wait_for_health; then
  log "已回退到旧版本, 原提交: ${PREVIOUS_COMMIT:-unknown}"
else
  compose ps
  compose logs --tail=120 backend frontend || true
  fail "新旧版本健康检查均失败, 请人工处理"
fi
exit 1
