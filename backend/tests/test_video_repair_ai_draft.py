import unittest

from fastapi import HTTPException

from app.api.video_repair import _validate_ai_draft


class AiDraftDecisionTests(unittest.TestCase):
    def test_accepts_ai_action_and_required_insert_position(self):
        visual = {"ref_type": "VIDEO_ASSET", "ref_id": 31}
        template = {"type": "CONTENT_INSERT", "duration_ms": 3000, "visual": visual}
        diagnosis = {"ai_proposal": {
            "action_template": template, "requires_input": ["insert_position", "audio"],
        }}
        _validate_ai_draft(diagnosis, {
            **template, "insert_at_ms": 6000, "audio": {"attachment_id": 4},
        }, "")

    def test_rejects_manual_action_and_material_changes(self):
        template = {"type": "VISUAL_REPLACE", "target_range": {"start_ms": 6000, "end_ms": 10000},
                    "material": {"ref_type": "VIDEO_ASSET", "ref_id": 31}}
        diagnosis = {"ai_proposal": {"action": template, "requires_input": []}}
        for action, reason in [
            ({**template, "material": {"ref_type": "VIDEO_ASSET", "ref_id": 32}}, ""),
            ({**template, "type": "CONTENT_INSERT"}, "人工修改"),
            (template, "人工修改"),
        ]:
            with self.subTest(action=action, reason=reason), self.assertRaises(HTTPException):
                _validate_ai_draft(diagnosis, action, reason)

    def test_rejects_draft_without_ai_proposal(self):
        with self.assertRaises(HTTPException):
            _validate_ai_draft({}, {"type": "VISUAL_REPLACE"}, "")

    def test_accepts_confirmed_range_only_when_ai_requests_it(self):
        template = {"type": "AUDIO_REPLACE"}
        action = {**template, "target_range": {"start_ms": 1000, "end_ms": 3000},
                  "audio": {"attachment_id": 4}}
        _validate_ai_draft({"ai_proposal": {"action_template": template,
                            "requires_input": ["target_range", "audio"]}}, action, "")
        with self.assertRaises(HTTPException):
            _validate_ai_draft({"ai_proposal": {"action_template": template,
                                "requires_input": ["audio"]}}, action, "")


if __name__ == "__main__":
    unittest.main()
