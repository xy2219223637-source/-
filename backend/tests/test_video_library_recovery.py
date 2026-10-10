import asyncio
import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import video_library as library
from app.models import VideoAsset, VideoAssetAnalysis, VideoHighlight
from app.services.storage import _provider_object_url
from app.services import video_analysis_queue


class VideoLibraryRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        for model in (VideoAsset, VideoAssetAnalysis, VideoHighlight):
            model.__table__.create(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        self.directory = tempfile.TemporaryDirectory()
        self.source = Path(self.directory.name) / "source.mp4"
        self.source.write_bytes(b"source video bytes")
        self.settings = SimpleNamespace(llm_vision_model="vision-model", whisper_enabled=False,
                                        video_library_max_duration_seconds=600)
        self.storage = MagicMock()
        self.storage.local_path.return_value = self.source
        self.storage.signed_url.return_value = "https://storage.test/video.mp4"
        self.llm = MagicMock()
        self.llm.chat_json = AsyncMock(return_value={"summary": "Video summary", "highlights": [
            {"start": 0, "end": 5, "description": "Product demonstration"}]})
        self.auth = SimpleNamespace(tenant_id="tenant-1", user_id="user-1")
        self.patches = [
            patch.object(library, "SessionLocal", self.sessions),
            patch.object(library, "get_settings", return_value=self.settings),
            patch.object(library, "get_storage_by_name", return_value=self.storage),
            patch.object(library, "_probe", return_value={"duration": 8.93, "width": 1080,
                                                       "height": 1920, "fps": 30, "has_audio": True}),
            patch.object(library, "LLMClient", return_value=self.llm),
            patch.object(library, "enqueue_video_analysis"),
            patch.object(library, "_serialize", side_effect=lambda db, asset, **kwargs: {"status": asset.status}),
        ]
        self.mocks = [p.start() for p in self.patches]

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.directory.cleanup()
        self.engine.dispose()

    def asset(self, provider="private_s3", **kwargs):
        values = dict(tenant_id="tenant-1", product_id=1, asset_id="asset-1", filename="source.mp4",
                      object_url=_provider_object_url(provider, "original/source.mp4"),
                      storage_provider=provider, content_hash="", library_type="finished",
                      status="analysis_failed", manual_tags=["keep me"])
        values.update(kwargs)
        with self.sessions() as db:
            asset = VideoAsset(**values)
            db.add(asset)
            db.commit()
            return asset.id, asset.object_url

    def test_finished_validation_uses_original_provider_and_preserves_location(self):
        for provider in ("aliyun_oss", "private_s3"):
            with self.subTest(provider=provider):
                aid, url = self.asset(provider, asset_id=provider, status="validating")
                library.validate_video_asset(aid)
                with self.sessions() as db:
                    asset = db.get(VideoAsset, aid)
                    self.assertEqual(asset.status, "queued_for_analysis")
                    self.assertEqual((asset.duration_seconds, asset.width, asset.height), (8.93, 1080, 1920))
                    self.assertEqual(asset.content_hash, hashlib.sha256(self.source.read_bytes()).hexdigest())
                    self.assertEqual((asset.storage_provider, asset.object_url), (provider, url))
                    self.assertEqual(asset.manual_tags, ["keep me"])
                self.assertEqual(self.mocks[2].call_args.args, (provider,))
                self.storage.save_bytes.assert_not_called()
                self.storage.save_path.assert_not_called()

    def test_failed_zero_duration_reanalysis_validates_before_ai(self):
        aid, url = self.asset()
        with self.sessions() as db:
            result = library.reanalyze(aid, db, self.auth)
        self.assertEqual(result["status"], "validating")
        self.llm.chat_json.assert_not_awaited()
        library.validate_video_asset(aid)
        asyncio.run(library.analyze_video_asset(aid))
        with self.sessions() as db:
            asset = db.get(VideoAsset, aid)
            self.assertEqual(asset.status, "analyzed")
            self.assertEqual(asset.object_url, url)
            analysis = db.query(VideoAssetAnalysis).filter_by(asset_id=aid).one()
            self.assertEqual(analysis.status, "completed")
            self.assertEqual(analysis.input_video_seconds, 8.93)
            self.assertEqual(db.query(VideoHighlight).filter_by(asset_id=aid).count(), 1)
        self.assertEqual(self.llm.chat_json.await_args.kwargs["model"], "vision-model")
        prompt = self.llm.chat_json.await_args.args[0][0]["content"][0]["text"]
        self.assertIn("8.930", prompt)

    def test_legacy_queued_zero_duration_is_validated_without_ai_request(self):
        aid, _ = self.asset(status="queued_for_analysis")
        asyncio.run(library.analyze_video_asset(aid))
        self.llm.chat_json.assert_not_awaited()
        with self.sessions() as db:
            self.assertEqual(db.get(VideoAsset, aid).status, "queued_for_analysis")
            self.assertEqual(db.get(VideoAsset, aid).duration_seconds, 8.93)
            self.assertEqual(db.query(VideoAssetAnalysis).count(), 0)

    def test_overlong_video_remains_failed_without_ai_request(self):
        aid, _ = self.asset(status="validating")
        self.mocks[3].return_value["duration"] = 601
        library.validate_video_asset(aid)
        with self.sessions() as db:
            self.assertEqual(db.get(VideoAsset, aid).status, "analysis_failed")
            self.assertIn("10", db.query(VideoAssetAnalysis).one().error_message)
        self.llm.chat_json.assert_not_awaited()

    def test_valid_metadata_can_reanalyze_without_revalidation(self):
        aid, _ = self.asset(duration_seconds=8.93, width=1080, height=1920)
        with self.sessions() as db:
            result = library.reanalyze(aid, db, self.auth)
        self.assertEqual(result["status"], "queued_for_analysis")

    def test_other_tenant_cannot_reanalyze(self):
        aid, _ = self.asset(tenant_id="tenant-2")
        with self.sessions() as db:
            with self.assertRaises(HTTPException) as error:
                library.reanalyze(aid, db, self.auth)
            self.assertEqual(error.exception.status_code, 404)
            self.assertEqual(db.get(VideoAsset, aid).status, "analysis_failed")

    def test_pending_validation_cannot_be_enqueued_twice(self):
        aid, _ = self.asset(status="validating")
        with self.sessions() as db:
            with self.assertRaises(HTTPException) as error:
                library.reanalyze(aid, db, self.auth)
            self.assertEqual(error.exception.status_code, 409)

    def test_disabled_worker_does_not_recover_or_consume_production_tasks(self):
        with patch.object(video_analysis_queue, "get_settings", return_value=SimpleNamespace(video_analysis_worker_enabled=False)), \
             patch.object(video_analysis_queue, "SessionLocal") as sessions:
            video_analysis_queue.start_video_analysis_worker()
        sessions.assert_not_called()


if __name__ == "__main__":
    unittest.main()
