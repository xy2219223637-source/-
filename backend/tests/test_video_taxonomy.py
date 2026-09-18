from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.video_taxonomy import AssetTaxonomyUpdate, PersonalTagCreate, catalog, create_personal_tag, update_assets
from app.api.video_library import get_asset
from app.auth import AuthContext
from app.database import Base
from app.models import Product, VideoAsset, VideoAssetTag, VideoCategory, VideoTag, VideoTagGroup
from app.services.video_taxonomy import ensure_default_taxonomy, inherit_public_tags_to_final


class VideoTaxonomyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="enrui-video-taxonomy-")
        database_path = Path(self.temp_dir.name) / "taxonomy.db"
        self.engine = create_engine(f"sqlite:///{database_path.as_posix()}")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()
        self.auth = AuthContext("owner-1", "tenant-1", "owner", "owner@example.com", "测试企业")
        self.other_auth = AuthContext("member-2", "tenant-1", "member", "member@example.com", "测试企业")
        self.db.add(Product(
            tenant_id="tenant-1",
            name="测试商品",
            category="护肤",
            price=1,
            description="",
            image_urls=[],
            detail_image_urls=[],
        ))
        self.db.commit()
        self.product = self.db.query(Product).first()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()
        self.temp_dir.cleanup()

    def _asset(self, asset_id: str, kind: str = "material") -> VideoAsset:
        asset = VideoAsset(
            tenant_id="tenant-1",
            product_id=self.product.id,
            asset_id=asset_id,
            filename=f"{asset_id}.mp4",
            source="manual_upload" if kind == "material" else "smart_compose_output",
            library_type="semi_finished" if kind == "material" else "finished",
            asset_kind=kind,
            object_url=f"/{asset_id}.mp4",
            content_hash=asset_id,
        )
        self.db.add(asset)
        self.db.commit()
        self.db.refresh(asset)
        return asset

    def test_defaults_assignments_and_personal_isolation(self) -> None:
        ensure_default_taxonomy(self.db, self.auth.tenant_id)
        self.db.commit()
        material_categories = self.db.query(VideoCategory).filter_by(
            tenant_id="tenant-1", object_type="material"
        ).all()
        self.assertTrue(any(row.parent_id is None for row in material_categories))
        child = next(row for row in material_categories if row.parent_id is not None)
        public_tags = self.db.query(VideoTag).filter_by(tenant_id="tenant-1", scope="public").limit(2).all()
        asset = self._asset("material-1")

        update_assets(AssetTaxonomyUpdate(
            asset_ids=[asset.id],
            set_category=True,
            category_id=child.id,
            add_tag_ids=[row.id for row in public_tags],
        ), self.db, self.auth)
        self.db.refresh(asset)
        self.assertEqual(asset.category_id, child.id)
        self.assertEqual(self.db.query(VideoAssetTag).filter_by(asset_id=asset.id).count(), 2)

        personal = create_personal_tag(PersonalTagCreate(name="待复审", applies_to="both"), self.db, self.auth)
        update_assets(AssetTaxonomyUpdate(asset_ids=[asset.id], add_tag_ids=[personal["id"]]), self.db, self.auth)
        owner_catalog = catalog("material", False, self.db, self.auth)
        member_catalog = catalog("material", False, self.db, self.other_auth)
        self.assertEqual([row["name"] for row in owner_catalog["personal_tags"]], ["待复审"])
        self.assertEqual(member_catalog["personal_tags"], [])

    def test_category_type_validation_and_public_tag_inheritance(self) -> None:
        ensure_default_taxonomy(self.db, self.auth.tenant_id)
        self.db.commit()
        source = self._asset("material-2")
        final = self._asset("final-1", "final_output")
        public_tag = self.db.query(VideoTag).filter_by(tenant_id="tenant-1", scope="public").first()
        final_category = self.db.query(VideoCategory).filter_by(
            tenant_id="tenant-1", object_type="final_output"
        ).first()

        with self.assertRaises(HTTPException) as raised:
            update_assets(AssetTaxonomyUpdate(
                asset_ids=[source.id], set_category=True, category_id=final_category.id
            ), self.db, self.auth)
        self.assertEqual(raised.exception.status_code, 422)
        self.db.rollback()

        update_assets(AssetTaxonomyUpdate(asset_ids=[source.id], add_tag_ids=[public_tag.id]), self.db, self.auth)
        inherited = inherit_public_tags_to_final(self.db, final, [source], self.auth.user_id)
        self.db.commit()
        self.assertEqual(inherited, 1)
        assignment = self.db.query(VideoAssetTag).filter_by(asset_id=final.id, tag_id=public_tag.id).one()
        self.assertEqual(assignment.source, "inherited")
        self.assertTrue(assignment.confirmed)

    def test_parent_category_is_not_assignable(self) -> None:
        ensure_default_taxonomy(self.db, self.auth.tenant_id)
        self.db.commit()
        asset = self._asset("material-parent")
        parent = self.db.query(VideoCategory).filter_by(
            tenant_id="tenant-1", object_type="material", parent_id=None
        ).first()

        with self.assertRaises(HTTPException) as raised:
            update_assets(AssetTaxonomyUpdate(
                asset_ids=[asset.id], set_category=True, category_id=parent.id
            ), self.db, self.auth)
        self.assertEqual(raised.exception.status_code, 422)
        self.assertIsNone(asset.category_id)

    def test_inactive_tag_remains_visible_and_can_only_be_removed(self) -> None:
        ensure_default_taxonomy(self.db, self.auth.tenant_id)
        self.db.commit()
        asset = self._asset("material-inactive-tag")
        tag = self.db.query(VideoTag).filter_by(
            tenant_id="tenant-1", scope="public", status="active"
        ).first()
        update_assets(AssetTaxonomyUpdate(asset_ids=[asset.id], add_tag_ids=[tag.id]), self.db, self.auth)
        tag.status = "inactive"
        self.db.commit()

        serialized = get_asset(asset.id, self.db, self.auth)
        self.assertEqual(serialized["taxonomy_tags"][0]["status"], "inactive")

        with self.assertRaises(HTTPException) as raised:
            update_assets(AssetTaxonomyUpdate(asset_ids=[asset.id], add_tag_ids=[tag.id]), self.db, self.auth)
        self.assertEqual(raised.exception.status_code, 422)

        update_assets(AssetTaxonomyUpdate(asset_ids=[asset.id], remove_tag_ids=[tag.id]), self.db, self.auth)
        self.assertEqual(self.db.query(VideoAssetTag).filter_by(asset_id=asset.id, tag_id=tag.id).count(), 0)

    def test_inactive_parent_category_cannot_receive_new_assignments(self) -> None:
        ensure_default_taxonomy(self.db, self.auth.tenant_id)
        self.db.commit()
        asset = self._asset("material-inactive-parent")
        child = self.db.query(VideoCategory).filter_by(
            tenant_id="tenant-1", object_type="material"
        ).filter(VideoCategory.parent_id.is_not(None)).first()
        parent = self.db.query(VideoCategory).filter_by(id=child.parent_id).one()
        parent.status = "inactive"
        self.db.commit()

        with self.assertRaises(HTTPException) as raised:
            update_assets(AssetTaxonomyUpdate(
                asset_ids=[asset.id], set_category=True, category_id=child.id
            ), self.db, self.auth)
        self.assertEqual(raised.exception.status_code, 422)

    def test_tag_in_inactive_group_cannot_receive_new_assignments(self) -> None:
        ensure_default_taxonomy(self.db, self.auth.tenant_id)
        self.db.commit()
        asset = self._asset("material-inactive-group")
        tag = self.db.query(VideoTag).filter_by(
            tenant_id="tenant-1", scope="public", status="active"
        ).first()
        group = self.db.query(VideoTagGroup).filter_by(id=tag.group_id).one()
        group.status = "inactive"
        self.db.commit()

        with self.assertRaises(HTTPException) as raised:
            update_assets(AssetTaxonomyUpdate(
                asset_ids=[asset.id], add_tag_ids=[tag.id]
            ), self.db, self.auth)
        self.assertEqual(raised.exception.status_code, 422)

    def test_asset_detail_is_tenant_scoped(self) -> None:
        asset = self._asset("material-detail")
        serialized = get_asset(asset.id, self.db, self.auth)
        self.assertEqual(serialized["id"], asset.id)

        other_tenant = AuthContext("owner-2", "tenant-2", "owner", "other@example.com", "其他企业")
        with self.assertRaises(HTTPException) as raised:
            get_asset(asset.id, self.db, other_tenant)
        self.assertEqual(raised.exception.status_code, 404)

    def test_asset_taxonomy_update_requires_library_permission(self) -> None:
        ensure_default_taxonomy(self.db, self.auth.tenant_id)
        self.db.commit()
        asset = self._asset("material-permission")
        child = self.db.query(VideoCategory).filter_by(
            tenant_id="tenant-1", object_type="material", status="active"
        ).filter(VideoCategory.parent_id.is_not(None)).first()

        with self.assertRaises(HTTPException) as raised:
            update_assets(AssetTaxonomyUpdate(
                asset_ids=[asset.id], set_category=True, category_id=child.id
            ), self.db, self.other_auth)
        self.assertEqual(raised.exception.status_code, 403)

        permitted = AuthContext(
            "member-2", "tenant-1", "member", "member@example.com", "测试企业",
            permissions=("finished_assets.access",),
        )
        update_assets(AssetTaxonomyUpdate(
            asset_ids=[asset.id], set_category=True, category_id=child.id
        ), self.db, permitted)
        self.db.refresh(asset)
        self.assertEqual(asset.category_id, child.id)


if __name__ == "__main__":
    unittest.main()
