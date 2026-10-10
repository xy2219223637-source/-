from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Product, Tenant, User, VideoAsset, VideoRepairInput, VideoRepairJob
from app.services.video_repair.analysis_normalization import normalize_analysis, normalize_transcript
from app.services.video_repair.migration import migrate_video_repair
from app.services.video_repair.migration_v2 import migrate_video_repair_v2
from app.services.video_repair.migration_v3 import migrate_video_repair_v3
from app.services.video_repair.insertion_planner import recommend_insertion_positions
from app.services.video_repair.protocol_v4 import InvalidRepairPlan, build_plan, plan_hash
from app.services.video_repair.state_machine_v4 import (
    InvalidRepairTransition,
    transition,
    validate_transition,
)
from app.services.video_repair.service import create_v4_task


class VideoRepairV4ProtocolTests(unittest.TestCase):
    def test_visual_plan_is_single_action_and_hash_is_stable(self):
        plan = build_plan(
            task_id=13,
            draft_revision=4,
            source_asset_id=149,
            product_id=8,
            action={
                "type": "VISUAL_REPLACE",
                "target_range": {"start_ms": 6000, "end_ms": 10000},
                "material": {
                    "ref_type": "VIDEO_ASSET",
                    "ref_id": 231,
                    "source_range": {"start_ms": 2100, "end_ms": 6100},
                    "content_hash": "material-sha256",
                },
                "fit_mode": "COVER",
            },
        )
        self.assertEqual(plan["schema_version"], "video_repair_plan.v4")
        self.assertNotIn("actions", plan)
        self.assertEqual(plan["action"]["duration_policy"], "KEEP_TOTAL_DURATION")
        self.assertEqual(plan_hash(plan), plan_hash(dict(reversed(list(plan.items())))))

    def test_audio_and_insert_require_action_specific_inputs(self):
        with self.assertRaisesRegex(InvalidRepairPlan, "attachment_id"):
            build_plan(
                task_id=1, draft_revision=1, source_asset_id=2, product_id=3,
                action={
                    "type": "AUDIO_REPLACE",
                    "target_range": {"start_ms": 0, "end_ms": 1000},
                    "audio": {"source_type": "ATTACHMENT"},
                },
            )
        with self.assertRaisesRegex(InvalidRepairPlan, "视觉和音频"):
            build_plan(
                task_id=1, draft_revision=1, source_asset_id=2, product_id=3,
                action={"type": "CONTENT_INSERT", "insert_at_ms": 0, "duration_ms": 1000},
            )

    def test_content_insert_accepts_candidate_original_audio(self):
        plan = build_plan(
            task_id=1, draft_revision=1, source_asset_id=2, product_id=3,
            action={
                "type": "CONTENT_INSERT", "insert_at_ms": 6200, "duration_ms": 3000,
                "visual": {"ref_type": "VIDEO_ASSET", "ref_id": 4,
                           "source_range": {"start_ms": 0, "end_ms": 3000}},
                "audio": {"source_type": "VIDEO_ASSET", "ref_type": "VIDEO_ASSET", "ref_id": 4,
                          "source_range": {"start_ms": 0, "end_ms": 3000}},
            },
        )
        self.assertEqual(plan["action"]["audio"]["source_type"], "VIDEO_ASSET")


class VideoRepairInsertionPlannerTests(unittest.TestCase):
    def test_without_timeline_evidence_returns_low_confidence_preview_default(self):
        result = recommend_insertion_positions({"review": {}, "timeline": {}, "observations": {}}, duration_ms=50000)
        self.assertEqual(result["recommended_ms"], 31000)
        self.assertEqual(result["confidence_level"], "LOW")
        self.assertEqual(len(result["alternatives"]), 2)

    def test_prefers_semantic_timeline_boundary(self):
        context = {"review": {}, "timeline": {"narrative_segments": [
            {"start_ms": 0, "end_ms": 7000, "speech_text": "这款产品先打开包装"},
            {"start_ms": 7000, "end_ms": 13000, "speech_text": "使用后效果很好"},
        ]}, "observations": {}}
        result = recommend_insertion_positions(context, duration_ms=20000)
        self.assertEqual(result["recommended_ms"], 7000)
        self.assertIn(result["confidence_level"], {"MEDIUM", "HIGH"})


class VideoRepairV4StateMachineTests(unittest.TestCase):
    def test_valid_preview_flow(self):
        task = SimpleNamespace(status="CONFIGURING")
        for target in ("PREVIEW_QUEUED", "PREVIEW_RENDERING", "PREVIEW_READY", "COMPLETED"):
            transition(task, target)
        self.assertEqual(task.status, "COMPLETED")

    def test_completed_task_cannot_return_to_configuration(self):
        with self.assertRaises(InvalidRepairTransition):
            validate_transition("COMPLETED", "CONFIGURING")


class VideoRepairAnalysisNormalizationTests(unittest.TestCase):
    def test_prefers_canonical_transcript_segments(self):
        payload = {
            "transcript": {"segments": [{"text": "canonical"}]},
            "asr_segments": [{"text": "legacy"}],
        }
        transcript = normalize_transcript(payload)
        self.assertEqual(transcript["segments"], [{"text": "canonical"}])
        self.assertEqual(transcript["text"], "canonical")

    def test_accepts_both_historical_segment_fields(self):
        for key in ("asr_segments", "transcript_segments"):
            normalized = normalize_analysis({key: [{"transcript": "旧字段"}]})
            self.assertEqual(normalized["schema_version"], "video_analysis.v2")
            self.assertEqual(normalized["transcript"]["text"], "旧字段")
            self.assertEqual(normalized["shots"], [])


class VideoRepairV3MigrationTests(unittest.TestCase):
    def test_migration_creates_preview_job_and_attachment_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "repair-v3.db"
            engine = create_engine("sqlite:///" + str(path))
            try:
                startup = [
                    table for table in Base.metadata.sorted_tables
                    if not table.info.get("explicit_migration_required")
                ]
                Base.metadata.create_all(engine, tables=startup)
                migrate_video_repair(engine)
                migrate_video_repair_v2(engine)
                first = migrate_video_repair_v3(engine)
                second = migrate_video_repair_v3(engine)
                self.assertEqual(first["status"], "migrated")
                self.assertEqual(second["status"], "already_current")
                tables = set(inspect(engine).get_table_names())
                self.assertTrue({
                    "video_repair_attachments", "video_repair_previews", "video_repair_jobs",
                }.issubset(tables))
                input_columns = {row["name"] for row in inspect(engine).get_columns("video_repair_inputs")}
                task_columns = {row["name"] for row in inspect(engine).get_columns("video_repair_tasks")}
                self.assertIn("expected_result", input_columns)
                self.assertTrue({
                    "product_id", "schema_version", "draft_revision",
                    "accepted_preview_id", "cancel_reason",
                }.issubset(task_columns))
                with Session(engine) as db:
                    db.add(Tenant(id="tenant", name="Tenant", code="tenant"))
                    db.add(User(
                        id="owner", email="owner@example.test", name="Owner",
                        password_hash="unused"))
                    db.add(Product(id=1, tenant_id="tenant", name="Product"))
                    db.add(VideoAsset(
                        id=1, tenant_id="tenant", product_id=1, asset_id="final-v1",
                        filename="v1.mp4", source="manual_upload", library_type="finished",
                        asset_kind="final_output", object_url="/v1.mp4", content_hash="a" * 64,
                        duration_seconds=12, width=1080, height=1920, fps=30, has_audio=True,
                        status="analyzed", reusable_status="review_required"))
                    db.add(VideoRepairInput(
                        id=1, tenant_id="tenant", created_by="owner", source_asset_id=1,
                        source_kind="MANUAL", audit_platform="INTERNAL", raw_reason="画面错误",
                        expected_result="展示正确商品", suggestions_json=[], time_ranges_json=[],
                        raw_payload_json={}))
                    db.commit()
                    task = create_v4_task(
                        db, tenant_id="tenant", user_id="owner", source_asset_id=1, input_id=1)
                    job = db.query(VideoRepairJob).filter_by(task_id=task.id).one()
                    self.assertEqual(task.schema_version, "video_repair_task.v2")
                    self.assertEqual(task.status, "ANALYZING")
                    self.assertEqual(job.status, "QUEUED")
                    self.assertEqual(job.job_type, "ANALYZE")
            finally:
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
