"""改密逻辑（mock，不连库）。"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from app.controllers.user_ctrl import change_password
from app.models.user import User


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


if __name__ == '__main__':
    unittest.main()
