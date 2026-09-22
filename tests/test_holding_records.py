"""get_holding_records SQL：系统未关单即可列出（不连库）。"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.controllers.dispose_ctrl import DISPOSE_CLOSE
from app.controllers.hold_report_ctrl import get_holding_records


def _sqls(db):
    return [str(call.args[0]) for call in db.session.execute.call_args_list]


def _params(db, index=0):
    args = db.session.execute.call_args_list[index].args
    if len(args) > 1:
        return args[1]
    return db.session.execute.call_args_list[index].kwargs.get('params') or {}


class GetHoldingRecordsSqlTest(unittest.TestCase):
    def _run(self, db, **kwargs):
        db.session.execute.return_value.scalar.return_value = 0
        db.session.execute.return_value.fetchall.return_value = []
        ok, msg, payload = get_holding_records(**kwargs)
        self.assertTrue(ok, msg)
        self.assertEqual(payload.get('total'), 0)
        return _sqls(db), _params(db)

    @patch(
        'app.controllers.hold_report_ctrl._table_names',
        return_value=(
            'FT_HOLD_INFO_TEST',
            'FT_HOLD_RECORD_TEST',
            'CIRCULATION_HISTORY_TEST',
            'HOLD_RECORD_ID',
        ),
    )
    @patch('app.controllers.hold_report_ctrl.db')
    def test_lists_unclosed_even_if_mes_unheld(self, db, _tables):
        sqls, params = self._run(db, current_owner_id=3)
        self.assertTrue(sqls)
        for sql in sqls:
            self.assertIn('NVL(r.STATUS, 0) <> :closed', sql)
            self.assertNotIn('i.ID IS NOT NULL', sql)
            self.assertNotIn('NVL(r.SOURCE, 0) = 1', sql)
            self.assertIn('AND NVL(i.HOLDING, 1) = 0', sql)
            self.assertIn('c.NEXT_OWNER_ID = :current_owner_id', sql)
        self.assertEqual(params.get('closed'), DISPOSE_CLOSE)
        self.assertEqual(params.get('current_owner_id'), 3)

    @patch(
        'app.controllers.hold_report_ctrl._table_names',
        return_value=(
            'FT_HOLD_INFO_TEST',
            'FT_HOLD_RECORD_TEST',
            'CIRCULATION_HISTORY_TEST',
            'HOLD_RECORD_ID',
        ),
    )
    @patch('app.controllers.hold_report_ctrl.db')
    def test_engineer_pending_filters_owner_and_current(self, db, _tables):
        sqls, params = self._run(db, owner_eng_id=3, current_owner_id=3)
        for sql in sqls:
            self.assertIn('p.PRO_ENG_ID = :owner_eng_id', sql)
            self.assertIn('c.NEXT_OWNER_ID = :current_owner_id', sql)
            self.assertIn('NVL(r.STATUS, 0) <> :closed', sql)
        self.assertEqual(params.get('owner_eng_id'), 3)
        self.assertEqual(params.get('current_owner_id'), 3)


if __name__ == '__main__':
    unittest.main()
