"""Deployment isolation, fault recovery and actual distributed rate-limit checks."""
from concurrent.futures import ProcessPoolExecutor
import os
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch
import uuid

from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import event, select, text

import test_billing_management as management
from app.api.billing import GrantRateLimiter
from app.auth import AuthContext
from app.models.billing import CreditAccount, CreditLedger, BillingOrder
from app.services.billing.isolation import BillingIsolationMiddleware


def rate_check(arguments):
    url, actor = arguments
    import redis
    client = redis.Redis.from_url(url)
    auth = AuthContext(actor, "test-tenant", "owner", "test", "test")
    try:
        with patch("app.api.billing.get_redis", return_value=client):
            GrantRateLimiter().check(auth, "test-tenant", 2)
        return 200
    except HTTPException as exc:
        return exc.status_code
    finally:
        client.close()


class SafetyTests(management.BillingFixture):
    def test_full_three_role_login_grant_quota_pricing_and_mock_flow(self):
        from app.api import auth
        from app.auth import hash_password
        from app.models import User
        from app.services.billing.orders import BillingOrderService
        self.service = BillingOrderService(self.sessions, self.settings)
        self.app.include_router(auth.router, prefix="/api")
        encoded = hash_password("billing-test-password")
        with self.sessions.begin() as db:
            for index, uid in enumerate(("platform", "owner", "member")):
                user = db.get(User, uid); user.phone=f"1390000000{index}"; user.password_hash=encoded
        tokens = {}
        for index, uid in enumerate(("platform", "owner", "member")):
            response = self.client.post("/api/auth/login", json={"phone":f"1390000000{index}","password":"billing-test-password"})
            self.assertEqual(response.status_code, 200, response.text)
            tokens[uid] = {"Authorization": "Bearer " + response.json()["access_token"]}
        grant = self.client.post("/api/platform-admin/tenants/a/credits/grants", headers={**tokens["platform"],"Idempotency-Key":str(uuid.uuid4())},
                                 json={"amount_credits":"10000","reason":"三角色验收"})
        self.assertEqual(grant.status_code, 201)
        for uid, limit in (("member","4000"),("member2","7000")):
            response=self.client.put(f"/api/tenant-admin/billing/members/{uid}/quota", headers=tokens["owner"],json={"total_limit_credits":limit,"reason":"本月总上限"})
            self.assertEqual(response.status_code,200)
        prices = [{"feature_code":"mock_fixed","billing_mode":"FIXED","version":1,"fixed_credits":"20"},
                  {"feature_code":"mock_text","billing_mode":"USAGE","version":1,"metrics":[
                    {"metric":"input_tokens","unit_size":1000,"credits_per_unit":"0.20"},
                    {"metric":"output_tokens","unit_size":1000,"credits_per_unit":"0.80"}]}]
        for payload in prices:
            response=self.client.post("/api/platform-admin/billing/price-rules",headers=tokens["platform"],json=payload)
            self.assertEqual(response.status_code,410,response.text)
            # Historical fixture uses the trusted legacy engine. The retired
            # public route must never create a new legacy consumption intent.
            from app.schemas.billing import PriceRequest
            rule = self.service.create_price(self.auth(), PriceRequest(**payload))
            self.service.activate_price(self.auth(), rule['id'])
            self.assertEqual(self.client.post(f"/api/platform-admin/billing/price-rules/{rule['id']}/activate",headers=tokens["platform"]).status_code,410)
        for kind in ("fixed","text"):
            response=self.client.post(f"/api/ai/mock/{kind}",headers={**tokens["member"],"Idempotency-Key":str(uuid.uuid4())},json={"text":"test"})
            self.assertEqual(response.status_code,410,response.text)
            self.service.run_mock(self.auth('member'), 'mock_' + kind, str(uuid.uuid4()), 'historical-fixture')
        result=self.client.get("/api/tenant-admin/billing/credits",headers=tokens["owner"]).json()
        self.assertEqual(result["balance_credits"],"9978.8000")
        self.assertEqual(result["reserved_credits"],"0.0000")
        self.assertEqual(self.client.get("/api/me/billing/quota",headers=tokens["member"]).json()["used_credits"],"21.2000")
        self.assertEqual(self.client.get("/api/me/billing/orders",headers=tokens["member"]).json()["total"],2)
        self.assertTrue(self.service.check_accounts()["ok"])

    def test_startup_verifies_schema_without_starting_provider_workers(self):
        import app.main as main
        with patch.object(main,"settings",self.settings), patch.object(main,"engine",self.engine), patch.object(main,"start_worker") as start:
            main.on_startup()
            start.assert_not_called()
            self.settings.billing_mock_enabled=True
            self.settings.billing_test_isolation=False
            with self.assertRaisesRegex(RuntimeError,"isolation"):
                main.on_startup()

    def test_request_audit_failure_does_not_change_committed_response(self):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        from starlette.responses import Response
        import app.main as main
        request=SimpleNamespace(method="POST",url=SimpleNamespace(path="/api/test"))
        async def next_response(_): return Response(status_code=201)
        with patch.object(main,"trace_fields",return_value={"request_id":"test-request"}), patch.object(main,"run_in_threadpool",new=AsyncMock(side_effect=RuntimeError("secondary audit fault"))):
            self.assertEqual(asyncio.run(main.audit_requests(request,next_response)).status_code,201)

    def test_isolation_blocks_all_old_ai_and_supplier_sync_without_network(self):
        self.app.add_middleware(BillingIsolationMiddleware, settings=self.settings)
        with patch("httpx.AsyncClient.send", side_effect=AssertionError("external HTTP forbidden")):
            for path in ("/api/products/1/generate", "/api/operations/billing/sync", "/api/video-repair-tasks", "/internal/billing/settle"):
                response = self.client.post(path, headers=self.headers("member"), json={})
                self.assertEqual(response.status_code, 403, response.text)
            self.assertEqual(self.client.get("/api/me/billing/quota", headers=self.headers("member")).status_code, 200)
        self.assertEqual(self.client.post("/api/ai/mock/fixed", headers=self.headers("member"), content=b"x"*20000).status_code, 413)
        def chunks():
            yield b"x"*10000
            yield b"y"*10000
        self.assertEqual(self.client.post("/api/ai/mock/fixed", headers=self.headers("member"), content=chunks()).status_code, 413)

    def test_both_ledger_and_audit_insert_failures_rollback(self):
        # Real DB flush fault after the account has already been created/changed.
        def reject_insert(mapper, connection, target):
            raise RuntimeError("synthetic ledger insertion fault")
        event.listen(CreditLedger, "before_insert", reject_insert)
        try:
            with self.assertRaises(RuntimeError):
                self.service.grant(self.auth(), "a", str(uuid.uuid4()), "50", "故障")
        finally:
            event.remove(CreditLedger, "before_insert", reject_insert)
        with self.sessions() as db:
            self.assertIsNone(db.scalar(select(CreditAccount)))
            self.assertIsNone(db.scalar(select(CreditLedger)))

    def test_sqlite_backup_is_recoverable_and_contains_previous_ledger(self):
        from app.services.billing.migration import migrate_billing
        self.grant()
        result = migrate_billing(self.engine)
        backup = result["backup_path"]
        self.assertTrue(Path(backup).is_file())
        from contextlib import closing
        with closing(sqlite3.connect(backup)) as recovered:
            self.assertEqual(recovered.execute("SELECT COUNT(*) FROM credit_ledger").fetchone()[0], 1)
            self.assertEqual(recovered.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_schema_rejects_cross_tenant_ledger_link_and_bad_account_state(self):
        self.grant()
        with self.sessions() as db:
            account_id = db.scalar(select(CreditAccount.id))
        with self.assertRaises(Exception):
            with self.engine.begin() as connection:
                connection.execute(text("UPDATE credit_accounts SET reserved_microcredits=balance_microcredits+1"))
        with self.assertRaises(Exception):
            with self.sessions.begin() as db:
                db.add(CreditLedger(tenant_id="b", account_id=account_id, event_type="GRANT", delta_microcredits=1,
                    balance_before_microcredits=0,balance_after_microcredits=1,business_key="cross-tenant-test",actor_type="USER",actor_id="platform",reason="test"))

    def test_billing_disabled_returns_not_found(self):
        self.settings.billing_enabled = False
        self.assertEqual(self.client.get("/api/me/billing/quota", headers=self.headers("member")).status_code, 404)


@unittest.skipUnless(os.environ.get("BILLING_TEST_REDIS_URL"), "Dedicated Redis test URL required")
class RedisTests(unittest.TestCase):
    def test_actual_redis_rate_limit_shared_across_processes(self):
        actor = "billing-test-"+uuid.uuid4().hex
        arguments = [(os.environ["BILLING_TEST_REDIS_URL"], actor)]*4
        with ProcessPoolExecutor(max_workers=2) as pool:
            codes = list(pool.map(rate_check, arguments))
        self.assertEqual(sorted(codes), [200,200,429,429])
