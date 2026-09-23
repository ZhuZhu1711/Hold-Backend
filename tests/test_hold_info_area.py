"""FT_HOLD_INFO 只处理 AREA=0（不连库）。"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.utils.database_util import (
    mark_hold_infos_dirty,
    query_dirty_hold_infos,
    query_hold_infos_by_record_id,
    query_online_hold_info,
)


def _cursor():
    cursor = MagicMock()
    cursor.description = [('ID',)]
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = (0,)
    cursor.rowcount = 0
    return cursor


def _connect(cursor):
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor
    return conn


class HoldInfoAreaSqlTest(unittest.TestCase):
    @patch('app.utils.database_util.oracledb.connect')
    def test_online_query_requires_area_zero(self, connect):
        cursor = _cursor()
        connect.return_value = _connect(cursor)
        rows = query_online_hold_info('FT_HOLD_INFO_TEST')
        self.assertEqual(rows, [])
        sql = cursor.execute.call_args.args[0]
        self.assertEqual(sql.count('AREA = 0'), 2)
        self.assertIn('HOLDING = 0', sql)

    @patch('app.utils.database_util.oracledb.connect')
    def test_linked_infos_require_area_zero(self, connect):
        cursor = _cursor()
        connect.return_value = _connect(cursor)
        rows = query_hold_infos_by_record_id(8, info_table='FT_HOLD_INFO_TEST')
        self.assertEqual(rows, [])
        sql = cursor.execute.call_args.args[0]
        self.assertIn('HOLD_RECORD_ID = :record_id', sql)
        self.assertIn('AREA = 0', sql)

    @patch('app.utils.database_util.oracledb.connect')
    def test_dirty_list_requires_area_zero(self, connect):
        cursor = _cursor()
        connect.return_value = _connect(cursor)
        items, total = query_dirty_hold_infos(info_table='FT_HOLD_INFO_TEST')
        self.assertEqual(items, [])
        self.assertEqual(total, 0)
        sqls = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertTrue(sqls)
        for sql in sqls:
            self.assertIn('AREA = 0', sql)
            self.assertIn('HOLD_RECORD_ID = :dirty_id', sql)

    @patch('app.utils.database_util.oracledb.connect')
    def test_mark_dirty_does_not_touch_other_area(self, connect):
        cursor = _cursor()
        connect.return_value = _connect(cursor)
        n = mark_hold_infos_dirty([3], info_table='FT_HOLD_INFO_TEST', reason='x')
        self.assertEqual(n, 0)
        sql = cursor.execute.call_args.args[0]
        self.assertIn('AREA = 0', sql)
        self.assertIn('NVL(HOLD_RECORD_ID, 0) = 0', sql)


if __name__ == '__main__':
    unittest.main()
