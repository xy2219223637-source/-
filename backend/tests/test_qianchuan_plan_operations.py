"""Offline state-protection tests. No provider write or worker is involved."""
from __future__ import annotations

from decimal import Decimal
import unittest
import uuid

from fastapi import HTTPException

import test_qianchuan_auth as auth_fixture

from app.auth import AuthContext
from app.models import (
    QianchuanAccountBinding as Binding,
    QianchuanDeliveryPlan as Plan,
    QianchuanPlanOperation as Operation,
    QianchuanUpload as Upload,
)
from app.schemas.qianchuan_plans import QianchuanPlanActionRequest
from app.services.qianchuan_plan.mock_executor import MockExecutionResult, MockQianchuanPlanExecutor
from app.services.qianchuan_plan.operations import QianchuanPlanOperationService


class QianchuanPlanOperationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = auth_fixture.QianchuanAuthorizationTests(methodName="runTest")
        self.fixture.setUp()
        self.__dict__.update(self.fixture.__dict__)
        _, self.connection_id = self.fixture.completed()
        self.service.sync_connection(self.connection_id)
        self.settings.qianchuan_plan_enabled = True
        self.settings.qianchuan_plan_write_enabled = True
        self.auth = AuthContext(
            user_id="owner", tenant_id="team-a", role="owner",
            email="owner@example.test", tenant_name="team-a", permissions=("*",))
        self.operations = QianchuanPlanOperationService(self.service)
        self.executor = MockQianchuanPlanExecutor(self.service, allow_mock=True)
        self.plan_id = str(uuid.uuid4())
        self.upload_id = str(uuid.uuid4())
        with self.sessions() as db:
            binding = db.query(Binding).filter_by(connection_id=self.connection_id).one()
            db.add(Upload(
                id=self.upload_id, tenant_id="team-a", created_by="owner", video_asset_id=1,
                binding_id=binding.id, advertiser_id=binding.advertiser_id,
                advertiser_name=binding.advertiser_name, source_content_hash="a" * 64,
                source_url="/api/storage/objects/test", filename="test.mp4", is_aigc=False,
                status="succeeded", material_id="7688613085071491098"))
            db.add(Plan(
                id=self.plan_id, tenant_id="team-a", created_by="owner",
                connection_id=self.connection_id, binding_id=binding.id,
                advertiser_id=binding.advertiser_id, credential_version=1,
                video_asset_id=1, upload_id=self.upload_id, material_id="7688613085071491098",
                product_id="9007199254740995", product_name="商品",
                aweme_id="9007199254740994", aweme_name="抖音号", name="测试计划",
                marketing_goal="VIDEO_PROM_GOODS", budget_minor=10000,
                roi_goal=Decimal("1.2500"), request_snapshot={"schema_version": 1}))
            db.commit()

    def tearDown(self):
        self.fixture.tearDown()

    @staticmethod
    def request(version=0, key="operation_key_0001", confirmed=True):
        return QianchuanPlanActionRequest(
            operation_version=version, idempotency_key=key, confirmed=confirmed)

    def reserve_submit(self, payload=None):
        with self.sessions() as db:
            return self.operations.reserve_submit(
                db, self.auth, self.plan_id, payload or self.request())

    def test_same_key_is_idempotent_and_different_retry_cannot_duplicate(self):
        before = len(self.calls)
        first = self.reserve_submit()
        second = self.reserve_submit()
        self.assertEqual(first["id"], second["id"])
        with self.assertRaises(HTTPException) as caught:
            self.reserve_submit(self.request(key="operation_key_0002"))
        self.assertEqual(caught.exception.status_code, 409)
        with self.sessions() as db:
            plan = db.get(Plan, self.plan_id)
            self.assertEqual((plan.local_status, plan.operation_version), ("submitting", 1))
            self.assertEqual(db.query(Operation).count(), 1)
        self.assertEqual(len(self.calls), before)

    def test_cas_rejects_stale_version_and_unconfirmed_action(self):
        with self.assertRaises(HTTPException) as stale:
            self.reserve_submit(self.request(version=1))
        self.assertEqual(stale.exception.status_code, 409)
        with self.assertRaises(HTTPException) as unconfirmed:
            self.reserve_submit(self.request(confirmed=False))
        self.assertEqual(unconfirmed.exception.status_code, 409)
        with self.sessions() as db:
            self.assertEqual(db.query(Operation).count(), 0)
            self.assertEqual(db.get(Plan, self.plan_id).local_status, "draft")

    def test_owner_key_cannot_be_reused_for_another_operation(self):
        first = self.reserve_submit()
        with self.assertRaises(HTTPException) as caught:
            with self.sessions() as db:
                self.operations.reserve_uncertain_recheck(
                    db, self.auth, self.plan_id,
                    self.request(version=0, key="operation_key_0001"))
        self.assertEqual(caught.exception.status_code, 409)
        self.assertTrue(first["id"])

    def test_write_gate_and_owner_scope_fail_before_reservation(self):
        self.settings.qianchuan_plan_write_enabled = False
        with self.assertRaises(HTTPException) as disabled:
            self.reserve_submit()
        self.assertEqual(disabled.exception.status_code, 409)
        self.settings.qianchuan_plan_write_enabled = True
        member = AuthContext(
            user_id="member", tenant_id="team-a", role="member",
            email="member@example.test", tenant_name="team-a",
            permissions=("final_videos.access",))
        with self.assertRaises(HTTPException) as hidden:
            with self.sessions() as db:
                self.operations.reserve_submit(db, member, self.plan_id, self.request())
        self.assertEqual(hidden.exception.status_code, 404)
        with self.sessions() as db:
            self.assertEqual(db.query(Operation).count(), 0)
            self.assertEqual(db.get(Plan, self.plan_id).local_status, "draft")

    def test_uncertain_submit_blocks_resend_and_allows_query_only_recheck(self):
        reserved = self.reserve_submit()
        with self.sessions() as db:
            result = self.operations.record_submit_result(
                db, self.auth, reserved["id"], outcome="uncertain",
                provider_request_id="provider-request", error_code="transport_uncertain")
        self.assertEqual(result["status"], "result_uncertain")

        with self.assertRaises(HTTPException) as resend:
            self.reserve_submit(self.request(version=1, key="operation_key_0002"))
        self.assertEqual(resend.exception.status_code, 409)

        with self.sessions() as db:
            recheck = self.operations.reserve_uncertain_recheck(
                db, self.auth, self.plan_id,
                self.request(version=1, key="operation_key_0003"))
            completed = self.operations.record_recheck_result(
                db, self.auth, recheck["id"], outcome="found",
                platform_ad_id="9007199254740999", provider_request_id="query-request")
        self.assertEqual(completed["status"], "succeeded")
        with self.sessions() as db:
            plan = db.get(Plan, self.plan_id)
            self.assertEqual((plan.local_status, plan.operation_version), ("submitted", 2))
            self.assertEqual(plan.platform_ad_id, "9007199254740999")

    def test_confirmed_submit_requires_platform_id_and_terminal_result_is_stable(self):
        reserved = self.reserve_submit()
        with self.sessions() as db:
            with self.assertRaises(HTTPException) as missing_id:
                self.operations.record_submit_result(
                    db, self.auth, reserved["id"], outcome="created")
            self.assertEqual(missing_id.exception.status_code, 422)
            first = self.operations.record_submit_result(
                db, self.auth, reserved["id"], outcome="created",
                platform_ad_id="9007199254740999")
            second = self.operations.record_submit_result(
                db, self.auth, reserved["id"], outcome="created",
                platform_ad_id="9007199254740999")
            self.assertEqual(first["id"], second["id"])
            with self.assertRaises(HTTPException) as conflicting:
                self.operations.record_submit_result(
                    db, self.auth, reserved["id"], outcome="uncertain")
        self.assertEqual(conflicting.exception.status_code, 409)
        with self.sessions() as db:
            plan = db.get(Plan, self.plan_id)
            self.assertEqual(plan.local_status, "submitted")
            self.assertEqual(plan.platform_ad_id, "9007199254740999")

    def test_inconclusive_recheck_stays_uncertain_and_cannot_overwrite_terminal_result(self):
        reserved = self.reserve_submit()
        with self.sessions() as db:
            self.operations.record_submit_result(db, self.auth, reserved["id"], outcome="uncertain")
        with self.sessions() as db:
            recheck = self.operations.reserve_uncertain_recheck(
                db, self.auth, self.plan_id,
                self.request(version=1, key="operation_key_0004"))
            first = self.operations.record_recheck_result(
                db, self.auth, recheck["id"], outcome="inconclusive")
            second = self.operations.record_recheck_result(
                db, self.auth, recheck["id"], outcome="inconclusive")
            self.assertEqual(first["id"], second["id"])
            with self.assertRaises(HTTPException) as conflicting:
                self.operations.record_recheck_result(
                    db, self.auth, recheck["id"], outcome="found",
                    platform_ad_id="9007199254740999")
        self.assertEqual(conflicting.exception.status_code, 409)
        with self.sessions() as db:
            self.assertEqual(db.get(Plan, self.plan_id).local_status, "result_uncertain")

    def test_mock_executor_requires_debug_and_explicit_opt_in(self):
        with self.assertRaises(RuntimeError):
            MockQianchuanPlanExecutor(self.service)
        self.settings.debug = False
        with self.assertRaises(RuntimeError):
            MockQianchuanPlanExecutor(self.service, allow_mock=True)

    def test_mock_executor_completes_reserved_create_without_provider_call(self):
        before = len(self.calls)
        reserved = self.reserve_submit()
        scripted = MockExecutionResult(
            outcome="created", platform_ad_id="9007199254740999")
        with self.sessions() as db:
            first = self.executor.execute(db, self.auth, reserved["id"], scripted)
            second = self.executor.execute(db, self.auth, reserved["id"], scripted)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["executor"], "mock")
        self.assertEqual(first["status"], "succeeded")
        self.assertEqual(len(self.calls), before)

    def test_mock_executor_routes_uncertain_to_query_only_recheck(self):
        before = len(self.calls)
        reserved = self.reserve_submit()
        with self.sessions() as db:
            uncertain = self.executor.execute(
                db, self.auth, reserved["id"], MockExecutionResult(outcome="uncertain"))
        self.assertEqual(uncertain["status"], "result_uncertain")
        with self.sessions() as db:
            recheck = self.operations.reserve_uncertain_recheck(
                db, self.auth, self.plan_id,
                self.request(version=1, key="operation_key_0005"))
            with self.assertRaises(HTTPException) as wrong_result:
                self.executor.execute(
                    db, self.auth, recheck["id"], MockExecutionResult(outcome="created"))
            self.assertEqual(wrong_result.exception.status_code, 422)
            found = self.executor.execute(
                db, self.auth, recheck["id"],
                MockExecutionResult(outcome="found", platform_ad_id="9007199254740999"))
        self.assertEqual(found["status"], "succeeded")
        self.assertEqual(len(self.calls), before)


if __name__ == "__main__":
    unittest.main()
