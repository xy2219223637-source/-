"""Preview by default; use --apply to persist verified historical relationships."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal
from app.models import VideoAsset
from app.services.video_lineage_backfill import backfill_video_lineage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--tenant-id")
    scope.add_argument("--scope-asset", help="Resolve tenant from a known public video asset ID")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    with SessionLocal() as db:
        tenant_id = args.tenant_id
        if args.scope_asset:
            rows = db.query(VideoAsset).filter_by(asset_id=args.scope_asset).all()
            if len(rows) != 1:
                parser.error("Scope asset must identify exactly one video")
            tenant_id = rows[0].tenant_id
        report = backfill_video_lineage(db, tenant_id, apply=args.apply)
        payload = {"apply": args.apply, "results": report,
                   "summary": {status: sum(row["status"] == status for row in report)
                               for status in ("ready", "restored", "existing", "skipped")}}
        args.report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(payload["summary"]))
        print(str(args.report))


if __name__ == "__main__":
    main()
