"""get_holding_records SQL：系统未关单即可列出（不连库）。"""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from flask import Flask

from app.controllers.dispose_ctrl import DISPOSE_CLOSE
from app.controllers.hold_report_ctrl import get_holding_records
from app.routes.hold_report_routes import hold_report_bp
from app.utils.auth_decorators import ROLE_ROOT


def _sqls(db):
    return [str(call.args[0]) for call in db.session.execute.call_args_list]


def _params(db, index=0):
    args = db.session.execute.call_args_list[index].args
    if len(args) > 1:
        return args[1]
    return db.session.execute.call_args_list[index].kwargs.get('params') or {}


class GetHoldingRecordsSqlTest(unittest.TestCase):
    def _run(self, db, **kwargs):
        db.session.execute.return_value.scalar.return_value = 0
        db.session.execute.return_value.fetchall.return_value = []
        ok, msg, payload = get_holding_records(**kwargs)
        self.assertTrue(ok, msg)
        self.assertEqual(payload.get('total'), 0)
        return _sqls(db), _params(db)

    @patch(
        'app.controllers.hold_report_ctrl._table_names',
        return_value=(
            'FT_HOLD_INFO_TEST',
            'FT_HOLD_RECORD_TEST',
            'CIRCULATION_HISTORY_TEST',
            'HOLD_RECORD_ID',
        ),
    )
    @patch('app.controllers.hold_report_ctrl.db')
    def test_lists_unclosed_even_if_mes_unheld(self, db, _tables):
        sqls, params = self._run(db, current_owner_id=3)
        self.assertTrue(sqls)
        for sql in sqls:
            self.assertIn('NVL(r.STATUS, 0) <> :closed', sql)
            self.assertNotIn('i.ID IS NOT NULL', sql)
            self.assertNotIn('NVL(r.SOURCE, 0) = 1', sql)
            self.assertIn('AND NVL(i.HOLDING, 1) = 0', sql)
            self.assertIn('c.NEXT_OWNER_ID = :current_owner_id', sql)
        self.assertEqual(params.get('closed'), DISPOSE_CLOSE)
        self.assertEqual(params.get('current_owner_id'), 3)

    @patch(
        'app.controllers.hold_report_ctrl._table_names',
        return_value=(
            'FT_HOLD_INFO_TEST',
            'FT_HOLD_RECORD_TEST',
            'CIRCULATION_HISTORY_TEST',
            'HOLD_RECORD_ID',
        ),
    )
    @patch('app.controllers.hold_report_ctrl.db')
    def test_engineer_pending_filters_owner_and_current(self, db, _tables):
        sqls, params = self._run(db, owner_eng_id=3, current_owner_id=3)
        for sql in sqls:
            self.assertIn('p.PRO_ENG_ID = :owner_eng_id', sql)
            self.assertIn('c.NEXT_OWNER_ID = :current_owner_id', sql)
            self.assertIn('NVL(r.STATUS, 0) <> :closed', sql)
        self.assertEqual(params.get('owner_eng_id'), 3)
        self.assertEqual(params.get('current_owner_id'), 3)


def _root_client():
    template_dir = os.path.abspath(
        os.path.join(os.path.dirname(__file__), '..', 'app', 'templates')
    )
    app = Flask(__name__, template_folder=template_dir)
    app.secret_key = 'test-holding-engineer'
    app.config['TESTING'] = True
    app.register_blueprint(hold_report_bp)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['user_name'] = 'root'
        sess['role'] = ROLE_ROOT
        sess['must_change_password'] = False
    return client


class RootEngineerPendingRouteTest(unittest.TestCase):
    def setUp(self):
        self.client = _root_client()

    def test_page_has_engineer_filter(self):
        resp = self.client.get('/admin/hold/holding')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('id="engineer_id"', html)
        self.assertIn('不限工程师待办', html)

    @patch('app.routes.hold_report_routes.hold_report_ctrl.get_holding_records')
    def test_engineer_id_filters_pending(self, get_holding):
        get_holding.return_value = (
            True, 'ok',
            {'items': [], 'total': 0, 'page': 1, 'page_size': 20, 'pages': 1},
        )
        resp = self.client.get('/admin/hold/api/holding_records?engineer_id=7')
        self.assertEqual(resp.status_code, 200)
        kwargs = get_holding.call_args.kwargs
        self.assertEqual(kwargs.get('owner_eng_id'), 7)
        self.assertEqual(kwargs.get('current_owner_id'), 7)

    @patch('app.routes.hold_report_routes.hold_report_ctrl.get_holding_records')
    def test_without_engineer_id_lists_all(self, get_holding):
        get_holding.return_value = (
            True, 'ok',
            {'items': [], 'total': 0, 'page': 1, 'page_size': 20, 'pages': 1},
        )
        resp = self.client.get('/admin/hold/api/holding_records')
        self.assertEqual(resp.status_code, 200)
        kwargs = get_holding.call_args.kwargs
        self.assertIsNone(kwargs.get('owner_eng_id'))
        self.assertIsNone(kwargs.get('current_owner_id'))

    def test_invalid_engineer_id(self):
        resp = self.client.get('/admin/hold/api/holding_records?engineer_id=abc')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('无效', resp.get_json()['msg'])

    @patch('app.routes.hold_report_routes.hold_report_ctrl.export_holding_records_xlsx')
    def test_export_uses_same_engineer_filter(self, export_xlsx):
        export_xlsx.return_value = (True, 'ok', b'PK')
        resp = self.client.get('/admin/hold/api/holding_records/export?engineer_id=7')
        self.assertEqual(resp.status_code, 200)
        kwargs = export_xlsx.call_args.kwargs
        self.assertEqual(kwargs.get('owner_eng_id'), 7)
        self.assertEqual(kwargs.get('current_owner_id'), 7)

    @patch('app.routes.hold_report_routes.user_ctrl.list_engineers')
    def test_engineer_options(self, list_engineers):
        list_engineers.return_value = (
            True, '获取成功',
            [{'id': 7, 'employee_no': 'E007', 'name': '王工'}],
        )
        resp = self.client.get('/admin/hold/api/engineers')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()['data']
        self.assertEqual(data[0]['id'], 7)
        self.assertEqual(data[0]['name'], '王工')
        self.assertNotIn('password', data[0])


if __name__ == '__main__':
    unittest.main()
