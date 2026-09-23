"""用户新增：密码存储与参数校验（无数据库）。"""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from app.controllers.auth_ctrl import normalize_login_password

from app.controllers.user_ctrl import (
    add_role,
    add_user,
    normalize_role_desc,
    normalize_role_id,
    get_all_users,
    remove_role,
    update_role,
)
from app.models.user import User
from app.routes.user_routes import user_bp


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

    @patch('app.controllers.user_ctrl.role_exists', return_value=False)
    def test_invalid_role(self, _exists):
        ok, msg = add_user({
            'employee_no': 'A1',
            'name': '张三',
            'password': 'Hold26',
            'role': 99,
        })
        self.assertFalse(ok)
        self.assertIn('角色无效', msg)

    @patch('app.controllers.user_ctrl.role_exists', return_value=True)
    def test_catalog_role_is_not_rejected_before_password(self, exists):
        ok, msg = add_user({
            'employee_no': 'A1',
            'name': '张三',
            'password': '123456',
            'role': 2,
        })
        self.assertFalse(ok)
        self.assertIn('字母和数字', msg)
        exists.assert_not_called()

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


class RoleCatalogValidationTest(unittest.TestCase):
    def test_role_id(self):
        self.assertEqual(normalize_role_id(2), (2, ''))
        self.assertEqual(normalize_role_id('8'), (8, ''))
        self.assertIsNone(normalize_role_id('')[0])
        self.assertIsNone(normalize_role_id(-1)[0])
        self.assertIsNone(normalize_role_id('1.5')[0])
        self.assertIsNone(normalize_role_id(True)[0])
        self.assertIn('超出范围', normalize_role_id(10 ** 11)[1])

    def test_role_desc_bytes(self):
        self.assertEqual(normalize_role_desc('  CP协助  ')[0], 'CP协助')
        self.assertIsNone(normalize_role_desc('   ')[0])
        self.assertIsNone(normalize_role_desc('测' * 34)[0])
        self.assertEqual(len(normalize_role_desc('测' * 33)[0]), 33)

    def test_add_role_rejects_before_db(self):
        self.assertIn('角色编号', add_role({})[1])
        self.assertIn('角色说明', add_role({'role_id': 3})[1])
        ok, msg = add_role({'role_id': 3, 'role_desc': '测' * 34})
        self.assertFalse(ok)
        self.assertIn('过长', msg)

    def test_update_role_requires_desc(self):
        ok, msg = update_role(1, {})
        self.assertFalse(ok)
        self.assertIn('角色说明', msg)

    def test_remove_role_rejected(self):
        ok, msg = remove_role(2)
        self.assertFalse(ok)
        self.assertIn('不可删除', msg)

    def test_user_list_rejects_bad_role_filter(self):
        ok, msg, rows = get_all_users(role='abc')
        self.assertFalse(ok)
        self.assertIn('角色无效', msg)
        self.assertEqual(rows, [])


class UserRolePageTest(unittest.TestCase):
    def setUp(self):
        app_dir = Path(__file__).resolve().parents[1] / 'app'
        self.app = Flask(__name__, template_folder=str(app_dir / 'templates'))
        self.app.secret_key = 'test-users'
        self.app.config['TESTING'] = True
        self.app.register_blueprint(user_bp)
        self.client = self.app.test_client()

    def _login_root(self):
        with self.client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['user_name'] = 'root'
            sess['role'] = 0
            sess['must_change_password'] = False

    def test_page_includes_role_management(self):
        self._login_root()
        resp = self.client.get('/admin/users')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn('角色管理', body)
        self.assertIn('新增角色', body)
        self.assertIn('id="role-filter"', body)
        self.assertIn('全部角色', body)
        self.assertNotIn('不能删除', body)
        self.assertNotIn('deleteRole', body)
        self.assertIn('/admin/users/api/roles', body)

    def test_create_role_validates_without_db(self):
        self._login_root()
        resp = self.client.post('/admin/users/api/roles', json={})
        self.assertEqual(resp.status_code, 400)
        self.assertIn('角色编号', resp.get_json()['msg'])

    def test_role_api_requires_login(self):
        resp = self.client.get('/admin/users/api/roles')
        self.assertEqual(resp.status_code, 401)


if __name__ == '__main__':
    unittest.main()
