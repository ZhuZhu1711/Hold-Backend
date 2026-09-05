"""手提 Hold / 附件 FTP 下架（不连 Oracle / FTP）。"""
from __future__ import annotations

import unittest

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


class ManualHoldTakenDownCtrlTest(unittest.TestCase):
    def test_create_returns_taken_down(self):
        ok, msg, data = manual_hold_ctrl.create_manual_hold({'line': 'FT'})
        self.assertFalse(ok)
        self.assertEqual(msg, manual_hold_ctrl.TAKEN_DOWN_MSG)
        self.assertIsNone(data)

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

    def test_create_api_410(self):
        resp = self.client.post('/admin/hold/api/manual_hold', json={'line': 'FT'})
        self.assertEqual(resp.status_code, 410)
        body = resp.get_json()
        self.assertEqual(body['code'], 410)
        self.assertEqual(body['msg'], manual_hold_ctrl.TAKEN_DOWN_MSG)

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
