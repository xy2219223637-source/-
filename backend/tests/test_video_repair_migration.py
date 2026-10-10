from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine, inspect

from app.database import Base
from app.services.video_repair.migration import REPAIR_TABLE_NAMES, migrate_video_repair


class VideoRepairMigrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "repair.db"
        self.engine = create_engine("sqlite:///" + str(self.path))
        startup = [table for table in Base.metadata.sorted_tables
                   if not table.info.get("explicit_migration_required")]
        Base.metadata.create_all(self.engine, tables=startup)

    def tearDown(self):
        self.engine.dispose()
        self.directory.cleanup()

    def test_migration_is_explicit_idempotent_and_preserves_base_tables(self):
        self.assertTrue(REPAIR_TABLE_NAMES.isdisjoint(inspect(self.engine).get_table_names()))
        first = migrate_video_repair(self.engine)
        second = migrate_video_repair(self.engine)
        self.assertEqual(first["status"], "migrated")
        self.assertEqual(second["status"], "already_current")
        self.assertEqual(set(first["created_tables"]), REPAIR_TABLE_NAMES)
        self.assertEqual(first["preserved_row_counts"], second["preserved_row_counts"])
        self.assertEqual(len(list(self.path.parent.glob("repair.db.video-repair-*.bak"))), 1)


if __name__ == "__main__":
    unittest.main()
