"""SOFTWARE_INFO 版本查询 / 更新（不连真实库）。"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from sqlalchemy.exc import SQLAlchemyError

from app.controllers.common_data_ctrl import (
    get_latest_software_info,
    update_latest_software_info,
)


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


class UpdateSoftwareInfoTest(unittest.TestCase):
    def test_empty_version_rejected(self):
        ok, msg, data = update_latest_software_info('  ', 'note')
        self.assertFalse(ok)
        self.assertIsNone(data)
        self.assertIn('请填写', msg)

    def test_comment_too_long_rejected(self):
        ok, msg, data = update_latest_software_info('1.0.0', '测' * 2000)
        self.assertFalse(ok)
        self.assertIsNone(data)
        self.assertIn('过长', msg)

    @patch('app.controllers.common_data_ctrl.db')
    def test_update_existing_row(self, db):
        db.session.execute.return_value.rowcount = 1
        ok, msg, data = update_latest_software_info('2.0.8', '修复合批')
        self.assertTrue(ok)
        self.assertEqual(msg, '保存成功')
        self.assertEqual(data, {'version': '2.0.8', 'comment': '修复合批'})
        sql = str(db.session.execute.call_args[0][0])
        self.assertIn('UPDATE SOFTWARE_INFO', sql)
        self.assertIn('"comment"', sql)
        db.session.commit.assert_called_once()

    @patch('app.controllers.common_data_ctrl.db')
    def test_insert_when_table_empty(self, db):
        first = MagicMock()
        first.rowcount = 0
        db.session.execute.side_effect = [first, MagicMock()]
        ok, msg, data = update_latest_software_info('1.0.0', '首发')
        self.assertTrue(ok)
        self.assertEqual(data['version'], '1.0.0')
        sqls = [str(call.args[0]) for call in db.session.execute.call_args_list]
        self.assertTrue(any('UPDATE' in sql for sql in sqls))
        self.assertTrue(any('INSERT' in sql for sql in sqls))
        db.session.commit.assert_called_once()


if __name__ == '__main__':
    unittest.main()
