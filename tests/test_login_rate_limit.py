"""登录滑动窗口限流（不连库）。"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from flask import Flask

from app.utils.rate_limit import (
    LOGIN_EMP_FAIL_MAX,
    LOGIN_IP_MAX,
    LOGIN_TOO_FREQUENT_MSG,
    SlidingWindowLimiter,
    check_login_rate,
    clear_login_failures,
    record_login_failure,
    reset_login_limiters,
)


class SlidingWindowLimiterTest(unittest.TestCase):
    def test_allows_until_max_then_blocks(self):
        limiter = SlidingWindowLimiter(max_hits=3, window_seconds=60)
        now = 1_000.0
        self.assertTrue(limiter.hit('k', now_ts=now))
        self.assertTrue(limiter.hit('k', now_ts=now + 1))
        self.assertTrue(limiter.hit('k', now_ts=now + 2))
        self.assertFalse(limiter.hit('k', now_ts=now + 3))
        self.assertGreaterEqual(limiter.retry_after('k', now_ts=now + 3), 1)

    def test_window_expiry_frees_slot(self):
        limiter = SlidingWindowLimiter(max_hits=1, window_seconds=10)
        self.assertTrue(limiter.hit('k', now_ts=100.0))
        self.assertFalse(limiter.hit('k', now_ts=109.0))
        self.assertTrue(limiter.hit('k', now_ts=111.0))

    def test_reset_clears_key(self):
        limiter = SlidingWindowLimiter(max_hits=1, window_seconds=60)
        self.assertTrue(limiter.hit('k', now_ts=1.0))
        limiter.reset('k')
        self.assertTrue(limiter.hit('k', now_ts=2.0))


class LoginRateHelperTest(unittest.TestCase):
    def setUp(self):
        reset_login_limiters()

    def test_ip_limit(self):
        now = 5_000.0
        for i in range(LOGIN_IP_MAX):
            limited, _ = check_login_rate('10.0.0.1', f'emp{i}', now_ts=now)
            self.assertFalse(limited)
        limited, retry = check_login_rate('10.0.0.1', 'empX', now_ts=now)
        self.assertTrue(limited)
        self.assertGreaterEqual(retry, 1)

    def test_emp_failure_limit_and_success_clears(self):
        now = 8_000.0
        ip = '10.0.0.8'
        emp = 'A0001'
        for i in range(LOGIN_EMP_FAIL_MAX):
            limited, _ = check_login_rate(ip, emp, now_ts=now + i)
            self.assertFalse(limited)
            record_login_failure(emp, now_ts=now + i)
        limited, _ = check_login_rate(ip, emp, now_ts=now + 20)
        self.assertTrue(limited)

        reset_login_limiters()
        for i in range(LOGIN_EMP_FAIL_MAX - 1):
            limited, _ = check_login_rate(ip, emp, now_ts=now + i)
            self.assertFalse(limited)
            record_login_failure(emp, now_ts=now + i)
        limited, _ = check_login_rate(ip, emp, now_ts=now + 10)
        self.assertFalse(limited)
        clear_login_failures(emp)
        for i in range(LOGIN_EMP_FAIL_MAX):
            limited, _ = check_login_rate(ip, emp, now_ts=now + 30 + i)
            self.assertFalse(limited)
            record_login_failure(emp, now_ts=now + 30 + i)


class LoginRateRouteTest(unittest.TestCase):
    def setUp(self):
        reset_login_limiters()
        app = Flask(__name__)
        app.secret_key = 'test-login-rate'
        app.config['TESTING'] = True
        from app.routes.auth_routes import auth_bp
        app.register_blueprint(auth_bp)
        self.client = app.test_client()

    @patch('app.routes.auth_routes.user_ctrl.login_logic')
    def test_api_login_returns_429(self, mock_login):
        mock_login.return_value = (False, '密码错误', None)
        for i in range(LOGIN_EMP_FAIL_MAX):
            resp = self.client.post('/api/login', json={
                'employee_no': 'A0001',
                'password': 'x',
            })
            self.assertEqual(resp.status_code, 401, i)
        resp = self.client.post('/api/login', json={
            'employee_no': 'A0001',
            'password': 'x',
        })
        self.assertEqual(resp.status_code, 429)
        body = resp.get_json()
        self.assertEqual(body['code'], 429)
        self.assertEqual(body['msg'], LOGIN_TOO_FREQUENT_MSG)
        self.assertTrue(resp.headers.get('Retry-After'))


if __name__ == '__main__':
    unittest.main()
