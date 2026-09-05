"""改密逻辑（mock，不连库）。"""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from app.controllers.user_ctrl import change_password
from app.models.user import User
from app.routes.auth_routes import auth_bp
from app.utils.auth_decorators import MUST_CHANGE_ALLOWED_ENDPOINTS
from app.utils.web_sso import reset_used_tickets


_APP_DIR = Path(__file__).resolve().parents[1] / 'app'


def _auth_app():
    app = Flask(__name__, template_folder=str(_APP_DIR / 'templates'))
    app.secret_key = 'test-change-password-page'
    app.config['TESTING'] = True
    app.register_blueprint(auth_bp)
    return app


def _user_with_password(employee_no: str, password: str) -> User:
    user = User()
    user.ID = 7
    user.EMPLOYEE_NO = employee_no
    user.NAME = '张三'
    user.ROLE = 1
    user.MUST_CHANGE_PWD = 1
    user.set_password(password)
    return user


class ChangePasswordTest(unittest.TestCase):
    def test_missing_user_id(self):
        ok, msg = change_password(None, 'old', 'Hold26')
        self.assertFalse(ok)
        self.assertEqual(msg, '请先登录')

    def test_empty_new_password(self):
        ok, msg = change_password(1, 'old', '')
        self.assertFalse(ok)
        self.assertEqual(msg, '请填写新密码')

    @patch('app.controllers.user_ctrl.db')
    @patch('app.controllers.user_ctrl.User')
    def test_wrong_old_password(self, UserMock, _db):
        UserMock.query.get.return_value = _user_with_password('A0001', 'Hold26')
        ok, msg = change_password(7, 'wrong', 'Hold27')
        self.assertFalse(ok)
        self.assertEqual(msg, '原密码错误')

    @patch('app.controllers.user_ctrl.db')
    @patch('app.controllers.user_ctrl.User')
    def test_weak_new_password(self, UserMock, _db):
        UserMock.query.get.return_value = _user_with_password('A0001', 'Hold26')
        ok, msg = change_password(7, 'Hold26', 'abc123')
        self.assertFalse(ok)
        self.assertIn('过于简单', msg)

    @patch('app.controllers.user_ctrl.db')
    @patch('app.controllers.user_ctrl.User')
    def test_same_as_old(self, UserMock, db_mock):
        UserMock.query.get.return_value = _user_with_password('A0001', 'Hold26')
        ok, msg = change_password(7, 'Hold26', 'Hold26')
        self.assertFalse(ok)
        self.assertIn('不能与原密码相同', msg)
        db_mock.session.commit.assert_not_called()

    @patch('app.controllers.user_ctrl.db')
    @patch('app.controllers.user_ctrl.User')
    def test_success_clears_flag(self, UserMock, db_mock):
        user = _user_with_password('A0001', 'Hold26')
        UserMock.query.get.return_value = user
        ok, msg = change_password(7, 'Hold26', 'Hold27')
        self.assertTrue(ok, msg)
        self.assertEqual(user.MUST_CHANGE_PWD, 0)
        self.assertTrue(user.check_password('Hold27'))
        db_mock.session.commit.assert_called_once()

    @patch('app.controllers.user_ctrl.User')
    def test_user_missing(self, UserMock):
        UserMock.query.get.return_value = None
        ok, msg = change_password(99, 'Hold26', 'Hold27')
        self.assertFalse(ok)
        self.assertEqual(msg, '用户不存在')


class ChangePasswordPageTest(unittest.TestCase):
    def setUp(self):
        reset_used_tickets()
        self.app = _auth_app()
        self.client = self.app.test_client()

    def _login_session(self, **extra):
        with self.client.session_transaction() as sess:
            sess['user_id'] = 7
            sess['user_name'] = '张三'
            sess['employee_no'] = 'A0001'
            sess['role'] = 1
            sess['must_change_password'] = True
            sess.update(extra)

    def test_page_shows_user_name_and_employee_no(self):
        self._login_session()
        resp = self.client.get('/change-password')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('用户名', html)
        self.assertIn('工号', html)
        self.assertIn('张三', html)
        self.assertIn('A0001', html)

    def test_web_sso_ticket_allowed_when_must_change(self):
        self.assertIn('auth.api_web_sso_ticket', MUST_CHANGE_ALLOWED_ENDPOINTS)
        self._login_session()
        resp = self.client.post('/api/web-sso-ticket')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body['code'], 200)
        url = body['data']['url']
        self.assertIn('/web-sso?ticket=', url)


class ApiLoginMustChangeTest(unittest.TestCase):
    def setUp(self):
        reset_used_tickets()
        from app.utils.rate_limit import reset_login_limiters
        reset_login_limiters()
        self.app = _auth_app()
        self.client = self.app.test_client()

    @patch('app.routes.auth_routes.user_ctrl.login_logic')
    def test_login_includes_change_password_url(self, mock_login):
        mock_login.return_value = (True, '登录成功', {
            'id': 7,
            'name': '张三',
            'role': 1,
            'employee_no': 'A0001',
            'must_change_password': True,
        })
        resp = self.client.post('/api/login', json={
            'employee_no': 'A0001',
            'password': 'x',
        })
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body['code'], 200)
        data = body['data']
        self.assertTrue(data['must_change_password'])
        self.assertEqual(data['redirect'], '/change-password')
        self.assertIn('/web-sso?ticket=', data['change_password_url'])

    @patch('app.routes.auth_routes.user_ctrl.login_logic')
    def test_normal_login_has_no_change_password_url(self, mock_login):
        mock_login.return_value = (True, '登录成功', {
            'id': 7,
            'name': '张三',
            'role': 0,
            'employee_no': 'A0001',
            'must_change_password': False,
        })
        resp = self.client.post('/api/login', json={
            'employee_no': 'A0001',
            'password': 'x',
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()['data']
        self.assertFalse(data['must_change_password'])
        self.assertNotIn('change_password_url', data)


if __name__ == '__main__':
    unittest.main()
