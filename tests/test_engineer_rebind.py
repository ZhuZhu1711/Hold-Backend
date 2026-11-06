"""型号改绑时把原工程师名下未关闭单转给新工程师（不连库）。"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.controllers.dispose_ctrl import (
    DISPOSE_TRANSFER,
    find_stale_engineer_holds,
    rebind_system_owned_holds,
    transfer_open_holds_on_rebind,
    transfer_system_owned_record,
)
from app.utils.auth_decorators import ROLE_ENGINEER, ROLE_PRODUCTION
from app.controllers.product_ctrl import update_product


def _sqls(db):
    return [str(call.args[0]) for call in db.session.execute.call_args_list]


class TransferOpenHoldsTest(unittest.TestCase):
    @patch('app.controllers.dispose_ctrl._circ_seq', return_value='CIRCULATION_HISTORY_TEST_SEQ')
    @patch('app.controllers.dispose_ctrl._circ_table', return_value='CIRCULATION_HISTORY_TEST')
    @patch('app.controllers.dispose_ctrl._record_table', return_value='FT_HOLD_RECORD_TEST')
    @patch('app.controllers.dispose_ctrl._next_positive_seq', side_effect=[101, 102])
    @patch('app.controllers.dispose_ctrl.db')
    def test_moves_every_open_record_still_owned_by_old_engineer(
        self, db, _seq, _record, _circ, _circ_seq,
    ):
        selected = MagicMock()
        selected.fetchall.return_value = [(11,), (12,)]
        db.session.execute.return_value = selected

        moved = transfer_open_holds_on_rebind(
            'P1', 3, 7, '型号工程师由 张三(3) 调整为 李四(7)',
        )

        self.assertEqual(moved, 2)
        sqls = _sqls(db)
        self.assertIn('c.NEXT_OWNER_ID = :from_owner', sqls[0])
        self.assertIn('NVL(r.STATUS, 0) <> :closed', sqls[0])
        self.assertEqual(db.session.execute.call_args_list[0].args[1]['from_owner'], 3)

        inserts = [c for c in db.session.execute.call_args_list if 'INSERT' in str(c.args[0])]
        updates = [c for c in db.session.execute.call_args_list if 'UPDATE' in str(c.args[0])]
        self.assertEqual(len(inserts), 2)
        self.assertEqual(len(updates), 2)
        for call in inserts:
            params = call.args[1]
            self.assertEqual(params['dispose'], DISPOSE_TRANSFER)
            self.assertEqual(params['next_owner_id'], 7)
            self.assertEqual(params['disposed_owner_id'], 1)
            self.assertEqual(params['dispose_source'], 'SYS')
        for call in updates:
            sql = str(call.args[0])
            self.assertIn('LAST_CIRCULATION_ID', sql)
            self.assertNotIn('STATUS', sql)
        db.session.commit.assert_not_called()

    @patch('app.controllers.dispose_ctrl._circ_seq', return_value='CIRCULATION_HISTORY_TEST_SEQ')
    @patch('app.controllers.dispose_ctrl._circ_table', return_value='CIRCULATION_HISTORY_TEST')
    @patch('app.controllers.dispose_ctrl._record_table', return_value='FT_HOLD_RECORD_TEST')
    @patch('app.controllers.dispose_ctrl.db')
    def test_record_id_limits_the_select(self, db, _record, _circ, _circ_seq):
        db.session.execute.return_value.fetchall.return_value = []
        moved = transfer_open_holds_on_rebind(
            'P1', 1, 7, '指定转交', disposed_owner_id=9, record_id=11,
        )
        self.assertEqual(moved, 0)
        sql = _sqls(db)[0]
        params = db.session.execute.call_args.args[1]
        self.assertIn('r.ID = :record_id', sql)
        self.assertEqual(params['record_id'], 11)
        self.assertEqual(params['from_owner'], 1)

    @patch('app.controllers.dispose_ctrl.db')
    def test_same_owner_writes_nothing(self, db):
        self.assertEqual(transfer_open_holds_on_rebind('P1', 3, 3, 'note'), 0)
        db.session.execute.assert_not_called()

    @patch('app.controllers.dispose_ctrl._circ_table', return_value='CIRCULATION_HISTORY_TEST')
    @patch('app.controllers.dispose_ctrl._record_table', return_value='FT_HOLD_RECORD_TEST')
    @patch('app.controllers.dispose_ctrl.db')
    def test_stale_query_skips_production_and_current_engineer(self, db, _record, _circ):
        db.session.execute.return_value.fetchall.return_value = []
        self.assertEqual(find_stale_engineer_holds(), [])
        sql = _sqls(db)[0]
        params = db.session.execute.call_args.args[1]
        self.assertIn('c.NEXT_OWNER_ID <> :prod_id', sql)
        self.assertIn('NVL(p.PRO_ENG_ID, :system_id)', sql)
        self.assertEqual(params['prod_id'], 181)
        self.assertEqual(params['system_id'], 1)


class UpdateProductRebindTest(unittest.TestCase):
    def _product(self, eng_id=3):
        product = MagicMock()
        product.PRODUCT_ID = 'P1'
        product.PRO_ENG_ID = eng_id
        return product

    @patch('app.controllers.product_ctrl.db')
    @patch('app.controllers.product_ctrl.transfer_open_holds_on_rebind', return_value=2)
    @patch('app.controllers.product_ctrl.User')
    @patch('app.controllers.product_ctrl.ProductInfo')
    def test_rebind_transfers_to_new_engineer(self, ProductInfo, User, transfer, db):
        product = self._product(3)
        ProductInfo.query.get.return_value = product
        ProductInfo.query.filter.return_value.all.return_value = [product]

        def _user(uid):
            user = MagicMock()
            user.NAME = {3: '张三', 7: '李四'}[int(uid)]
            return user

        User.query.get.side_effect = _user

        ok, msg = update_product(1, {'engineer_id': 7})

        self.assertTrue(ok, msg)
        self.assertEqual(product.PRO_ENG_ID, 7)
        transfer.assert_called_once_with(
            'P1', 3, 7, '型号工程师由 张三(3) 调整为 李四(7)',
        )
        db.session.commit.assert_called_once()

    @patch('app.controllers.product_ctrl.db')
    @patch('app.controllers.product_ctrl.transfer_open_holds_on_rebind')
    @patch('app.controllers.product_ctrl.User')
    @patch('app.controllers.product_ctrl.ProductInfo')
    def test_unchanged_binding_does_not_transfer(self, ProductInfo, User, transfer, db):
        product = self._product(3)
        ProductInfo.query.get.return_value = product
        ProductInfo.query.filter.return_value.all.return_value = [product]
        User.query.get.return_value = MagicMock(NAME='张三')

        ok, msg = update_product(1, {'engineer_id': 3, 'gross_die': 9})

        self.assertTrue(ok, msg)
        transfer.assert_not_called()
        self.assertEqual(product.GROSS_DIE, 9)
        db.session.commit.assert_called_once()

    @patch('app.controllers.product_ctrl.db')
    @patch('app.controllers.product_ctrl.transfer_open_holds_on_rebind')
    @patch('app.controllers.product_ctrl.ProductInfo')
    def test_gross_die_only_does_not_transfer(self, ProductInfo, transfer, db):
        product = self._product(3)
        ProductInfo.query.get.return_value = product

        ok, msg = update_product(1, {'gross_die': 4})

        self.assertTrue(ok, msg)
        transfer.assert_not_called()
        self.assertEqual(product.PRO_ENG_ID, 3)


class RebindSystemHoldsTest(unittest.TestCase):
    @patch('app.controllers.dispose_ctrl.db')
    @patch('app.controllers.dispose_ctrl.transfer_open_holds_on_rebind', return_value=2)
    @patch('app.controllers.dispose_ctrl._lookup_pro_eng_id', return_value=7)
    def test_bound_product_moves_system_holds(self, _lookup, transfer, db):
        ok, msg, moved = rebind_system_owned_holds('P1', 9)
        self.assertTrue(ok, msg)
        self.assertEqual(moved, 2)
        transfer.assert_called_once_with(
            'P1', 1, 7, '按型号重绑：系统待办调整为工程师 7',
            disposed_owner_id=9,
        )
        db.session.commit.assert_called_once()

    @patch('app.controllers.dispose_ctrl.db')
    @patch('app.controllers.dispose_ctrl.transfer_open_holds_on_rebind')
    @patch('app.controllers.dispose_ctrl._lookup_pro_eng_id', return_value=1)
    def test_unbound_product_does_not_write(self, _lookup, transfer, db):
        ok, msg, moved = rebind_system_owned_holds('NEW', 9)
        self.assertFalse(ok)
        self.assertIn('尚未绑定', msg)
        self.assertEqual(moved, 0)
        transfer.assert_not_called()
        db.session.commit.assert_not_called()
        db.session.execute.assert_not_called()

    @patch('app.controllers.dispose_ctrl.db')
    @patch(
        'app.controllers.dispose_ctrl._lookup_pro_eng_id',
        side_effect=lambda pid: {'A': 7, 'B': 1}[pid],
    )
    @patch(
        'app.controllers.dispose_ctrl._product_ids_owned_by_system',
        return_value=['A', 'B'],
    )
    @patch('app.controllers.dispose_ctrl.transfer_open_holds_on_rebind', return_value=1)
    def test_rebind_all_skips_products_still_unbound(self, transfer, _products, _lookup, db):
        ok, msg, moved = rebind_system_owned_holds(None, 9)
        self.assertTrue(ok, msg)
        self.assertEqual(moved, 1)
        transfer.assert_called_once()
        self.assertEqual(transfer.call_args.args[0], 'A')
        self.assertEqual(transfer.call_args.args[1], 1)
        self.assertEqual(transfer.call_args.args[2], 7)


class TransferSystemHoldTest(unittest.TestCase):
    def _hold(self, owner=1, status=0, product='NEW-1'):
        return {
            'id': 11,
            'product_id': product,
            'status': status,
            'owner_id': owner,
        }

    @patch('app.controllers.dispose_ctrl.db')
    @patch('app.controllers.dispose_ctrl.transfer_open_holds_on_rebind')
    @patch('app.controllers.dispose_ctrl._load_system_hold')
    def test_rejects_when_owner_is_not_system(self, load, transfer, db):
        load.return_value = self._hold(owner=7)
        ok, msg, data = transfer_system_owned_record(11, 8, 9)
        self.assertFalse(ok)
        self.assertIn('仅系统', msg)
        self.assertIsNone(data)
        transfer.assert_not_called()
        db.session.commit.assert_not_called()

    @patch('app.controllers.dispose_ctrl.db')
    @patch('app.controllers.dispose_ctrl.transfer_open_holds_on_rebind')
    @patch('app.controllers.dispose_ctrl._user_role', return_value=ROLE_PRODUCTION)
    @patch('app.controllers.dispose_ctrl._load_system_hold')
    def test_rejects_non_engineer_target(self, load, _role, transfer, db):
        load.return_value = self._hold()
        ok, msg, data = transfer_system_owned_record(11, 8, 9)
        self.assertFalse(ok)
        self.assertIn('产品工程师', msg)
        self.assertIsNone(data)
        transfer.assert_not_called()
        db.session.commit.assert_not_called()

    @patch('app.controllers.dispose_ctrl.db')
    @patch('app.controllers.dispose_ctrl.transfer_open_holds_on_rebind', return_value=1)
    @patch('app.controllers.dispose_ctrl._user_role', return_value=ROLE_ENGINEER)
    @patch('app.controllers.dispose_ctrl._load_system_hold')
    def test_transfers_one_system_hold(self, load, _role, transfer, db):
        load.return_value = self._hold()
        ok, msg, data = transfer_system_owned_record(11, 8, 9)
        self.assertTrue(ok, msg)
        transfer.assert_called_once_with(
            'NEW-1', 1, 8, '指定转交：由系统用户转给工程师 8',
            disposed_owner_id=9,
            record_id=11,
        )
        db.session.commit.assert_called_once()
        self.assertEqual(data['next_owner_id'], 8)

    def test_rejects_system_user_as_target(self):
        ok, msg, data = transfer_system_owned_record(11, 1, 9)
        self.assertFalse(ok)
        self.assertIn('不能转交', msg)
        self.assertIsNone(data)


if __name__ == '__main__':
    unittest.main()
