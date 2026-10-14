from datetime import datetime
from types import SimpleNamespace
import unittest

from app.services.ecommerce_video_analysis_cache import _binding


class CurrentAssetBindingTest(unittest.TestCase):
    def setUp(self):
        self.asset = SimpleNamespace(id=7, tenant_id="t1", asset_id="current-public-id",
                                     source="legacy_video:123", content_hash="a" * 64,
                                     duration_seconds=8)
        self.row = SimpleNamespace(asset_id=7, tenant_id="t1", status="completed",
                                   completed_at=datetime.utcnow(), input_video_seconds=8,
                                   prompt_version="video-library-v4-configured-asr",
                                   analysis_json={"schema_version": "1.0", "asset_id": "old-public-id"})

    def test_completed_current_record_does_not_require_old_library_identity(self):
        self.assertEqual(_binding(self.row, self.asset, []),
                         {"method": "current_asset_record", "asset_id": 7})
        self.asset.source = "manual_upload"
        self.assertEqual(_binding(self.row, self.asset, [])['method'], "current_asset_record")

    def test_imported_observation_uses_current_foreign_key(self):
        self.row.prompt_version = "imported-legacy:302"
        self.assertIsNotNone(_binding(self.row, self.asset, []))

    def test_existing_hash_mismatch_is_never_adopted(self):
        self.row.analysis_json["source_hash"] = "b" * 64
        self.assertIsNone(_binding(self.row, self.asset, []))

    def test_adoption_rejects_unverified_records(self):
        cases = [("tenant_id", "t2"), ("asset_id", 8), ("status", "failed"),
                 ("completed_at", None), ("input_video_seconds", 9),
                 ("prompt_version", "refinement-asr")]
        for field, value in cases:
            with self.subTest(field=field):
                original = getattr(self.row, field)
                setattr(self.row, field, value)
                self.assertIsNone(_binding(self.row, self.asset, []))
                setattr(self.row, field, original)


if __name__ == "__main__":
    unittest.main()
