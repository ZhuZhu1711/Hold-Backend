"""密码策略（不连库）。"""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.controllers.auth_ctrl import normalize_login_password
from app.utils.password_policy import user_must_change_password, validate_new_password


class ValidateNewPasswordTest(unittest.TestCase):
    def test_empty(self):
        ok, msg = validate_new_password('A1', '')
        self.assertFalse(ok)
        self.assertEqual(msg, '请填写密码')

    def test_too_short(self):
        ok, msg = validate_new_password('A1', 'Ab1')
        self.assertFalse(ok)
        self.assertIn('至少', msg)

    def test_digits_only(self):
        ok, msg = validate_new_password('A1', '1234567')
        self.assertFalse(ok)
        self.assertIn('字母和数字', msg)

    def test_letters_only(self):
        ok, msg = validate_new_password('A1', 'abcdef')
        self.assertFalse(ok)
        self.assertIn('字母和数字', msg)

    def test_same_as_employee_no(self):
        ok, msg = validate_new_password('Hold26', 'Hold26')
        self.assertFalse(ok)
        self.assertIn('工号', msg)

    def test_employee_no_case_insensitive(self):
        ok, msg = validate_new_password('Hold26', 'hold26')
        self.assertFalse(ok)
        self.assertIn('工号', msg)

    def test_weak_abc123(self):
        ok, msg = validate_new_password('A1', 'abc123')
        self.assertFalse(ok)
        self.assertIn('过于简单', msg)

    def test_md5_hex_rejected(self):
        hashed = normalize_login_password('Hold26')
        ok, msg = validate_new_password('A1', hashed)
        self.assertFalse(ok)
        self.assertIn('明文', msg)

    def test_compliant(self):
        ok, msg = validate_new_password('A0001', 'Hold26')
        self.assertTrue(ok, msg)
        self.assertEqual(msg, '')


class MustChangePasswordTest(unittest.TestCase):
    def test_none_user(self):
        self.assertTrue(user_must_change_password(None))

    def test_null_column(self):
        self.assertTrue(user_must_change_password(SimpleNamespace(MUST_CHANGE_PWD=None)))

    def test_flag_one(self):
        self.assertTrue(user_must_change_password(SimpleNamespace(MUST_CHANGE_PWD=1)))

    def test_flag_zero(self):
        self.assertFalse(user_must_change_password(SimpleNamespace(MUST_CHANGE_PWD=0)))


if __name__ == '__main__':
    unittest.main()
