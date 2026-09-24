"""型号改绑时把原工程师名下未关闭单转给新工程师（不连库）。"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.controllers.dispose_ctrl import (
    DISPOSE_TRANSFER,
    find_stale_engineer_holds,
    transfer_open_holds_on_rebind,
)
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


if __name__ == '__main__':
    unittest.main()
