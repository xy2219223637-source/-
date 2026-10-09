from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.video_library import _normalize_analysis, analyze_video_asset
from app.database import Base
from app.models import Product, VideoAsset, VideoAssetAnalysis, VideoHighlight
from app.services.video_repair.candidates import find_candidates


class MaterialLibraryAnalysisTests(unittest.TestCase):
    def test_uploaded_video_analysis_persists_repair_segments(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine, tables=[Product.__table__, VideoAsset.__table__,
                                                 VideoAssetAnalysis.__table__, VideoHighlight.__table__])
        sessions = sessionmaker(bind=engine)
        with sessions() as db:
            db.add(Product(id=1, tenant_id="tenant", name="素材库商品", description="面霜"))
            db.add(VideoAsset(id=1, tenant_id="tenant", product_id=1, asset_id="clip",
                              filename="clip.mp4", source="material_library_asset:1",
                              library_type="repair_only", asset_kind="material", object_url="/clip.mp4",
                              content_hash="hash", duration_seconds=8, width=1080, height=1920,
                              status="queued_for_analysis"))
            db.commit()

        class Storage:
            def local_path(self, _url):
                return None

            def signed_url(self, url):
                return url

        class Model:
            async def chat_json(self, messages, **_kwargs):
                self.__class__.prompt = messages[0]["content"][0]["text"]
                return {"summary": "展示面霜", "highlights": [{
                    "start": 1, "end": 5, "description": "打开面霜瓶盖",
                    "product_visibility": "clear", "audio_mode": "mute_ok",
                    "on_screen_text": [], "content_role": "product_demo", "score": .8,
                }]}

        with patch("app.api.video_library.SessionLocal", sessions), \
             patch("app.api.video_library._asset_storage", return_value=Storage()), \
             patch("app.api.video_library.LLMClient", Model), \
             patch("app.api.video_library.get_settings", return_value=SimpleNamespace(
                 llm_vision_model="test-model", whisper_enabled=False)):
            asyncio.run(analyze_video_asset(1))
        self.assertIn("product_visibility", Model.prompt)
        with sessions() as db:
            video = db.get(VideoAsset, 1)
            analysis = db.query(VideoAssetAnalysis).filter_by(asset_id=1, status="completed").one()
            segment = db.query(VideoHighlight).filter_by(asset_id=1).one()
            self.assertEqual(video.status, "analyzed")
            self.assertEqual(analysis.analysis_json["highlights"][0]["product_visibility"], "clear")
            self.assertEqual(segment.start_seconds, 1)
        engine.dispose()

    def test_repair_segments_are_searchable_and_silent_insert_needs_separate_audio(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine, tables=[Product.__table__, VideoAsset.__table__,
                                                 VideoAssetAnalysis.__table__, VideoHighlight.__table__])
        sessions = sessionmaker(bind=engine)
        with sessions() as db:
            db.add(Product(id=1, tenant_id="tenant", name="商品"))
            source = VideoAsset(tenant_id="tenant", product_id=1, asset_id="source", filename="source.mp4",
                                source="video_repair_upload", library_type="finished", asset_kind="final_output",
                                object_url="/source.mp4", content_hash="a", duration_seconds=12,
                                width=1080, height=1920, status="analyzed")
            material = VideoAsset(tenant_id="tenant", product_id=1, asset_id="material", filename="material.mp4",
                                  source="material_library_asset:1", library_type="repair_only", asset_kind="material",
                                  object_url="/material.mp4", content_hash="b", duration_seconds=10,
                                  width=1080, height=1920, status="analyzed", has_audio=False)
            db.add_all([source, material]); db.flush()
            normalized = _normalize_analysis(material, {"summary": "涂抹商品", "highlights": [{
                "start": 2, "end": 7, "description": "手部涂抹商品", "content_role": "usage",
                "product_visibility": "clear", "audio_mode": "mute_ok", "on_screen_text": ["品牌名"],
                "score": .9,
            }]})
            analysis = VideoAssetAnalysis(tenant_id="tenant", asset_id=material.id,
                                          status="completed", analysis_json=normalized, summary="涂抹商品")
            db.add(analysis); db.flush()
            segment = normalized["highlights"][0]
            db.add(VideoHighlight(tenant_id="tenant", asset_id=material.id, analysis_id=analysis.id,
                                  highlight_id=segment["highlight_id"], start_seconds=segment["start"],
                                  end_seconds=segment["end"], description=segment["description"],
                                  content_role="usage", score=.9))
            db.commit()
            candidates = find_candidates(db, tenant_id="tenant", source_asset=source,
                                         action="PROCESS_INSERT", target_duration=3,
                                         library_only=True, allow_silent_insert=True)
            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0]["source_range"], {"start": 2.0, "end": 7.0})
            self.assertEqual(candidates[0]["product_visibility"], "clear")
            self.assertEqual(candidates[0]["audio_mode"], "mute_ok")
            self.assertEqual(candidates[0]["on_screen_text"], ["品牌名"])
            self.assertEqual(find_candidates(db, tenant_id="other", source_asset=source,
                                             action="PROCESS_INSERT", target_duration=3,
                                             library_only=True, allow_silent_insert=True), [])
        engine.dispose()


if __name__ == "__main__":
    unittest.main()
