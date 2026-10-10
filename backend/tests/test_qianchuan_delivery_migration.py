"""Schema and safety tests for the explicit Qianchuan delivery migration."""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import Base
from app.models import (
    Product, QianchuanAccountBinding, QianchuanCapabilityProbe, QianchuanConnection,
    QianchuanDeliveryPlan, QianchuanPlanOperation, QianchuanUpload, Tenant, User, VideoAsset,
)
from app.services.qianchuan_plan.migration import PLAN_TABLE_NAMES, migrate_qianchuan_delivery


class QianchuanDeliveryMigrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "delivery.db"
        self.engine = create_engine("sqlite:///" + str(self.path))
        startup_tables = [table for table in Base.metadata.sorted_tables
                          if not table.info.get("explicit_migration_required")]
        Base.metadata.create_all(self.engine, tables=startup_tables)
        with Session(self.engine) as db:
            db.add(Tenant(id="tenant", name="tenant", code="tenant"))
            db.add(User(id="owner", email="owner@example.test", name="owner", password_hash="unused"))
            db.add(Product(id=1, tenant_id="tenant", name="product"))
            db.add(VideoAsset(id=1, tenant_id="tenant", product_id=1, asset_id="asset-1",
                              filename="test.mp4", object_url="/api/storage/objects/test",
                              content_hash="a" * 64, asset_kind="final_output"))
            db.add(QianchuanConnection(id="connection", tenant_id="tenant", created_by="owner",
                                       credential_version=3, status="active"))
            db.add(QianchuanAccountBinding(id="binding", tenant_id="tenant", connection_id="connection",
                                           source_account_id="100", source_account_type="SHOP",
                                           advertiser_id="200", is_valid=True))
            db.add(QianchuanUpload(id="upload", tenant_id="tenant", created_by="owner",
                                   video_asset_id=1, binding_id="binding", advertiser_id="200",
                                   source_content_hash="a" * 64, source_url="/objects/test",
                                   filename="test.mp4", is_aigc=False, status="succeeded",
                                   material_id="300"))
            db.commit()

    def tearDown(self):
        self.engine.dispose()
        self.directory.cleanup()

    def test_migration_is_explicit_idempotent_and_preserves_legacy_rows(self):
        self.assertTrue(PLAN_TABLE_NAMES.isdisjoint(inspect(self.engine).get_table_names()))
        first = migrate_qianchuan_delivery(self.engine)
        second = migrate_qianchuan_delivery(self.engine)
        self.assertEqual(first["status"], "migrated")
        self.assertEqual(second["status"], "already_current")
        self.assertEqual(set(first["created_tables"]), PLAN_TABLE_NAMES)
        self.assertTrue(PLAN_TABLE_NAMES <= set(inspect(self.engine).get_table_names()))
        self.assertEqual(first["legacy_row_counts"], {
            "qianchuan_connections": 1,
            "qianchuan_account_bindings": 1,
            "qianchuan_uploads": 1,
        })
        self.assertEqual(len(list(self.path.parent.glob("delivery.db.qianchuan-delivery-*.bak"))), 1)

    def test_platform_plan_and_operation_keys_are_unique_in_owner_scope(self):
        migrate_qianchuan_delivery(self.engine)
        plan_values = dict(
            tenant_id="tenant", created_by="owner", connection_id="connection", binding_id="binding",
            advertiser_id="200", credential_version=3, video_asset_id=1, upload_id="upload",
            material_id="300", product_id="400", aweme_id="500", name="test plan",
            marketing_goal="VIDEO_PROM_GOODS", budget_minor=10000, roi_goal=Decimal("1.2500"),
            platform_ad_id="600",
        )
        with Session(self.engine) as db:
            db.add(QianchuanDeliveryPlan(id="plan-one", **plan_values))
            db.commit()
            db.add(QianchuanPlanOperation(id="op-one", plan_id="plan-one", tenant_id="tenant",
                                          created_by="owner", operation_type="create",
                                          idempotency_key="same-key", request_version=0))
            db.commit()
            db.add(QianchuanPlanOperation(id="op-two", plan_id="plan-one", tenant_id="tenant",
                                          created_by="owner", operation_type="sync",
                                          idempotency_key="same-key", request_version=0))
            with self.assertRaises(IntegrityError):
                db.commit()
            db.rollback()
            db.add(QianchuanDeliveryPlan(id="plan-two", **plan_values))
            with self.assertRaises(IntegrityError):
                db.commit()
            db.rollback()
            self.assertEqual(db.execute(select(QianchuanDeliveryPlan.id)).scalars().all(), ["plan-one"])

    def test_capability_conclusion_is_constrained_and_version_bound(self):
        migrate_qianchuan_delivery(self.engine)
        with Session(self.engine) as db:
            db.add(QianchuanCapabilityProbe(
                id="probe-one", tenant_id="tenant", created_by="owner", connection_id="connection",
                binding_id="binding", advertiser_id="200", credential_version=3,
                capability="available_products", conclusion="no_sample_data", http_status=200,
                response_summary={"response_keys": ["product_list", "page_info"]},
                expires_at=datetime.utcnow() + timedelta(minutes=30)))
            db.commit()
            row = db.get(QianchuanCapabilityProbe, "probe-one")
            self.assertEqual((row.connection_id, row.binding_id, row.credential_version),
                             ("connection", "binding", 3))
            db.add(QianchuanCapabilityProbe(
                id="probe-two", tenant_id="tenant", created_by="owner", connection_id="connection",
                binding_id="binding", advertiser_id="200", credential_version=3,
                capability="available_products", conclusion="guessed",
                expires_at=datetime.utcnow() + timedelta(minutes=30)))
            with self.assertRaises(IntegrityError):
                db.commit()


if __name__ == "__main__":
    unittest.main()

