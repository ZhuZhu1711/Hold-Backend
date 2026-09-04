"""Hold Info 导出：筛选校验 + 工程师仅所属型号。"""
from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from app.controllers.hold_info_export_ctrl import (
    _ensure_owned_product,
    _parse_filters,
    export_hold_info_xlsx,
    preview_hold_info_export,
)


def _ok_payload(product_id='GC1084-3.5'):
    return True, 'ok', {
        'excel_rows': [],
        'preview_rows': [],
        'total': 0,
        'truncated': False,
        'product_id': product_id,
        'lot_id': '',
        'start_dttm': '2026-01-01 00:00:00',
        'end_dttm': '2026-01-02 23:59:59',
    }


class ParseFiltersTest(unittest.TestCase):
    def test_require_product(self):
        ok, msg, parsed = _parse_filters('', '2026-01-01', '2026-01-02')
        self.assertFalse(ok)
        self.assertIn('型号', msg)
        self.assertIsNone(parsed)

    def test_end_before_start(self):
        ok, msg, parsed = _parse_filters('P1', '2026-01-02', '2026-01-01')
        self.assertFalse(ok)
        self.assertIn('不能早于', msg)
        self.assertIsNone(parsed)

    def test_ok(self):
        ok, msg, parsed = _parse_filters('P1', '2026-01-01', '2026-01-02')
        self.assertTrue(ok)
        self.assertEqual(parsed[0], 'P1')
        self.assertEqual(parsed[1], '2026-01-01 00:00:00')
        self.assertEqual(parsed[2], '2026-01-02 23:59:59')
        self.assertEqual(parsed[3], '')
        self.assertEqual(parsed[4], '')

    def test_optional_lot_and_route(self):
        ok, msg, parsed = _parse_filters(
            'P1', '2026-01-01', '2026-01-02',
            lot_id=' C196717 ', route_id=' MP ',
        )
        self.assertTrue(ok)
        self.assertEqual(parsed[3], 'C196717')
        self.assertEqual(parsed[4], 'MP')


class OwnedProductGuardTest(unittest.TestCase):
    def test_root_skips_check(self):
        ok, msg = _ensure_owned_product('OTHER-1', owner_eng_id=None)
        self.assertTrue(ok)
        self.assertEqual(msg, 'ok')

    @patch('app.controllers.engineer_ctrl.engineer_owns_product', return_value=False)
    def test_engineer_rejects_unowned(self, _owns):
        ok, msg = _ensure_owned_product('OTHER-1', owner_eng_id=12)
        self.assertFalse(ok)
        self.assertIn('不属于', msg)

    @patch('app.controllers.engineer_ctrl.engineer_owns_product', return_value=True)
    def test_engineer_allows_owned(self, _owns):
        ok, msg = _ensure_owned_product('GC1084-3.5', owner_eng_id=12)
        self.assertTrue(ok)
        self.assertEqual(msg, 'ok')


class PreviewExportScopeTest(unittest.TestCase):
    @patch('app.controllers.hold_info_export_ctrl._collect_rows')
    def test_preview_root_does_not_check_ownership(self, collect):
        collect.return_value = _ok_payload('OTHER-1')
        ok, msg, data = preview_hold_info_export(
            'OTHER-1', '2026-01-01', '2026-01-02',
        )
        self.assertTrue(ok)
        self.assertEqual(data['product_id'], 'OTHER-1')
        collect.assert_called_once()

    @patch('app.controllers.hold_info_export_ctrl._collect_rows')
    @patch('app.controllers.engineer_ctrl.engineer_owns_product', return_value=False)
    def test_preview_rejects_unowned_before_query(self, _owns, collect):
        ok, msg, data = preview_hold_info_export(
            'OTHER-1', '2026-01-01', '2026-01-02', owner_eng_id=12,
        )
        self.assertFalse(ok)
        self.assertIn('不属于', msg)
        self.assertIsNone(data)
        collect.assert_not_called()

    @patch('app.controllers.hold_info_export_ctrl._collect_rows')
    @patch('app.controllers.engineer_ctrl.engineer_owns_product', return_value=True)
    def test_preview_allows_owned(self, _owns, collect):
        collect.return_value = _ok_payload()
        ok, msg, data = preview_hold_info_export(
            'GC1084-3.5', '2026-01-01', '2026-01-02', owner_eng_id=12,
        )
        self.assertTrue(ok)
        self.assertEqual(data['product_id'], 'GC1084-3.5')
        collect.assert_called_once()

    @patch('app.controllers.hold_info_export_ctrl._collect_rows')
    @patch('app.controllers.engineer_ctrl.engineer_owns_product', return_value=False)
    def test_xlsx_rejects_unowned_before_query(self, _owns, collect):
        ok, msg, content = export_hold_info_xlsx(
            'OTHER-1', '2026-01-01', '2026-01-02', owner_eng_id=12,
        )
        self.assertFalse(ok)
        self.assertIn('不属于', msg)
        self.assertIsNone(content)
        collect.assert_not_called()

    @patch('app.controllers.hold_info_export_ctrl._collect_rows')
    def test_preview_passes_route_id(self, collect):
        collect.return_value = _ok_payload()
        ok, msg, data = preview_hold_info_export(
            'GC1084-3.5', '2026-01-01', '2026-01-02', route_id='ENG',
        )
        self.assertTrue(ok)
        self.assertEqual(data['route_id'], 'ENG')
        self.assertEqual(collect.call_args.kwargs.get('route_id'), 'ENG')


class QueryHoldRecordsFilterTest(unittest.TestCase):
    @patch('app.controllers.hold_info_export_ctrl._record_table', return_value='FT_HOLD_RECORD')
    def test_route_id_like_clause(self, _table):
        from app.controllers.hold_info_export_ctrl import _query_hold_records

        cursor = Mock()
        cursor.fetchall.return_value = []
        _query_hold_records(
            cursor, 'P1', '2026-01-01 00:00:00', '2026-01-02 23:59:59',
            route_id='MP',
        )
        sql, params = cursor.execute.call_args[0]
        self.assertIn('ROUTE_ID', sql)
        self.assertEqual(params['route_id'], '%MP%')
        self.assertNotIn('lot_id', params)

    @patch('app.controllers.hold_info_export_ctrl._record_table', return_value='FT_HOLD_RECORD')
    def test_empty_route_id_omits_clause(self, _table):
        from app.controllers.hold_info_export_ctrl import _query_hold_records

        cursor = Mock()
        cursor.fetchall.return_value = []
        _query_hold_records(
            cursor, 'P1', '2026-01-01 00:00:00', '2026-01-02 23:59:59',
        )
        sql, params = cursor.execute.call_args[0]
        self.assertNotIn(':route_id', sql)
        self.assertNotIn('route_id', params)


if __name__ == '__main__':
    unittest.main()
