from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import threading
import unittest
from unittest.mock import patch
import uuid

from fastapi import HTTPException
from sqlalchemy import select, text

from test_billing_management import BillingFixture
from app.models.billing import BillingOrder, BillingPriceRule, CreditAccount, CreditLedger, MemberQuota
from app.schemas.billing import PriceRequest
from app.services.billing.amounts import calculate
from app.services.billing.orders import BillingOrderService, KnownNoUsage, MockAI


class OrderTests(BillingFixture):
    def setUp(self):
        super().setUp()
        self.service = BillingOrderService(self.sessions, self.settings, lambda: self.time or datetime.now(timezone.utc))
        self.grant("10000")
        self.quota(value="4000")
        self.quota("member2", "7000")
        self.fixed = self.publish("mock_fixed", "20")
        self.usage = self.publish("mock_text")

    def publish(self, feature, fixed=None, version=1):
        payload = {"feature_code": feature, "version": version, "billing_mode": "FIXED" if fixed else "USAGE"}
        if fixed:
            payload["fixed_credits"] = fixed
        else:
            payload["metrics"] = [{"metric":"input_tokens", "unit_size":1000, "credits_per_unit":"0.20"},
                                  {"metric":"output_tokens", "unit_size":1000, "credits_per_unit":"0.80"}]
        result = self.service.create_price(self.auth(), PriceRequest(**payload))
        self.service.activate_price(self.auth(), result["id"])
        return result

    def reserve(self, feature="mock_fixed", user="member", request=None):
        return self.service.reserve(self.auth(user), request or uuid.uuid4().hex, feature,
                                    {} if feature=="mock_fixed" else {"input_tokens":3000,"output_tokens":2000}, "test-business")

    def test_T06_T07_T18_historical_fixed_usage_shared_pool(self):
        self.assertEqual(self.service.estimate("mock_text",{"input_tokens":2000,"output_tokens":1000})["estimated_credits"],"1.2000")
        for feature, charged in (("fixed", "20.0000"), ("text", "1.2000")):
            result, _ = self.service.run_mock(self.auth('member'), 'mock_' + feature, str(uuid.uuid4()), 'historical-fixture')
            self.assertEqual(result['charged_credits'], charged)
        result = self.client.get("/api/tenant-admin/billing/credits", headers=self.headers("owner")).json()
        self.assertEqual(result["balance_credits"], "9978.8000")
        self.assertEqual(result["reserved_credits"], "0.0000")
        self.assertEqual(self.service.own_quota(self.auth("member"))["used_credits"], "21.2000")
        self.assertEqual(self.service.own_quota(self.auth("member2"))["used_credits"], "0.0000")
        self.assertTrue(self.service.check_accounts()["ok"])

    def test_T09_historical_engine_replay_executes_once_and_body_conflict(self):
        key = str(uuid.uuid4())
        with patch.object(MockAI, "execute", wraps=MockAI().execute) as execute:
            first, _ = self.service.run_mock(self.auth('member'), 'mock_text', key, 'x')
            replay, _ = self.service.run_mock(self.auth('member'), 'mock_text', key, 'x')
            with self.assertRaises(HTTPException) as conflict:
                self.service.run_mock(self.auth('member'), 'mock_text', key, 'y')
            self.assertEqual(execute.call_count, 1)
        # SQLite's timestamp serialization can differ in tz suffix after reload; identity/result must be stable.
        self.assertEqual(first, replay)
        self.assertEqual(conflict.exception.status_code, 409)
        order = self.reserve()
        self.service.settle(order["id"], {}, {"ok":True})
        self.service.settle(order["id"], {}, {"ok":True})
        with self.assertRaises(HTTPException) as caught:
            self.service.settle(order["id"], {}, {"ok":False})
        self.assertEqual(caught.exception.detail["code"], "IDEMPOTENCY_CONFLICT")

    def test_T08_failure_proven_and_uncertain_have_different_outcomes(self):
        class Failed:
            def execute(self, feature): raise KnownNoUsage()
        class Uncertain:
            def execute(self, feature): raise TimeoutError()
        for adapter, expected in ((Failed(), "CANCELED"), (Uncertain(), "RESERVED")):
            with self.assertRaises(HTTPException):
                self.service.run_mock(self.auth("member"), "mock_fixed", str(uuid.uuid4()), "x", adapter)
            with self.sessions() as db:
                row = db.scalars(select(BillingOrder).order_by(BillingOrder.created_at.desc())).first()
                self.assertEqual(row.status, expected)
                if expected == "RESERVED":
                    self.assertEqual(row.reconciliation_status, "NEEDS_REVIEW")
        self.assertTrue(self.service.check_accounts()["ok"])

    def test_T10_no_execution_when_no_quota_or_balance(self):
        self.quota(value="0")
        with patch.object(MockAI, "execute") as execute:
            with self.assertRaises(HTTPException) as caught:
                self.service.run_mock(self.auth('member'), 'mock_fixed', str(uuid.uuid4()), 'historical-fixture')
            self.assertEqual(caught.exception.status_code, 409)
            execute.assert_not_called()
        self.quota(value="100")
        self.publish("mock_fixed", "20000", 2)
        with self.assertRaises(HTTPException) as caught:
            self.reserve()
        self.assertEqual(caught.exception.detail["code"], "INSUFFICIENT_CREDITS")

    def test_T12_old_snapshot_and_price_immutability(self):
        order = self.reserve()
        self.publish("mock_fixed", "40", 2)
        self.assertEqual(self.service.settle(order["id"], {}, {})["charged_credits"], "20.0000")
        for sql in ("UPDATE billing_price_rules SET fixed_microcredits=1", "UPDATE billing_orders SET period_key='2000-01'"):
            with self.assertRaises(Exception):
                with self.engine.begin() as connection:
                    connection.execute(text(sql))

    def test_T13_T23_no_public_settle_no_usage_spoof_no_other_orders(self):
        self.assertEqual(self.client.post("/internal/billing/settle", json={}).status_code, 404)
        self.assertEqual(self.client.post("/api/ai/mock/text", headers=self.headers("member"), json={"actual_usage":{"input_tokens":1}}).status_code, 410)
        self.service.run_mock(self.auth("member"), "mock_text", str(uuid.uuid4()), "x")
        self.assertEqual(self.service.own_orders(self.auth("member2"))["total"], 0)
        self.assertEqual(self.service.own_orders(self.auth("other"))["total"], 0)
        self.settings.billing_mock_enabled=False
        self.assertEqual(self.client.post("/api/ai/mock/text", headers=self.headers("member"), json={}).status_code, 410)

    def test_T14_unknown_rates_metrics_and_final_rounding(self):
        self.assertEqual(calculate({"billing_mode":"USAGE","metrics":[{"metric":"input_tokens","unit_size":3,"credits_per_unit":"0.0001"},
                                                                  {"metric":"output_tokens","unit_size":3,"credits_per_unit":"0.0001"}]},
                                   {"input_tokens":1,"output_tokens":1}), 1)
        for usage in ({"unknown":1}, {"input_tokens":True,"output_tokens":0}, {"input_tokens":-1,"output_tokens":0}):
            with self.assertRaises(HTTPException):
                calculate(self.usage, usage)
        with self.assertRaises(HTTPException):
            self.service.reserve(self.auth("member"), uuid.uuid4().hex, "unknown", {}, "x")
        # A registered feature with only drafts also fails closed.
        with self.transaction_for_test() as db:
            row = db.get(BillingPriceRule, self.usage["id"]); row.status="RETIRED"
        with self.assertRaises(HTTPException): self.reserve("mock_text")

    def transaction_for_test(self):
        return self.sessions.begin()

    def test_T15_month_boundary_settlement_uses_original_quota(self):
        self.time = datetime(2026,10,31,15,59,tzinfo=timezone.utc)
        self.quota(value="4000")
        order = self.reserve()
        self.time += timedelta(minutes=2)
        self.service.own_quota(self.auth("member"))
        self.service.settle(order["id"], {}, {})
        self.assertEqual(self.service.own_quota(self.auth("member"))["used_credits"], "0.0000")
        with self.sessions() as db:
            old = db.scalar(select(MemberQuota).where(MemberQuota.user_id=="member",MemberQuota.period_key=="2026-10"))
            self.assertEqual(old.used_microcredits, 200000)
            self.assertEqual(old.reserved_microcredits, 0)

    def test_T16_timeout_unknown_preserves_reservation_and_known_outcome_recovers(self):
        pending = self.reserve()
        unknown = self.reserve()
        ready = self.reserve("mock_text")
        self.service.claim(unknown["id"])
        self.service.claim(ready["id"])
        self.service.record_outcome(ready["id"], {"input_tokens":2000,"output_tokens":1000}, {"ok":True})
        self.time = datetime.now(timezone.utc) + timedelta(hours=2)
        self.assertEqual(self.service.reconcile()["scanned"], 3)
        with self.sessions() as db:
            self.assertEqual(db.get(BillingOrder, unknown["id"]).status, "RESERVED")
        self.service.reconcile(recover=True)
        self.service.reconcile(recover=True)
        with self.sessions() as db:
            self.assertEqual(db.get(BillingOrder, pending["id"]).status, "CANCELED")
            self.assertEqual(db.get(BillingOrder, ready["id"]).status, "SETTLED")
            self.assertEqual(db.get(BillingOrder, unknown["id"]).status, "RESERVED")
        self.assertTrue(self.service.check_accounts()["ok"])

    def test_T11_T20_concurrent_members_cannot_overspend_last_pool(self):
        self.quota(value="20000")
        self.publish("mock_fixed", "9900", 2)
        previous=self.reserve()
        self.service.settle(previous["id"],{}, {})
        self.publish("mock_fixed", "60", 3)
        barrier = threading.Barrier(2)
        def reserve(user):
            barrier.wait()
            try: return self.reserve(user=user)["id"]
            except HTTPException: return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(reserve, ("member", "member2")))
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertTrue(self.service.check_accounts()["ok"])
        with self.sessions() as db:
            account=db.scalar(select(CreditAccount))
            self.assertEqual(account.balance_microcredits,1000000)
            self.assertEqual(account.reserved_microcredits,600000)

    def test_T18_consumption_500_only_changes_actor_usage(self):
        self.publish("mock_fixed","500",2)
        order=self.reserve()
        self.service.settle(order["id"],{}, {})
        self.assertEqual(self.service.balance(self.auth("owner"))["balance_credits"],"9500.0000")
        self.assertEqual(self.service.own_quota(self.auth("member"))["used_credits"],"500.0000")
        self.assertEqual(self.service.own_quota(self.auth("member2"))["used_credits"],"0.0000")
        self.assertTrue(self.service.check_accounts()["ok"])

    def test_cancel_settle_race_and_usage_over_reservation(self):
        order = self.reserve()
        barrier = threading.Barrier(2)
        def terminal(action):
            barrier.wait()
            try: return action()["status"]
            except HTTPException: return "CONFLICT"
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes=list(pool.map(terminal, (lambda:self.service.cancel(order["id"]), lambda:self.service.settle(order["id"],{},{}))))
        self.assertIn("CONFLICT", outcomes)
        self.assertTrue(self.service.check_accounts()["ok"])
        order = self.reserve("mock_text")
        with self.assertRaises(HTTPException):
            self.service.settle(order["id"], {"input_tokens":4000,"output_tokens":2000}, {})
        with self.sessions() as db:
            self.assertEqual(db.get(BillingOrder,order["id"]).reconciliation_status, "NEEDS_REVIEW")
        self.assertTrue(self.service.check_accounts()["ok"])

    def test_concurrent_same_business_key_executes_mock_once(self):
        started, release = threading.Event(), threading.Event()
        class BlockingMock:
            calls = 0
            def execute(adapter, feature):
                adapter.calls += 1
                started.set()
                if not release.wait(10): raise RuntimeError("test synchronization timeout")
                return {"ok":True}, {}
        adapter = BlockingMock()
        key = str(uuid.uuid4())
        with ThreadPoolExecutor(max_workers=3) as pool:
            first = pool.submit(self.service.run_mock, self.auth("member"), "mock_fixed", key, "x", adapter)
            try:
                self.assertTrue(started.wait(10))
                others = [pool.submit(self.service.run_mock, self.auth("member"), "mock_fixed", key, "x", adapter) for _ in range(2)]
                self.assertTrue(all(f.result(timeout=10)[1] for f in others))
            finally:
                release.set()
            self.assertEqual(first.result(timeout=10)[0]["status"], "SETTLED")
        self.assertEqual(adapter.calls, 1)
        self.assertTrue(self.service.check_accounts()["ok"])

    def test_member_quota_race_and_quota_lowering_cannot_break_reservations(self):
        self.publish("mock_fixed", "60", 2)
        self.quota(value="100")
        barrier = threading.Barrier(2)
        def reserve(_):
            barrier.wait()
            try: return self.reserve()["id"]
            except HTTPException: return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(reserve, range(2)))
        self.assertEqual(sum(r is not None for r in results), 1)
        with self.assertRaises(HTTPException):
            self.service.set_quota(self.auth("owner"), "member", "0", "下调")
        self.assertTrue(self.service.check_accounts()["ok"])

    def test_response_loss_and_disabled_member_recovery(self):
        order = self.reserve("mock_text")
        usage = {"input_tokens":2000,"output_tokens":1000}
        self.service.claim(order["id"])
        self.service.record_outcome(order["id"],usage,{"ok":True})
        from app.models import TenantMember
        with self.sessions.begin() as db:
            member=db.scalar(select(TenantMember).where(TenantMember.user_id=="member")); member.status="disabled"
        # Trusted settlement still completes the original authorization; no new spend.
        self.service.settle(order["id"],usage,{"ok":True})
        self.service.settle(order["id"],usage,{"ok":True})
        with self.assertRaises(HTTPException): self.reserve()
        self.assertTrue(self.service.check_accounts()["ok"])

    def test_revoke_leaves_reserved_order_settleable_and_balanced(self):
        order = self.reserve()
        self.service.revoke(self.auth(), "a", str(uuid.uuid4()), "9980", "纠正多发")
        with self.assertRaises(HTTPException):
            self.service.revoke(self.auth(), "a", str(uuid.uuid4()), "0.0001", "纠正多发")
        self.service.settle(order["id"], {}, {"ok": True})
        self.assertEqual(self.service.balance(self.auth())["balance_credits"], "0.0000")
        self.assertEqual(self.service.own_quota(self.auth("member"))["used_credits"], "20.0000")
        self.assertTrue(self.service.check_accounts()["ok"])

    def test_revoke_races_with_reservation_without_spending_reserved_credits(self):
        self.service.revoke(self.auth(), "a", str(uuid.uuid4()), "9980", "纠正多发")
        def attempt(kind):
            try:
                if kind == "reserve":
                    self.reserve()
                else:
                    self.service.revoke(self.auth(), "a", str(uuid.uuid4()), "20", "纠正多发")
                return 201
            except HTTPException as error:
                return error.status_code
        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertEqual(sorted(executor.map(attempt, ["reserve", "revoke"])), [201, 409])
        self.assertTrue(self.service.check_accounts()["ok"])

    def test_platform_price_summary_and_manual_token_adjustment(self):
        path = "/api/platform-admin/billing/price-rules"
        for uid in ("owner", "member", "other"):
            self.assertEqual(self.client.get(path + "/summary", headers=self.headers(uid)).status_code, 403)
        summary_response = self.client.get(path + "/summary", headers=self.headers())
        self.assertEqual(summary_response.status_code, 410)
        summary = self.service.price_summary(self.auth())["items"]
        self.assertEqual({row["feature_code"] for row in summary}, {"mock_fixed", "mock_text"})
        self.assertTrue(all(row["next_version"] == 2 for row in summary))
        self.assertEqual(next(row for row in summary if row["feature_code"] == "mock_fixed")["active"]["fixed_credits"], "20.0000")
        old = self.reserve("mock_text")
        payload = {"feature_code":"mock_text", "version":2, "billing_mode":"USAGE", "metrics":[
            {"metric":"input_tokens", "unit_size":1000, "credits_per_unit":"2"},
            {"metric":"output_tokens", "unit_size":1000, "credits_per_unit":"4"}]}
        response = self.client.post(path, json=payload, headers=self.headers())
        self.assertEqual(response.status_code, 410, response.text)
        price = self.service.create_price(self.auth(), PriceRequest(**payload))
        self.assertEqual(price['status'], 'DRAFT')
        self.assertEqual(self.service.estimate("mock_text", {"input_tokens":2000,"output_tokens":1000})["estimated_credits"], "1.2000")
        price_id = price['id']
        self.assertEqual(self.client.post(path+f"/{price_id}/activate", headers=self.headers()).status_code, 410)
        self.service.activate_price(self.auth(), price_id)
        self.assertEqual(self.service.estimate("mock_text", {"input_tokens":2000,"output_tokens":1000})["estimated_credits"], "8.0000")
        self.assertEqual(self.service.settle(old["id"], {"input_tokens":2000,"output_tokens":1000}, {})["charged_credits"], "1.2000")
        payload["version"] = 99
        self.assertEqual(self.client.post(path, json=payload, headers=self.headers()).status_code, 410)
        self.service.create_price(self.auth(), PriceRequest(**payload))
        summary = self.service.price_summary(self.auth())["items"]
        row = next(item for item in summary if item["feature_code"] == "mock_text")
        self.assertEqual(row["next_version"], 100)
        self.assertEqual(row["active"]["pricing_version"], 2)
        self.assertEqual(self.client.post(path, json=payload, headers=self.headers()).status_code, 410)
        self.assertTrue(self.service.check_accounts()["ok"])
