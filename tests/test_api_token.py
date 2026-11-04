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
            sess['must_change_password'] = False
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
            sess['must_change_password'] = False
        resp = client.get('/api/eng-only')
        self.assertEqual(resp.status_code, 200)

    def test_legacy_session_without_flag_must_change(self):
        app = _make_app('correct-token')
        client = app.test_client()
        with client.session_transaction() as sess:
            sess['user_id'] = 12
            sess['role'] = ROLE_ENGINEER
        resp = client.get('/api/need-login')
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(resp.get_json()['data']['must_change_password'])

    def test_session_non_engineer_still_403_without_token(self):
        app = _make_app('correct-token')
        client = app.test_client()
        with client.session_transaction() as sess:
            sess['user_id'] = 99
            sess['role'] = 9
            sess['must_change_password'] = False
        resp = client.get('/api/eng-only')
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.get_json()['code'], 403)

    def test_must_change_blocks_json_api(self):
        app = _make_app('correct-token')
        client = app.test_client()
        with client.session_transaction() as sess:
            sess['user_id'] = 12
            sess['role'] = ROLE_ENGINEER
            sess['must_change_password'] = True
        resp = client.get('/api/need-login')
        self.assertEqual(resp.status_code, 403)
        body = resp.get_json()
        self.assertEqual(body['code'], 403)
        self.assertTrue(body['data']['must_change_password'])

    def test_token_bypasses_must_change(self):
        app = _make_app('correct-token')
        client = app.test_client()
        with client.session_transaction() as sess:
            sess['user_id'] = 12
            sess['role'] = ROLE_ENGINEER
            sess['must_change_password'] = True
        resp = client.get('/api/need-login', headers={'X-Hold-Token': 'correct-token'})
        self.assertEqual(resp.status_code, 200)

    def test_no_header_is_401(self):
        client = _make_app('correct-token').test_client()
        resp = client.get('/api/need-login')
        self.assertEqual(resp.status_code, 401)


class ApiTokenEnvNameTest(unittest.TestCase):
    def test_unset_reads_debug_token(self):
        from app.config import hold_api_token_env_name, hold_env, load_hold_api_token

        self.assertEqual(hold_env({}), 'test')
        self.assertEqual(hold_api_token_env_name({}), 'HOLD_API_TOKEN_DEBUG')
        name, value = load_hold_api_token(
            environ={'HOLD_API_TOKEN': 'prod-secret', 'HOLD_API_TOKEN_DEBUG': 'dbg-secret'},
        )
        self.assertEqual(name, 'HOLD_API_TOKEN_DEBUG')
        self.assertEqual(value, 'dbg-secret')

    def test_release_reads_hold_api_token(self):
        from app.config import hold_api_token_env_name, hold_env, is_test_env, load_hold_api_token

        environ = {
            'HOLD_ENV': 'release',
            'HOLD_API_TOKEN': 'prod-secret',
            'HOLD_API_TOKEN_DEBUG': 'dbg-secret',
        }
        self.assertEqual(hold_env(environ), 'release')
        self.assertFalse(is_test_env(environ))
        self.assertEqual(hold_api_token_env_name(environ), 'HOLD_API_TOKEN')
        name, value = load_hold_api_token(environ=environ)
        self.assertEqual(name, 'HOLD_API_TOKEN')
        self.assertEqual(value, 'prod-secret')

    def test_test_env_reads_hold_api_token_debug(self):
        from app.config import hold_api_token_env_name, is_test_env, load_hold_api_token

        environ = {
            'HOLD_ENV': 'test',
            'HOLD_API_TOKEN': 'prod-secret',
            'HOLD_API_TOKEN_DEBUG': 'dbg-secret',
        }
        self.assertTrue(is_test_env(environ))
        self.assertEqual(hold_api_token_env_name(environ), 'HOLD_API_TOKEN_DEBUG')
        name, value = load_hold_api_token(environ=environ)
        self.assertEqual(name, 'HOLD_API_TOKEN_DEBUG')
        self.assertEqual(value, 'dbg-secret')

    def test_test_env_does_not_fallback_to_prod_token(self):
        from app.config import load_hold_api_token

        name, value = load_hold_api_token(
            environ={'HOLD_ENV': 'debug', 'HOLD_API_TOKEN': 'prod-secret'},
        )
        self.assertEqual(name, 'HOLD_API_TOKEN_DEBUG')
        self.assertEqual(value, '')

    def test_db_profile_follows_hold_env(self):
        from app.config import DB_PROFILES, db_profile, oracle_dsn, oracle_sqlalchemy_uri

        test_profile = db_profile({})
        release_profile = db_profile({'HOLD_ENV': 'release'})
        self.assertIs(test_profile, DB_PROFILES['test'])
        self.assertIs(release_profile, DB_PROFILES['release'])
        self.assertIn(test_profile['host'], oracle_dsn(test_profile))
        self.assertIn(test_profile['service_name'], oracle_sqlalchemy_uri(test_profile))
        self.assertIn(release_profile['user'], oracle_sqlalchemy_uri(release_profile))

    def test_unknown_hold_env_does_not_select_release(self):
        from app.config import hold_env

        for raw in ('staging', 'prod', 'production'):
            with self.assertRaises(SystemExit):
                hold_env({'HOLD_ENV': raw})


if __name__ == '__main__':
    unittest.main()
