"""固定 Header Token 与 Session Cookie 双通道。"""
from __future__ import annotations

import unittest

from flask import Flask, jsonify, session

from app.config import Config
from app.utils.auth_decorators import (
    ROLE_ENGINEER,
    engineer_required,
    login_required,
)


def _make_app(token=''):
    app = Flask(__name__)
    app.secret_key = 'test-api-token-secret'
    app.config['HOLD_API_TOKEN'] = token
    app.config['TESTING'] = True

    @app.route('/api/need-login')
    @login_required
    def need_login():
        return jsonify({
            'code': 200,
            'user_id': session.get('user_id'),
            'role': session.get('role'),
            'user_name': session.get('user_name'),
        })

    @app.route('/api/eng-only')
    @engineer_required
    def eng_only():
        return jsonify({
            'code': 200,
            'user_id': session.get('user_id'),
            'role': session.get('role'),
        })

    return app


class ApiTokenAuthTest(unittest.TestCase):
    def test_empty_config_rejects_any_header(self):
        client = _make_app('').test_client()
        resp = client.get('/api/need-login', headers={'X-Hold-Token': 'whatever'})
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.get_json()['code'], 401)

    def test_wrong_token_is_401(self):
        client = _make_app('correct-token').test_client()
        resp = client.get('/api/need-login', headers={'X-Hold-Token': 'wrong-token'})
        self.assertEqual(resp.status_code, 401)

    def test_matching_token_passes_login_required(self):
        client = _make_app('correct-token').test_client()
        resp = client.get('/api/need-login', headers={'X-Hold-Token': 'correct-token'})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data['user_id'], Config.SYSTEM_USER_ID)
        self.assertEqual(data['role'], 0)
        self.assertEqual(data['user_name'], 'API_TOKEN')

    def test_matching_token_skips_engineer_role(self):
        client = _make_app('correct-token').test_client()
        resp = client.get('/api/eng-only', headers={'X-Hold-Token': 'correct-token'})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data['user_id'], Config.SYSTEM_USER_ID)
        self.assertEqual(data['role'], 0)

    def test_session_without_token_still_works(self):
        app = _make_app('correct-token')
        client = app.test_client()
        with client.session_transaction() as sess:
            sess['user_id'] = 12
            sess['role'] = ROLE_ENGINEER
            sess['user_name'] = '张三'
        resp = client.get('/api/need-login')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data['user_id'], 12)
        self.assertEqual(data['role'], ROLE_ENGINEER)

    def test_session_engineer_can_call_eng_only(self):
        app = _make_app('correct-token')
        client = app.test_client()
        with client.session_transaction() as sess:
            sess['user_id'] = 12
            sess['role'] = ROLE_ENGINEER
        resp = client.get('/api/eng-only')
        self.assertEqual(resp.status_code, 200)

    def test_session_non_engineer_still_403_without_token(self):
        app = _make_app('correct-token')
        client = app.test_client()
        with client.session_transaction() as sess:
            sess['user_id'] = 99
            sess['role'] = 9
        resp = client.get('/api/eng-only')
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.get_json()['code'], 403)

    def test_no_header_is_401(self):
        client = _make_app('correct-token').test_client()
        resp = client.get('/api/need-login')
        self.assertEqual(resp.status_code, 401)


if __name__ == '__main__':
    unittest.main()
