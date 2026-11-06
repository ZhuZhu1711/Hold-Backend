"""异常反馈上游 POST。HTTP 全部 mock，不连库。"""
from __future__ import annotations

import json
import unittest
from io import BytesIO
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from flask import Flask

from app.config import abnormal_feedback_profile
from app.controllers.abnormal_feedback_ctrl import submit_abnormal_feedback
from app.controllers.dispose_ctrl import (
    DISPOSE_SAMPLE_DONE,
    dispose_record,
    get_pending_records,
)
from app.routes.abnormal_feedback_routes import abnormal_feedback_bp
from app.utils.abnormal_feedback import (
    REQUEST_TIMEOUT,
    SUPPLIER_NAME,
    build_abnormal_feedback_body,
    post_abnormal_feedback,
)


PROFILES = {
    'test': {
        'url': 'http://test.example/abnormal',
        'api_key': 'test-key',
    },
    'release': {
        'url': 'http://release.example/abnormal',
        'api_key': 'release-key',
    },
}


class _Response:
    def __init__(self, raw, status=200):
        self._raw = raw if isinstance(raw, bytes) else raw.encode('utf-8')
        self.status = status

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class BuildBodyTest(unittest.TestCase):
    def test_skeleton_supplier_and_empty_fields(self):
        body = build_abnormal_feedback_body({'lotId': 'SHOULD_IGNORE'})
        self.assertEqual(body['supplierName'], SUPPLIER_NAME)
        self.assertEqual(body['supplierName'], 'GCZJ')
        for key in ('processName', 'priority', 'reporterName', 'gcContact', 'productName'):
            self.assertEqual(body[key], '')
        abnormal = body['abnormals'][0]
        self.assertEqual(abnormal['abnormalType'], '')
        lot = abnormal['lots'][0]
        self.assertEqual(lot['waferIds'], [])
        for key in (
            'lotId', 'device', 'lotType', 'holdReason', 'startTime', 'endTime',
            'holdStage', 'holdType', 'holdTime', 'route', 'ruleCode',
        ):
            self.assertEqual(lot[key], '')


class ProfileTest(unittest.TestCase):
    def test_env_selects_url_and_key(self):
        with patch.dict('app.config.ABNORMAL_FEEDBACK_PROFILES', PROFILES, clear=True):
            test_profile = abnormal_feedback_profile({'HOLD_ENV': ''})
            release_profile = abnormal_feedback_profile({'HOLD_ENV': 'release'})
        self.assertEqual(test_profile['url'], 'http://test.example/abnormal')
        self.assertEqual(test_profile['api_key'], 'test-key')
        self.assertEqual(release_profile['url'], 'http://release.example/abnormal')
        self.assertEqual(release_profile['api_key'], 'release-key')


class PostTest(unittest.TestCase):
    def _profiles(self):
        return patch.dict('app.config.ABNORMAL_FEEDBACK_PROFILES', PROFILES, clear=True)

    def test_posts_test_profile(self):
        captured = {}

        def fake_open(req, timeout=None):
            captured['req'] = req
            captured['timeout'] = timeout
            return _Response(b'{"code":200,"data":"NCMR_261104_002"}', 200)

        with self._profiles(), patch(
            'app.utils.abnormal_feedback.urllib.request.urlopen', fake_open,
        ):
            ok, status, msg, data = post_abnormal_feedback(environ={'HOLD_ENV': 'test'})

        req = captured['req']
        sent = json.loads(req.data.decode('utf-8'))
        self.assertTrue(ok)
        self.assertEqual(status, 200)
        self.assertEqual(msg, '已提交上游')
        self.assertEqual(req.full_url, 'http://test.example/abnormal')
        self.assertEqual(req.get_header('X-api-key'), 'test-key')
        self.assertEqual(req.get_method(), 'POST')
        self.assertEqual(captured['timeout'], REQUEST_TIMEOUT)
        self.assertEqual(sent['supplierName'], 'GCZJ')
        self.assertEqual(data['upstream_status'], 200)
        self.assertEqual(data['ncmr_no'], 'NCMR_261104_002')
        self.assertEqual(data['upstream_body']['code'], 200)
        self.assertNotIn('test-key', msg)
        self.assertNotIn('test-key', json.dumps(data))

    def test_posts_release_profile(self):
        captured = {}

        def fake_open(req, timeout=None):
            captured['req'] = req
            return _Response(b'{"code":200,"data":" NCMR_REL "}', 201)

        with self._profiles(), patch(
            'app.utils.abnormal_feedback.urllib.request.urlopen', fake_open,
        ):
            ok, status, msg, data = post_abnormal_feedback(environ={'HOLD_ENV': 'release'})

        self.assertTrue(ok, msg)
        self.assertEqual(status, 200)
        self.assertEqual(captured['req'].full_url, 'http://release.example/abnormal')
        self.assertEqual(captured['req'].get_header('X-api-key'), 'release-key')
        self.assertEqual(data['upstream_status'], 201)
        self.assertEqual(data['ncmr_no'], 'NCMR_REL')
        self.assertNotIn('release-key', json.dumps(data))

    def test_empty_config_does_not_call(self):
        empty = {'test': {'url': '', 'api_key': ''}, 'release': {'url': 'http://x', 'api_key': ''}}
        with patch.dict('app.config.ABNORMAL_FEEDBACK_PROFILES', empty, clear=True), patch(
            'app.utils.abnormal_feedback.urllib.request.urlopen',
        ) as urlopen:
            ok, status, msg, data = post_abnormal_feedback(environ={'HOLD_ENV': 'test'})
            ok_rel, status_rel, msg_rel, data_rel = post_abnormal_feedback(
                environ={'HOLD_ENV': 'release'},
            )
        urlopen.assert_not_called()
        self.assertFalse(ok)
        self.assertEqual(status, 503)
        self.assertIn('未配置', msg)
        self.assertIsNone(data)
        self.assertFalse(ok_rel)
        self.assertEqual(status_rel, 503)
        self.assertIsNone(data_rel)
        self.assertNotIn('http://x', msg_rel)

    def test_upstream_http_error(self):
        err = HTTPError(
            'http://test.example/abnormal',
            400,
            'Bad Request',
            None,
            BytesIO('批次无效'.encode('utf-8')),
        )
        with self._profiles(), patch(
            'app.utils.abnormal_feedback.urllib.request.urlopen', side_effect=err,
        ):
            ok, status, msg, data = post_abnormal_feedback(environ={'HOLD_ENV': 'test'})
        self.assertFalse(ok)
        self.assertEqual(status, 502)
        self.assertIn('400', msg)
        self.assertIn('批次无效', msg)
        self.assertEqual(data['upstream_status'], 400)
        self.assertNotIn('test-key', msg)

    def test_upstream_network_error(self):
        with self._profiles(), patch(
            'app.utils.abnormal_feedback.urllib.request.urlopen',
            side_effect=URLError('refused'),
        ):
            ok, status, msg, data = post_abnormal_feedback(environ={'HOLD_ENV': 'test'})
        self.assertFalse(ok)
        self.assertEqual(status, 502)
        self.assertIn('refused', msg)
        self.assertIsNone(data)
        self.assertNotIn('test-key', msg)

    def test_business_code_is_not_success(self):
        with self._profiles(), patch(
            'app.utils.abnormal_feedback.urllib.request.urlopen',
            return_value=_Response(b'{"code":500,"data":"NCMR_X"}', 200),
        ):
            ok, status, msg, data = post_abnormal_feedback(environ={'HOLD_ENV': 'test'})
        self.assertFalse(ok)
        self.assertEqual(status, 502)
        self.assertIn('code=500', msg)
        self.assertNotIn('ncmr_no', data)

    def test_empty_data_is_not_success(self):
        with self._profiles(), patch(
            'app.utils.abnormal_feedback.urllib.request.urlopen',
            return_value=_Response(b'{"code":200,"data":"  "}', 200),
        ):
            ok, status, msg, data = post_abnormal_feedback(environ={'HOLD_ENV': 'test'})
        self.assertFalse(ok)
        self.assertEqual(status, 502)
        self.assertIn('未返回反馈单号', msg)
        self.assertNotIn('ncmr_no', data)


def _make_app():
    app = Flask(__name__)
    app.secret_key = 'test-abnormal-feedback'
    app.config['TESTING'] = True
    app.config['HOLD_API_TOKEN'] = ''
    app.register_blueprint(abnormal_feedback_bp)
    return app


class RouteTest(unittest.TestCase):
    def test_requires_login(self):
        client = _make_app().test_client()
        resp = client.post('/api/abnormal_feedback', json={})
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.get_json()['code'], 401)

    def test_logged_in_forwards(self):
        app = _make_app()
        client = app.test_client()
        with patch(
            'app.routes.abnormal_feedback_routes.submit_abnormal_feedback',
            return_value=(True, 200, '已提交上游', {
                'hold_record_id': 12,
                'ncmr_no': 'NCMR_261104_002',
            }),
        ) as submit:
            with client.session_transaction() as sess:
                sess['user_id'] = 1
                sess['must_change_password'] = False
            resp = client.post('/api/abnormal_feedback', json={'hold_record_id': 12})
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body['code'], 200)
        self.assertEqual(body['msg'], '已提交上游')
        self.assertEqual(body['data']['ncmr_no'], 'NCMR_261104_002')
        submit.assert_called_once_with({'hold_record_id': 12})


def _record_row(status=0, ncmr=None, rid=12):
    row = MagicMock()
    row._mapping = {'ID': rid, 'STATUS': status, 'NCMR_NO': ncmr}
    return row


class SubmitFeedbackTest(unittest.TestCase):
    def _db(self, row=None, rowcount=1):
        select = MagicMock()
        select.fetchone.return_value = row
        update = MagicMock()
        update.rowcount = rowcount
        db = MagicMock()
        db.session.execute.side_effect = [select, update]
        return db

    @patch('app.controllers.abnormal_feedback_ctrl.post_abnormal_feedback')
    @patch('app.controllers.abnormal_feedback_ctrl.resolve_hold_record_table', return_value='FT_HOLD_RECORD_TEST')
    @patch('app.controllers.abnormal_feedback_ctrl.db')
    def test_saves_ticket_on_success(self, db, _table, post):
        db.session.execute.side_effect = self._db(_record_row()).session.execute.side_effect
        post.return_value = (True, 200, '已提交上游', {'ncmr_no': 'NCMR_261104_002'})
        ok, status, msg, data = submit_abnormal_feedback({'hold_record_id': 12})
        self.assertTrue(ok, msg)
        self.assertEqual(status, 200)
        self.assertEqual(data['ncmr_no'], 'NCMR_261104_002')
        self.assertEqual(data['hold_record_id'], 12)
        update_sql = str(db.session.execute.call_args_list[1].args[0])
        update_params = db.session.execute.call_args_list[1].args[1]
        self.assertIn('FT_HOLD_RECORD_TEST', update_sql)
        self.assertIn('NCMR_NO', update_sql)
        self.assertEqual(update_params['ncmr_no'], 'NCMR_261104_002')
        self.assertEqual(update_params['rid'], 12)
        db.session.commit.assert_called_once()
        post.assert_called_once()

    @patch('app.controllers.abnormal_feedback_ctrl.post_abnormal_feedback')
    @patch('app.controllers.abnormal_feedback_ctrl.resolve_hold_record_table', return_value='FT_HOLD_RECORD_TEST')
    @patch('app.controllers.abnormal_feedback_ctrl.db')
    def test_upstream_failure_does_not_write(self, db, _table, post):
        db.session.execute.return_value.fetchone.return_value = _record_row()
        post.return_value = (False, 502, '上游连接失败: refused', None)
        ok, status, msg, data = submit_abnormal_feedback({'hold_record_id': 12})
        self.assertFalse(ok)
        self.assertEqual(status, 502)
        self.assertEqual(db.session.execute.call_count, 1)
        db.session.commit.assert_not_called()
        self.assertIsNone(data)

    @patch('app.controllers.abnormal_feedback_ctrl.post_abnormal_feedback')
    @patch('app.controllers.abnormal_feedback_ctrl.resolve_hold_record_table', return_value='FT_HOLD_RECORD_TEST')
    @patch('app.controllers.abnormal_feedback_ctrl.db')
    def test_duplicate_does_not_call_upstream(self, db, _table, post):
        db.session.execute.return_value.fetchone.return_value = _record_row(ncmr='NCMR_OLD')
        ok, status, msg, _data = submit_abnormal_feedback({'hold_record_id': 12})
        self.assertFalse(ok)
        self.assertEqual(status, 409)
        self.assertIn('已反馈上游', msg)
        post.assert_not_called()
        db.session.commit.assert_not_called()

    @patch('app.controllers.abnormal_feedback_ctrl.post_abnormal_feedback')
    @patch('app.controllers.abnormal_feedback_ctrl.db')
    def test_missing_record(self, db, post):
        db.session.execute.return_value.fetchone.return_value = None
        ok, status, msg, _data = submit_abnormal_feedback({'hold_record_id': '8'})
        self.assertFalse(ok)
        self.assertEqual(status, 404)
        self.assertIn('不存在', msg)
        post.assert_not_called()

    @patch('app.controllers.abnormal_feedback_ctrl.post_abnormal_feedback')
    def test_invalid_id(self, post):
        ok, status, msg, data = submit_abnormal_feedback({})
        self.assertFalse(ok)
        self.assertEqual(status, 400)
        self.assertIn('hold_record_id', msg)
        self.assertIsNone(data)
        post.assert_not_called()

    @patch('app.controllers.abnormal_feedback_ctrl.post_abnormal_feedback')
    @patch('app.controllers.abnormal_feedback_ctrl.db')
    def test_already_disposed_does_not_call_upstream(self, db, post):
        db.session.execute.return_value.fetchone.return_value = _record_row(status=1)
        ok, status, msg, _data = submit_abnormal_feedback({'hold_record_id': 12})
        self.assertFalse(ok)
        self.assertEqual(status, 400)
        self.assertIn('仅未处置', msg)
        post.assert_not_called()

    @patch('app.controllers.abnormal_feedback_ctrl.post_abnormal_feedback')
    @patch('app.controllers.abnormal_feedback_ctrl.resolve_hold_record_table', return_value='FT_HOLD_RECORD_TEST')
    @patch('app.controllers.abnormal_feedback_ctrl.db')
    def test_update_miss_logs_ticket(self, db, _table, post):
        select = MagicMock()
        select.fetchone.return_value = _record_row()
        update = MagicMock()
        update.rowcount = 0
        db.session.execute.side_effect = [select, update]
        post.return_value = (True, 200, '已提交上游', {'ncmr_no': 'NCMR_261104_002'})
        with self.assertLogs('app.controllers.abnormal_feedback_ctrl', level='ERROR') as logs:
            ok, status, msg, data = submit_abnormal_feedback({'hold_record_id': 12})
        self.assertFalse(ok)
        self.assertEqual(status, 500)
        self.assertEqual(data['ncmr_no'], 'NCMR_261104_002')
        self.assertIn('NCMR_261104_002', '\n'.join(logs.output))
        self.assertIn('12', '\n'.join(logs.output))
        db.session.commit.assert_not_called()
        db.session.rollback.assert_called_once()


class DisposeBlockTest(unittest.TestCase):
    @patch('app.controllers.dispose_ctrl.db')
    @patch('app.controllers.dispose_ctrl._load_record', return_value={'NCMR_NO': 'NCMR_261104_002', 'STATUS': 0})
    def test_engineer_dispose_rejected(self, _load, db):
        ok, msg, data = dispose_record(12, 1, 2, 1)
        self.assertFalse(ok)
        self.assertIsNone(data)
        self.assertIn('等待上游指令', msg)
        db.session.execute.assert_not_called()
        db.session.commit.assert_not_called()

    @patch('app.controllers.dispose_ctrl.db')
    @patch('app.controllers.dispose_ctrl._load_record', return_value={'NCMR_NO': 'NCMR_261104_002', 'STATUS': 0})
    def test_sample_done_rejected(self, _load, db):
        ok, msg, data = dispose_record(12, DISPOSE_SAMPLE_DONE, 181, 9)
        self.assertFalse(ok)
        self.assertIsNone(data)
        self.assertIn('等待上游指令', msg)
        db.session.commit.assert_not_called()

    @patch('app.controllers.dispose_ctrl.db')
    def test_pending_excludes_feedback_records(self, db):
        db.session.execute.return_value.scalar.return_value = 0
        db.session.execute.return_value.fetchall.return_value = []
        ok, msg, payload = get_pending_records(owner_id=3)
        self.assertTrue(ok, msg)
        self.assertEqual(payload.get('total'), 0)
        sqls = [str(call.args[0]) for call in db.session.execute.call_args_list]
        self.assertTrue(sqls)
        for sql in sqls:
            self.assertIn('r.NCMR_NO IS NULL', sql)
            self.assertIn('NVL(r.STATUS, 0) <> :closed', sql)
