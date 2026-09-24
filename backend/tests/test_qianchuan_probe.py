"""Offline tests for the read-only capability probe; no real credentials or network."""
from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace

from scripts.qianchuan_probe_readonly import (
    capability_conclusion,
    mask,
    opaque_id,
    probe_plan_capabilities,
    response_summary,
)
from app.config import Settings


class FakeClient:
    def __init__(self):
        self.calls = []

    def request(self, method, path, *, token="", params=None, **_kwargs):
        self.calls.append((method, path, token, params or {}))
        if path.endswith("/aweme/authorized/get/"):
            return {"aweme_id_list": [{"aweme_id": "99887766"}], "page_info": {"page": 1}}
        if path.endswith("/product/available/get/"):
            return {"product_list": [], "page_info": {"page": 1}}
        if path.endswith("/uni_promotion/list/"):
            if params["marketing_goal"] == "VIDEO_PROM_GOODS":
                return {"ad_list": [{"ad_info": {"id": 123456789}, "stats_info": {"stat_cost": 1}}],
                        "page_info": {"page": 1}}
            return {"ad_list": [], "page_info": {"page": 1}}
        if path.endswith("/uni_promotion/ad/detail/"):
            return {"ad_info": {"id": 123456789}}
        if path.endswith("/uni_promotion/ad/material/get/"):
            return {"material_list": [], "page_info": {"page": 1}}
        if path.endswith("/material/ad/get/"):
            return {"ad_list": [], "page_info": {"page": 1}}
        raise AssertionError(path)


class QianchuanProbeTests(unittest.TestCase):
    def test_plan_sidecar_flags_default_off(self):
        settings = Settings(_env_file=None)
        self.assertFalse(settings.qianchuan_plan_enabled)
        self.assertFalse(settings.qianchuan_plan_write_enabled)
        self.assertFalse(settings.qianchuan_plan_worker_enabled)

    def test_capability_conclusions_do_not_treat_empty_or_invalid_as_verified(self):
        self.assertEqual(capability_conclusion({"list": [{}]}, "success", "list"), "verified")
        self.assertEqual(capability_conclusion({"list": []}, "success", "list"), "no_sample_data")
        self.assertEqual(capability_conclusion({}, "success", "list"), "contract_uncertain")
        self.assertEqual(capability_conclusion(None, "account_forbidden"), "permission_missing")
        self.assertEqual(capability_conclusion({}, "success", applicable=False), "account_not_applicable")

    def test_summary_contains_only_shape_not_rows(self):
        data = {"list": [{"secret": "do-not-print"}], "page_info": {"page": 1, "total": 1}}
        summary = response_summary(data, "list")
        self.assertEqual(summary["row_count"], 1)
        self.assertEqual(summary["response_keys"], ["list", "page_info"])
        self.assertEqual(summary["pagination_keys"], ["page", "total"])
        self.assertNotIn("do-not-print", json.dumps(summary))

    def test_local_selectors_and_masks_never_echo_full_values(self):
        value = "4f947c61-c13b-4c01-afcb-c5fbb3793ada"
        self.assertEqual(opaque_id(value), value)
        self.assertEqual(mask("12345678"), "****5678")
        with self.assertRaises(ValueError):
            opaque_id("not-a-uuid")

    def test_plan_probe_is_get_only_and_redacts_token_and_platform_ids(self):
        client = FakeClient()
        output = io.StringIO()
        token = "super-secret-access-token"
        advertiser_id = "987654321"
        with redirect_stdout(output):
            probe_plan_capabilities(client, token, advertiser_id,
                                    SimpleNamespace(material_id="777788889999"))
        rendered = output.getvalue()
        self.assertTrue(client.calls)
        self.assertTrue(all(method == "GET" for method, *_rest in client.calls))
        self.assertNotIn(token, rendered)
        self.assertNotIn(advertiser_id, rendered)
        self.assertNotIn("777788889999", rendered)
        self.assertNotIn("123456789", rendered)
        self.assertIn('"conclusion": "verified"', rendered)
        self.assertIn('"conclusion": "no_sample_data"', rendered)


if __name__ == "__main__":
    unittest.main()
