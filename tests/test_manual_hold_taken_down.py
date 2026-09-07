"""手提 Hold：创建 API 已恢复；页面 / 列表 / 附件下载仍下架（不连 Oracle / FTP）。"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from flask import Flask

from app.controllers import manual_hold_ctrl
from app.routes.common_data_routes import common_data_bp
from app.routes.engineer_routes import engineer_bp
from app.routes.hold_report_routes import hold_report_bp
from app.routes.production_routes import production_bp
from app.utils.auth_decorators import ROLE_ENGINEER, ROLE_PRODUCTION, ROLE_ROOT


def _make_client():
    app = Flask(__name__)
    app.secret_key = 'test-manual-hold-down'
    app.config['TESTING'] = True
    app.register_blueprint(hold_report_bp)
    app.register_blueprint(engineer_bp)
    app.register_blueprint(production_bp)
    app.register_blueprint(common_data_bp)
    return app.test_client()


class ManualHoldCreateRestoredCtrlTest(unittest.TestCase):
    @patch.object(
        manual_hold_ctrl,
        'resolve_manual_product_id',
        return_value=(False, '缺少必填字段: product_id'),
    )
    def test_create_not_taken_down(self, _resolve):
        ok, msg, data = manual_hold_ctrl.create_manual_hold({'line': 'FT'})
        self.assertFalse(ok)
        self.assertNotEqual(msg, manual_hold_ctrl.TAKEN_DOWN_MSG)
        self.assertIsNone(data)
        _resolve.assert_called_once()


class ManualHoldTakenDownCtrlTest(unittest.TestCase):
    def test_annex_image_closed(self):
        ok, msg, data = manual_hold_ctrl.get_annex_image(1, 0)
        self.assertFalse(ok)
        self.assertEqual(msg, manual_hold_ctrl.ANNEX_FTP_TAKEN_DOWN_MSG)
        self.assertIsNone(data)

    def test_annex_zip_closed(self):
        ok, msg, data = manual_hold_ctrl.get_annex_zip(1)
        self.assertFalse(ok)
        self.assertEqual(msg, manual_hold_ctrl.ANNEX_FTP_TAKEN_DOWN_MSG)

    def test_products_closed(self):
        ok, msg, data = manual_hold_ctrl.list_manual_hold_products('FT')
        self.assertFalse(ok)
        self.assertEqual(data, [])

    def test_recent_closed(self):
        ok, msg, data = manual_hold_ctrl.list_recent_manual_holds()
        self.assertFalse(ok)
        self.assertEqual(data, [])

    def test_normalize_still_validates(self):
        ok, msg, rec = manual_hold_ctrl.normalize_manual_hold({})
        self.assertFalse(ok)
        self.assertIsNone(rec)
        self.assertNotEqual(msg, manual_hold_ctrl.TAKEN_DOWN_MSG)


class ManualHoldTakenDownRouteTest(unittest.TestCase):
    def setUp(self):
        self.client = _make_client()
        with self.client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['user_name'] = 'root'
            sess['role'] = ROLE_ROOT
            sess['must_change_password'] = False

    @patch('app.routes.hold_report_routes.manual_hold_ctrl.create_manual_hold')
    def test_create_api_200(self, mock_create):
        mock_create.return_value = (True, '创建成功', {'ID': 88})
        resp = self.client.post('/admin/hold/api/manual_hold', json={'line': 'FT'})
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body['code'], 200)
        self.assertEqual(body['msg'], '创建成功')
        self.assertEqual(body['data']['ID'], 88)
        mock_create.assert_called_once()

    @patch('app.routes.hold_report_routes.manual_hold_ctrl.create_manual_hold')
    def test_create_api_400(self, mock_create):
        mock_create.return_value = (False, '缺少必填字段: product_id', None)
        resp = self.client.post('/admin/hold/api/manual_hold', json={'line': 'FT'})
        self.assertEqual(resp.status_code, 400)
        body = resp.get_json()
        self.assertEqual(body['code'], 400)
        self.assertIn('缺少', body['msg'])

    def test_create_api_unauthenticated(self):
        client = _make_client()
        resp = client.post('/admin/hold/api/manual_hold', json={'line': 'FT'})
        self.assertEqual(resp.status_code, 401)

    def test_annex_image_410(self):
        resp = self.client.get('/admin/hold/api/annex_image?record_id=1')
        self.assertEqual(resp.status_code, 410)
        self.assertEqual(resp.get_json()['msg'], manual_hold_ctrl.ANNEX_FTP_TAKEN_DOWN_MSG)

    def test_annex_zip_410(self):
        resp = self.client.get('/admin/hold/api/annex_zip?record_id=1')
        self.assertEqual(resp.status_code, 410)
        self.assertEqual(resp.get_json()['msg'], manual_hold_ctrl.ANNEX_FTP_TAKEN_DOWN_MSG)

    def test_products_410(self):
        resp = self.client.get('/admin/hold/api/manual_hold/products?line=FT')
        self.assertEqual(resp.status_code, 410)

    def test_recent_410(self):
        resp = self.client.get('/admin/hold/api/manual_hold/recent')
        self.assertEqual(resp.status_code, 410)

    def test_admin_page_410(self):
        resp = self.client.get('/admin/hold/manual')
        self.assertEqual(resp.status_code, 410)


class EngProdManualPageTakenDownTest(unittest.TestCase):
    def test_eng_manual_410(self):
        client = _make_client()
        with client.session_transaction() as sess:
            sess['user_id'] = 12
            sess['user_name'] = 'eng'
            sess['role'] = ROLE_ENGINEER
            sess['must_change_password'] = False
        resp = client.get('/eng/manual')
        self.assertEqual(resp.status_code, 410)

    def test_prod_manual_410(self):
        client = _make_client()
        with client.session_transaction() as sess:
            sess['user_id'] = 99
            sess['user_name'] = 'prod'
            sess['role'] = ROLE_PRODUCTION
            sess['must_change_password'] = False
        resp = client.get('/prod/manual')
        self.assertEqual(resp.status_code, 410)


class FtpProbeKeptTest(unittest.TestCase):
    def test_ftp_status_route_registered(self):
        app = Flask(__name__)
        app.secret_key = 'test-ftp-probe'
        app.register_blueprint(common_data_bp)
        rules = {rule.rule for rule in app.url_map.iter_rules()}
        self.assertIn('/api/common_data/ftp/status', rules)


if __name__ == '__main__':
    unittest.main()
