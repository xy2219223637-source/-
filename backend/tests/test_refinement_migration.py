from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.video_refinement import _serialize, _validate_source
from app.database import Base
from app.models import (
    EcommerceProduct, EcommerceVideoAsset, EcommerceVideoLineageSegment,
    EcommerceVideoRefinementAnalysisJob, EcommerceVideoRefinementPlan,
    EcommerceVideoRefinementPreview, EcommerceVideoRefinementRequest,
    EcommerceVideoRefinementSnapshot, EcommerceVideoRefinementTask,
    EcommerceVideoRefinementVersion, EcommerceVideoRefinementVoiceover,
    Product, VideoAsset, VideoLineageSegment, VideoRefinementAnalysisJob,
    VideoRefinementPlan, VideoRefinementPreview, VideoRefinementRequest,
    VideoRefinementSnapshot, VideoRefinementTask, VideoRefinementVersion,
    VideoRefinementVoiceover,
)
from app.schemas.video_refinement import RefinementItem
from app.services.refinement_plan import content_hash
from scripts.migrate_legacy_refinements import MARKER, migrate_legacy_refinements, row_data


class RefinementMigrationTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", poolclass=StaticPool)
        event.listen(self.engine, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"))
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, autoflush=False)()
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)
        directory = tempfile.TemporaryDirectory(prefix="refinement-migration-test-")
        self.addCleanup(directory.cleanup)
        self.backup_dir = Path(directory.name)
        self.db.add_all([Product(id=1, tenant_id="t1", name="旧商品"),
                         EcommerceProduct(id=11, tenant_id="t1", name="新商品")])
        self.db.flush()
        self.assets = {}
        for old_id, kind in ((11,"final_output"),(12,"material"),(13,"final_output")):
            old = VideoAsset(id=old_id, tenant_id="t1", product_id=1, asset_id=f"old-{old_id}",
                filename=f"video-{old_id}.mp4", library_type="finished", asset_kind=kind,
                object_url=f"/old-{old_id}.mp4", content_hash=str(old_id % 10)*64,
                duration_seconds=8, width=720, height=1280, fps=30)
            new = EcommerceVideoAsset(id=old_id+100, tenant_id="t1", product_id=11,
                asset_id=f"new-{old_id}", filename=old.filename, library_type="finished", asset_kind=kind,
                source=f"legacy_video:{old_id}", object_url=f"/new-{old_id}.mp4", content_hash=old.content_hash,
                duration_seconds=8, width=720, height=1280, fps=30, status="uploaded")
            self.db.add_all([old,new]); self.assets[old_id] = new
        self.db.flush()
        self.source = {"asset_id":11, "product_id":1, "product_name":"旧商品", "filename":"video-11.mp4",
            "object_url":"/old-11.mp4", "content_hash":"1"*64, "duration_ms":8000, "width":720,
            "height":1280, "fps":30, "has_audio":True}
        self.snapshot_json = {"schema_version":"video_refinement_snapshot.v2", "is_mock":False,
            "tenant_id":"t1", "source":deepcopy(self.source), "engineering_plan":{"product_id":1},
            "material_catalog":[{"asset_id":12,"product_id":1,"object_url":"/old-12.mp4","duration_ms":8000}],
            "voiceover_assets":[{"id":7}], "transcript":[{"cue_id":"tts-7-0","text":"商品展示"}]}
        self.task = VideoRefinementTask(id=1, tenant_id="t1", product_id=1, source_asset_id=11,
            source_hash="1"*64, source_snapshot=deepcopy(self.source), user_brief="替换商品画面",
            created_by="owner", status="COMPLETED", current_revision=1, confirmed_revision=1,
            created_at=datetime(2026,10,1), updated_at=datetime(2026,10,2))
        self.db.add(self.task); self.db.flush()
        self.snapshot = VideoRefinementSnapshot(id=5, task_id=1, schema_version="video_refinement_snapshot.v2",
            is_mock=False, snapshot_json=self.snapshot_json, content_hash=content_hash(self.snapshot_json))
        self.request = VideoRefinementRequest(id=6, tenant_id="t1", task_id=1, revision=0,
            snapshot_json=self.snapshot_json, snapshot_hash=content_hash(self.snapshot_json), options_json={})
        self.voice = VideoRefinementVoiceover(id=7, tenant_id="t1", task_id=1, revision=1,
            item_id="visual", fingerprint="v"*64, script="商品展示", parameters_json={},
            timing_json={"segments":[{"cue_id":"tts-7-0"}]}, status="ready")
        self.db.add_all([self.snapshot,self.request,self.voice]); self.db.flush()
        item = RefinementItem(item_id="visual", type="VISUAL_REPLACE", range={"start_ms":1000,"end_ms":2000},
            operation="展示细节", reason="修改意见", material_asset_id=12, voiceover_id=7,
            material_segments=[{"asset_id":12,"source_range":{"start_ms":0,"end_ms":1000}}],
            evidence=["material-12"]).model_dump()
        plan_json = {"schema_version":"video_refinement_plan.v2", "source":deepcopy(self.source),
            "snapshot_hash":self.request.snapshot_hash, "request_snapshot_id":6, "items":[item],
            "execution_blockers":[], "summary":{"objective":"替换商品画面","expected_duration_ms":8000},
            "narration_track":[{"source_kind":"tts","source_asset_id":7}],
            "analysis":{"context":deepcopy(self.snapshot_json)}}
        self.plan = VideoRefinementPlan(id=8, task_id=1, revision=1, source_snapshot_id=5,
            plan_json=plan_json, plan_hash=content_hash(plan_json), origin="ai")
        self.db.add(self.plan); self.db.flush()
        self.preview = VideoRefinementPreview(id=9, tenant_id="t1", task_id=1, plan_id=8, revision=1,
            plan_hash=self.plan.plan_hash, status="accepted", output_hash="3"*64,
            output_url="/old-13.mp4", recipe_json=[{"asset_id":12,"object_url":"/old-12.mp4"}])
        self.db.add(self.preview); self.db.flush()
        self.version = VideoRefinementVersion(id=10, tenant_id="t1", task_id=1, preview_id=9,
            source_snapshot_id=5, parent_asset_id=11, output_asset_id=13, plan_id=8, version=2, accepted_by="owner")
        self.job = VideoRefinementAnalysisJob(id=11, tenant_id="t1", task_id=1, revision=0,
            fingerprint="j"*64, snapshot_json=self.snapshot_json, status="ready",
            result_json={"source":deepcopy(self.source)}, batches_json={"source-context-old":{"source":deepcopy(self.source)}})
        self.lineage = VideoLineageSegment(tenant_id="t1",source_asset_id=12,original_asset_id=12,target_asset_id=13,
            plan_version_id=8,variant_id="refinement-1-r1",segment_index=0,source_start=0,source_end=1,target_start=1,target_end=2)
        self.db.add_all([self.version,self.job,self.lineage])
        self.existing = EcommerceVideoRefinementTask(id=1, tenant_id="t1", product_id=11, source_asset_id=111,
            source_hash="1"*64, source_snapshot={**self.source,"asset_id":111,"product_id":11,"object_url":"/new-11.mp4"},
            user_brief="已有新任务",created_by="owner")
        self.db.add(self.existing); self.db.commit()

    def migrate(self):
        result = migrate_legacy_refinements(self.db,"t1",apply=True,expected_tasks=1,backup_dir=self.backup_dir)
        self.db.commit()
        return result

    def test_preflight_is_read_only_and_apply_is_additive_idempotent_and_api_readable(self):
        original = row_data(self.task)
        existing = row_data(self.existing)
        summary = migrate_legacy_refinements(self.db,"t1")
        self.assertEqual(summary["tasks_to_copy"],1)
        self.assertEqual(self.db.query(EcommerceVideoRefinementTask).count(),1)
        self.assertEqual(list(self.backup_dir.iterdir()),[])
        result = self.migrate()
        copied = self.db.get(EcommerceVideoRefinementTask,result["task_map"][1])
        self.assertNotEqual(copied.id,self.existing.id)
        self.assertEqual((copied.source_asset_id,copied.product_id,copied.status),(111,11,"COMPLETED"))
        self.assertEqual(copied.source_snapshot[MARKER]["task_id"],1)
        _validate_source(self.db,copied)
        serialized = _serialize(self.db,copied)
        self.assertEqual(serialized["output_asset_id"],113)
        self.assertEqual(serialized["source_snapshot"]["source"]["asset_id"],111)
        self.assertEqual(serialized["previews"][0]["output_url"],"/new-13.mp4")
        self.assertEqual(row_data(self.task),original)
        self.assertEqual(row_data(self.existing),existing)
        self.assertEqual(json.loads(Path(result["backup_path"]).read_text(encoding="utf-8"))["asset_map"]["12"],112)
        again = self.migrate()
        self.assertEqual(again["tasks_to_copy"],0)
        self.assertEqual(again["task_map"],result["task_map"])
        self.assertEqual(self.db.query(EcommerceVideoRefinementVersion).count(),1)

    def test_rewrites_materials_voices_snapshots_hashes_and_lineage_without_reusing_old_checkpoints(self):
        self.migrate()
        snapshot = self.db.query(EcommerceVideoRefinementSnapshot).one()
        request = self.db.query(EcommerceVideoRefinementRequest).one()
        plan = self.db.query(EcommerceVideoRefinementPlan).one()
        voice = self.db.query(EcommerceVideoRefinementVoiceover).one()
        preview = self.db.query(EcommerceVideoRefinementPreview).one()
        item = plan.plan_json["items"][0]
        self.assertEqual(item["material_segments"][0]["asset_id"],112)
        self.assertEqual(item["evidence"],["material-112"])
        self.assertEqual(item["voiceover_id"],voice.id)
        self.assertEqual(plan.plan_json["narration_track"][0]["source_asset_id"],voice.id)
        self.assertEqual(snapshot.snapshot_json["voiceover_assets"][0]["id"],voice.id)
        self.assertEqual(snapshot.snapshot_json["transcript"][0]["cue_id"],f"tts-{voice.id}-0")
        self.assertEqual(plan.plan_json["request_snapshot_id"],request.id)
        self.assertEqual(plan.plan_json["snapshot_hash"],request.snapshot_hash)
        self.assertEqual(snapshot.content_hash,content_hash(snapshot.snapshot_json))
        self.assertEqual(plan.plan_hash,content_hash(plan.plan_json))
        self.assertEqual(preview.plan_hash,plan.plan_hash)
        self.assertIsNone(snapshot.snapshot_json["engineering_plan"])
        self.assertEqual(snapshot.snapshot_json["legacy_engineering_plan"],{"product_id":1})
        job = self.db.query(EcommerceVideoRefinementAnalysisJob).one()
        self.assertEqual(job.batches_json,{})
        self.assertIn("legacy_analysis_checkpoints",job.result_json)
        lineage = self.db.query(EcommerceVideoLineageSegment).one()
        self.assertEqual((lineage.source_asset_id,lineage.original_asset_id,lineage.target_asset_id,lineage.plan_version_id),
                         (112,112,113,plan.id))

    def test_running_history_is_paused_and_missing_materials_are_visible_structured_blockers(self):
        self.db.delete(self.version); self.db.delete(self.lineage); self.db.commit()
        self.db.delete(self.db.get(VideoAsset,12)); self.db.delete(self.assets[12]); self.db.commit()
        self.task.status="PLAN_CONFIRMED"
        self.preview.status="rendering_v3"
        self.voice.status="queued_v3"
        self.job.status="analyzing_v3"
        self.db.commit()
        self.migrate()
        copied = self.db.query(EcommerceVideoRefinementTask).filter(EcommerceVideoRefinementTask.id!=1).one()
        self.assertEqual((copied.status,copied.confirmed_revision),("MATERIAL_REQUIRED",None))
        self.assertEqual(copied.updated_at,self.task.updated_at)
        plan = self.db.query(EcommerceVideoRefinementPlan).one().plan_json
        self.assertFalse(plan["items"][0]["enabled"])
        self.assertEqual(plan["execution_blockers"][-1]["code"],"MATERIAL_REQUIRED")
        self.assertIn("12",plan["execution_blockers"][-1]["message"])
        for model in (EcommerceVideoRefinementPreview,EcommerceVideoRefinementVoiceover,EcommerceVideoRefinementAnalysisJob):
            self.assertEqual(self.db.query(model).one().status,"failed")

    def test_wrong_counts_tenants_hashes_or_ambiguous_asset_mappings_never_copy_history(self):
        self.assertEqual(migrate_legacy_refinements(self.db,"t2")["tasks_to_copy"],0)
        with self.assertRaisesRegex(ValueError,"预期"):
            migrate_legacy_refinements(self.db,"t1",apply=True,expected_tasks=2,backup_dir=self.backup_dir)
        self.assets[11].content_hash="z"*64; self.db.commit()
        with self.assertRaisesRegex(ValueError,"哈希"):
            self.migrate()
        self.assets[11].content_hash="1"*64
        duplicate = EcommerceVideoAsset(tenant_id="t1",product_id=11,asset_id="duplicate",filename="duplicate.mp4",
                                       source="legacy_video:11",object_url="/duplicate.mp4",content_hash="1"*64)
        self.db.add(duplicate); self.db.commit()
        with self.assertRaisesRegex(ValueError,"多个电商视频"):
            self.migrate()
        self.assertEqual(self.db.query(EcommerceVideoRefinementTask).count(),1)
        self.assertEqual(list(self.backup_dir.iterdir()),[])
