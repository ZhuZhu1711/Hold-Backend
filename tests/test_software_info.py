"""SOFTWARE_INFO 版本查询（不连真实库）。"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from sqlalchemy.exc import SQLAlchemyError

from app.controllers.common_data_ctrl import get_latest_software_info


class LatestSoftwareInfoTest(unittest.TestCase):
    @patch('app.controllers.common_data_ctrl.db')
    def test_reads_version_and_comment(self, db):
        db.session.execute.return_value.first.return_value = ('1.2.0', '请更新')
        ok, msg, data = get_latest_software_info()
        self.assertTrue(ok)
        self.assertEqual(msg, 'success')
        self.assertEqual(data, {'version': '1.2.0', 'comment': '请更新'})
        sql = str(db.session.execute.call_args[0][0])
        self.assertIn('"comment"', sql)
        self.assertIn('ROWNUM', sql)

    @patch('app.controllers.common_data_ctrl.db')
    def test_empty_table(self, db):
        db.session.execute.return_value.first.return_value = None
        ok, msg, data = get_latest_software_info()
        self.assertTrue(ok)
        self.assertEqual(data, {'version': '', 'comment': ''})

    @patch('app.controllers.common_data_ctrl.db')
    def test_db_error(self, db):
        db.session.execute.side_effect = SQLAlchemyError('boom')
        ok, msg, data = get_latest_software_info()
        self.assertFalse(ok)
        self.assertIsNone(data)
        self.assertIn('查询失败', msg)
