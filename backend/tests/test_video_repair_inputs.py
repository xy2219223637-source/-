from __future__ import annotations

import unittest

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Product, Tenant, User, VideoAsset, VideoAssetAnalysis
from app.schemas.video_repair import VideoRepairInputCreate
from app.services.video_repair.ai_context import build_ai_context
from app.services.video_repair.inputs import create_input
from app.services.video_repair.migration import migrate_video_repair


class VideoRepairInputTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        startup = [table for table in Base.metadata.sorted_tables
                   if not table.info.get("explicit_migration_required")]
        Base.metadata.create_all(self.engine, tables=startup)
        migrate_video_repair(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        with self.sessions() as db:
            db.add(Tenant(id="tenant", name="Tenant", code="tenant"))
            db.add(User(id="owner", email="owner@example.test", name="Owner", password_hash="unused"))
            db.add(Product(id=1, tenant_id="tenant", name="Product"))
            db.add(VideoAsset(
                id=1, tenant_id="tenant", product_id=1, asset_id="asset-v1", filename="v1.mp4",
                source="smart_compose_output", library_type="finished", asset_kind="final_output",
                object_url="/v1.mp4", content_hash="a" * 64, duration_seconds=12,
                width=1080, height=1920, fps=30, has_audio=True, status="analyzed",
                reusable_status="review_required"))
            db.commit()

    def tearDown(self):
        self.engine.dispose()

    def test_manual_input_is_created_without_qianchuan(self):
        with self.sessions() as db:
            row = create_input(
                db, tenant_id="tenant", user_id="owner", source_asset_id=1,
                source_kind="MANUAL", audit_platform="INTERNAL", raw_reason="6 到 10 秒画面错误",
                suggestions=[], time_ranges=[{"start": 6, "end": 10}], raw_payload={})
            db.commit()
            self.assertEqual(row.source_kind, "MANUAL")
            self.assertEqual(row.time_ranges_json, [{"start": 6, "end": 10}])
            duplicate = create_input(
                db, tenant_id="tenant", user_id="owner", source_asset_id=1,
                source_kind="MANUAL", audit_platform="INTERNAL", raw_reason="6 到 10 秒画面错误",
                suggestions=[], time_ranges=[{"start": 6, "end": 10}], raw_payload={})
            self.assertEqual(duplicate.id, row.id)

    def test_public_client_cannot_spoof_platform_input(self):
        with self.sessions() as db:
            with self.assertRaises(HTTPException) as raised:
                create_input(
                    db, tenant_id="tenant", user_id="owner", source_asset_id=1,
                    source_kind="QIANCHUAN_PLATFORM", audit_platform="AD", raw_reason="拒审",
                    suggestions=[], time_ranges=[], raw_payload={})
            self.assertEqual(raised.exception.status_code, 403)
            self.assertEqual(raised.exception.detail["code"], "INPUT_SOURCE_FORBIDDEN")

    def test_time_range_must_fit_source_asset(self):
        with self.sessions() as db:
            with self.assertRaises(HTTPException) as raised:
                create_input(
                    db, tenant_id="tenant", user_id="owner", source_asset_id=1,
                    source_kind="MANUAL", audit_platform="INTERNAL", raw_reason="问题",
                    suggestions=[], time_ranges=[{"start": 10, "end": 13}], raw_payload={})
            self.assertEqual(raised.exception.detail["code"], "INVALID_TIME_RANGE")

    def test_new_input_uses_description_and_suggestions_only(self):
        payload = VideoRepairInputCreate.model_validate({
            "raw_reason": "6 到 10 秒画面错误", "suggestions": ["替换真实商品画面"],
            "expected_result": "旧字段", "time_ranges": [{"start": 1, "end": 2}],
            "category_hint": "COPY",
        })
        self.assertEqual(set(payload.model_dump()), {"raw_reason", "suggestions"})
        with self.sessions() as db:
            row = create_input(
                db, tenant_id="tenant", user_id="owner", source_asset_id=1,
                source_kind="MANUAL", audit_platform="INTERNAL",
                raw_reason=payload.raw_reason, suggestions=payload.suggestions,
                time_ranges=[], raw_payload={})
            context = build_ai_context(
                db, repair_input=row, source_asset=db.get(VideoAsset, 1), base_plan={})
            self.assertEqual(context["review"]["suggestions"], ["替换真实商品画面"])
            self.assertEqual(context["review"]["explicit_time_ranges"][0]["start_ms"], 6000)
            self.assertNotIn("expected_result", context["review"])
            self.assertEqual(context["review"]["category_hint"], "UNKNOWN")

    def test_context_uses_the_validated_analysis_snapshot(self):
        with self.sessions() as db:
            repair_input = create_input(
                db, tenant_id="tenant", user_id="owner", source_asset_id=1,
                source_kind="MANUAL", audit_platform="INTERNAL", raw_reason="口播错误",
                suggestions=[], time_ranges=[], raw_payload={})
            validated = VideoAssetAnalysis(
                tenant_id="tenant", asset_id=1, status="completed",
                analysis_json={"source_hash": "a" * 64,
                               "transcript": {"segments": [{"text": "正确视频"}]}})
            newer = VideoAssetAnalysis(
                tenant_id="tenant", asset_id=1, status="completed",
                analysis_json={"source_hash": "stale",
                               "transcript": {"segments": [{"text": "过期结果"}]}})
            db.add_all([validated, newer])
            db.flush()
            context = build_ai_context(
                db, repair_input=repair_input, source_asset=db.get(VideoAsset, 1),
                base_plan={}, source_analysis=validated)
            self.assertEqual(context["observations"]["asr_segments"][0]["text"], "正确视频")



if __name__ == "__main__":
    unittest.main()
