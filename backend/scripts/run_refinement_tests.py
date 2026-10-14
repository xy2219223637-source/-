"""Run refinement regressions without deployment settings or credentials."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest


def main() -> int:
    backend = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(backend))
    from app.config import Settings

    Settings.model_config["env_file"] = None
    for field in Settings.model_fields:
        os.environ.pop(field.upper(), None)
    patterns = sys.argv[1:] or ["test_refinement*.py", "test_video_refinement.py"]
    with tempfile.TemporaryDirectory(prefix="refinement-test-bootstrap-") as directory:
        os.environ.update(
            DATABASE_URL="sqlite:///" + (Path(directory) / "bootstrap.db").as_posix(),
            REQUIRE_POSTGRES="false", RUN_DATABASE_INIT="false",
            RUN_STARTUP_RECOVERY="false", RUN_BACKGROUND_WORKERS="false",
            LLM_MOCK_MODE="true", STORAGE_CACHE_DIR=str(Path(directory) / "cache"),
        )
        suite = unittest.TestSuite()
        for pattern in patterns:
            suite.addTests(unittest.defaultTestLoader.discover(str(backend / "tests"), pattern=pattern))
        try:
            result = unittest.TextTestRunner(verbosity=1).run(suite)
            return 0 if result.wasSuccessful() else 1
        finally:
            from app.database import engine
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
