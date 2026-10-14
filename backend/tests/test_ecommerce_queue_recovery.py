from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import ecommerce_video_library as library
from app.database import Base
from app.models import EcommerceProduct, EcommerceVideoAsset, EcommerceVideoAssetAnalysis, EcommerceVideoHighlight
from app.services import video_analysis_queue as queue


class EcommerceQueueRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        event.listen(self.engine, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"))
        Base.metadata.create_all(self.engine, tables=[
            table for name, table in Base.metadata.tables.items() if name.startswith("ecommerce_")
        ])
        self.addCleanup(self.engine.dispose)
        self.sequence = 0
        self.settings = SimpleNamespace(llm_vision_model="vision-test", director_asr_required=False)
        self.result = {
            "summary": "Completed result", "video_tags": ["demo"],
            "highlights": [{"start": 0, "end": 4, "track_role": "b_roll", "content_role": "product_demo"}],
        }
        self.client = Mock()
        self.client.chat_json = AsyncMock(return_value=self.result)
        self.storage = Mock()
        self.storage.signed_url.return_value = "https://video.example/source.mp4"
        with Session(self.engine) as db:
            db.add(EcommerceProduct(id=1, tenant_id="tenant-a", name="Product"))
            db.commit()
        for item in (
            patch.object(queue, "SessionLocal", side_effect=lambda: Session(self.engine)),
            patch.object(library, "SessionLocal", side_effect=lambda: Session(self.engine)),
            patch.object(library, "get_settings", return_value=self.settings),
            patch.object(library, "LLMClient", return_value=self.client),
            patch.object(library, "_asset_storage", return_value=self.storage),
        ):
            item.start()
            self.addCleanup(item.stop)

    def asset(self, **overrides):
        self.sequence += 1
        values = dict(
            tenant_id="tenant-a", product_id=1, asset_id=f"asset-{self.sequence}", filename="source.mp4",
            object_url="/api/storage/objects/test", content_hash=f"hash-{self.sequence}", library_type="finished",
            asset_kind="material", status="queued_for_analysis", duration_seconds=8.5, width=540, height=960,
            has_audio=False, excluded=False,
        )
        values.update(overrides)
        with Session(self.engine) as db:
            row = EcommerceVideoAsset(**values)
            db.add(row)
            db.commit()
            return row.id

    def analysis(self, asset_id, status="analyzing"):
        with Session(self.engine) as db:
            row = EcommerceVideoAssetAnalysis(
                tenant_id="tenant-a", asset_id=asset_id, status=status,
                model="vision-test", prompt_version="test", summary="",
            )
            db.add(row)
            db.commit()
            return row.id

    def test_live_lease_protects_another_workers_analysis(self):
        asset_id = self.asset(status="analyzing", updated_at=datetime.utcnow() - timedelta(hours=1))
        analysis_id = self.analysis(asset_id)
        lock = Mock()
        lock.acquire.return_value = False
        with Session(self.engine) as db, patch.object(queue, "_ecommerce_lock", return_value=lock) as factory:
            self.assertEqual(queue.recover_interrupted_ecommerce_video_analyses(db), 0)
            self.assertEqual(db.get(EcommerceVideoAsset, asset_id).status, "analyzing")
            self.assertEqual(db.get(EcommerceVideoAssetAnalysis, analysis_id).status, "analyzing")
        factory.assert_called_once_with(f"ecommerce-video-analysis-{asset_id}")
        lock.acquire.assert_called_once_with(blocking=False)
        lock.release.assert_not_called()

    def test_orphan_is_recovered_once_without_changing_completed_history(self):
        asset_id = self.asset(status="analyzing")
        active_id = self.analysis(asset_id)
        completed_id = self.analysis(asset_id, "completed")
        lock = Mock()
        lock.acquire.return_value = True
        with Session(self.engine) as db, patch.object(queue, "_ecommerce_lock", return_value=lock):
            self.assertEqual(queue.recover_interrupted_ecommerce_video_analyses(db), 1)
            self.assertEqual(queue.recover_interrupted_ecommerce_video_analyses(db), 0)
            self.assertEqual(db.get(EcommerceVideoAsset, asset_id).status, "queued_for_analysis")
            interrupted = db.get(EcommerceVideoAssetAnalysis, active_id)
            self.assertEqual(interrupted.status, "interrupted")
            self.assertIsNotNone(interrupted.completed_at)
            self.assertTrue(interrupted.error_message)
            self.assertEqual(db.get(EcommerceVideoAssetAnalysis, completed_id).status, "completed")
        lock.acquire.assert_called_once_with(blocking=False)
        lock.release.assert_called_once()

    def test_without_redis_fresh_heartbeat_survives_and_stale_job_recovers(self):
        fresh_id = self.asset(status="analyzing", updated_at=datetime.utcnow())
        stale_id = self.asset(status="analyzing", updated_at=datetime.utcnow() - timedelta(seconds=121))
        fresh_analysis = self.analysis(fresh_id)
        stale_analysis = self.analysis(stale_id)
        with Session(self.engine) as db, patch.object(queue, "_ecommerce_lock", return_value=None):
            self.assertEqual(queue.recover_interrupted_ecommerce_video_analyses(db), 1)
            self.assertEqual(db.get(EcommerceVideoAsset, fresh_id).status, "analyzing")
            self.assertEqual(db.get(EcommerceVideoAssetAnalysis, fresh_analysis).status, "analyzing")
            self.assertEqual(db.get(EcommerceVideoAsset, stale_id).status, "queued_for_analysis")
            self.assertEqual(db.get(EcommerceVideoAssetAnalysis, stale_analysis).status, "interrupted")

    def test_locked_analysis_head_does_not_starve_later_queued_asset(self):
        self._assert_locked_head_is_skipped("queued_for_analysis", queue._next_ecommerce_asset_id, "analysis")

    def test_legacy_queued_final_is_never_selected_for_material_analysis(self):
        self.asset(asset_kind="final_output", created_at=datetime.utcnow() - timedelta(hours=1))
        material_id = self.asset()
        with patch.object(queue, "_ecommerce_lock", return_value=None) as factory:
            self.assertEqual(queue._next_ecommerce_asset_id(), material_id)
        factory.assert_called_once_with(f"ecommerce-video-analysis-{material_id}")

    def test_final_is_not_requeued_by_material_analysis_recovery(self):
        asset_id = self.asset(asset_kind="final_output", status="analyzing",
                              updated_at=datetime.utcnow() - timedelta(hours=1))
        analysis_id = self.analysis(asset_id)
        with Session(self.engine) as db, patch.object(queue, "_ecommerce_lock") as factory:
            self.assertEqual(queue.recover_interrupted_ecommerce_video_analyses(db), 0)
            self.assertEqual(db.get(EcommerceVideoAsset, asset_id).status, "analyzing")
            self.assertEqual(db.get(EcommerceVideoAssetAnalysis, analysis_id).status, "analyzing")
        factory.assert_not_called()

    def test_final_multipart_file_still_enters_media_validation_queue(self):
        final_id = self.asset(asset_kind="final_output", status="validating")
        with patch.object(queue, "_ecommerce_lock", return_value=None):
            self.assertEqual(queue._next_ecommerce_validation_id(), final_id)
            self.assertIsNone(queue._next_ecommerce_asset_id())

    def test_locked_validation_head_does_not_starve_later_validation(self):
        self._assert_locked_head_is_skipped("validating", queue._next_ecommerce_validation_id, "validation")

    def _assert_locked_head_is_skipped(self, status, select_next, task):
        first = self.asset(status=status, created_at=datetime.utcnow() - timedelta(hours=1))
        second = self.asset(status=status)
        held = Mock()
        held.locked.return_value = True
        free = Mock()
        free.locked.return_value = False
        locks = {f"ecommerce-video-{task}-{first}": held, f"ecommerce-video-{task}-{second}": free}
        with patch.object(queue, "_ecommerce_lock", side_effect=locks.__getitem__):
            self.assertEqual(select_next(), second)
        held.locked.assert_called_once()
        free.locked.assert_called_once()
        held.acquire.assert_not_called()
        free.acquire.assert_not_called()

    def test_ecommerce_lease_uses_short_timeout_and_cross_thread_token(self):
        with patch.object(queue, "task_lock") as factory:
            self.assertIs(queue._ecommerce_lock("test-task"), factory.return_value)
        factory.assert_called_once_with("test-task", timeout=120, thread_local=False)

    def test_running_task_renews_lease_and_database_heartbeat_then_releases(self):
        asset_id = self.asset(status="analyzing")
        lock = Mock()
        lock.acquire.return_value = True
        committed = threading.Event()
        heartbeat_db = Mock()
        heartbeat_db.commit.side_effect = committed.set
        context = Mock()
        context.__enter__ = Mock(return_value=heartbeat_db)
        context.__exit__ = Mock(return_value=False)

        def callback(received_id):
            self.assertEqual(received_id, asset_id)
            self.assertTrue(committed.wait(2), "Task heartbeat never reached the database")
            lock.release.assert_not_called()

        with patch.object(queue, "_ecommerce_lock", return_value=lock) as factory, \
                patch.object(queue, "ECOMMERCE_HEARTBEAT_SECONDS", .01), \
                patch.object(queue, "SessionLocal", return_value=context):
            queue._run_ecommerce_task(asset_id, "analysis", callback)
        factory.assert_called_once_with(f"ecommerce-video-analysis-{asset_id}")
        lock.acquire.assert_called_once_with(blocking=False)
        lock.extend.assert_called_with(120, replace_ttl=True)
        heartbeat_db.query.assert_called_with(EcommerceVideoAsset)
        self.assertGreaterEqual(heartbeat_db.query.return_value.filter.return_value.update.call_count, 1)
        lock.release.assert_called_once()

    def test_callback_failure_still_releases_lease(self):
        lock = Mock()
        lock.acquire.return_value = True
        callback = Mock(side_effect=ValueError("callback failed"))
        with patch.object(queue, "_ecommerce_lock", return_value=lock):
            with self.assertRaisesRegex(ValueError, "callback failed"):
                queue._run_ecommerce_task(7, "analysis", callback)
        callback.assert_called_once_with(7)
        lock.release.assert_called_once()

    def test_heartbeat_refreshes_database_ownership_without_redis(self):
        old_time = datetime.utcnow() - timedelta(seconds=121)
        asset_id = self.asset(status="analyzing", updated_at=old_time)
        committed = threading.Event()

        def heartbeat_session():
            db = Session(self.engine)
            event.listen(db, "after_commit", lambda _: committed.set(), once=True)
            return db

        def callback(received_id):
            self.assertEqual(received_id, asset_id)
            self.assertTrue(committed.wait(2), "Database ownership heartbeat did not commit")
            with Session(self.engine) as db:
                self.assertGreater(db.get(EcommerceVideoAsset, asset_id).updated_at, old_time)

        with patch.object(queue, "_ecommerce_lock", return_value=None), \
                patch.object(queue, "ECOMMERCE_HEARTBEAT_SECONDS", .01), \
                patch.object(queue, "SessionLocal", side_effect=heartbeat_session):
            queue._run_ecommerce_task(asset_id, "analysis", callback)

    def test_contended_lease_does_not_start_callback(self):
        lock = Mock()
        lock.acquire.return_value = False
        callback = Mock()
        with patch.object(queue, "_ecommerce_lock", return_value=lock), patch.object(queue, "_stop") as stop:
            queue._run_ecommerce_task(7, "analysis", callback)
        callback.assert_not_called()
        stop.wait.assert_called_once_with(1)
        lock.release.assert_not_called()

    def test_queued_only_worker_ignores_stale_nonqueued_ids(self):
        for status in ("uploaded", "validating", "analyzing", "analysis_failed", "analyzed"):
            with self.subTest(status=status):
                asset_id = self.asset(status=status)
                asyncio.run(library.analyze_video_asset(asset_id, force=True, queued_only=True))
                with Session(self.engine) as db:
                    self.assertEqual(db.get(EcommerceVideoAsset, asset_id).status, status)
                    self.assertEqual(db.query(EcommerceVideoAssetAnalysis).filter_by(asset_id=asset_id).count(), 0)
        self.client.chat_json.assert_not_awaited()

    def test_concurrent_worker_calls_claim_only_one_analysis(self):
        asset_id = self.asset()

        async def run_two_workers():
            started = asyncio.Event()
            finish = asyncio.Event()

            async def delayed_result(*args, **kwargs):
                started.set()
                await finish.wait()
                return self.result

            self.client.chat_json.side_effect = delayed_result
            first = asyncio.create_task(library.analyze_video_asset(asset_id, force=True, queued_only=True))
            try:
                await asyncio.wait_for(started.wait(), 2)
                await asyncio.wait_for(library.analyze_video_asset(asset_id, force=True, queued_only=True), 2)
            finally:
                finish.set()
                await asyncio.wait_for(first, 2)

        asyncio.run(run_two_workers())
        self.client.chat_json.assert_awaited_once()
        with Session(self.engine) as db:
            self.assertEqual(db.get(EcommerceVideoAsset, asset_id).status, "analyzed")
            self.assertEqual(db.query(EcommerceVideoAssetAnalysis).filter_by(asset_id=asset_id).count(), 1)

    def test_nonqueued_direct_call_does_not_duplicate_active_analysis(self):
        asset_id = self.asset(status="analyzing")
        active_id = self.analysis(asset_id)
        asyncio.run(library.analyze_video_asset(asset_id, force=True))
        self.client.chat_json.assert_not_awaited()
        with Session(self.engine) as db:
            self.assertEqual(db.get(EcommerceVideoAsset, asset_id).status, "analyzing")
            self.assertEqual(db.query(EcommerceVideoAssetAnalysis).filter_by(asset_id=asset_id).one().id, active_id)

    def test_conditional_claim_rejects_state_changed_since_asset_read(self):
        asset_id = self.asset()
        intercepted = []

        def analysis_session():
            db = Session(self.engine)

            def competing_claim(execution):
                if not execution.is_update or execution.statement.table.name != "ecommerce_video_assets":
                    return
                if intercepted:
                    return
                intercepted.append(True)
                with Session(self.engine) as other:
                    other.get(EcommerceVideoAsset, asset_id).status = "analyzing"
                    other.add(EcommerceVideoAssetAnalysis(
                        tenant_id="tenant-a", asset_id=asset_id, status="analyzing",
                        model="other-worker", prompt_version="test", summary="",
                    ))
                    other.commit()

            event.listen(db, "do_orm_execute", competing_claim)
            return db

        with patch.object(library, "SessionLocal", side_effect=analysis_session):
            asyncio.run(library.analyze_video_asset(asset_id, force=True, queued_only=True))
        self.assertEqual(intercepted, [True])
        self.client.chat_json.assert_not_awaited()
        with Session(self.engine) as db:
            self.assertEqual(db.get(EcommerceVideoAsset, asset_id).status, "analyzing")
            self.assertEqual(db.query(EcommerceVideoAssetAnalysis).filter_by(asset_id=asset_id).one().model, "other-worker")

    def test_recovered_old_attempt_cannot_overwrite_replacement_success_or_failure(self):
        for fail_old_request in (False, True):
            with self.subTest(fail_old_request=fail_old_request):
                asset_id = self.asset()
                replacement = {}

                async def recover_and_replace(*args, **kwargs):
                    with Session(self.engine) as db:
                        old = db.query(EcommerceVideoAssetAnalysis).filter_by(asset_id=asset_id).one()
                        replacement["old_id"] = old.id
                        db.get(EcommerceVideoAsset, asset_id).updated_at = datetime.utcnow() - timedelta(seconds=121)
                        db.commit()
                        with patch.object(queue, "_ecommerce_lock", return_value=None):
                            self.assertEqual(queue.recover_interrupted_ecommerce_video_analyses(db), 1)
                        row = db.get(EcommerceVideoAsset, asset_id)
                        row.status = "analyzed"
                        row.ai_tags = ["replacement-tag"]
                        current = EcommerceVideoAssetAnalysis(
                            tenant_id="tenant-a", asset_id=asset_id, status="completed", summary="Replacement result",
                            model="vision-test", prompt_version="test", analysis_json={"replacement": True},
                        )
                        db.add(current)
                        db.flush()
                        db.add(EcommerceVideoHighlight(
                            tenant_id="tenant-a", asset_id=asset_id, analysis_id=current.id,
                            highlight_id=f"replacement-{asset_id}", start_seconds=1, end_seconds=3,
                            description="Replacement highlight", reason="test", recommended_roles=["product_demo"],
                        ))
                        db.commit()
                        replacement["current_id"] = current.id
                    if fail_old_request:
                        raise ValueError("Obsolete request failed")
                    return self.result

                self.client.chat_json.reset_mock()
                self.client.chat_json.side_effect = recover_and_replace
                asyncio.run(library.analyze_video_asset(asset_id, force=True, queued_only=True))
                self.client.chat_json.assert_awaited_once()
                self.client.chat_vision.assert_not_called()
                with Session(self.engine) as db:
                    row = db.get(EcommerceVideoAsset, asset_id)
                    self.assertEqual(row.status, "analyzed")
                    self.assertEqual(row.ai_tags, ["replacement-tag"])
                    old = db.get(EcommerceVideoAssetAnalysis, replacement["old_id"])
                    self.assertEqual(old.status, "interrupted")
                    current = db.get(EcommerceVideoAssetAnalysis, replacement["current_id"])
                    self.assertEqual(current.status, "completed")
                    self.assertEqual(current.summary, "Replacement result")
                    highlight = db.query(EcommerceVideoHighlight).filter_by(asset_id=asset_id).one()
                    self.assertEqual(highlight.analysis_id, current.id)
                    self.assertEqual(highlight.description, "Replacement highlight")


if __name__ == "__main__":
    unittest.main()
