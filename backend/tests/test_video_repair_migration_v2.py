from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine, inspect

from app.database import Base
from app.services.video_repair.migration import migrate_video_repair
from app.services.video_repair.migration_v2 import migrate_video_repair_v2


class VideoRepairMigrationV2Tests(unittest.TestCase):
    def test_current_schema_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "repair-v2.db"
            engine = create_engine("sqlite:///" + str(path))
            startup = [table for table in Base.metadata.sorted_tables
                       if not table.info.get("explicit_migration_required")]
            Base.metadata.create_all(engine, tables=startup)
            migrate_video_repair(engine)
            first = migrate_video_repair_v2(engine)
            second = migrate_video_repair_v2(engine)
            self.assertEqual(first["status"], "already_current")
            self.assertEqual(second["status"], "already_current")
            columns = {item["name"]: item for item in inspect(engine).get_columns("video_repair_tasks")}
            self.assertFalse(columns["input_id"]["nullable"])
            self.assertTrue(columns["review_feedback_id"]["nullable"])
            self.assertTrue(columns["base_plan_version_id"]["nullable"])
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
