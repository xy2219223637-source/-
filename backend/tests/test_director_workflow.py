import unittest
from datetime import timedelta
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import AuthContext, current_auth
from app.database import Base, get_db, migrate_legacy_schema
from app.main import app
from app.api import director as director_api
from app.api.director import _fallback_line, _shot_out
from app.models import DirectorCaptureSession, DirectorProject, DirectorSection, ShootingTask
from app.services.director_workflow import (
    _analysis_error_message,
    _combine_shot_group,
    _fallback_sections,
    _fallback_shot_analysis,
    _confidence,
    _group_similar_shots,
    _keyframe_ratios,
    _normalize_groups,
    _normalize_sections,
    _normalize_shot_ranges,
    _normalize_video_insights,
    _paragraph_text,
    _primary_product_name,
    _probe_media,
    _public_video_url,
    _rate,
    _render_ai_material_file,
    _short_tags,
    _transcripts_for_ranges,
)
from app.services.llm import (
    _normalize_volcengine_asr_response,
    resolve_director_asr_provider,
)
from app.services.video_capture import (
    capture_filename,
    capture_platform,
    capture_token_hash,
    capture_token_matches,
)


class DirectorWorkflowTests(unittest.TestCase):
    def test_ark_auto_provider_routes_asr(self):
        class StubSettings:
            director_asr_provider = "auto"
            director_asr_api_base = ""
            llm_api_base = "https://ark.cn-beijing.volces.com/api/v3"

        settings = StubSettings()
        self.assertEqual(resolve_director_asr_provider(settings), "volcengine_asr")

    def test_volcengine_asr_utterances_keep_millisecond_timestamps(self):
        payload = {
            "audio_info": {"duration": 3200},
            "result": {
                "text": "第一句第二句",
                "utterances": [
                    {"start_time": 120, "end_time": 1400, "text": "第一句"},
                    {"start_time": 1600, "end_time": 3100, "text": "第二句"},
                ],
            },
        }
        self.assertEqual(
            _normalize_volcengine_asr_response(payload),
            [
                {"start_ms": 120, "end_ms": 1400, "text": "第一句"},
                {"start_ms": 1600, "end_ms": 3100, "text": "第二句"},
            ],
        )

    def test_volcengine_asr_preserves_word_timestamps(self):
        payload = {
            "result": {
                "utterances": [
                    {
                        "start_time": 0,
                        "end_time": 1800,
                        "text": "第一句",
                        "words": [
                            {"start_time": 0, "end_time": 600, "text": "第一"},
                            {"start_time": 700, "end_time": 1800, "text": "句"},
                        ],
                    }
                ]
            }
        }
        self.assertEqual(
            _normalize_volcengine_asr_response(payload)[0]["words"],
            [
                {"start_ms": 0, "end_ms": 600, "text": "第一"},
                {"start_ms": 700, "end_ms": 1800, "text": "句"},
            ],
        )

    def test_fallback_sections_cover_the_full_timeline(self):
        sections = _fallback_sections(42)
        self.assertEqual(sections[0]["start_ms"], 0)
        self.assertEqual(sections[-1]["end_ms"], 42_000)
        self.assertTrue(all(left["end_ms"] == right["start_ms"] for left, right in zip(sections, sections[1:])))

    def test_invalid_ai_sections_fall_back_to_editable_structure(self):
        sections = _normalize_sections([{"title": "only one"}], 30)
        self.assertGreaterEqual(len(sections), 4)
        self.assertTrue(all(section["title"] for section in sections))

    def test_fallback_copy_uses_product_context(self):
        text = _fallback_line("核心卖点介绍", "测试气垫", "轻薄服帖")
        self.assertIn("测试气垫", text)
        self.assertIn("轻薄服帖", text)

    def test_reference_url_rejects_private_networks(self):
        with self.assertRaises(ValueError):
            _public_video_url("http://127.0.0.1/reference.mp4")

    def test_scene_boundaries_cover_timeline_without_fixed_time_splitting(self):
        ranges = _normalize_shot_ranges([0.2, 1.0, 10.0], 12.0, min_seconds=0.65)
        self.assertEqual(ranges, [(0.0, 1.0), (1.0, 10.0), (10.0, 12.0)])

    def test_asr_segments_are_assigned_to_one_best_matching_shot(self):
        segments = [
            {"start_ms": 0, "end_ms": 1100, "text": "第一句"},
            {"start_ms": 900, "end_ms": 2100, "text": "第二句"},
            {"start_ms": 2500, "end_ms": 3000, "text": "第三句"},
        ]
        self.assertEqual(
            _transcripts_for_ranges(segments, [(0, 800), (800, 2200), (2200, 3200)]),
            ["第一句", "第二句", "第三句"],
        )

    def test_asr_words_can_split_an_utterance_across_real_shots(self):
        segments = [
            {
                "start_ms": 0,
                "end_ms": 2000,
                "text": "第一句第二句",
                "words": [
                    {"start_ms": 0, "end_ms": 800, "text": "第一句"},
                    {"start_ms": 1100, "end_ms": 1900, "text": "第二句"},
                ],
            }
        ]
        self.assertEqual(
            _transcripts_for_ranges(segments, [(0, 1000), (1000, 2000)]),
            ["第一句", "第二句"],
        )

    def test_semantically_similar_adjacent_shots_are_grouped(self):
        shared = {"story_role": "demo", "subject": "女性面部", "scene": "室内梳妆台", "product": "粉底液"}
        shots = [
            {**shared, "start_ms": 0, "end_ms": 1200, "action": "手持粉底液涂抹面部"},
            {**shared, "start_ms": 1200, "end_ms": 2500, "action": "手持粉底液继续涂抹面部"},
            {**shared, "story_role": "cta", "start_ms": 2500, "end_ms": 3200, "action": "展示商品"},
        ]
        self.assertEqual([len(group) for group in _group_similar_shots(shots)], [2, 1])

    def test_ai_groups_are_normalized_to_cover_every_shot(self):
        shots = [{"index": index} for index in range(5)]
        groups = _normalize_groups([{"title": "异常", "start_shot": "bad"}, {"title": "开场", "start_shot": 0, "end_shot": 1}], shots)
        covered = [index for group in groups for index in range(group["start_shot"], group["end_shot"] + 1)]
        self.assertEqual(covered, list(range(5)))

    def test_invalid_model_confidence_degrades_to_zero(self):
        self.assertEqual(_confidence("not-a-number"), 0.0)
        self.assertEqual(_confidence(2), 1.0)

    def test_media_rate_parses_ffprobe_fraction(self):
        self.assertAlmostEqual(_rate("30000/1001"), 29.97, places=2)
        self.assertEqual(_rate("25"), 25)

    def test_keyframe_count_adapts_between_three_and_five(self):
        self.assertEqual(len(_keyframe_ratios(2)), 3)
        self.assertEqual(len(_keyframe_ratios(4)), 4)
        self.assertEqual(len(_keyframe_ratios(7)), 5)
        self.assertTrue(all(0 < ratio < 1 for ratio in _keyframe_ratios(7)))

    def test_video_insights_have_stable_shape(self):
        shots = [
            {"index": 0, "start_ms": 0, "end_ms": 1000, "story_role": "hook", "transcript": "开场"},
            {"index": 1, "start_ms": 1000, "end_ms": 3000, "story_role": "cta", "transcript": "立即了解"},
        ]
        result = _normalize_video_insights({"hook": {"content": "问题钩子", "shot_indexes": [0]}}, shots)
        self.assertEqual(result["hook"]["content"], "问题钩子")
        self.assertEqual(result["cta"]["shot_indexes"], [1])
        self.assertEqual(result["rhythm"]["average_shot_seconds"], 1.5)

    def test_primary_product_name_uses_the_most_frequent_observed_name(self):
        shots = [
            {"product": "轻透素颜霜"},
            {"product": "轻透素颜霜"},
            {"product": "保湿面霜"},
        ]
        self.assertEqual(_primary_product_name("保湿面霜、轻透素颜霜", shots), "轻透素颜霜")

    def test_summary_tags_are_short_unique_and_capped(self):
        values = [" 轻薄服帖 ", "自然提亮", "轻薄服帖", "持妆", "便捷上妆", "温和", "额外标签"]
        self.assertEqual(_short_tags(values, 5), ["轻薄服帖", "自然提亮", "持妆", "便捷上妆", "温和"])
        self.assertTrue(all(len(item) <= 24 for item in _short_tags(["很长" * 20], 5)))

    def test_detailed_copy_converts_arrays_to_a_natural_paragraph(self):
        text = _paragraph_text(["适合通勤人群", "希望快速完成底妆"])
        self.assertEqual(text, "适合通勤人群；希望快速完成底妆")
        self.assertNotIn("[", text)
        self.assertNotIn("'", text)

    def test_shot_detail_fields_survive_fallback_merge_and_serialization(self):
        fallback = _fallback_shot_analysis({"index": 0})
        self.assertIn("props", fallback)
        self.assertIn("emotion", fallback)
        merged = _combine_shot_group(
            [
                {
                    "start_ms": 0,
                    "end_ms": 1000,
                    "props": "化妆刷",
                    "emotion": "自然放松",
                    "confidence": 0.8,
                }
            ],
            0,
        )
        self.assertEqual(merged["props"], "化妆刷")
        self.assertEqual(merged["emotion"], "自然放松")
        row = SimpleNamespace(
            id=1,
            section_id=2,
            sort_order=0,
            start_ms=0,
            end_ms=1000,
            transcript="台词",
            story_role="demo",
            subject="人物",
            action="展示",
            scene="室内",
            shot_size="近景",
            camera="平视固定镜头",
            description="人物展示产品",
            clip_url="",
            thumbnail_url="",
            confidence=0.8,
            analysis={"product": "素颜霜", "props": "化妆刷", "emotion": "自然放松", "director_intent": "展示用法"},
        )
        output = _shot_out(row)
        self.assertEqual(output["props"], "化妆刷")
        self.assertEqual(output["emotion"], "自然放松")

    def test_ai_material_is_rendered_as_silent_video(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.jpg"
            output = Path(directory) / "output.mp4"
            Image.new("RGB", (320, 480), "white").save(source)
            self.assertTrue(_render_ai_material_file(source, output, 0.5))
            self.assertGreater(output.stat().st_size, 0)
            media = _probe_media(output)
            self.assertGreater(media["duration_seconds"], 0)
            self.assertEqual((media["width"], media["height"]), (720, 1280))
            self.assertGreater(media["fps"], 0)

    def test_director_routes_are_registered(self):
        paths = {route.path for route in app.routes}
        self.assertIn("/api/director-projects", paths)
        self.assertIn("/api/director-projects/capture-sessions", paths)
        self.assertIn("/api/director-projects/capture-sessions/{capture_id}/upload", paths)
        self.assertIn("/api/director-projects/{project_id}/analysis", paths)
        self.assertIn("/api/director-projects/{project_id}/mix", paths)
        self.assertIn("/api/director-projects/{project_id}/shots", paths)
        self.assertIn("/api/director-projects/shooting-directory", paths)
        self.assertIn("/api/director-projects/shooting-tasks", paths)
        self.assertIn("/api/director-projects/{project_id}/shooting-tasks", paths)

    def test_director_tables_can_be_created(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        tables = set(inspect(engine).get_table_names())
        self.assertTrue({"director_projects", "director_capture_sessions", "director_sections", "director_shots", "director_assets", "shooting_tasks"}.issubset(tables))

    def test_browser_capture_platforms_and_share_urls_are_normalized(self):
        self.assertEqual(capture_platform("https://www.douyin.com/jingxuan?modal_id=123"), "douyin")
        self.assertEqual(capture_platform("https://www.xiaohongshu.com/explore/123"), "xiaohongshu")
        self.assertEqual(capture_platform("https://v.kuaishou.com/abc"), "kuaishou")
        self.assertEqual(capture_platform("https://example.com/watch/123"), "generic")
        self.assertEqual(capture_filename("https://cdn.example.com/video?id=1", "video/webm", "generic"), "video.webm")

    def test_browser_capture_token_is_one_way_and_exact(self):
        digest = capture_token_hash("capture-token")
        self.assertNotEqual(digest, "capture-token")
        self.assertTrue(capture_token_matches("capture-token", digest))
        self.assertFalse(capture_token_matches("other-token", digest))

    def test_legacy_director_ocr_column_is_removed_idempotently(self):
        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE director_shots (id INTEGER PRIMARY KEY, onscreen_text TEXT NOT NULL)"))
        migrate_legacy_schema(engine)
        migrate_legacy_schema(engine)
        columns = {column["name"] for column in inspect(engine).get_columns("director_shots")}
        self.assertNotIn("onscreen_text", columns)

    def test_database_errors_are_not_exposed_to_users(self):
        message = _analysis_error_message(SQLAlchemyError("sensitive database statement"))
        self.assertEqual(message, "解析结果保存失败, 请重试. 如仍失败, 请联系管理员查看后台日志.")
        self.assertNotIn("sensitive", message)


class DirectorCaptureApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(cls.engine)
        cls.session_factory = sessionmaker(bind=cls.engine)
        test_app = FastAPI()
        test_app.include_router(director_api.router, prefix="/api")

        def override_db():
            db = cls.session_factory()
            try:
                yield db
            finally:
                db.close()

        test_app.dependency_overrides[get_db] = override_db
        test_app.dependency_overrides[current_auth] = lambda: AuthContext(
            user_id="test-user",
            tenant_id="test-tenant",
            role="owner",
            email="test@example.com",
            tenant_name="Test Tenant",
        )
        cls.client = TestClient(test_app)

    def setUp(self):
        with self.session_factory() as db:
            db.query(ShootingTask).delete()
            db.query(DirectorCaptureSession).delete()
            db.query(DirectorProject).delete()
            db.commit()

    def test_script_can_be_assigned_to_simulated_feishu_member_and_deleted(self):
        with self.session_factory() as db:
            project = DirectorProject(
                tenant_id="test-tenant",
                name="测试拍摄脚本",
                mode="original",
                source_kind="prompt",
                status="ready",
            )
            db.add(project)
            db.flush()
            db.add(
                DirectorSection(
                    tenant_id="test-tenant",
                    project_id=project.id,
                    sort_order=0,
                    title="开场",
                    script_lines=[{"id": "line-1", "text": "测试台词"}],
                )
            )
            db.commit()
            project_id = project.id

        directory_response = self.client.get("/api/director-projects/shooting-directory")
        self.assertEqual(directory_response.status_code, 200)
        self.assertEqual(directory_response.json()[0]["departments"][0]["members"][0]["name"], "张三")

        create_response = self.client.post(
            f"/api/director-projects/{project_id}/shooting-tasks",
            json={
                "channel_id": "feishu",
                "department_id": "shooting-department-1",
                "assignee_id": "zhang-san",
                "note": "按脚本完成拍摄",
            },
        )
        self.assertEqual(create_response.status_code, 200)
        created = create_response.json()
        self.assertEqual(created["project_name"], "测试拍摄脚本")
        self.assertEqual(created["channel_name"], "飞书")
        self.assertEqual(created["department_name"], "拍摄部1")
        self.assertEqual(created["assignee_name"], "张三")
        self.assertEqual(self.client.get("/api/director-projects/shooting-tasks").json()[0]["id"], created["id"])

        duplicate_response = self.client.post(
            f"/api/director-projects/{project_id}/shooting-tasks",
            json={
                "channel_id": "feishu",
                "department_id": "shooting-department-1",
                "assignee_id": "zhang-san",
            },
        )
        self.assertEqual(duplicate_response.status_code, 409)

        delete_response = self.client.delete(f"/api/director-projects/{project_id}")
        self.assertEqual(delete_response.status_code, 200)
        self.assertEqual(self.client.get("/api/director-projects/shooting-tasks").json(), [])

    def test_capture_session_uploads_once_and_queues_existing_workflow(self):
        create_response = self.client.post(
            "/api/director-projects/capture-sessions",
            json={"url": "https://www.douyin.com/jingxuan?modal_id=123"},
        )
        self.assertEqual(create_response.status_code, 200)
        created = create_response.json()
        capture = created["capture"]
        self.assertEqual(created["project"]["status"], "capturing")
        self.assertEqual(capture["source_platform"], "douyin")

        class FakeStorage:
            uploaded = b""

            async def upload_path(self, path, filename, subdir):
                self.uploaded = path.read_bytes()
                self.filename = filename
                self.subdir = subdir
                return "/api/storage/objects/captured"

        storage = FakeStorage()
        headers = {
            "X-Capture-Token": capture["token"],
            "X-Video-Filename": "sample.mp4",
            "Content-Type": "video/mp4",
        }
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.jpg"
            video = Path(directory) / "video.mp4"
            Image.new("RGB", (64, 64), "green").save(source)
            self.assertTrue(_render_ai_material_file(source, video, 0.3))
            video_bytes = video.read_bytes()
            with patch("app.api.director.get_storage", return_value=storage), patch(
                "app.api.director.analyze_reference_project", return_value=None
            ):
                upload_response = self.client.post(
                    f"/api/director-projects/capture-sessions/{capture['id']}/upload",
                    headers=headers,
                    content=video_bytes,
                )
        self.assertEqual(upload_response.status_code, 200)
        self.assertEqual(upload_response.json()["status"], "queued")
        self.assertEqual(storage.uploaded, video_bytes)
        self.assertEqual(storage.filename, "sample.mp4")
        self.assertEqual(storage.subdir, "director/sources")

        duplicate_response = self.client.post(
            f"/api/director-projects/capture-sessions/{capture['id']}/upload",
            headers=headers,
            content=b"duplicate",
        )
        self.assertEqual(duplicate_response.status_code, 409)

    def test_capture_session_rejects_an_invalid_token(self):
        created = self.client.post(
            "/api/director-projects/capture-sessions",
            json={"url": "https://www.xiaohongshu.com/explore/123"},
        ).json()
        response = self.client.patch(
            f"/api/director-projects/capture-sessions/{created['capture']['id']}",
            headers={"X-Capture-Token": "wrong-token"},
            json={"stage": "opening_source"},
        )
        self.assertEqual(response.status_code, 401)

    def test_invalid_media_never_reaches_storage_or_analysis(self):
        created = self.client.post("/api/director-projects/capture-sessions", json={"url": "https://www.douyin.com/video/7666807663723875611"}).json()
        capture = created["capture"]
        with patch("app.api.director.get_storage") as storage, patch("app.api.director.analyze_reference_project") as analyze:
            response = self.client.post(
                f"/api/director-projects/capture-sessions/{capture['id']}/upload",
                headers={"X-Capture-Token": capture["token"], "Content-Type": "video/mp4"},
                content=b"<html>verification required</html>",
            )
        self.assertEqual(response.status_code, 415)
        storage.assert_not_called()
        analyze.assert_not_called()
        project = self.client.get(f"/api/director-projects/{created['project']['id']}").json()
        self.assertEqual(project["status"], "failed")
        self.assertEqual(project["source_url"], "")

    def test_retry_uses_same_project_and_revokes_previous_session(self):
        url = "https://www.douyin.com/video/7666807663723875611"
        created = self.client.post("/api/director-projects/capture-sessions", json={"url": url}).json()
        capture = created["capture"]
        project_id = created["project"]["id"]
        headers = {"X-Capture-Token": capture["token"]}
        failed = self.client.post(f"/api/director-projects/capture-sessions/{capture['id']}/fail", headers=headers,
                                  json={"message": "采集失败", "diagnostics": {"version": "0.5.0", "last_code": "not_video"}})
        self.assertEqual(failed.status_code, 200)
        with patch("app.api.director.analyze_reference_project") as analyze:
            self.assertEqual(self.client.post(f"/api/director-projects/{project_id}/reanalyze").status_code, 409)
            analyze.assert_not_called()
        retry = self.client.post("/api/director-projects/capture-sessions", json={"url": url, "project_id": project_id})
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.json()["project"]["id"], project_id)
        self.assertNotEqual(retry.json()["capture"]["token"], capture["token"])
        self.assertEqual(self.client.patch(f"/api/director-projects/capture-sessions/{capture['id']}", headers=headers, json={"stage": "downloading"}).status_code, 409)
        with self.session_factory() as db:
            previous = db.get(DirectorCaptureSession, capture["id"])
            self.assertEqual(previous.details["diagnostics"]["last_code"], "not_video")

    def test_active_capture_cannot_be_replaced_and_expiry_allows_retry(self):
        url = "https://www.douyin.com/video/7666807663723875611"
        created = self.client.post("/api/director-projects/capture-sessions", json={"url": url}).json()
        payload = {"url": url, "project_id": created["project"]["id"]}
        self.assertEqual(self.client.post("/api/director-projects/capture-sessions", json=payload).status_code, 409)
        with self.session_factory() as db:
            session = db.get(DirectorCaptureSession, created["capture"]["id"])
            session.expires_at = director_api._utcnow() - timedelta(seconds=1)
            db.commit()
        project = self.client.get(f"/api/director-projects/{payload['project_id']}").json()
        self.assertEqual(project["status"], "failed")
        self.assertEqual(self.client.post("/api/director-projects/capture-sessions", json=payload).status_code, 200)

    def test_retry_cannot_access_another_tenants_project(self):
        url = "https://www.douyin.com/video/7666807663723875611"
        created = self.client.post("/api/director-projects/capture-sessions", json={"url": url}).json()
        project_id = created["project"]["id"]
        with self.session_factory() as db:
            db.get(DirectorProject, project_id).tenant_id = "other-tenant"
            db.commit()
        self.assertEqual(self.client.post("/api/director-projects/capture-sessions", json={"url": url, "project_id": project_id}).status_code, 404)

    def test_uploading_session_rejects_late_progress_and_failure_reports(self):
        created = self.client.post("/api/director-projects/capture-sessions", json={"url": "https://www.douyin.com/video/7666807663723875611"}).json()
        capture = created["capture"]
        with self.session_factory() as db:
            db.get(DirectorCaptureSession, capture["id"]).status = "uploading"
            db.get(DirectorProject, created["project"]["id"]).progress_stage = "validating_media"
            db.commit()
        path = f"/api/director-projects/capture-sessions/{capture['id']}"
        headers = {"X-Capture-Token": capture["token"]}
        self.assertEqual(self.client.patch(path, headers=headers, json={"stage": "downloading"}).status_code, 409)
        self.assertEqual(self.client.post(path + "/fail", headers=headers, json={"message": "late failure"}).status_code, 409)
        self.assertEqual(self.client.get(f"/api/director-projects/{created['project']['id']}").json()["progress_stage"], "validating_media")

    def test_superseded_upload_cannot_overwrite_retry_or_start_analysis(self):
        created = self.client.post("/api/director-projects/capture-sessions", json={"url": "https://www.douyin.com/video/7666807663723875611"}).json()
        capture = created["capture"]
        session_factory = self.session_factory

        class DelayedStorage:
            async def upload_path(self, path, filename, subdir):
                with session_factory() as db:
                    db.get(DirectorCaptureSession, capture["id"]).status = "superseded"
                    db.get(DirectorProject, created["project"]["id"]).progress_stage = "waiting_extension"
                    db.commit()
                return "/api/storage/objects/obsolete-upload"

        with patch("app.api.director._probe_media", return_value={"width": 64, "height": 64, "duration_seconds": 1}), patch(
            "app.api.director.get_storage", return_value=DelayedStorage()
        ), patch("app.api.director.analyze_reference_project") as analyze:
            response = self.client.post(
                f"/api/director-projects/capture-sessions/{capture['id']}/upload",
                headers={"X-Capture-Token": capture["token"], "Content-Type": "video/mp4"}, content=b"media fixture",
            )
        self.assertEqual(response.status_code, 409)
        analyze.assert_not_called()
        project = self.client.get(f"/api/director-projects/{created['project']['id']}").json()
        self.assertEqual(project["source_url"], "")
        self.assertEqual(project["progress_stage"], "waiting_extension")


if __name__ == "__main__":
    unittest.main()
