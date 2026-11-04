"""FT产品主管：可分配角色、产品线别、用户/产品接口（不连库）。"""
from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import MagicMock, patch

from flask import Flask

from app.controllers.product_ctrl import (
    add_product,
    line_type_from_product_id,
    lookup_pie_by_product,
    lookup_products_by_pie,
    product_family,
    update_family,
    update_product,
)
from app.controllers.user_ctrl import actor_may_assign_role
from app.routes.product_routes import product_bp
from app.routes.user_routes import user_bp
from app.utils.auth_decorators import (
    ROLE_ENGINEER,
    ROLE_FT_PRODUCT_MANAGER,
    ROLE_PRODUCTION,
    ROLE_ROOT,
    home_endpoint_for_role,
)


class AssignRoleTest(unittest.TestCase):
    def test_manager_can_only_create_engineer(self):
        ok, msg = actor_may_assign_role(ROLE_FT_PRODUCT_MANAGER, ROLE_ENGINEER)
        self.assertTrue(ok, msg)
        for role in (ROLE_ROOT, ROLE_PRODUCTION, ROLE_FT_PRODUCT_MANAGER, 'x'):
            ok, msg = actor_may_assign_role(ROLE_FT_PRODUCT_MANAGER, role)
            self.assertFalse(ok, role)
            self.assertIn('产品工程师', msg)

    def test_root_is_not_limited_here(self):
        ok, msg = actor_may_assign_role(ROLE_ROOT, ROLE_FT_PRODUCT_MANAGER)
        self.assertTrue(ok, msg)

    def test_home_is_product_page(self):
        self.assertEqual(
            home_endpoint_for_role(ROLE_FT_PRODUCT_MANAGER),
            'product.product_list_page',
        )


class LineTypeTest(unittest.TestCase):
    def test_suffix_rules(self):
        self.assertEqual(line_type_from_product_id('GC1084-3.5'), 0)
        self.assertEqual(line_type_from_product_id('GC4103-2.6'), 1)
        self.assertEqual(line_type_from_product_id('GC1'), -1)
        self.assertEqual(line_type_from_product_id('X-2.6-3.5'), 0)

    def test_family_strips_only_the_form_suffix(self):
        self.assertEqual(product_family('GC8613-3.5'), 'GC8613')
        self.assertEqual(product_family('GC8613-2.6'), 'GC8613')
        self.assertEqual(product_family('GC8613'), 'GC8613')
        self.assertEqual(product_family('X-2.6-3.5'), 'X-2.6')

    @patch('app.controllers.product_ctrl.User')
    @patch('app.controllers.product_ctrl.db')
    @patch('app.controllers.product_ctrl.ProductInfo')
    def test_add_product_ignores_request_line_type(self, product_cls, db, user_cls):
        product_cls.query.filter_by.return_value.first.return_value = None
        product_cls.query.filter.return_value.all.return_value = []
        user_cls.query.get.return_value = MagicMock(NAME='工程师')
        created = product_cls.return_value
        ok, msg = add_product({
            'product_id': 'GC4103-2.6',
            'gross_die': 10,
            'line_type': 0,
            'engineer_id': 4,
        })
        self.assertTrue(ok, msg)
        self.assertEqual(created.PRODUCT_ID, 'GC4103-2.6')
        self.assertEqual(created.LINE_TYPE, 1)
        self.assertEqual(created.GROSS_DIE, 10)
        self.assertEqual(created.PRO_ENG_ID, 4)
        self.assertIsNone(created.PIE)
        db.session.add.assert_called_once_with(created)
        db.session.commit.assert_called_once()

    @patch('app.controllers.product_ctrl.db')
    @patch('app.controllers.product_ctrl.ProductInfo')
    def test_blank_pie_is_stored_as_null(self, product_cls, db):
        product_cls.query.filter_by.return_value.first.return_value = None
        product_cls.query.filter.return_value.all.return_value = []
        created = product_cls.return_value
        ok, msg = add_product({
            'product_id': 'GC1084-3.5',
            'gross_die': 1,
            'pie': '   ',
        })
        self.assertTrue(ok, msg)
        self.assertIsNone(created.PIE)
        db.session.add.assert_called_once_with(created)

    @patch('app.controllers.product_ctrl.db')
    @patch('app.controllers.product_ctrl.ProductInfo')
    def test_pie_over_50_chars_is_rejected(self, product_cls, db):
        product_cls.query.filter_by.return_value.first.return_value = None
        ok, msg = add_product({
            'product_id': 'GC1084-3.5',
            'gross_die': 1,
            'pie': 'P' * 51,
        })
        self.assertFalse(ok)
        self.assertIn('50', msg)
        db.session.add.assert_not_called()
        db.session.commit.assert_not_called()


def _client(role):
    app = Flask(__name__)
    app.secret_key = 'test-ft-product-manager'
    app.config['TESTING'] = True
    app.register_blueprint(user_bp)
    app.register_blueprint(product_bp)
    client = app.test_client()
    if role is None:
        return client
    with client.session_transaction() as sess:
        sess['user_id'] = 8
        sess['user_name'] = 'lead'
        sess['role'] = role
        sess['must_change_password'] = False
    return client


class _User:
    ID = 1
    EMPLOYEE_NO = 'E1'
    NAME = '张三'
    ROLE = 1


class UserRouteTest(unittest.TestCase):
    def test_anonymous_is_401(self):
        resp = _client(None).get('/admin/users/api')
        self.assertEqual(resp.status_code, 401)

    def test_engineer_is_403(self):
        resp = _client(ROLE_ENGINEER).get('/admin/users/api')
        self.assertEqual(resp.status_code, 403)

    @patch('app.routes.user_routes.user_ctrl.get_all_users', return_value=(True, '获取成功', [_User()]))
    def test_manager_can_list(self, _get_users):
        resp = _client(ROLE_FT_PRODUCT_MANAGER).get('/admin/users/api')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()['data'][0]['employee_no'], 'E1')

    @patch('app.routes.user_routes.user_ctrl.add_user', return_value=(True, '用户添加成功'))
    def test_manager_cannot_assign_other_roles(self, add_user):
        client = _client(ROLE_FT_PRODUCT_MANAGER)
        for role in (ROLE_ROOT, ROLE_PRODUCTION):
            resp = client.post('/admin/users/api', json={
                'employee_no': 'E2',
                'name': '李四',
                'password': 'Hold26',
                'role': role,
            })
            self.assertEqual(resp.status_code, 403, role)
            self.assertIn('产品工程师', resp.get_json()['msg'])
        add_user.assert_not_called()

    @patch('app.routes.user_routes.user_ctrl.add_user', return_value=(True, '用户添加成功'))
    def test_manager_can_create_engineer(self, add_user):
        resp = _client(ROLE_FT_PRODUCT_MANAGER).post('/admin/users/api', json={
            'employee_no': 'E2',
            'name': '李四',
            'password': 'Hold26',
            'role': ROLE_ENGINEER,
        })
        self.assertEqual(resp.status_code, 200)
        add_user.assert_called_once()


class _Product:
    ID = 3
    PRODUCT_ID = 'GC1084-3.5'
    GROSS_DIE = 100
    LINE_TYPE = 0
    UPDATE_DTTM = None
    PRO_ENG_ID = None
    PIE = None
    owner = None


class ProductRouteTest(unittest.TestCase):
    def test_anonymous_is_401(self):
        resp = _client(None).get('/admin/products/api')
        self.assertEqual(resp.status_code, 401)

    def test_engineer_is_403(self):
        resp = _client(ROLE_ENGINEER).get('/admin/products/api')
        self.assertEqual(resp.status_code, 403)

    @patch(
        'app.routes.product_routes.product_ctrl.get_all_products',
        return_value=(True, '获取成功', [_Product()]),
    )
    def test_manager_can_list(self, _get_products):
        resp = _client(ROLE_FT_PRODUCT_MANAGER).get('/admin/products/api')
        self.assertEqual(resp.status_code, 200)
        item = resp.get_json()['data'][0]
        self.assertEqual(item['family'], 'GC1084')
        self.assertEqual(item['product_id'], 'GC1084')
        self.assertEqual(item['forms'][0]['product_id'], 'GC1084-3.5')
        self.assertEqual(item['forms'][0]['line_type'], 0)
        self.assertIsNone(item['pie'])


class PieBindingTest(unittest.TestCase):
    @patch('app.controllers.product_ctrl.db')
    @patch('app.controllers.product_ctrl.ProductInfo')
    def test_update_pie_leaves_engineer_alone(self, product_cls, db):
        product = _Product()
        product.PRO_ENG_ID = 3
        product.PIE = '旧值'
        product_cls.query.get.return_value = product
        product_cls.query.filter.return_value.all.return_value = [product]

        ok, msg = update_product(3, {'pie': '  张三  '})

        self.assertTrue(ok, msg)
        self.assertEqual(product.PIE, '张三')
        self.assertEqual(product.PRO_ENG_ID, 3)
        db.session.commit.assert_called_once()

    @patch('app.controllers.product_ctrl.db')
    @patch('app.controllers.product_ctrl.ProductInfo')
    def test_update_without_pie_keeps_binding(self, product_cls, db):
        product = _Product()
        product.PIE = '张三'
        product_cls.query.get.return_value = product

        ok, msg = update_product(3, {'gross_die': 8})

        self.assertTrue(ok, msg)
        self.assertEqual(product.PIE, '张三')
        self.assertEqual(product.GROSS_DIE, 8)

    @patch('app.controllers.product_ctrl.ProductInfo')
    def test_lookup_by_family_or_form_returns_same_pie(self, product_cls):
        ate = _Product()
        ate.PRODUCT_ID = 'GC8613-3.5'
        ate.PIE = None
        ate.UPDATE_DTTM = date(2026, 1, 1)
        wlt = _Product()
        wlt.ID = 4
        wlt.PRODUCT_ID = 'GC8613-2.6'
        wlt.LINE_TYPE = 1
        wlt.PIE = '张三'
        wlt.UPDATE_DTTM = date(2026, 2, 1)
        product_cls.query.filter.return_value.all.return_value = [ate, wlt]

        for key in ('GC8613', '  GC8613-3.5  ', 'GC8613-2.6'):
            ok, msg, data = lookup_pie_by_product(key)
            self.assertTrue(ok, msg)
            self.assertEqual(data['product_id'], 'GC8613')
            self.assertEqual(data['pie'], '张三')
            self.assertEqual(
                [form['product_id'] for form in data['forms']],
                ['GC8613-2.6', 'GC8613-3.5'],
            )

    @patch('app.controllers.product_ctrl.ProductInfo')
    def test_lookup_unbound_product_returns_null_pie(self, product_cls):
        product_cls.query.filter.return_value.all.return_value = [_Product()]

        ok, msg, data = lookup_pie_by_product('GC1084-3.5')

        self.assertTrue(ok, msg)
        self.assertEqual(data['product_id'], 'GC1084')
        self.assertIsNone(data['pie'])

    @patch('app.controllers.product_ctrl.ProductInfo')
    def test_lookup_missing_product(self, product_cls):
        product_cls.query.filter.return_value.all.return_value = []

        ok, msg, data = lookup_pie_by_product('NOPE')

        self.assertFalse(ok)
        self.assertEqual(msg, '产品不存在')
        self.assertIsNone(data)

    @patch('app.controllers.product_ctrl.ProductInfo')
    def test_lookup_by_pie_collapses_forms(self, product_cls):
        ate = _Product()
        ate.PRODUCT_ID = 'GC8613-3.5'
        ate.PIE = '张三'
        wlt = _Product()
        wlt.ID = 4
        wlt.PRODUCT_ID = 'GC8613-2.6'
        wlt.LINE_TYPE = 1
        wlt.PIE = '张三'
        other = _Product()
        other.ID = 5
        other.PRODUCT_ID = 'GC4103-2.6'
        other.PIE = '张三'
        chain = product_cls.query.filter.return_value.order_by.return_value
        chain.all.return_value = [other, wlt, ate]

        ok, msg, data = lookup_products_by_pie('  张三 ')

        self.assertTrue(ok, msg)
        self.assertEqual(data['pie'], '张三')
        self.assertEqual(
            [item['product_id'] for item in data['products']],
            ['GC4103', 'GC8613'],
        )
        gc8613 = next(item for item in data['products'] if item['product_id'] == 'GC8613')
        self.assertEqual(len(gc8613['forms']), 2)

    @patch('app.controllers.product_ctrl.db')
    @patch('app.controllers.product_ctrl.transfer_open_holds_on_rebind')
    @patch('app.controllers.product_ctrl.User')
    @patch('app.controllers.product_ctrl.ProductInfo')
    def test_family_save_writes_both_forms_and_transfers_only_changed(self, product_cls, user_cls, transfer, db):
        ate = _Product()
        ate.ID = 1
        ate.PRODUCT_ID = 'GC8613-3.5'
        ate.PRO_ENG_ID = 3
        ate.GROSS_DIE = 100
        wlt = _Product()
        wlt.ID = 2
        wlt.PRODUCT_ID = 'GC8613-2.6'
        wlt.LINE_TYPE = 1
        wlt.PRO_ENG_ID = 7
        wlt.GROSS_DIE = 80
        product_cls.query.filter.return_value.all.return_value = [ate, wlt]

        def _user(uid):
            user = MagicMock()
            user.NAME = {3: '张三', 7: '李四'}[int(uid)]
            return user

        user_cls.query.get.side_effect = _user

        ok, msg = update_family('GC8613', {
            'engineer_id': 7,
            'pie': '王五',
            'forms': [{'id': 1, 'gross_die': 90}],
        })

        self.assertTrue(ok, msg)
        self.assertEqual(ate.PRO_ENG_ID, 7)
        self.assertEqual(wlt.PRO_ENG_ID, 7)
        self.assertEqual(ate.PIE, '王五')
        self.assertEqual(wlt.PIE, '王五')
        self.assertEqual(ate.GROSS_DIE, 90)
        self.assertEqual(wlt.GROSS_DIE, 80)
        transfer.assert_called_once()
        self.assertEqual(transfer.call_args.args[0], 'GC8613-3.5')
        db.session.commit.assert_called_once()

    @patch('app.controllers.product_ctrl.db')
    @patch('app.controllers.product_ctrl.ProductInfo')
    def test_gross_die_only_does_not_touch_the_other_form(self, product_cls, db):
        product = _Product()
        product.PIE = '张三'
        product.PRO_ENG_ID = 3
        product_cls.query.get.return_value = product

        ok, msg = update_product(3, {'gross_die': 8})

        self.assertTrue(ok, msg)
        self.assertEqual(product.GROSS_DIE, 8)
        self.assertEqual(product.PIE, '张三')
        self.assertEqual(product.PRO_ENG_ID, 3)
        product_cls.query.filter.assert_not_called()

    @patch('app.controllers.product_ctrl.db')
    @patch('app.controllers.product_ctrl.ProductInfo')
    def test_new_form_inherits_family_binding(self, product_cls, db):
        sibling = _Product()
        sibling.PRODUCT_ID = 'GC8613-3.5'
        sibling.PRO_ENG_ID = 4
        sibling.PIE = '张三'
        product_cls.query.filter_by.return_value.first.return_value = None
        product_cls.query.filter.return_value.all.return_value = [sibling]
        created = product_cls.return_value

        ok, msg = add_product({'product_id': 'GC8613-2.6', 'gross_die': 8})

        self.assertTrue(ok, msg)
        self.assertEqual(created.PRODUCT_ID, 'GC8613-2.6')
        self.assertEqual(created.LINE_TYPE, 1)
        self.assertEqual(created.PRO_ENG_ID, 4)
        self.assertEqual(created.PIE, '张三')
        self.assertEqual(sibling.PRO_ENG_ID, 4)
        self.assertEqual(sibling.PIE, '张三')

    def test_lookup_blank_pie_is_rejected(self):
        ok, msg, data = lookup_products_by_pie('   ')
        self.assertFalse(ok)
        self.assertEqual(msg, 'PIE 不能为空')
        self.assertIsNone(data)


class PieRouteTest(unittest.TestCase):
    def test_anonymous_by_product_is_401(self):
        resp = _client(None).get('/admin/products/api/by-product?product_id=GC1084-3.5')
        self.assertEqual(resp.status_code, 401)

    def test_engineer_by_pie_is_403(self):
        resp = _client(ROLE_ENGINEER).get('/admin/products/api/by-pie?pie=张三')
        self.assertEqual(resp.status_code, 403)

    @patch(
        'app.routes.product_routes.product_ctrl.lookup_pie_by_product',
        return_value=(True, '获取成功', {'product_id': 'GC1084-3.5', 'pie': None}),
    )
    def test_by_product_unbound(self, _lookup):
        resp = _client(ROLE_FT_PRODUCT_MANAGER).get(
            '/admin/products/api/by-product?product_id=GC1084-3.5',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.get_json()['data']['pie'])

    @patch(
        'app.routes.product_routes.product_ctrl.lookup_pie_by_product',
        return_value=(False, '产品不存在', None),
    )
    def test_missing_product_is_404(self, _lookup):
        resp = _client(ROLE_ROOT).get('/admin/products/api/by-product?product_id=NOPE')
        self.assertEqual(resp.status_code, 404)

    @patch(
        'app.routes.product_routes.product_ctrl.lookup_products_by_pie',
        return_value=(False, 'PIE 不能为空', None),
    )
    def test_blank_pie_is_400(self, _lookup):
        resp = _client(ROLE_ROOT).get('/admin/products/api/by-pie?pie=')
        self.assertEqual(resp.status_code, 400)

    @patch('app.routes.product_routes.product_ctrl.lookup_products_by_pie')
    def test_by_pie_returns_products(self, lookup):
        lookup.return_value = (
            True,
            '获取成功',
            {
                'pie': '张三',
                'products': [{
                    'product_id': 'GC8613',
                    'engineer_name': '未分配',
                    'forms': [
                        {'product_id': 'GC8613-3.5', 'line_type': 0},
                        {'product_id': 'GC8613-2.6', 'line_type': 1},
                    ],
                }],
            },
        )
        resp = _client(ROLE_FT_PRODUCT_MANAGER).get('/admin/products/api/by-pie?pie=张三')

        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()['data']
        self.assertEqual(body['pie'], '张三')
        self.assertEqual([item['product_id'] for item in body['products']], ['GC8613'])
        self.assertEqual(body['products'][0]['engineer_name'], '未分配')
        self.assertEqual(body['products'][0]['forms'][1]['line_type'], 1)


if __name__ == '__main__':
    unittest.main()
