from __future__ import annotations

import asyncio
import hashlib
import io
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException, UploadFile
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import ecommerce_video_library as library
from app.auth import AuthContext
from app.config import Settings
from app.database import Base
from app.models import EcommerceProduct, EcommerceVideoAsset, EcommerceVideoAssetAnalysis, EcommerceVideoHighlight
from app.services import video_analysis_queue as queue


class EcommerceVideoAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        event.listen(self.engine, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"))
        Base.metadata.create_all(self.engine, tables=[
            table for name, table in Base.metadata.tables.items() if name.startswith("ecommerce_")
        ])
        self.addCleanup(self.engine.dispose)
        directory = tempfile.TemporaryDirectory(prefix="ecommerce-analysis-test-")
        self.addCleanup(directory.cleanup)
        self.source = Path(directory.name) / "source.mp4"
        self.source.write_bytes(b"test video bytes")
        self.settings = SimpleNamespace(
            llm_vision_model="vision-test", whisper_enabled=False, director_asr_required=False,
            video_library_max_duration_seconds=600, ecommerce_video_max_upload_mb=300,
        )
        self.media = {"duration": 8.5, "width": 540, "height": 960, "fps": 30, "has_audio": True}
        self.storage = Mock()
        self.storage.local_path.return_value = self.source
        self.storage.signed_url.return_value = "https://video.example/source.mp4"
        self.client = Mock()
        self.client.chat_json = AsyncMock(return_value={
            "summary": "Product demonstration", "video_tags": ["demo"],
            "highlights": [{"start": 0, "end": 4, "track_role": "b_roll", "content_role": "product_demo"}],
        })
        with Session(self.engine) as db:
            db.add(EcommerceProduct(id=1, tenant_id="tenant-a", name="Product"))
            db.commit()
        patches = [
            patch.object(library, "SessionLocal", side_effect=lambda: Session(self.engine)),
            patch.object(library, "get_settings", return_value=self.settings),
            patch.object(library, "_asset_storage", return_value=self.storage),
            patch.object(library, "LLMClient", return_value=self.client),
            patch.object(library, "transcribe_configured_media", return_value={"enabled": False, "text": "", "segments": [], "words": []}),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def asset(self, **overrides):
        values = dict(
            tenant_id="tenant-a", product_id=1, asset_id="asset-test", filename="source.mp4",
            object_url="/api/storage/objects/test", content_hash="old", library_type="finished",
            asset_kind="material", status="analysis_failed", duration_seconds=0, width=0, height=0,
            has_audio=False, excluded=False,
        )
        values.update(overrides)
        with Session(self.engine) as db:
            row = EcommerceVideoAsset(**values)
            db.add(row)
            db.commit()
            return row.id

    def test_analysis_repairs_metadata_before_calling_vision_model(self):
        asset_id = self.asset()
        with patch.object(library, "_probe", return_value=self.media) as probe:
            asyncio.run(library.analyze_video_asset(asset_id))
        probe.assert_called_once_with(self.source)
        self.client.chat_json.assert_awaited_once()
        call = self.client.chat_json.call_args
        self.assertEqual(call.kwargs["model"], "vision-test")
        self.assertIn("8.500", call.args[0][0]["content"][0]["text"])
        with Session(self.engine) as db:
            row = db.get(EcommerceVideoAsset, asset_id)
            self.assertEqual(row.status, "analyzed")
            self.assertEqual((row.duration_seconds, row.width, row.height, row.fps, row.has_audio),
                             (8.5, 540, 960, 30, True))
            self.assertEqual(row.content_hash, hashlib.sha256(self.source.read_bytes()).hexdigest())
            analysis = db.query(EcommerceVideoAssetAnalysis).filter_by(asset_id=asset_id).one()
            self.assertEqual(analysis.input_video_seconds, 8.5)
            self.assertEqual(analysis.request_count, 1)
            self.assertEqual(analysis.status, "completed")
            self.assertEqual(db.query(EcommerceVideoHighlight).filter_by(asset_id=asset_id).count(), 1)

    def test_invalid_media_fails_before_any_model_request(self):
        asset_id = self.asset()
        with patch.object(library, "_probe", side_effect=HTTPException(422, "invalid video")):
            asyncio.run(library.analyze_video_asset(asset_id))
        self.client.chat_json.assert_not_awaited()
        with Session(self.engine) as db:
            self.assertEqual(db.get(EcommerceVideoAsset, asset_id).status, "analysis_failed")
            analysis = db.query(EcommerceVideoAssetAnalysis).filter_by(asset_id=asset_id).one()
            self.assertEqual(analysis.error_message, "invalid video")
            self.assertEqual(analysis.status, "failed")
            self.assertEqual(analysis.request_count, 0)

    def test_configured_asr_is_used_even_when_legacy_whisper_is_disabled(self):
        asset_id = self.asset(duration_seconds=8.5, width=540, height=960, has_audio=True)
        transcript = {"enabled": True, "provider": "volcengine_asr", "text": "Product speech",
                      "segments": [{"start": 0, "end": 3, "text": "Product speech"}],
                      "words": [{"start": 0, "end": 3, "text": "Product speech"}]}
        with patch.object(library, "transcribe_configured_media", return_value=transcript) as transcribe:
            asyncio.run(library.analyze_video_asset(asset_id))
        transcribe.assert_called_once()
        self.assertEqual(transcribe.call_args.args[0], self.source)
        self.assertIn("Product speech", self.client.chat_json.call_args.args[0][0]["content"][0]["text"])
        with Session(self.engine) as db:
            analysis = db.query(EcommerceVideoAssetAnalysis).filter_by(asset_id=asset_id).one()
            self.assertEqual(analysis.analysis_json["transcript"], transcript)
            self.assertNotIn("transcript_error", analysis.analysis_json)
            self.assertEqual(db.query(EcommerceVideoHighlight).filter_by(asset_id=asset_id).one().speech_text,
                             "Product speech")

    def test_silent_media_does_not_request_asr(self):
        asset_id = self.asset(duration_seconds=8.5, width=540, height=960, has_audio=False)
        with patch.object(library, "transcribe_configured_media") as transcribe:
            asyncio.run(library.analyze_video_asset(asset_id))
        transcribe.assert_not_called()
        self.client.chat_json.assert_awaited_once()

    def test_optional_asr_failure_preserves_real_diagnostic_and_visual_results(self):
        asset_id = self.asset(duration_seconds=8.5, width=540, height=960, has_audio=True)
        with patch.object(library, "transcribe_configured_media", side_effect=RuntimeError("ASR service unavailable")):
            asyncio.run(library.analyze_video_asset(asset_id))
        with Session(self.engine) as db:
            self.assertEqual(db.get(EcommerceVideoAsset, asset_id).status, "analyzed")
            analysis = db.query(EcommerceVideoAssetAnalysis).filter_by(asset_id=asset_id).one()
            self.assertEqual(analysis.analysis_json["transcript_error"], "ASR service unavailable")

    def test_required_asr_failure_stops_before_vision_request(self):
        asset_id = self.asset(duration_seconds=8.5, width=540, height=960, has_audio=True)
        self.settings.director_asr_required = True
        with patch.object(library, "transcribe_configured_media", side_effect=RuntimeError("ASR service unavailable")):
            asyncio.run(library.analyze_video_asset(asset_id))
        self.client.chat_json.assert_not_awaited()
        with Session(self.engine) as db:
            self.assertEqual(db.get(EcommerceVideoAsset, asset_id).status, "analysis_failed")
            analysis = db.query(EcommerceVideoAssetAnalysis).filter_by(asset_id=asset_id).one()
            self.assertIn("ASR service unavailable", analysis.error_message)
            self.assertEqual(analysis.request_count, 0)

    def test_direct_upload_validates_then_queues_and_completes_analysis(self):
        auth = AuthContext("user", "tenant-a", "owner", "test@example.com", "Test")
        upload = UploadFile(filename="new.mp4", file=io.BytesIO(b"new video"), size=9)
        self.storage.name = "test"
        self.storage.upload_path = AsyncMock(return_value="/uploaded/new.mp4")
        with Session(self.engine) as db, \
             patch.object(library, "_tenant_video_storage", return_value=self.storage), \
             patch.object(library, "_probe", return_value=self.media), \
             patch.object(library, "enqueue_video_analysis") as enqueue:
            result = asyncio.run(library.upload_assets(1, "manual_upload", "finished", "material", [upload], db, auth))
        self.assertEqual(result[0]["status"], "queued_for_analysis")
        self.assertEqual(result[0]["duration_seconds"], 8.5)
        enqueue.assert_called_once()
        queue._run_ecommerce_asset(result[0]["id"])
        with Session(self.engine) as db:
            self.assertEqual(db.get(EcommerceVideoAsset, result[0]["id"]).status, "analyzed")

    def test_direct_final_upload_validates_media_without_queuing_analysis(self):
        auth = AuthContext("user", "tenant-a", "owner", "test@example.com", "Test")
        upload = UploadFile(filename="final.mp4", file=io.BytesIO(b"new video"), size=9)
        self.storage.name = "test"
        self.storage.upload_path = AsyncMock(return_value="/uploaded/final.mp4")
        with Session(self.engine) as db, \
             patch.object(library, "_tenant_video_storage", return_value=self.storage), \
             patch.object(library, "_probe", return_value=self.media) as probe, \
             patch.object(library, "enqueue_video_analysis") as enqueue:
            result = asyncio.run(library.upload_assets(1, "manual_upload", "finished", "final_output", [upload], db, auth))
        self.assertEqual(result[0]["status"], "uploaded")
        self.assertEqual(result[0]["reusable_status"], "blocked")
        self.assertEqual((result[0]["duration_seconds"], result[0]["width"], result[0]["height"]), (8.5, 540, 960))
        probe.assert_called_once()
        enqueue.assert_not_called()
        queue._run_ecommerce_asset(result[0]["id"])
        self.client.chat_json.assert_not_awaited()
        with Session(self.engine) as db:
            self.assertEqual(db.query(EcommerceVideoAssetAnalysis).count(), 0)

    def test_direct_upload_rejects_invalid_metadata_before_storage_or_queue(self):
        auth = AuthContext("user", "tenant-a", "owner", "test@example.com", "Test")
        self.storage.upload_path = AsyncMock()
        for duration in (0, float("nan"), 601):
            with self.subTest(duration=duration):
                upload = UploadFile(filename="bad.mp4", file=io.BytesIO(b"bad video"), size=9)
                with Session(self.engine) as db, \
                     patch.object(library, "_probe", return_value={**self.media, "duration": duration}), \
                     patch.object(library, "_tenant_video_storage", return_value=self.storage), \
                     patch.object(library, "enqueue_video_analysis") as enqueue:
                    with self.assertRaises(HTTPException) as error:
                        asyncio.run(library.upload_assets(1, "manual_upload", "finished", "material", [upload], db, auth))
                self.assertEqual(error.exception.status_code, 422)
                enqueue.assert_not_called()
        self.storage.upload_path.assert_not_awaited()
        with Session(self.engine) as db:
            self.assertEqual(db.query(EcommerceVideoAsset).count(), 0)

    def test_multipart_upload_passes_validation_before_analysis(self):
        auth = AuthContext("user", "tenant-a", "owner", "test@example.com", "Test")
        self.storage.name = "test"
        self.storage.begin_multipart = AsyncMock(return_value=("source/new.mp4", "upload-id"))
        self.storage.signed_part_url.return_value = "https://upload.example/part1"
        self.storage.complete_multipart = AsyncMock(return_value=("/uploaded/new.mp4", 9, "etag"))
        with Session(self.engine) as db, \
             patch.object(library, "_tenant_video_storage", return_value=self.storage), \
             patch.object(library, "get_storage_by_name", return_value=self.storage), \
             patch.object(library, "enqueue_video_analysis") as enqueue:
            session = asyncio.run(library.start_multipart(library.MultipartStart(
                product_id=1, filename="new.mp4", size_bytes=9, asset_kind="material"), db, auth))
            result = asyncio.run(library.complete_multipart(library.MultipartComplete(
                session_id=session["session_id"]), db, auth))
        self.assertEqual(result["status"], "validating")
        enqueue.assert_called_once()
        queue._run_ecommerce_asset(result["id"])
        self.client.chat_json.assert_not_awaited()
        with patch.object(library, "_probe", return_value=self.media):
            library.validate_video_asset(result["id"])
        queue._run_ecommerce_asset(result["id"])
        with Session(self.engine) as db:
            self.assertEqual(db.get(EcommerceVideoAsset, result["id"]).status, "analyzed")
            self.assertEqual(db.query(EcommerceVideoAssetAnalysis).count(), 1)

    def test_multipart_final_upload_only_validates_file_and_media_metadata(self):
        auth = AuthContext("user", "tenant-a", "owner", "test@example.com", "Test")
        self.storage.name = "test"
        self.storage.begin_multipart = AsyncMock(return_value=("source/final.mp4", "upload-id"))
        self.storage.signed_part_url.return_value = "https://upload.example/part1"
        self.storage.complete_multipart = AsyncMock(return_value=("/uploaded/final.mp4", 9, "etag"))
        with Session(self.engine) as db, \
             patch.object(library, "_tenant_video_storage", return_value=self.storage), \
             patch.object(library, "get_storage_by_name", return_value=self.storage), \
             patch.object(library, "enqueue_video_analysis") as enqueue:
            session = asyncio.run(library.start_multipart(library.MultipartStart(
                product_id=1, filename="final.mp4", size_bytes=9, asset_kind="final_output"), db, auth))
            result = asyncio.run(library.complete_multipart(library.MultipartComplete(
                session_id=session["session_id"]), db, auth))
        self.assertEqual(result["status"], "validating")
        enqueue.assert_called_once()
        with patch.object(library, "_probe", return_value=self.media) as probe, \
             patch.object(library, "transcribe_configured_media") as transcribe:
            queue._run_ecommerce_asset(result["id"])
            library.validate_video_asset(result["id"])
            queue._run_ecommerce_asset(result["id"])
        probe.assert_called_once_with(self.source)
        transcribe.assert_not_called()
        self.client.chat_json.assert_not_awaited()
        with Session(self.engine) as db:
            asset = db.get(EcommerceVideoAsset, result["id"])
            self.assertEqual(asset.status, "uploaded")
            self.assertEqual(asset.content_hash, hashlib.sha256(self.source.read_bytes()).hexdigest())
            self.assertEqual((asset.duration_seconds, asset.width, asset.height), (8.5, 540, 960))
            self.assertEqual(db.query(EcommerceVideoAssetAnalysis).count(), 0)

    def test_legacy_queued_final_never_requests_library_analysis(self):
        asset_id = self.asset(asset_kind="final_output", status="queued_for_analysis", has_audio=True,
                              duration_seconds=8.5, width=540, height=960)
        with patch.object(library, "transcribe_configured_media") as transcribe, \
             patch.object(library, "_probe") as probe:
            asyncio.run(library.analyze_video_asset(asset_id, force=True))
            queue._run_ecommerce_asset(asset_id)
        transcribe.assert_not_called()
        probe.assert_not_called()
        self.client.chat_json.assert_not_awaited()
        with Session(self.engine) as db:
            self.assertEqual(db.query(EcommerceVideoAssetAnalysis).count(), 0)
            self.assertEqual(db.get(EcommerceVideoAsset, asset_id).status, "queued_for_analysis")

    def test_final_reanalysis_is_rejected_without_changing_upload_state(self):
        asset_id = self.asset(asset_kind="final_output", status="uploaded")
        auth = AuthContext("user", "tenant-a", "owner", "test@example.com", "Test")
        with Session(self.engine) as db, patch.object(library, "enqueue_video_analysis") as enqueue:
            with self.assertRaises(HTTPException) as error:
                library.reanalyze(asset_id, db, auth)
            self.assertEqual(error.exception.status_code, 422)
            self.assertEqual(db.get(EcommerceVideoAsset, asset_id).status, "uploaded")
            enqueue.assert_not_called()

    def test_invalid_final_file_reports_validation_failure_without_ai_analysis(self):
        asset_id = self.asset(asset_kind="final_output", status="validating")
        with patch.object(library, "_probe", side_effect=HTTPException(422, "invalid video")):
            library.validate_video_asset(asset_id)
        self.client.chat_json.assert_not_awaited()
        with Session(self.engine) as db:
            self.assertEqual(db.get(EcommerceVideoAsset, asset_id).status, "validation_failed")
            validation = db.query(EcommerceVideoAssetAnalysis).filter_by(asset_id=asset_id).one()
            self.assertEqual((validation.model, validation.prompt_version), ("media-validation", "ffprobe-v1"))
            self.assertEqual(validation.error_message, "invalid video")
            self.assertEqual(validation.request_count, 0)

    def test_validation_reads_local_media_and_hashes_actual_bytes(self):
        asset_id = self.asset(status="validating")
        with patch.object(library, "_probe", return_value=self.media) as probe:
            library.validate_video_asset(asset_id)
        probe.assert_called_once_with(self.source)
        self.storage.signed_url.assert_not_called()
        with Session(self.engine) as db:
            row = db.get(EcommerceVideoAsset, asset_id)
            self.assertEqual(row.status, "queued_for_analysis")
            self.assertEqual(row.duration_seconds, 8.5)
            self.assertEqual(row.content_hash, hashlib.sha256(self.source.read_bytes()).hexdigest())

    def test_validation_rejects_zero_nonfinite_and_overlong_duration(self):
        asset_id = self.asset(status="validating")
        for duration in (0, float("nan"), 601):
            with self.subTest(duration=duration):
                with Session(self.engine) as db:
                    db.get(EcommerceVideoAsset, asset_id).status = "validating"
                    db.commit()
                with patch.object(library, "_probe", return_value={**self.media, "duration": duration}):
                    library.validate_video_asset(asset_id)
                with Session(self.engine) as db:
                    self.assertEqual(db.get(EcommerceVideoAsset, asset_id).status, "analysis_failed")
        self.client.chat_json.assert_not_awaited()

    def test_retry_validates_missing_metadata_and_is_idempotent_while_active(self):
        asset_id = self.asset()
        auth = AuthContext("user", "tenant-a", "owner", "test@example.com", "Test")
        with Session(self.engine) as db, patch.object(library, "enqueue_video_analysis") as enqueue:
            self.assertEqual(library.reanalyze(asset_id, db, auth)["status"], "validating")
            self.assertEqual(library.reanalyze(asset_id, db, auth)["status"], "validating")
            enqueue.assert_called_once()
            row = db.get(EcommerceVideoAsset, asset_id)
            row.status = "analysis_failed"
            row.duration_seconds, row.width, row.height = 8.5, 540, 960
            db.commit()
            self.assertEqual(library.reanalyze(asset_id, db, auth)["status"], "queued_for_analysis")

    def test_excluded_asset_cannot_be_left_in_an_unprocessable_queue(self):
        asset_id = self.asset(excluded=True, reusable_status="excluded")
        auth = AuthContext("user", "tenant-a", "owner", "test@example.com", "Test")
        with Session(self.engine) as db, patch.object(library, "enqueue_video_analysis") as enqueue:
            with self.assertRaises(HTTPException) as error:
                library.reanalyze(asset_id, db, auth)
            self.assertEqual(error.exception.status_code, 409)
            self.assertEqual(db.get(EcommerceVideoAsset, asset_id).status, "analysis_failed")
            enqueue.assert_not_called()

    def test_completed_result_is_visible_when_a_newer_attempt_was_interrupted(self):
        asset_id = self.asset(status="analyzed", duration_seconds=8.5, width=540, height=960)
        auth = AuthContext("user", "tenant-a", "owner", "test@example.com", "Test")
        with Session(self.engine) as db:
            completed = EcommerceVideoAssetAnalysis(
                tenant_id="tenant-a", asset_id=asset_id, status="completed", summary="Completed result",
                model="vision-test", prompt_version="test", analysis_json={},
            )
            db.add(completed)
            db.flush()
            db.add(EcommerceVideoAssetAnalysis(
                tenant_id="tenant-a", asset_id=asset_id, status="interrupted", summary="",
                model="vision-test", prompt_version="test", analysis_json={},
            ))
            db.commit()
            detail = library.get_asset(asset_id, db, auth)
            listing = library.list_assets(product_id=1, asset_kind="material", db=db, auth=auth)
            self.assertEqual(detail["analysis"]["id"], completed.id)
            self.assertEqual(listing[0]["analysis"]["summary"], "Completed result")
            db.get(EcommerceVideoAsset, asset_id).status = "analyzing"
            db.commit()
            self.assertEqual(library.get_asset(asset_id, db, auth)["analysis"]["status"], "interrupted")

    def test_worker_explicitly_reanalyzes_instead_of_returning_cached_result(self):
        with patch.object(library, "analyze_video_asset", new_callable=AsyncMock) as analyze:
            queue._run_ecommerce_asset(7)
        analyze.assert_awaited_once_with(7, force=True, queued_only=True)

    def test_frame_extraction_uses_existing_executable_resolver(self):
        target = self.source.parent / "frames"

        def run(command, **kwargs):
            Path(command[-1].replace("%02d", "01")).write_bytes(b"frame")

        with patch.object(library, "_ffmpeg_executable", return_value="configured-ffmpeg"), \
             patch.object(library.subprocess, "run", side_effect=run) as process:
            frames = library._extract_frames(self.source, .5, target)
        self.assertEqual(process.call_args.args[0][0], "configured-ffmpeg")
        self.assertEqual(len(frames), 1)
        with patch.object(library, "_ffmpeg_executable", return_value="configured-ffmpeg"), \
             patch.object(library.subprocess, "run"):
            with self.assertRaises(ValueError):
                library._extract_frames(self.source, 8.5, self.source.parent / "empty-frames")

    def test_worker_configuration_inherits_or_overrides_background_setting(self):
        for background, override, expected in ((False, None, False), (True, None, True),
                                               (False, True, True), (True, False, False)):
            with self.subTest(background=background, override=override):
                settings = Settings(_env_file=None, run_background_workers=background,
                                    run_ecommerce_video_analysis_worker=override)
                self.assertEqual(settings.ecommerce_video_analysis_worker_enabled, expected)

    def test_ecommerce_only_worker_never_scans_legacy_libraries(self):
        stopped = threading.Event()
        with patch.object(queue, "_stop", stopped), \
             patch.object(queue, "_next_validation_id") as legacy_validation, \
             patch.object(queue, "_next_asset_id") as legacy_asset, \
             patch.object(queue, "_next_ecommerce_validation_id", return_value=None), \
             patch.object(queue, "_next_ecommerce_asset_id", return_value=7), \
             patch.object(queue, "task_lock", return_value=None), \
             patch.object(queue, "_run_ecommerce_asset", side_effect=lambda _: stopped.set()) as analyze:
            queue._loop(include_legacy=False, include_ecommerce=True)
        analyze.assert_called_once_with(7)
        legacy_validation.assert_not_called()
        legacy_asset.assert_not_called()

    def test_ecommerce_only_startup_preserves_legacy_analysis_states(self):
        with patch.object(queue, "_thread", None), \
             patch.object(queue, "SessionLocal", side_effect=lambda: Session(self.engine)), \
             patch.object(queue, "recover_interrupted_video_analyses") as legacy_recovery, \
             patch.object(queue, "recover_interrupted_ecommerce_video_analyses") as recovery, \
             patch.object(queue.threading, "Thread") as thread, \
             patch.object(queue, "enqueue_video_analysis"):
            queue.start_video_analysis_worker(include_legacy=False, include_ecommerce=True)
        legacy_recovery.assert_not_called()
        recovery.assert_called_once()
        self.assertEqual(thread.call_args.kwargs["kwargs"], {"include_legacy": False, "include_ecommerce": True})
        thread.return_value.start.assert_called_once()

    def test_restarting_active_worker_does_not_interrupt_its_current_analysis(self):
        active_thread = Mock()
        active_thread.is_alive.return_value = True
        with patch.object(queue, "_thread", active_thread), patch.object(queue, "SessionLocal") as database:
            queue.start_video_analysis_worker()
        database.assert_not_called()


if __name__ == "__main__":
    unittest.main()
