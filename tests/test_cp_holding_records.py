"""CP Hold 列表：测试表、未关闭过滤、角色（不连库）。"""
from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import patch

from flask import Flask

from app.config import cp_hold_tables, is_test_env, Config
from app.controllers.cp_hold_ctrl import DISPOSE_CLOSE, get_cp_holding_records
from app.routes.cp_routes import cp_bp
from app.utils.auth_decorators import (
    ROLE_CP_PRODUCTION,
    ROLE_ENGINEER,
    ROLE_PRODUCTION,
    ROLE_QUALITY,
    ROLE_ROOT,
)


def _sqls(db):
    return [str(call.args[0]) for call in db.session.execute.call_args_list]


def _params(db, index=0):
    args = db.session.execute.call_args_list[index].args
    if len(args) > 1:
        return args[1]
    return db.session.execute.call_args_list[index].kwargs.get('params') or {}


class _Row:
    def __init__(self, mapping):
        self._mapping = mapping


class CpTableNameTest(unittest.TestCase):
    def test_test_and_release_tables_match(self):
        self.assertEqual(
            cp_hold_tables(True),
            ('CP_HOLD_RECORD', 'CP_CIRCULATION_HISTORY'),
        )
        self.assertEqual(cp_hold_tables(False), cp_hold_tables(True))

    def test_current_config_matches_env(self):
        record, circ = cp_hold_tables(is_test_env())
        self.assertEqual(Config.CP_HOLD_RECORD_TABLE, record)
        self.assertEqual(Config.CP_CIRCULATION_TABLE, circ)
        self.assertEqual(Config.HOLD_INFO_TABLE, 'FT_HOLD_INFO')
        self.assertEqual(Config.HOLD_RECORD_TABLE, 'FT_HOLD_RECORD')
        self.assertEqual(Config.CIRCULATION_HISTORY_TABLE, 'CIRCULATION_HISTORY')
        self.assertEqual(Config.HOLD_PREDICT_TABLE, 'FT_HOLD_PREDICT')


@patch(
    'app.controllers.cp_hold_ctrl.Config.CP_CIRCULATION_TABLE',
    'CP_CIRCULATION_HISTORY_TEST',
)
@patch(
    'app.controllers.cp_hold_ctrl.Config.CP_HOLD_RECORD_TABLE',
    'CP_HOLD_RECORD_TEST',
)
class GetCpHoldingRecordsSqlTest(unittest.TestCase):
    def _row(self):
        return _Row({
            'ID': 9,
            'PRODUCT_ID': 'CP-1',
            'STATION': 'CP1',
            'EQUIP_ID': 'E1',
            'LOT_ID': 'LOT9',
            'WAFER_ID': '#01#02',
            'HOLD_CODE': '027',
            'HOLD_REASON': 'yield',
            'GRADE_NUM': 'A:1',
            'STATUS': 0,
            'HOLD_DTTM': datetime(2026, 10, 28, 8, 30, 0),
            'LAST_DISPOSE': 0,
        })

    @patch('app.controllers.cp_hold_ctrl.db')
    def test_lists_open_cp_records_from_test_tables(self, db):
        db.session.execute.return_value.scalar.return_value = 1
        db.session.execute.return_value.fetchall.return_value = [self._row()]
        ok, msg, payload = get_cp_holding_records(
            product_id='cp',
            keyword='lot',
            page=1,
            page_size=20,
        )
        self.assertTrue(ok, msg)
        sqls = _sqls(db)
        self.assertEqual(len(sqls), 2)
        for sql in sqls:
            self.assertIn('CP_HOLD_RECORD_TEST', sql)
            self.assertIn('CP_CIRCULATION_HISTORY_TEST', sql)
            self.assertIn('NVL(r.STATUS, 0) <> :closed', sql)
            self.assertNotIn('RECORD_TYPE', sql)
            self.assertNotIn('PRO_ENG_ID', sql)
            self.assertNotIn('FT_HOLD', sql)
            self.assertNotIn('.AREA', sql)
        self.assertIn('ORDER BY r.HOLD_DTTM DESC NULLS LAST', sqls[1])
        self.assertEqual(_params(db).get('closed'), DISPOSE_CLOSE)
        self.assertEqual(_params(db).get('product_id'), 'cp')
        self.assertIn('r.PRODUCT_ID = :product_id', sqls[0])
        self.assertNotIn('LIKE UPPER(:product_id)', sqls[0])
        self.assertEqual(_params(db).get('keyword'), '%lot%')
        item = payload['items'][0]
        self.assertEqual(item['id'], 9)
        self.assertEqual(item['lot_id'], 'LOT9')
        self.assertEqual(item['wafer_id'], '#01#02')
        self.assertEqual(item['hold_dttm'], '2026-10-28 08:30:00')
        self.assertEqual(item['last_dispose'], 0)
        self.assertEqual(item['last_dispose_label'], '创建')
        self.assertEqual(payload['total'], 1)

    @patch('app.controllers.cp_hold_ctrl.db')
    def test_without_filters_has_no_keyword_bind(self, db):
        db.session.execute.return_value.scalar.return_value = 0
        db.session.execute.return_value.fetchall.return_value = []
        ok, msg, payload = get_cp_holding_records()
        self.assertTrue(ok, msg)
        self.assertEqual(payload['total'], 0)
        params = _params(db)
        self.assertNotIn('product_id', params)
        self.assertNotIn('keyword', params)
        for sql in _sqls(db):
            self.assertNotIn(':keyword', sql)
            self.assertNotIn(':product_id', sql)


def _client(role):
    app = Flask(__name__)
    app.secret_key = 'test-cp-holding'
    app.config['TESTING'] = True
    app.register_blueprint(cp_bp)
    client = app.test_client()
    if role is None:
        return client
    with client.session_transaction() as sess:
        sess['user_id'] = 8
        sess['user_name'] = 'cp'
        sess['role'] = role
        sess['must_change_password'] = False
    return client


class CpHoldingRouteTest(unittest.TestCase):
    def _ok_payload(self):
        return (
            True,
            '获取成功',
            {'items': [{'id': 1}], 'total': 1, 'page': 1, 'page_size': 20, 'pages': 1},
        )

    def test_anonymous_is_401(self):
        resp = _client(None).get('/cp/api/holding_records')
        self.assertEqual(resp.status_code, 401)

    def test_engineer_is_403(self):
        resp = _client(ROLE_ENGINEER).get('/cp/api/holding_records')
        self.assertEqual(resp.status_code, 403)

    def test_quality_and_production_are_403(self):
        for role in (ROLE_QUALITY, ROLE_PRODUCTION):
            resp = _client(role).get('/cp/api/holding_records')
            self.assertEqual(resp.status_code, 403, role)

    @patch('app.routes.cp_routes.cp_hold_ctrl.get_cp_holding_records')
    def test_cp_role_is_200(self, get_records):
        get_records.return_value = self._ok_payload()
        resp = _client(ROLE_CP_PRODUCTION).get(
            '/cp/api/holding_records?product_id=P&keyword=L&page=2&page_size=50'
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body['code'], 200)
        self.assertEqual(body['data'][0]['id'], 1)
        self.assertEqual(body['total'], 1)
        kwargs = get_records.call_args.kwargs
        self.assertEqual(kwargs.get('product_id'), 'P')
        self.assertEqual(kwargs.get('keyword'), 'L')
        self.assertEqual(kwargs.get('page'), '2')
        self.assertEqual(kwargs.get('page_size'), '50')

    @patch('app.routes.cp_routes.cp_hold_ctrl.get_cp_holding_records')
    def test_root_is_200(self, get_records):
        get_records.return_value = self._ok_payload()
        resp = _client(ROLE_ROOT).get('/cp/api/holding_records')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()['code'], 200)


if __name__ == '__main__':
    unittest.main()
