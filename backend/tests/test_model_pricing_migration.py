"""Frozen v2 schema/history → v3, entirely within temporary SQLite files."""
from pathlib import Path
from contextlib import closing
import sqlite3
import tempfile
import unittest

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Tenant, User, Team, MemberGroup, TenantRole, TenantMember
from app.services.billing.migration import migrate_billing, verify, checksum


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'legacy.db'
        self.engine = create_engine('sqlite:///' + str(self.path))
        Base.metadata.create_all(self.engine, tables=[model.__table__ for model in (Tenant, User, Team, MemberGroup, TenantRole, TenantMember)])
        with closing(sqlite3.connect(self.path)) as db:
            for name in ('billing_v2.sql', 'billing_v2_rows.sql'):
                db.executescript((Path(__file__).parent / 'fixtures' / name).read_text(encoding='utf-8'))

    def tearDown(self):
        self.engine.dispose()
        self.temp.cleanup()

    def test_v2_upgrade_keeps_legacy_order_and_ledger_and_rejects_identity_mutation(self):
        with self.engine.connect() as db:
            verify(db, version=2)
            before = [tuple(row) for row in db.execute(text('SELECT id, pricing_rule_id, pricing_snapshot_json, usage_json FROM billing_orders'))]
            ledger = [tuple(row) for row in db.execute(text('SELECT * FROM credit_ledger ORDER BY id'))]
        result = migrate_billing(self.engine, backup_confirmed=True)
        self.assertEqual(result['version'], 3)
        self.assertTrue(Path(result['backup_path']).exists())
        with self.engine.connect() as db:
            verify(db)
            self.assertEqual([tuple(row) for row in db.execute(text('SELECT id, pricing_rule_id, pricing_snapshot_json, usage_json FROM billing_orders'))], before)
            self.assertEqual([tuple(row) for row in db.execute(text('SELECT * FROM credit_ledger ORDER BY id'))], ledger)
            self.assertEqual(db.execute(text('SELECT execution_mode,model_price_id,metering_json FROM billing_orders')).one(), ('LEGACY', None, None))
        for sql in ('DELETE FROM credit_ledger', 'DELETE FROM billing_orders', "UPDATE billing_orders SET execution_mode='REAL'", "UPDATE billing_price_rules SET fixed_microcredits=1"):
            with self.assertRaises(Exception):
                with self.engine.begin() as db:
                    db.execute(text(sql))
        self.assertEqual(migrate_billing(self.engine)['status'], 'already_current')
        from app.config import Settings
        from app.auth import AuthContext
        from app.services.billing.orders import BillingOrderService
        service = BillingOrderService(sessionmaker(bind=self.engine), Settings(_env_file=None))
        history = service.own_orders(AuthContext('member', 'a', 'member', '', ''))
        self.assertEqual(history['items'][0]['charged_credits'], '20.0000')

    def test_v2_checksum_matches_frozen_baseline_and_pg_checksum(self):
        from sqlalchemy.dialects import postgresql
        self.assertEqual(checksum(self.engine.dialect, 2), '41513f518c64c9fc6a2f9d407ff8d2f7b976f7b1c330d25ee6958b65959d0833')
        self.assertEqual(checksum(postgresql.dialect(), 2), 'abe98f12938384f7b4a52bcdcd58fa610d00573ed87ce968dfb2d6f17f66d79e')
