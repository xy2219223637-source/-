#!/usr/bin/env bash
set -Eeuo pipefail
SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE="${1:-}"
DRY_RUN="${2:-}"
case "$SERVICE" in
  production) APP_DIR=/opt/enrui-ai-commerce-agent ;;
  artinjecter) APP_DIR=/opt/enrui-ai-commerce-agent-artinjecter ;;
  test) APP_DIR=/opt/enrui-ai-commerce-agent-test ;;
  *) echo 'Usage: bash deploy/install-runtime.sh production|artinjecter|test [--dry-run]' >&2; exit 2 ;;
esac
[[ $# -le 2 && ( -z "$DRY_RUN" || "$DRY_RUN" == --dry-run ) ]] || exit 2
[[ -f "$APP_DIR/shared/.env" ]] || { echo 'Provision the existing service resources and shared/.env first.' >&2; exit 1; }
if [[ "$DRY_RUN" == --dry-run ]]; then
  printf 'Install versioned runtime for %s into %s; preserve environment files; do not restart containers.\n' "$SERVICE" "$APP_DIR"
  exit 0
fi
[[ $(id -u) == 0 ]] || { echo 'Run as root.' >&2; exit 1; }
umask 077
BACKUP=$(mktemp -d "/var/backups/enrui-runtime-${SERVICE}-XXXXXXXX")
save() {
  if [[ -e "$APP_DIR/$1" || -L "$APP_DIR/$1" ]]; then
    mkdir -p "$BACKUP/$(dirname "$1")"
    cp -a "$APP_DIR/$1" "$BACKUP/$1"
  fi
}
save deploy.sh
save shared/prepare-build-cache.py
install -m 600 "$SOURCE_DIR/deploy/prepare-build-cache.py" "$APP_DIR/shared/prepare-build-cache.py"
case "$SERVICE" in
  production)
    save deploy-production.sh
    save shared/shared-database-runtime.py
    save shared/docker-compose.server.yml
    install -m 700 "$SOURCE_DIR/deploy.sh" "$APP_DIR/deploy.sh"
    install -m 700 "$SOURCE_DIR/deploy/deploy-production.sh" "$APP_DIR/deploy-production.sh"
    install -m 600 "$SOURCE_DIR/deploy/shared-runtime/task_isolation_runtime.py" "$APP_DIR/shared/shared-database-runtime.py"
    install -m 600 "$SOURCE_DIR/deploy/compose/production.yml" "$APP_DIR/shared/docker-compose.server.yml"
    ;;
  artinjecter)
    save shared/shared-database-runtime.py
    save shared/docker-compose.server.yml
    install -m 700 "$SOURCE_DIR/deploy/deploy-artinjecter.sh" "$APP_DIR/deploy.sh"
    install -m 600 "$SOURCE_DIR/deploy/shared-runtime/task_isolation_runtime.py" "$APP_DIR/shared/shared-database-runtime.py"
    install -m 600 "$SOURCE_DIR/deploy/compose/artinjecter.yml" "$APP_DIR/shared/docker-compose.server.yml"
    ;;
  test)
    save runtime
    RELEASE=$(mktemp -d "$APP_DIR/runtime-releases/versioned-XXXXXXXX")
    install -m 600 "$SOURCE_DIR"/deploy/test-runtime/* "$RELEASE/"
    python3 - "$RELEASE" <<'PY'
import hashlib,json,sys
from pathlib import Path
root=Path(sys.argv[1])
manifest={path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(root.iterdir()) if path.is_file()}
(root/'source-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
PY
    LINK="$APP_DIR/.runtime-link-$(basename "$RELEASE")"
    ln -s "$RELEASE" "$LINK"
    [[ -L "$APP_DIR/runtime" || ! -e "$APP_DIR/runtime" ]] || { echo 'Runtime must be a managed symlink.' >&2; exit 1; }
    mv -Tf "$LINK" "$APP_DIR/runtime"
    install -m 700 "$SOURCE_DIR/deploy/deploy-test.sh" "$APP_DIR/deploy.sh"
    ;;
esac
printf 'Installed %s runtime. Backup: %s. Run the service compatibility check before recreating containers.\n' "$SERVICE" "$BACKUP"
