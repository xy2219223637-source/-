#!/usr/bin/env bash
set -Eeuo pipefail

PRODUCTION_SCRIPT="/opt/enrui-ai-commerce-agent/deploy-production.sh"
TEST_SCRIPT="/opt/enrui-ai-commerce-agent-test/deploy.sh"
ARTINJECTER_SCRIPT="/opt/enrui-ai-commerce-agent-artinjecter/deploy.sh"
TEST_REPO_DIR="/opt/enrui-ai-commerce-agent-test/repository"
TEST_REPO_URL="https://gitee.com/enrui_1/enrui-ai-commerce-agent.git"
SELECTED_ENV="${DEPLOY_ENV:-}"
SELECTED_BRANCH="${DEPLOY_BRANCH:-}"
DRY_RUN=false
ENV_ARGUMENT=false
PRODUCTION_ARGS=()

usage() {
  cat <<'EOF'
用法: bash /opt/enrui-ai-commerce-agent/deploy.sh [test|production|artinjecter] [选项]

不指定环境时, 先选择测试, 生产或 artinjecter 环境.
test: 列出远端分支, 按序号或分支名选择并确认后更新测试环境.
production: 使用原有生产部署流程, 保留分支选择, --config-only 和 --pull.
artinjecter: 独立选择分支部署, 复用生产数据库.
--dry-run: 仅预览调用, 测试分支查询只读取远端列表, 不拉取代码或更新容器.
--help: 显示说明.

示例:
  bash /opt/enrui-ai-commerce-agent/deploy.sh
  bash /opt/enrui-ai-commerce-agent/deploy.sh test
  DEPLOY_BRANCH=feat/domain-management bash /opt/enrui-ai-commerce-agent/deploy.sh test
  bash /opt/enrui-ai-commerce-agent/deploy.sh production --config-only
  DEPLOY_BRANCH=main bash /opt/enrui-ai-commerce-agent/deploy.sh production

非交互调用必须通过参数或 DEPLOY_ENV 明确指定环境.
非交互代码部署使用 DEPLOY_BRANCH 指定远端分支, 不支持 commit 或 tag.
EOF
}

fail() { printf '部署入口: %s\n' "$*" >&2; exit 2; }

select_test_branch() {
  local remote_source="$TEST_REPO_URL" remote_output branch index other_index selection confirmation
  local -a remote_branches ordered_branches=()

  if [[ -d "$TEST_REPO_DIR/.git" ]]; then
    remote_source="$(git -C "$TEST_REPO_DIR" remote get-url origin)" || fail "无法读取测试仓库 origin"
  fi
  remote_output="$(git ls-remote --heads "$remote_source")" || fail "无法从测试仓库读取远端分支"
  [[ -n "$remote_output" ]] || fail "测试仓库没有可部署的远端分支"
  mapfile -t remote_branches < <(printf '%s\n' "$remote_output" | awk '{sub("^refs/heads/", "", $2); print $2}' | LC_ALL=C sort -u)
  for branch in "${remote_branches[@]}"; do
    [[ "$branch" != main ]] || ordered_branches+=("$branch")
  done
  for branch in "${remote_branches[@]}"; do
    [[ "$branch" == main ]] || ordered_branches+=("$branch")
  done

  if [[ -z "$SELECTED_BRANCH" ]]; then
    [[ -t 0 ]] || fail "非交互执行时必须设置 DEPLOY_BRANCH"
    printf '请选择需要部署的分支:\n\n'
    for index in "${!ordered_branches[@]}"; do
      printf '%d. %s\n' "$((index + 1))" "${ordered_branches[$index]}"
    done
    other_index=$((${#ordered_branches[@]} + 1))
    printf '%d. 其他分支\n\n' "$other_index"
    printf '请输入序号或分支名:\n> '
    read -r selection || exit 0
    if [[ "$selection" =~ ^[0-9]+$ ]]; then
      [[ ${#selection} -le 9 ]] || fail "无效的分支序号: $selection"
      index=$((10#$selection))
      if ((index >= 1 && index < other_index)); then
        SELECTED_BRANCH="${ordered_branches[$((index - 1))]}"
      elif ((index == other_index)); then
        printf '请输入分支名:\n> '
        read -r SELECTED_BRANCH || exit 0
      else
        fail "无效的分支序号: $selection"
      fi
    else
      SELECTED_BRANCH="$selection"
    fi
  fi

  [[ -n "$SELECTED_BRANCH" && "$SELECTED_BRANCH" != -* ]] || fail "无效的分支名: $SELECTED_BRANCH"
  git check-ref-format "refs/heads/$SELECTED_BRANCH" >/dev/null 2>&1 || fail "无效的分支名: $SELECTED_BRANCH"
  local found=false
  for branch in "${remote_branches[@]}"; do
    [[ "$branch" != "$SELECTED_BRANCH" ]] || found=true
  done
  [[ "$found" == true ]] || fail "远端仓库不存在分支: $SELECTED_BRANCH"

  if [[ -n "${DEPLOY_BRANCH:-}" ]]; then
    printf '使用 DEPLOY_BRANCH 部署分支: %s\n' "$SELECTED_BRANCH"
  else
    printf '\n当前选择: %s\n确认部署? (y/n)\n> ' "$SELECTED_BRANCH"
    read -r confirmation || exit 0
    if [[ ! "$confirmation" =~ ^[Yy]$ ]]; then
      printf '已取消部署.\n'
      exit 0
    fi
  fi
}

while (($#)); do
  case "$1" in
    test|production|artinjecter)
      [[ "$ENV_ARGUMENT" == false ]] || fail "只能选择一个环境"
      SELECTED_ENV="$1"
      ENV_ARGUMENT=true
      shift
      ;;
    --config-only|--pull) PRODUCTION_ARGS+=("$1"); shift ;;
    --dry-run) DRY_RUN=true; shift ;;
    --help|-h) usage; exit 0 ;;
    *) fail "未知参数: $1, 使用 --help 查看说明" ;;
  esac
done

[[ -z "${TEST_GIT_REF:-}" ]] || fail "不再支持 TEST_GIT_REF, 请使用分支菜单或 DEPLOY_BRANCH"

if [[ -z "$SELECTED_ENV" ]]; then
  [[ -t 0 ]] || fail "非交互执行必须指定 test, production 或 artinjecter"
  printf '请选择更新环境:\n1. 测试环境 (test.waynepartners.xyz)\n2. 生产环境 (waynepartners.xyz)\n3. artinjecter (artinjecter.waynepartners.xyz)\n0. 退出\n> '
  read -r selection || exit 0
  case "$selection" in
    1|test) SELECTED_ENV=test ;;
    2|production) SELECTED_ENV=production ;;
    3|artinjecter) SELECTED_ENV=artinjecter ;;
    0|'') printf '已取消部署.\n'; exit 0 ;;
    *) fail "无效的环境选项: $selection" ;;
  esac
fi

case "$SELECTED_ENV" in
  test)
    [[ ${#PRODUCTION_ARGS[@]} -eq 0 ]] || fail "--config-only 和 --pull 仅适用于 production 和 artinjecter"
    printf '更新环境: 测试\n'
    select_test_branch
    SCRIPT="$TEST_SCRIPT"
    ARGS=("$SELECTED_BRANCH")
    ;;
  production|artinjecter)
    if [[ " ${PRODUCTION_ARGS[*]} " == *' --config-only '* && " ${PRODUCTION_ARGS[*]} " == *' --pull '* ]]; then
      fail "--config-only 不能与 --pull 同时使用"
    fi
    if [[ "$SELECTED_ENV" == artinjecter ]]; then
      SCRIPT="$ARTINJECTER_SCRIPT"
    else
      SCRIPT="$PRODUCTION_SCRIPT"
    fi
    ARGS=("${PRODUCTION_ARGS[@]}")
    printf '更新环境: %s\n' "$SELECTED_ENV"
    ;;
  *) fail "无效环境: $SELECTED_ENV" ;;
esac

if [[ "$DRY_RUN" == true ]]; then
  printf '仅预览, 不执行部署.\n调用: bash %s' "$SCRIPT"
  if ((${#ARGS[@]})); then
    printf ' %q' "${ARGS[@]}"
  fi
  printf '\n'
  exit 0
fi

[[ -f "$SCRIPT" ]] || fail "部署脚本未安装: $SCRIPT"
exec bash "$SCRIPT" "${ARGS[@]}"
