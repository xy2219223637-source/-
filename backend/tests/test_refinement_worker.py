from __future__ import annotations

from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from app.config import get_settings
from app.services.refinement_queue import refinement_worker_state, require_refinement_worker


class RefinementWorkerTest(unittest.TestCase):
    def test_override_inherits_global_switch_only_when_unset(self):
        for shared, override, expected in ((False,None,False),(True,None,True),(False,True,True),(True,False,False)):
            with self.subTest(shared=shared, override=override):
                settings = get_settings().model_copy(update={"run_background_workers":shared,"run_refinement_worker":override})
                self.assertEqual(settings.refinement_worker_enabled,expected)

    def test_disabled_and_dead_workers_reject_new_work(self):
        for enabled, alive, stopping, expected in ((False,True,False,"disabled"),(True,False,False,"stopped"),(True,True,True,"stopped"),(True,True,False,"running")):
            with self.subTest(expected=expected, stopping=stopping), \
                 patch("app.config.get_settings",return_value=SimpleNamespace(refinement_worker_enabled=enabled)), \
                 patch("app.services.refinement_queue._thread",MagicMock(is_alive=MagicMock(return_value=alive))), \
                 patch("app.services.refinement_queue._stop",MagicMock(is_set=MagicMock(return_value=stopping))):
                state = refinement_worker_state()
                self.assertEqual(state["status"],expected)
                if expected == "running":
                    require_refinement_worker()
                else:
                    with self.assertRaises(HTTPException) as caught:
                        require_refinement_worker()
                    self.assertEqual(caught.exception.status_code,503)
                    self.assertIn("无需重复提交",caught.exception.detail)

    def test_startup_can_enable_only_refinement_or_exclude_it(self):
        from app import main

        for shared, override, refine in ((False,None,False),(False,True,True),(True,None,True),(True,False,False)):
            with self.subTest(shared=shared, override=override), ExitStack() as stack:
                settings = get_settings().model_copy(update={"run_database_init":False,"run_startup_recovery":False,"run_background_workers":shared,"run_refinement_worker":override})
                stack.enter_context(patch.object(main,"settings",settings))
                names = ("start_worker","start_video_analysis_worker","start_refinement_worker","start_qianchuan_worker","start_video_repair_worker","init_db","recover_startup_tasks")
                calls = {name:stack.enter_context(patch.object(main,name)) for name in names}
                main.on_startup()
                for name, call in calls.items():
                    expected = refine if name == "start_refinement_worker" else shared if name.startswith("start_") else False
                    self.assertEqual(call.call_count,int(expected),name)
