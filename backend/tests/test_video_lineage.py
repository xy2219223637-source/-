from __future__ import annotations

import asyncio
from datetime import date, datetime
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.auto_edit import render
from app.api.video_library import get_asset, list_assets, validate_video_asset
from app.api.video_lineage import get_lineage
from app.auth import AuthContext
from app.database import Base
from app.models import EditingPlanVersion, Product, VideoAsset, VideoEditProject, VideoEditProjectAsset, VideoLineageSegment, VideoRenderJob
from app.services.video_lineage import record_render_lineage, reference_counts
from app.services.video_lineage_backfill import backfill_video_lineage


class VideoLineageTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="enrui-lineage-")
        self.engine = create_engine(f"sqlite:///{Path(self.temp.name).as_posix()}/test.db")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine, autoflush=False)
        self.db = self.session()
        self.auth = AuthContext("owner", "t1", "owner", "owner@example.com", "测试")
        self.product = Product(tenant_id="t1", name="测试", category="测试", price=1, description="", image_urls=[], detail_image_urls=[])
        self.db.add(self.product)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        self.temp.cleanup()

    def asset(self, name, kind="material", tenant="t1", digest=None):
        asset = VideoAsset(tenant_id=tenant, product_id=self.product.id, asset_id=name,
                           filename=f"{name}.mp4", library_type="finished" if kind == "final_output" else "semi_finished",
                           asset_kind=kind, object_url=f"/{name}.mp4", content_hash=digest or name,
                           duration_seconds=20, status="analyzed")
        self.db.add(asset)
        self.db.commit()
        return asset

    def clip(self, source, start=1.125, end=3.625):
        return {"asset_id": source.asset_id, "source_range": {"start": start, "end": end},
                "timeline_range": {"start": 100, "end": 200}}

    def record(self, source, target, clips=None, mode="smart_compose", audio=None):
        clips = clips or [self.clip(source)]
        variant = {"variant_id": "V1", "edit_mode": mode,
                   "video_track": clips, "narrative_segments": [self.clip(audio)] if audio else clips}
        record_render_lineage(self.db, target, variant, [source] + ([audio] if audio else []), 12)
        self.db.commit()

    def query(self, asset, direction="used", **kwargs):
        return get_lineage(asset.id, direction=direction, page=kwargs.pop("page", 1),
                           page_size=kwargs.pop("page_size", 6), db=self.db, auth=self.auth, **kwargs)

    def test_repeated_segments_count_once_and_audio_is_not_a_picture_source(self):
        source, audio = self.asset("picture"), self.asset("audio")
        target = self.asset("final", "final_output")
        self.record(source, target, [self.clip(source)] * 3, audio=audio)
        self.assertEqual(reference_counts(self.db, "t1", [source.id, audio.id]), {source.id: 1})
        used = self.query(source)
        self.assertEqual((used["total"], used["reference_count"]), (1, 1))
        self.assertEqual(len(used["items"][0]["segments"]), 3)
        self.assertEqual(used["items"][0]["segments"][1]["target_start"], 2.5)
        self.assertEqual(used["items"][0]["segments"][0]["source_start"], 1.125)
        self.assertEqual(self.query(target, "sources")["source_count"], 1)
        self.assertEqual(get_asset(source.id, self.db, self.auth)["reference_count"], 1)
        listed = list_assets(self.product.id, None, None, self.db, self.auth)
        self.assertEqual(next(row for row in listed if row["id"] == source.id)["reference_count"], 1)

    def test_direct_only_and_legacy_origin_ids_do_not_invent_relations(self):
        source = self.asset("source")
        first, second = self.asset("first", "final_output"), self.asset("second", "final_output")
        self.record(source, first)
        self.record(first, second)
        self.assertEqual(self.query(source)["reference_count"], 1)
        self.assertEqual(self.query(first)["reference_count"], 1)
        legacy = self.asset("legacy", "final_output")
        legacy.origin_asset_ids = [source.asset_id]
        self.db.commit()
        self.assertEqual(self.query(legacy, "sources")["source_count"], 0)

    def test_remix_and_manual_use_the_rendered_picture_track(self):
        source, decoy = self.asset("source"), self.asset("decoy")
        for mode in ("finished_remix", "manual_compose"):
            target = self.asset(mode, "final_output")
            variant = {"variant_id": "V1", "edit_mode": mode,
                       "video_track": [self.clip(decoy)], "narrative_segments": [self.clip(source)]}
            record_render_lineage(self.db, target, variant, [source, decoy], 1)
            self.db.commit()
            item = self.query(target, "sources")["items"][0]
            self.assertEqual(item["source"]["id"], source.id)
            self.assertEqual(item["segments"][0]["source_start"], 0 if mode == "manual_compose" else 1.125)

    def test_immutable_records_and_invalid_cross_tenant_source(self):
        source, target = self.asset("source"), self.asset("target", "final_output")
        self.record(source, target)
        with self.assertRaises(ValueError):
            self.record(source, target)
        self.db.rollback()
        foreign = self.asset("foreign", tenant="t2")
        second = self.asset("second", "final_output")
        with self.assertRaises(ValueError):
            self.record(foreign, second)
        self.db.rollback()
        with self.assertRaises(HTTPException) as raised:
            self.query(foreign)
        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(reference_counts(self.db, "t2", [source.id]), {})

    def test_identical_reupload_tracks_original_final_without_filename_matching(self):
        original = self.asset("original", "final_output", digest="a" * 64)
        copied = self.asset("copied", digest="a" * 64)
        target = self.asset("target", "final_output")
        self.record(copied, target)
        self.assertEqual(reference_counts(self.db, "t1", [original.id, copied.id]), {original.id: 1, copied.id: 1})
        self.assertEqual(self.query(target, "sources")["items"][0]["source"]["id"], original.id)
        self.assertEqual(self.query(original)["items"][0]["source"]["id"], original.id)
        different = self.asset("different")
        different.filename = original.filename
        self.db.commit()
        self.record(different, self.asset("unrelated", "final_output"))
        self.assertEqual(self.query(original)["reference_count"], 1)

    def test_filters_pagination_and_inclusive_shanghai_dates(self):
        source = self.asset("source")
        for i in range(8):
            target = self.asset(f"target-{i}", "final_output")
            self.record(source, target)
        rows = self.db.query(VideoLineageSegment).order_by(VideoLineageSegment.id).all()
        rows[0].created_at = datetime(2026, 9, 16, 16, 0)
        rows[1].created_at = datetime(2026, 9, 17, 15, 59, 59)
        for row in rows[2:]:
            row.created_at = datetime(2026, 9, 17, 16, 0)
        self.db.commit()
        result = self.query(source, start_date=date(2026, 9, 17), end_date=date(2026, 9, 17))
        self.assertEqual((result["total"], result["reference_count"]), (2, 8))
        self.assertEqual(self.query(source, page=2)["page"], 2)
        self.assertEqual(len(self.query(source, page=2)["items"]), 2)
        self.assertEqual(len(self.query(source, page=2)["matched_asset_ids"]), 8)
        self.assertEqual(self.query(source, q="target-3")["total"], 1)
        self.assertEqual(self.query(source, q="%")["total"], 0)
        with self.assertRaises(HTTPException):
            self.query(source, start_date=date(2026, 9, 18), end_date=date(2026, 9, 17))

    def test_ambiguous_identical_finals_do_not_guess_an_original(self):
        first = self.asset("first", "final_output", digest="b" * 64)
        second = self.asset("second", "final_output", digest="b" * 64)
        copied = self.asset("copied", digest="b" * 64)
        target = self.asset("target", "final_output")
        self.record(copied, target)
        self.assertEqual(reference_counts(self.db, "t1", [first.id, second.id, copied.id]), {copied.id: 1})
        self.assertEqual(self.query(target, "sources")["items"][0]["source"]["id"], copied.id)

    def historical_output(self, source, name="historical", audio=None):
        project = VideoEditProject(tenant_id="t1", product_id=self.product.id, name=name)
        self.db.add(project)
        self.db.flush()
        clip = self.clip(source, 2, 6)
        variant = {"variant_id": "V1", "edit_mode": "smart_compose", "actual_duration_seconds": 4,
                   "video_track": [clip], "narrative_segments": [self.clip(audio or source, 0, 4)]}
        plan = EditingPlanVersion(tenant_id="t1", project_id=project.id, version=1, plan_json={"variants": [variant]})
        self.db.add(plan)
        self.db.flush()
        job = VideoRenderJob(tenant_id="t1", project_id=project.id, plan_version_id=plan.id,
                             variant_id="V1", output_url=f"/{name}.mp4", duration_seconds=4, status="completed")
        self.db.add(job)
        self.db.flush()
        target = self.asset(name, "final_output")
        target.project_id=project.id
        target.duration_seconds=4
        target.origin_asset_ids=list({source.asset_id, (audio or source).asset_id})
        self.db.commit()
        return target, job, plan

    def test_history_dry_run_apply_and_idempotence(self):
        source, audio = self.asset("picture"), self.asset("audio")
        target, _, _ = self.historical_output(source, audio=audio)
        preview = backfill_video_lineage(self.db, "t1")
        self.assertEqual(preview[0]["status"], "ready")
        self.assertEqual(self.db.query(VideoLineageSegment).count(), 0)
        applied = backfill_video_lineage(self.db, "t1", apply=True)
        self.assertEqual(applied[0]["status"], "restored")
        row = self.db.query(VideoLineageSegment).one()
        self.assertEqual((row.source_start, row.source_end, row.target_start, row.target_end), (2, 6, 0, 4))
        self.assertEqual(row.created_at, target.created_at)
        self.assertEqual(reference_counts(self.db, "t1", [source.id, audio.id]), {source.id: 1})
        self.assertEqual(backfill_video_lineage(self.db, "t1", apply=True)[0]["status"], "existing")
        self.assertEqual(self.db.query(VideoLineageSegment).count(), 1)
        self.assertEqual(backfill_video_lineage(self.db, "t2", apply=True), [])

    def test_history_missing_job_and_ambiguous_job_are_not_inferred(self):
        source=self.asset("source")
        target, job, _=self.historical_output(source)
        job.output_url="/different-output.mp4"
        self.db.commit()
        self.assertEqual(backfill_video_lineage(self.db,"t1",True)[0]["status"],"skipped")
        job.output_url=target.object_url
        self.db.add(VideoRenderJob(tenant_id="t1",project_id=job.project_id,plan_version_id=job.plan_version_id,
                                  variant_id="V1",output_url=target.object_url,duration_seconds=4,status="completed"))
        self.db.commit()
        self.assertEqual(backfill_video_lineage(self.db,"t1",True)[0]["status"],"skipped")
        self.assertEqual(self.db.query(VideoLineageSegment).count(),0)

    def test_history_inconsistent_plan_is_skipped_without_blocking_valid_outputs(self):
        source=self.asset("source")
        invalid, _, _=self.historical_output(source,"invalid")
        invalid.duration_seconds=10
        self.db.commit()
        valid, _, _=self.historical_output(source,"valid")
        results=backfill_video_lineage(self.db,"t1",True)
        self.assertEqual({r["id"]:r["status"] for r in results},{invalid.id:"skipped",valid.id:"restored"})
        self.assertEqual(self.db.query(VideoLineageSegment).one().target_asset_id,valid.id)

    def test_finished_multipart_validation_records_sha256(self):
        import hashlib
        asset = self.asset("upload")
        asset.library_type = "finished"
        asset.status = "validating"
        asset.content_hash = "oss:etag"
        self.db.commit()
        path = Path(self.temp.name) / "upload.mp4"
        path.write_bytes(b"identical-video-file")
        storage = Mock()
        storage.local_path.return_value = path
        with patch("app.api.video_library.SessionLocal", self.session), patch("app.api.video_library.get_storage", return_value=storage), patch("app.api.video_library._probe", return_value={"duration": 20, "width": 320, "height": 240, "fps": 30, "has_audio": True}):
            validate_video_asset(asset.id)
        self.db.refresh(asset)
        self.assertEqual(asset.content_hash, hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(asset.status, "queued_for_analysis")

    def test_render_success_rerender_and_failed_registration_are_atomic(self):
        source = self.asset("source")
        source.library_type = "finished"
        source.reusable_status = "reusable"
        project = VideoEditProject(tenant_id="t1", product_id=self.product.id, name="test",
                                   edit_mode="manual_compose", structure=[{"role": "hook", "asset_ids": [source.asset_id]}])
        self.db.add(project)
        self.db.flush()
        self.db.add(VideoEditProjectAsset(tenant_id="t1", project_id=project.id, asset_id=source.id))
        self.db.commit()
        def fake_render(plan, assets, output, ratio):
            output.write_bytes(b"rendered-video")
            return 20
        storage = Mock()
        storage.upload = AsyncMock(return_value="/rendered.mp4")
        with patch("app.api.auto_edit._render_variant", side_effect=fake_render), patch("app.api.auto_edit.get_storage", return_value=storage), patch("app.api.auto_edit.enqueue_video_analysis"):
            asyncio.run(render(project.id, self.db, self.auth))
            first_target = self.db.query(VideoLineageSegment).one().target_asset_id
            asyncio.run(render(project.id, self.db, self.auth))
            self.assertEqual(self.query(source)["reference_count"], 2)
            self.assertEqual(self.db.query(VideoLineageSegment).filter_by(target_asset_id=first_target).count(), 1)
            with patch("app.api.auto_edit.inherit_public_tags_to_final", side_effect=RuntimeError("registration failed")):
                asyncio.run(render(project.id, self.db, self.auth))
            self.assertEqual(self.db.query(VideoAsset).filter_by(asset_kind="final_output").count(), 2)
            self.assertEqual(self.db.query(VideoLineageSegment).count(), 2)
            self.assertEqual(self.db.query(VideoRenderJob).one().status, "failed")


if __name__ == "__main__":
    unittest.main()
