"""Offline B-stage contracts, with real Bearer/RBAC and database transactions."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import sessionmaker

from app.api.billing import router, get_grant_limiter
from app.auth import AuthContext, create_token
from app.config import Settings
from app.database import Base, get_db
from app.models import Tenant, User, TenantMember, TenantRole, Team, MemberGroup
from app.models.billing import CreditAccount, CreditLedger, MemberQuota, BillingAuditEvent, BillingIdempotency
from app.services.billing.service import BillingService, get_billing_service
from app.services.billing.migration import migrate_billing


class NoLimit:
    def check(self, *args):
        pass


class BillingFixture(unittest.TestCase):
    def make_engine(self):
        return create_engine("sqlite:///" + str(Path(self.temp.name)/"billing.db"), connect_args={"check_same_thread": False, "timeout": 15})

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = self.make_engine()
        @event.listens_for(self.engine, "connect")
        def foreign_keys(connection, _):
            if self.engine.dialect.name == "sqlite":
                connection.execute("PRAGMA foreign_keys=ON")
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)
        identity_tables = [model.__table__ for model in (Tenant, User, Team, MemberGroup, TenantRole, TenantMember)]
        Base.metadata.create_all(self.engine, tables=identity_tables)
        migrate_billing(self.engine, backup_confirmed=True)
        self.settings = Settings(_env_file=None, billing_enabled=True, billing_mock_enabled=True, billing_test_isolation=True,
                                 auth_secret="test-only-secret", require_postgres=False)
        self.auth_patch = patch("app.auth.get_settings", return_value=self.settings)
        self.auth_patch.start()
        self.time = None
        self.service = BillingService(self.sessions, self.settings, lambda: self.time or datetime.now(timezone.utc))
        with self.sessions.begin() as db:
            db.add_all([Tenant(id="a", name="A", code="a"), Tenant(id="b", name="B", code="b")])
            db.add_all([User(id=uid, email=f"{uid}@test.invalid", name=uid, password_hash="unused", is_platform_admin=uid=="platform")
                        for uid in ("platform", "owner", "member", "member2", "other", "limited")])
            db.flush()
            db.add(TenantRole(id=1, tenant_id="a", name="团队管理员", code="custom", permissions=["tenant_teams.manage"]))
            db.flush()
            for uid, tenant, role in (("platform", "a", "owner"), ("owner", "a", "owner"), ("member", "a", "member"),
                                      ("member2", "a", "member"), ("other", "b", "owner"), ("limited", "a", "member")):
                db.add(TenantMember(tenant_id=tenant, user_id=uid, role=role, role_id=1 if uid=="limited" else None))
        self.app = FastAPI()
        self.app.include_router(router, prefix="/api")
        def db_override():
            with self.sessions() as db:
                yield db
        self.app.dependency_overrides[get_db] = db_override
        self.app.dependency_overrides[get_billing_service] = lambda: self.service
        self.app.dependency_overrides[get_grant_limiter] = NoLimit
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        self.auth_patch.stop()
        self.engine.dispose()
        self.temp.cleanup()

    def headers(self, uid="platform", key=None):
        return {"Authorization": "Bearer " + create_token(uid, "b" if uid=="other" else "a"), "Idempotency-Key": key or str(uuid.uuid4())}

    def auth(self, uid="platform"):
        return AuthContext(uid, "b" if uid=="other" else "a", "owner", "test", "test")

    def grant(self, value="1000", uid="platform", key=None, tenant="a", reason="测试"):
        return self.client.post(f"/api/platform-admin/tenants/{tenant}/credits/grants", json={"amount_credits": value, "reason": reason}, headers=self.headers(uid, key))

    def quota(self, uid="member", value="100", actor="owner"):
        return self.client.put(f"/api/tenant-admin/billing/members/{uid}/quota", json={"total_limit_credits": value, "reason": "月额度"}, headers=self.headers(actor))


class ManagementTests(BillingFixture):
    def test_T01_T02_T24_T26_grant_replay_original_response_and_conflict(self):
        key = str(uuid.uuid4())
        first = self.grant(key=key)
        self.assertEqual(first.status_code, 201, first.text)
        self.grant("10")
        repeat = self.grant(key=key)
        self.assertEqual(repeat.status_code, 200)
        self.assertEqual(first.json(), repeat.json())
        self.assertEqual(self.grant("1001", key=key).status_code, 409)
        self.assertEqual(self.grant(key=key, reason="不同").status_code, 409)
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(CreditAccount)).balance_microcredits, 10100000)
            self.assertEqual(len(db.scalars(select(CreditLedger)).all()), 2)
            self.assertEqual(len(db.scalars(select(BillingAuditEvent)).all()), 2)

    def test_T03_T28_T31_real_auth_permissions_and_pagination(self):
        for user in ("owner", "member", "other", "limited"):
            self.assertEqual(self.grant(uid=user).status_code, 403)
        self.assertEqual(self.client.post("/api/platform-admin/tenants/a/credits/grants", json={"amount_credits":"1"}).status_code, 401)
        self.grant()
        path = "/api/platform-admin/tenants/a/credits/grants"
        self.assertEqual(self.client.get(path, headers=self.headers("owner")).status_code, 403)
        self.assertEqual(self.client.get(path+"?page_size=101", headers=self.headers()).status_code, 422)
        self.assertEqual(self.client.get(path, headers=self.headers()).json()["total"], 1)

    def test_T27_strict_amount_reason_extra_fields_and_missing_key(self):
        for value in ("0", "-1", "0.00001", "100001", "1e2", "NaN", 1.2, "1.00000"):
            with self.subTest(value=value):
                self.assertEqual(self.grant(value).status_code, 422)
        self.assertEqual(self.grant(reason="  ").status_code, 400)
        self.assertEqual(self.grant(reason="长"*201).status_code, 400)
        response = self.client.post("/api/platform-admin/tenants/a/credits/grants", headers=self.headers(),
                                    json={"amount_credits":"1", "reason":"x", "role":"platform_admin"})
        self.assertEqual(response.status_code, 422)
        headers = self.headers(); headers.pop("Idempotency-Key")
        self.assertEqual(self.client.post("/api/platform-admin/tenants/a/credits/grants", headers=headers,
                                         json={"amount_credits":"1", "reason":"x"}).status_code, 422)
        with self.sessions() as db:
            self.assertIsNone(db.scalar(select(CreditLedger)))

    def test_T04_T05_T17_T19_T22_T23_shared_quota_is_not_a_wallet(self):
        self.grant("10000")
        self.assertEqual(self.quota(value="4000").status_code, 200)
        self.assertEqual(self.quota("member2", "7000").status_code, 200)
        self.assertEqual(self.quota(value="6000").status_code, 200)
        self.assertEqual(self.quota("other").status_code, 404)
        self.assertEqual(self.quota(actor="limited").status_code, 403)
        self.assertEqual(self.quota(actor="member").status_code, 403)
        self.assertEqual(self.client.get("/api/tenant-admin/billing/ledger", headers=self.headers("member")).status_code, 403)
        with self.sessions.begin() as db:
            q = db.scalar(select(MemberQuota).where(MemberQuota.user_id=="member"))
            q.used_microcredits = 5000000
        self.assertEqual(self.quota(value="499").status_code, 409)
        self.assertEqual(self.quota("member2", "0").status_code, 200)
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(CreditAccount)).balance_microcredits, 100000000)
            self.assertEqual(len(db.scalars(select(CreditLedger)).all()), 1)
            self.assertEqual(len(db.scalars(select(BillingAuditEvent).where(BillingAuditEvent.action=="QUOTA_SET")).all()), 4)

    def test_T21_month_rollover_preserves_history(self):
        self.time = datetime(2026, 10, 31, 15, 59, tzinfo=timezone.utc)
        self.quota(value="150")
        self.time = datetime(2026, 10, 31, 16, 0, tzinfo=timezone.utc)
        result = self.client.get("/api/me/billing/quota", headers=self.headers("member"))
        self.assertEqual(result.json()["period_key"], "2026-11")
        self.assertEqual(result.json()["limit_credits"], "150.0000")
        with self.sessions() as db:
            self.assertEqual(len(db.scalars(select(MemberQuota)).all()), 2)

    def test_T25_T29_concurrent_grants_and_account_creation(self):
        barrier = threading.Barrier(4)
        key = str(uuid.uuid4())
        def call(_):
            barrier.wait()
            return self.service.grant(self.auth(), "a", key, "100", "并发")
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(call, range(4)))
        self.assertEqual(len({r[0]["grant_id"] for r in results}), 1)
        self.assertEqual(sum(not r[1] for r in results), 1)
        self.assertEqual(self.grant(tenant="missing").status_code, 404)
        with self.sessions() as db:
            self.assertEqual(len(db.scalars(select(CreditAccount)).all()), 1)

    def test_T30_audit_failure_rolls_back_balance_ledger_and_key(self):
        key = str(uuid.uuid4())
        with patch.object(self.service, "audit", side_effect=RuntimeError("synthetic audit fault")):
            with self.assertRaises(RuntimeError):
                self.service.grant(self.auth(), "a", key, "100", "故障")
        with self.sessions() as db:
            self.assertIsNone(db.scalar(select(CreditAccount)))
            self.assertIsNone(db.scalar(select(CreditLedger)))
            self.assertIsNone(db.scalar(select(BillingIdempotency)))
        self.assertEqual(self.service.grant(self.auth(), "a", key, "100", "故障")[0]["amount_credits"], "100.0000")

    def test_migration_reentry_and_history_immutable(self):
        self.assertEqual(migrate_billing(self.engine, backup_confirmed=True)["status"], "already_current")
        self.grant()
        for table in ("credit_ledger", "billing_audit_events", "billing_idempotency"):
            with self.assertRaises(Exception):
                with self.engine.begin() as connection:
                    connection.execute(text(f"DELETE FROM {table}"))
        with self.engine.begin() as connection:
            connection.execute(text("DROP INDEX ix_credit_ledger_tenant_created"))
        with self.assertRaisesRegex(RuntimeError, "index"):
            migrate_billing(self.engine, backup_confirmed=True)

    def test_limiter_missing_backend_fails_closed(self):
        self.app.dependency_overrides.pop(get_grant_limiter)
        with patch("app.api.billing.get_redis", return_value=None):
            self.assertEqual(self.grant().status_code, 503)

    def revoke(self, value="100", uid="platform", key=None, tenant="a", reason="纠正多发"):
        return self.client.post(f"/api/platform-admin/tenants/{tenant}/credits/revocations", json={"amount_credits": value, "reason": reason}, headers=self.headers(uid, key))

    def test_revoke_replay_conflict_audit_and_tenant_isolation(self):
        self.grant()
        self.quota()
        key = str(uuid.uuid4())
        first = self.revoke(key=key)
        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(self.revoke(key=key).json(), first.json())
        self.assertEqual(self.revoke(key=key).status_code, 200)
        self.assertEqual(self.revoke("101", key=key).status_code, 409)
        for uid in ("owner", "member", "other", "limited"):
            self.assertEqual(self.revoke(uid=uid).status_code, 403)
        self.assertEqual(self.revoke(tenant="b").status_code, 409)
        self.assertEqual(self.client.get("/api/platform-admin/tenants/a/credits", headers=self.headers("owner")).status_code, 403)
        self.assertEqual(self.client.get("/api/platform-admin/tenants/a/credits", headers=self.headers()).json()["available_credits"], "900.0000")
        history = self.client.get("/api/platform-admin/tenants/a/credits/adjustments", headers=self.headers()).json()
        self.assertEqual(history["total"], 2)
        self.assertEqual({row["event_type"] for row in history["items"]}, {"GRANT", "REVOKE"})
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(MemberQuota)).limit_microcredits, 1000000)
            row = db.scalar(select(CreditLedger).where(CreditLedger.event_type == "REVOKE"))
            self.assertEqual(row.delta_microcredits, -1000000)
            self.assertEqual(row.reason, "纠正多发")
            self.assertEqual(db.scalar(select(BillingAuditEvent).where(BillingAuditEvent.action == "REVOKE")).resource_id, row.id)

    def test_revoke_protects_reserved_and_rejects_invalid_amounts(self):
        self.grant()
        with self.sessions.begin() as db:
            db.scalar(select(CreditAccount)).reserved_microcredits = 9000000
        for value in ("0", "-1", "0.00001", "100001", "1e2", 1):
            self.assertEqual(self.revoke(value).status_code, 422)
        self.assertEqual(self.revoke(reason=" ").status_code, 400)
        self.assertEqual(self.revoke("100.0001").status_code, 409)
        self.assertEqual(self.revoke("100").status_code, 201)
        self.assertEqual(self.revoke("0.0001").status_code, 409)
        with self.sessions() as db:
            row = db.scalar(select(CreditAccount))
            self.assertEqual(row.balance_microcredits, row.reserved_microcredits)

    def test_concurrent_revocations_do_not_overdraw(self):
        self.grant("100")
        def attempt(_):
            try:
                self.service.revoke(self.auth(), "a", str(uuid.uuid4()), "60", "纠正多发")
                return 201
            except HTTPException as error:
                return error.status_code
        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertEqual(sorted(executor.map(attempt, range(2))), [201, 409])
        self.assertEqual(self.service.balance(self.auth())["balance_credits"], "40.0000")

    def test_v1_upgrade_preserves_history_and_immutability(self):
        from app.models.billing import BillingSchemaVersion
        from app.services.billing.migration import checksum, verify
        from sqlalchemy.schema import CreateTable
        self.grant()
        with self.engine.begin() as connection:
            clause = next(str(c.sqltext) for c in CreditLedger.__table__.constraints if c.name == "ck_credit_ledger_direction")
            old_clause = clause.replace("OR (event_type = 'REVOKE' AND delta_microcredits < 0 AND order_id IS NULL) ", "")
            if connection.dialect.name == "postgresql":
                connection.execute(text("ALTER TABLE credit_ledger DROP CONSTRAINT ck_credit_ledger_direction"))
                connection.execute(text("ALTER TABLE credit_ledger ADD CONSTRAINT ck_credit_ledger_direction CHECK (" + old_clause + ")"))
            else:
                ddl = str(CreateTable(CreditLedger.__table__).compile(dialect=connection.dialect)).replace(clause, old_clause).replace("CREATE TABLE credit_ledger", "CREATE TABLE credit_ledger_old", 1)
                connection.execute(text(ddl))
                connection.execute(text("INSERT INTO credit_ledger_old SELECT * FROM credit_ledger"))
                connection.execute(text("DROP TABLE credit_ledger"))
                connection.execute(text("ALTER TABLE credit_ledger_old RENAME TO credit_ledger"))
                for index in CreditLedger.__table__.indexes:
                    index.create(connection)
                connection.execute(text("CREATE TRIGGER billing_immutable_credit_ledger BEFORE UPDATE ON credit_ledger BEGIN SELECT RAISE(ABORT, 'immutable'); END"))
                connection.execute(text("CREATE TRIGGER billing_no_delete_credit_ledger BEFORE DELETE ON credit_ledger BEGIN SELECT RAISE(ABORT, 'immutable'); END"))
            connection.execute(BillingSchemaVersion.__table__.update().values(version=1, checksum=checksum(connection.dialect, 1)))
        self.assertEqual(migrate_billing(self.engine, backup_confirmed=True)["status"], "upgraded")
        self.assertEqual(migrate_billing(self.engine, backup_confirmed=True)["status"], "already_current")
        with self.engine.connect() as connection:
            verify(connection)
        self.assertEqual(self.revoke().status_code, 201)
        self.assertEqual(self.service.balance(self.auth())["balance_credits"], "900.0000")
        with self.assertRaises(Exception):
            with self.engine.begin() as connection:
                connection.execute(text("DELETE FROM credit_ledger"))
