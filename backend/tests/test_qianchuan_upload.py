"""Upload state machine regression tests, synthetic MP4 bytes and no external requests."""
import hashlib
import json
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from test_qianchuan_auth import QianchuanAuthorizationTests, PREFIX
from app.models import QianchuanUpload as Upload, QianchuanAccountBinding as Binding, VideoAsset, QianchuanConnection as Connection
from app.services.qianchuan.uploads import UploadService

A06 = '/open_api/2/file/video/ad/'
A07 = '/open_api/v1.0/qianchuan/video/get/'
A10 = '/open_api/v1.0/qianchuan/report/uni_promotion/config/get/'
A11 = '/open_api/v1.0/qianchuan/report/uni_promotion/data/get/'


class UploadTests(QianchuanAuthorizationTests):
    def setUp(self):
        super().setUp()
        self.bytes = b'\x00\x00\x00\x18ftypisom' + b'synthetic-video' * 40
        self.path = Path(self.directory.name) / 'test.mp4'
        self.path.write_bytes(self.bytes)
        self.signature = hashlib.md5(self.bytes).hexdigest()
        with self.sessions() as db:
            db.get(VideoAsset, 1).content_hash = hashlib.sha256(self.bytes).hexdigest()
            db.get(VideoAsset, 1).status = 'analyzed'
            db.commit()
        _, self.connection_id = self.completed()
        self.service.sync_connection(self.connection_id)
        with self.sessions() as db:
            self.binding_id = db.query(Binding).one().id
        self.uploads = UploadService(self.service, lambda url: self.path)
        self.custom[A06] = lambda r: self.envelope({'material_id': 9007199254740995,
            'video_id': 'v-test-1', 'video_signature': self.signature})
        self.custom[A07] = lambda r: self.envelope({'list': [{'material_id': 9007199254740995,
            'id': 'v-test-1', 'signature': self.signature}], 'page_info': {'page': 1, 'total_page': 1}})

    def create_upload(self, headers=None, **extra):
        return self.browser.post(PREFIX + '/uploads', headers=headers or self.headers, json={
            'video_asset_id': 1, 'binding_id': self.binding_id, 'filename': 'test.mp4', 'is_aigc': True, **extra})

    def stored(self, upload_id):
        with self.sessions() as db:
            return self.uploads.view(db.get(Upload, upload_id))

    def test_upload_multipart_verified_and_persistent(self):
        response = self.create_upload(headers=self.member_headers)
        self.assertEqual(response.status_code, 202, response.text)
        upload_id = response.json()['id']
        self.assertEqual(self.call_count(A06), 0)  # HTTP creation does not download or send.
        self.uploads.run_next()
        row = self.stored(upload_id)
        self.assertEqual(row['status'], 'succeeded')
        self.assertEqual(row['material_id'], '9007199254740995')
        request = next(r for r in self.calls if r.url.path == A06)
        body = request.content
        for expected in (self.bytes, self.signature.encode(), b'UPLOAD_BY_FILE', b'name="is_aigc"\r\n\r\ntrue', b'9007199254740993'):
            self.assertIn(expected, body)
        self.assertIn('multipart/form-data; boundary=', request.headers['content-type'])
        query = [r for r in self.calls if r.url.path == A07][-1]
        self.assertEqual(json.loads(query.url.params['filtering']), {'material_ids': [9007199254740995]})
        result = self.browser.get(PREFIX + '/uploads?video_asset_id=1', headers=self.headers)
        self.assertEqual(result.json()[0]['status'], 'succeeded')
        self.assertNotIn('source_url', result.json()[0])

    def test_upload_duplicate_create_and_worker_claim(self):
        first, second = self.create_upload(), self.create_upload()
        self.assertEqual(first.json()['id'], second.json()['id'])
        self.uploads.send(first.json()['id']); self.uploads.send(first.json()['id'])
        self.assertEqual(self.call_count(A06), 1)
        self.assertEqual(self.create_upload().json()['status'], 'succeeded')

    def test_upload_tenant_and_input_boundaries(self):
        self.assertEqual(self.create_upload(headers=self.other_headers).status_code, 404)
        self.assertEqual(self.create_upload(video_asset_id=2).status_code, 404)
        self.assertEqual(self.create_upload(video_asset_id=3).status_code, 404)
        self.assertEqual(self.create_upload(binding_id='00000000-0000-0000-0000-000000000000').status_code, 409)
        self.assertEqual(self.create_upload(is_aigc='yes').status_code, 422)
        self.assertEqual(self.create_upload(filename='../secret.mp4').status_code, 400)
        row = self.create_upload().json()
        self.assertEqual(self.browser.get(PREFIX + '/uploads?video_asset_id=1', headers=self.other_headers).status_code, 404)
        self.assertEqual(self.browser.post(PREFIX + '/uploads/' + row['id'] + '/retry', headers=self.other_headers).status_code, 404)
        self.assertEqual(self.call_count(A06), 0)

    def test_upload_source_changed_never_sends(self):
        upload_id = self.create_upload().json()['id']
        self.path.write_bytes(self.bytes + b'changed')
        self.uploads.send(upload_id)
        self.assertEqual(self.stored(upload_id)['status'], 'failed')
        self.assertEqual(self.call_count(A06), 0)

    def test_removed_connection_preserves_uploaded_material_history(self):
        upload_id = self.create_upload().json()['id']
        self.uploads.send(upload_id)
        response = self.browser.post(PREFIX + '/connections/' + self.connection_id + '/remove', headers=self.headers)
        self.assertEqual(response.status_code, 204)
        rows = self.browser.get(PREFIX + '/uploads?video_asset_id=1', headers=self.headers).json()
        self.assertEqual(rows[0]['status'], 'succeeded')
        self.assertEqual(rows[0]['material_id'], '9007199254740995')
        self.assertEqual(self.create_upload().status_code, 409)

    def test_upload_disabled_before_send(self):
        upload_id = self.create_upload().json()['id']
        self.browser.delete(PREFIX + '/connections/' + self.connection_id, headers=self.headers)
        self.uploads.send(upload_id)
        self.assertEqual(self.call_count(A06), 0)
        self.assertEqual(self.stored(upload_id)['status'], 'failed')

    def test_upload_timeout_queries_without_resending(self):
        def timeout(r): raise httpx.ReadTimeout('secret-must-not-leak')
        self.custom[A06] = timeout
        upload_id = self.create_upload().json()['id']
        self.uploads.send(upload_id)
        self.assertEqual(self.stored(upload_id)['status'], 'unknown')
        self.assertNotIn('secret-must-not-leak', self.stored(upload_id)['error_message'])
        self.uploads.verify(upload_id)
        self.assertEqual(self.stored(upload_id)['status'], 'succeeded')
        query = next(r for r in self.calls if r.url.path == A07)
        self.assertEqual(json.loads(query.url.params['filtering']), {'signatures': [self.signature]})
        self.assertEqual(self.call_count(A06), 1)

    def test_upload_success_query_denied_keeps_remote_ids(self):
        original_query = self.custom[A07]
        self.custom[A07] = lambda r: (httpx.Response(200, json={'code': 40000, 'message': 'mock-access'})
                                     if self.call_count(A06) else original_query(r))
        upload_id = self.create_upload().json()['id']
        self.uploads.send(upload_id)
        self.uploads.verify(upload_id); self.uploads.verify(upload_id)
        row = self.stored(upload_id)
        self.assertEqual(row['status'], 'uploaded')
        self.assertEqual(row['material_id'], '9007199254740995')
        self.assertTrue(row['can_verify']); self.assertFalse(row['auto_pending'])
        self.browser.post(PREFIX + '/uploads/' + upload_id + '/retry', headers=self.headers)
        self.uploads.run_next()
        self.assertEqual(self.call_count(A06), 1)

    def test_upload_connection_disabled_during_send_preserves_ids(self):
        def sent(r):
            self.browser.delete(PREFIX + '/connections/' + self.connection_id, headers=self.headers)
            return self.envelope({'material_id': 9007199254740995, 'video_id': 'v-test-1'})
        self.custom[A06] = sent
        upload_id = self.create_upload().json()['id']
        self.uploads.send(upload_id)
        self.assertEqual(self.stored(upload_id)['status'], 'uploaded')
        self.assertEqual(self.stored(upload_id)['material_id'], '9007199254740995')
        self.assertEqual(self.call_count(A07), 1)  # Only the preflight, before disabling.

    def test_upload_preflight_account_permission_blocks_write(self):
        self.custom[A07] = lambda r: httpx.Response(200, json={'code': 40002,
            'message': 'No permission to operate account 200'})
        upload_id = self.create_upload().json()['id']
        self.uploads.send(upload_id)
        row = self.stored(upload_id)
        self.assertEqual(row['status'], 'failed')
        self.assertIn('无权操作', row['error_message'])
        self.assertEqual(self.call_count(A06), 0)

    def test_upload_restart_preparing_vs_uploading(self):
        upload_id = self.create_upload().json()['id']
        self.uploads.update(upload_id, status='running', phase='preparing')
        self.uploads.recover()
        self.assertEqual(self.stored(upload_id)['status'], 'queued')
        self.uploads.update(upload_id, status='running', phase='uploading', signature=self.signature)
        self.uploads.recover()
        self.assertEqual(self.stored(upload_id)['status'], 'unknown')
        self.uploads.run_next()
        self.assertEqual(self.stored(upload_id)['status'], 'succeeded')
        self.assertEqual(self.call_count(A06), 0)

    def test_upload_unrelated_query_result_not_success(self):
        self.custom[A07] = lambda r: self.envelope({'list': [{'material_id': 123, 'id': 'other',
            'signature': 'wrong'}], 'page_info': {'page': 1, 'total_page': 1}})
        upload_id = self.create_upload().json()['id']
        self.uploads.send(upload_id)
        self.assertEqual(self.stored(upload_id)['status'], 'uploaded')

    def test_upload_expiring_credentials_require_scope_refresh(self):
        upload_id = self.create_upload().json()['id']
        with self.sessions() as db:
            db.get(Connection, self.connection_id).access_token_expires_at = datetime.utcnow()
            db.commit()
        self.uploads.send(upload_id)
        self.assertEqual(self.call_count(A06), 0)
        with self.sessions() as db:
            self.assertEqual(db.get(Connection, self.connection_id).status, 'sync_pending')
        self.service.sync_connection(self.connection_id)
        self.browser.post(PREFIX + '/uploads/' + upload_id + '/retry', headers=self.headers)
        self.uploads.run_next()
        self.assertEqual(self.stored(upload_id)['status'], 'succeeded')

    def report_config(self):
        return {"custom_config_datas": [{
            "data_topic": "OVERALL_ROI_PRODUCT_MATERIAL",
            "dimensions": [
                {"field": "material_id", "name": "素材ID"},
                {"field": "roi2_material_type_v3", "name": "素材类型"},
            ],
            "metrics": [
                {"field": "stat_cost_for_roi2", "name": "整体消耗"},
                {"field": "video_play_count_for_roi2_v2", "name": "视频播放数"},
                {"field": "video_play_duration_3s_rate_for_roi2", "name": "3秒播放率"},
            ],
        }]}

    def test_report_uses_uploaded_scope_and_normalizes_metrics(self):
        upload_id = self.create_upload().json()['id']
        self.uploads.send(upload_id)
        self.custom[A10] = lambda r: self.envelope(self.report_config())
        self.custom[A11] = lambda r: self.envelope({
            "page_info": {"page": 1, "page_size": 10, "total_number": 1, "total_page": 1},
            "rows": [{"dimensions": {"material_id": 9007199254740995}, "metrics": {
                "stat_cost_for_roi2": "12.34", "video_play_count_for_roi2_v2": 56,
                "video_play_duration_3s_rate_for_roi2": "0.42"}}],
        })
        response = self.browser.get(PREFIX + f'/uploads/{upload_id}/report?days=7', headers=self.member_headers)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result['status'], 'available')
        self.assertEqual(result['material_id'], '9007199254740995')
        self.assertEqual(result['metrics']['stat_cost_for_roi2'], {"label": "整体消耗", "value": "12.34"})
        self.assertEqual(result['metrics']['video_play_count_for_roi2_v2']['value'], '56')
        self.assertEqual(result['per_second']['status'], 'unavailable')
        config_request = next(r for r in self.calls if r.url.path == A10)
        self.assertEqual(json.loads(config_request.url.params['data_topics']), ['OVERALL_ROI_PRODUCT_MATERIAL'])
        data_request = next(r for r in self.calls if r.url.path == A11)
        self.assertEqual(json.loads(data_request.url.params['dimensions']), ['material_id'])
        self.assertEqual(json.loads(data_request.url.params['filters']), [
            {"field": "material_id", "operator": 7, "values": ["9007199254740995"]},
            {"field": "roi2_material_type_v3", "operator": 7, "values": ["3"]},
        ])
        self.assertNotIn('mock-access', response.text)

    def test_report_empty_tenant_and_state_boundaries(self):
        upload_id = self.create_upload().json()['id']
        self.assertEqual(self.browser.get(PREFIX + f'/uploads/{upload_id}/report',
            headers=self.other_headers).status_code, 404)
        self.assertEqual(self.browser.get(PREFIX + f'/uploads/{upload_id}/report',
            headers=self.headers).status_code, 409)
        self.uploads.send(upload_id)
        self.custom[A10] = lambda r: self.envelope(self.report_config())
        self.custom[A11] = lambda r: self.envelope({
            "page_info": {"page": 1, "page_size": 10, "total_number": 0, "total_page": 0}, "rows": []})
        response = self.browser.get(PREFIX + f'/uploads/{upload_id}/report?days=30', headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result['status'], 'no_data')
        self.assertIsNone(result['metrics']['stat_cost_for_roi2']['value'])
        self.assertEqual(self.browser.get(PREFIX + f'/uploads/{upload_id}/report?days=181',
            headers=self.headers).status_code, 422)

    def test_report_falls_back_only_when_account_has_no_overall_capability(self):
        upload_id = self.create_upload().json()['id']
        self.uploads.send(upload_id)
        def config(request):
            topic = json.loads(request.url.params['data_topics'])[0]
            if topic == 'OVERALL_ROI_PRODUCT_MATERIAL':
                return httpx.Response(200, json={'code': 40000,
                    'message': '当前账户暂无乘方投放能力，无法获取对应数据', 'request_id': 'test-no-overall'})
            self.assertEqual(topic, 'SITE_PROMOTION_PRODUCT_POST_DATA_VIDEO')
            data = self.report_config()
            data['custom_config_datas'][0]['data_topic'] = topic
            data['custom_config_datas'][0]['dimensions'] = [
                {'field': 'material_id'}, {'field': 'roi2_material_video_name', 'is_required': True}]
            return self.envelope(data)
        self.custom[A10] = config
        self.custom[A11] = lambda r: self.envelope({
            'page_info': {'page': 1, 'total_page': 1},
            'rows': [{'dimensions': {'material_id': '9007199254740995', 'roi2_material_video_name': 'test'},
                      'metrics': {'stat_cost_for_roi2': '12.34'}}]})
        result = self.browser.get(PREFIX + f'/uploads/{upload_id}/report', headers=self.headers)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['data_topic'], 'SITE_PROMOTION_PRODUCT_POST_DATA_VIDEO')
        self.assertEqual(result.json()['metrics']['stat_cost_for_roi2']['value'], '12.34')
        self.assertEqual(self.call_count(A10), 2)
        self.assertEqual(self.call_count(A11), 1)
        request = next(r for r in self.calls if r.url.path == A11)
        self.assertEqual(json.loads(request.url.params['dimensions']), ['material_id', 'roi2_material_video_name'])
        self.assertEqual(json.loads(request.url.params['filters']), [
            {'field': 'material_id', 'operator': 7, 'values': ['9007199254740995']}])

    def test_report_does_not_fallback_on_other_platform_errors(self):
        upload_id = self.create_upload().json()['id']
        self.uploads.send(upload_id)
        for code in [40000, 40002]:
            with self.subTest(code=code):
                before = self.call_count(A10)
                self.custom[A10] = lambda r: httpx.Response(200, json={
                    'code': code, 'message': 'mock-access private-message', 'request_id': 'safe-request'})
                response = self.browser.get(PREFIX + f'/uploads/{upload_id}/report', headers=self.headers)
                self.assertEqual(response.status_code, 502)
                self.assertEqual(self.call_count(A10), before + 1)
                self.assertNotIn('mock-access', response.text)
                self.assertNotIn('private-message', response.text)
        self.assertEqual(self.call_count(A11), 0)

    def test_report_fallback_stops_when_connection_disabled(self):
        upload_id = self.create_upload().json()['id']
        self.uploads.send(upload_id)
        def reject_and_disable(request):
            self.browser.delete(PREFIX + '/connections/' + self.connection_id, headers=self.headers)
            return httpx.Response(200, json={'code': 40000,
                'message': '当前账户暂无乘方投放能力，无法获取对应数据'})
        self.custom[A10] = reject_and_disable
        response = self.browser.get(PREFIX + f'/uploads/{upload_id}/report', headers=self.headers)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.call_count(A10), 1)
        self.assertEqual(self.call_count(A11), 0)


def load_tests(loader, tests, pattern):
    # Reuse the auth fixture without executing inherited auth tests twice.
    return unittest.TestSuite(UploadTests(name) for name in UploadTests.__dict__ if name.startswith('test_'))
