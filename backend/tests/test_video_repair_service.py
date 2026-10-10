from __future__ import annotations

from datetime import datetime
import unittest

from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base
from app.models import (
    EditingPlanVersion, Product, QianchuanReviewFeedback, Tenant, User, VideoAsset,
    VideoAssetAnalysis, VideoEditProject, VideoHighlight, VideoLineageSegment, VideoRepairInput,
    VideoRepairTask,
)
from app.services.video_repair.migration import migrate_video_repair
from app.services.video_repair.service import create_task, select_candidate


class VideoRepairServiceTests(unittest.TestCase):
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
            db.add(VideoEditProject(id=1, tenant_id="tenant", product_id=1, name="Project",
                                    target_duration_seconds=12, variant_count=1))
            source = VideoAsset(
                id=1, tenant_id="tenant", project_id=1, product_id=1, asset_id="asset-v1",
                filename="v1.mp4", source="smart_compose_output", library_type="finished",
                asset_kind="final_output", object_url="/v1.mp4", content_hash="a" * 64,
                duration_seconds=12, width=1080, height=1920, fps=30, has_audio=True,
                status="analyzed", reusable_status="review_required")
            original = VideoAsset(
                id=2, tenant_id="tenant", product_id=1, asset_id="asset-source",
                filename="source.mp4", source="manual_upload", library_type="semi_finished",
                asset_kind="material", object_url="/source.mp4", content_hash="c" * 64,
                duration_seconds=20, width=1080, height=1920, fps=30, has_audio=True,
                status="analyzed", reusable_status="reusable")
            candidate = VideoAsset(
                id=3, tenant_id="tenant", product_id=1, asset_id="asset-candidate",
                filename="candidate.mp4", source="manual_upload", library_type="semi_finished",
                asset_kind="material", object_url="/source.mp4", content_hash="b" * 64,
                duration_seconds=20, width=1080, height=1920, fps=30, has_audio=True,
                status="analyzed", reusable_status="review_required")
            flat_source = VideoAsset(
                id=4, tenant_id="tenant", project_id=None, product_id=1,
                asset_id="asset-flat-v1", filename="flat-v1.mp4",
                source="manual_upload", library_type="finished", asset_kind="final_output",
                object_url="/flat-v1.mp4", content_hash="d" * 64,
                duration_seconds=12, width=1080, height=1920, fps=30, has_audio=True,
                status="analyzed", reusable_status="review_required")
            db.add_all([source, original, candidate, flat_source]); db.flush()
            analysis = VideoAssetAnalysis(
                id=1, tenant_id="tenant", asset_id=3, status="completed", completed_at=datetime.utcnow())
            original_analysis = VideoAssetAnalysis(
                id=2, tenant_id="tenant", asset_id=2, status="completed", completed_at=datetime.utcnow())
            db.add_all([analysis, original_analysis]); db.flush()
            db.add(VideoHighlight(
                tenant_id="tenant", asset_id=3, analysis_id=1, highlight_id="highlight-1",
                start_seconds=2, end_seconds=8, description="合规商品展示",
                content_role="product_demo", score=.9))
            db.add(VideoHighlight(
                tenant_id="tenant", asset_id=2, analysis_id=2, highlight_id="offending-highlight",
                start_seconds=5, end_seconds=11, description="原违规画面",
                content_role="product_demo", score=1.0))
            clip = {"asset_id":"asset-source","highlight_id":"old","source_range":{"start":0,"end":12},
                    "timeline_range":{"start":0,"end":12},"duration_seconds":12,"content_role":"product_demo"}
            narration = dict(clip); narration.update({"audio_strategy":"source","speech_text":"原口播"})
            plan = EditingPlanVersion(
                id=1, tenant_id="tenant", project_id=1, version=1, validation_status="valid",
                plan_json={"variants":[{"variant_id":"V1","edit_mode":"smart_compose",
                    "actual_duration_seconds":12,"target_duration_seconds":12,
                    "video_track":[clip],"narrative_segments":[narration]}]})
            db.add(plan); db.flush()
            db.add(VideoLineageSegment(
                tenant_id="tenant", source_asset_id=2, target_asset_id=1, plan_version_id=1,
                variant_id="V1", segment_index=0, source_start=0, source_end=12,
                target_start=0, target_end=12))
            db.add(QianchuanReviewFeedback(
                id=1, tenant_id="tenant", created_by="owner", video_asset_id=1,
                source="mock", audit_platform="CONTENT", review_status="REJECTED",
                raw_reason="视频 6-10 秒画面可能引发误解",
                raw_suggestions_json=["替换画面"], provider_payload_json={},
                provider_time_ranges_json=[{"start":6,"end":10}]))
            db.add(VideoRepairInput(
                id=1, tenant_id="tenant", created_by="owner", source_asset_id=1,
                source_kind="MANUAL", audit_platform="INTERNAL",
                raw_reason="视频 6-10 秒画面可能引发误解", suggestions_json=["替换画面"],
                time_ranges_json=[{"start": 6, "end": 10}], raw_payload_json={}))
            db.add(VideoRepairInput(
                id=2, tenant_id="tenant", created_by="owner", source_asset_id=4,
                source_kind="MANUAL", audit_platform="INTERNAL",
                raw_reason="视频 2-6 秒没有展示商品", suggestions_json=["替换商品画面"],
                time_ranges_json=[{"start": 2, "end": 6}],
                raw_payload_json={"category_hint": "VISUAL"}))
            db.commit()

    def tearDown(self):
        self.engine.dispose()

    def test_create_is_idempotent_and_candidate_selection_is_allowlisted(self):
        with self.sessions() as db:
            first = create_task(db, tenant_id="tenant", user_id="owner",
                                source_asset_id=1, input_id=1)
            second = create_task(db, tenant_id="tenant", user_id="owner",
                                 source_asset_id=1, input_id=1)
            self.assertEqual(first.id, second.id)
            self.assertEqual(first.status, "NEEDS_CONFIRMATION")
            action = first.repair_plan_json["actions"][0]
            self.assertEqual(action["type"], "VISUAL_REPLACE")
            self.assertEqual(action["candidate_ids"], ["asset-candidate:highlight-1"])
            self.assertIsNone(action["selected_candidate_id"])
            self.assertEqual(first.selected_candidates_json, {})
            self.assertIn("人工确认", " ".join(action["candidates"][0]["warnings"]))
            with self.assertRaisesRegex(Exception, "候选不属于"):
                select_candidate(db, first, action["action_id"], "asset-other:highlight-x")
            db.add(VideoRepairTask(
                tenant_id="tenant", created_by="owner", input_id=1, review_feedback_id=1,
                source_asset_id=1, base_plan_version_id=1, status="ANALYZING"))
            with self.assertRaises(IntegrityError):
                db.commit()

    def test_flat_final_video_without_lineage_uses_visual_fallback(self):
        with self.sessions() as db:
            task = create_task(db, tenant_id="tenant", user_id="owner",
                               source_asset_id=4, input_id=2)
            self.assertIsNone(task.base_plan_version_id)
            self.assertEqual(task.status, "NEEDS_CONFIRMATION")
            self.assertEqual(task.repair_plan_json["render_mode"], "FLAT_VISUAL")
            self.assertEqual(task.repair_plan_json["actions"][0]["type"], "VISUAL_REPLACE")
            self.assertIn("保留原音频", " ".join(task.repair_plan_json["warnings"]))


if __name__ == "__main__":
    unittest.main()
