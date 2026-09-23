"""SQLite compatibility migration for pre-personal Qianchuan upload databases."""
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import MetaData, UniqueConstraint, create_engine, inspect, select
from sqlalchemy.orm import Session

from app.database import Base, migrate_qianchuan_upload_uniqueness
from app.models import (QianchuanUpload, QianchuanAccountBinding, QianchuanConnection,
    Product, Tenant, User, VideoAsset)


class QianchuanPersonalMigrationTests(unittest.TestCase):
    def test_replaces_legacy_upload_uniqueness_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = create_engine("sqlite:///" + str(Path(directory) / "migration.db"))
            try:
                Base.metadata.create_all(engine, tables=[table for table in Base.metadata.sorted_tables
                    if table.name != "qianchuan_uploads"])
                metadata = MetaData()
                metadata.reflect(bind=engine)
                old = QianchuanUpload.__table__.to_metadata(metadata)
                old.constraints.remove(next(item for item in old.constraints
                    if isinstance(item, UniqueConstraint) and item.name == "uq_qianchuan_upload_owner_binding_target"))
                old.append_constraint(UniqueConstraint("tenant_id", "video_asset_id", "source_content_hash",
                    "advertiser_id", name="uq_qianchuan_upload_source_target"))
                old.create(engine)
                with Session(engine) as db:
                    db.add(Tenant(id="tenant", name="tenant", code="tenant"))
                    for user_id in ("one", "two"):
                        db.add(User(id=user_id, email=f"{user_id}@example.test", name=user_id, password_hash="unused"))
                    db.add(Product(id=1, tenant_id="tenant", name="product"))
                    db.add(VideoAsset(id=1, tenant_id="tenant", product_id=1, asset_id="asset-1",
                        filename="test.mp4", object_url="/api/storage/objects/test", content_hash="a" * 64,
                        asset_kind="final_output"))
                    for user_id in ("one", "two"):
                        db.add(QianchuanConnection(id=f"connection-{user_id}", tenant_id="tenant", created_by=user_id))
                        db.add(QianchuanAccountBinding(id=f"binding-{user_id}", tenant_id="tenant",
                            connection_id=f"connection-{user_id}", source_account_id="100",
                            source_account_type="SHOP", advertiser_id="200"))
                    db.commit()
                values = dict(tenant_id="tenant", video_asset_id=1, source_content_hash="a" * 64,
                    advertiser_id="200", advertiser_name="account", source_url="/api/storage/objects/test",
                    filename="test.mp4", is_aigc=False)
                with engine.begin() as connection:
                    connection.execute(old.insert().values(id="upload-one", created_by="one",
                        binding_id="binding-one", **values))
                migrate_qianchuan_upload_uniqueness(engine)
                migrate_qianchuan_upload_uniqueness(engine)
                self.assertEqual(len(list(Path(directory).glob('migration.db.qianchuan-personal-*.bak'))), 1)
                names = {item["name"] for item in inspect(engine).get_unique_constraints("qianchuan_uploads")}
                self.assertIn("uq_qianchuan_upload_owner_binding_target", names)
                self.assertNotIn("uq_qianchuan_upload_source_target", names)
                with engine.begin() as connection:
                    self.assertEqual(connection.execute(select(QianchuanUpload.id)).scalars().all(), ["upload-one"])
                    connection.execute(QianchuanUpload.__table__.insert().values(id="upload-two", created_by="two",
                        binding_id="binding-two", **values))
                    self.assertEqual(connection.execute(select(QianchuanUpload.id)).scalars().all(),
                        ["upload-one", "upload-two"])
            finally:
                engine.dispose()
