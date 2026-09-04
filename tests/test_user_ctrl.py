"""用户新增：密码存储与参数校验（无数据库）。"""
from __future__ import annotations

import unittest

from app.controllers.auth_ctrl import normalize_login_password
from app.controllers.user_ctrl import add_user
from app.models.user import User


class UserPasswordTest(unittest.TestCase):
    def test_set_password_stores_md5_on_password_column(self):
        user = User()
        user.set_password('123456')
        self.assertEqual(user.PASSWORD, 'e10adc3949ba59abbe56e057f20f883e')
        self.assertFalse(hasattr(user, 'PASSWORD_HASH') and getattr(user, 'PASSWORD_HASH') not in (None, user.PASSWORD))

    def test_set_password_accepts_md5_hex(self):
        user = User()
        user.set_password('e10adc3949ba59abbe56e057f20f883e')
        self.assertEqual(user.PASSWORD, 'e10adc3949ba59abbe56e057f20f883e')

    def test_check_password(self):
        user = User()
        user.set_password('123456')
        self.assertTrue(user.check_password('123456'))
        self.assertTrue(user.check_password('e10adc3949ba59abbe56e057f20f883e'))
        self.assertFalse(user.check_password('wrong'))

    def test_normalize_empty(self):
        self.assertIsNone(normalize_login_password(''))
        self.assertIsNone(normalize_login_password(None))


class AddUserValidationTest(unittest.TestCase):
    def test_missing_fields(self):
        self.assertEqual(add_user({})[1], '请填写工号')
        self.assertEqual(add_user({'employee_no': 'A1'})[1], '请填写姓名')
        self.assertEqual(add_user({'employee_no': 'A1', 'name': '张三'})[1], '请填写密码')

    def test_invalid_role(self):
        ok, msg = add_user({
            'employee_no': 'A1',
            'name': '张三',
            'password': 'Hold26',
            'role': 2,
        })
        self.assertFalse(ok)
        self.assertIn('角色无效', msg)

    def test_name_too_long(self):
        ok, msg = add_user({
            'employee_no': 'A1',
            'name': 'N' * 21,
            'password': 'Hold26',
        })
        self.assertFalse(ok)
        self.assertIn('姓名最长', msg)

    def test_weak_password_rejected(self):
        ok, msg = add_user({
            'employee_no': 'A1',
            'name': '张三',
            'password': '123456',
        })
        self.assertFalse(ok)
        self.assertIn('字母和数字', msg)

    def test_employee_no_as_password_rejected(self):
        ok, msg = add_user({
            'employee_no': 'Hold26',
            'name': '张三',
            'password': 'Hold26',
        })
        self.assertFalse(ok)
        self.assertIn('工号', msg)

    def test_abc123_rejected(self):
        ok, msg = add_user({
            'employee_no': 'A1',
            'name': '张三',
            'password': 'abc123',
        })
        self.assertFalse(ok)
        self.assertIn('过于简单', msg)


if __name__ == '__main__':
    unittest.main()
