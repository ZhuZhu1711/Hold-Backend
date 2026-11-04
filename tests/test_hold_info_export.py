"""Hold Info 导出：筛选校验 + 工程师仅所属型号。"""
from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from app.controllers.hold_info_export_ctrl import (
    LOOKUP_MAX_IDS,
    RECORDS_MAX_ROWS,
    _collect_rows,
    _ensure_owned_product,
    _latest_hold_per_wafer,
    _parse_filters,
    _prepare_export_holds,
    _process_hold,
    export_hold_info_xlsx,
    preview_hold_info_export,
    query_hold_info_records,
    query_hold_wafers,
    query_merge_sources,
    query_pre_handlers,
    query_test_programs,
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


def _hold(wafer, lot, dttm, hold_id, route='PRODUCTION'):
    return (wafer, 'P1', 'EQ', dttm, 'reason', route, lot, 'MP', hold_id)


class LatestHoldPerWaferTest(unittest.TestCase):
    def test_same_wafer_keeps_newest(self):
        rows = _latest_hold_per_wafer([
            _hold('C1-01', 'C1', '2026-01-01 00:00:00', 1),
            _hold('C1-02', 'C1', '2026-01-01 08:00:00', 2),
            _hold('C1-01', 'C1', '2026-01-03 00:00:00', 3),
        ])
        self.assertEqual([r[8] for r in rows], [2, 3])

    def test_same_timestamp_keeps_larger_id(self):
        rows = _latest_hold_per_wafer([
            _hold('C1-01', 'C1', '2026-01-01 00:00:00', 5),
            _hold('C1-01', 'C1', '2026-01-01 00:00:00', 9),
        ])
        self.assertEqual([r[8] for r in rows], [9])

    def test_display_wafer_matches_full_id(self):
        rows = _latest_hold_per_wafer([
            _hold('#01', '679PK7.01', '2026-01-01 00:00:00', 1),
            _hold('679PK7-01', '679PK7', '2026-01-02 00:00:00', 2),
            _hold('#14', '679PK7.14', '2026-01-02 00:00:00', 4),
        ])
        self.assertEqual([r[8] for r in rows], [2, 4])

    def test_missing_wafer_stays_separate(self):
        rows = _latest_hold_per_wafer([
            _hold('', 'C1', '2026-01-01 00:00:00', 1),
            _hold('', 'C1', '2026-01-02 00:00:00', 2),
        ])
        self.assertEqual([r[8] for r in rows], [1, 2])

    def test_hold_time_is_earliest_in_lot(self):
        prepared = _prepare_export_holds([
            _hold('C1-01', 'C1', '2026-01-05 00:00:00', 5),
            _hold('C1-01', 'C1', '2026-01-01 00:00:00', 1),
            _hold('C1-02', 'C1-02', '2026-01-03 00:00:00', 3),
            _hold('C2-01', 'C2', '2026-01-02 00:00:00', 2),
        ])
        by_id = {hold[8]: hold_time for hold, hold_time in prepared}
        self.assertEqual(set(by_id), {5, 3, 2})
        self.assertEqual(by_id[5], '2026-01-01 00:00:00')
        self.assertEqual(by_id[3], '2026-01-01 00:00:00')
        self.assertEqual(by_id[2], '2026-01-02 00:00:00')
        kept = {hold[8]: hold[3] for hold, _hold_time in prepared}
        self.assertEqual(kept[5], '2026-01-05 00:00:00')


class CollectRowsDedupeTest(unittest.TestCase):
    @patch('app.controllers.hold_info_export_ctrl._process_hold')
    @patch('app.controllers.hold_info_export_ctrl._query_hold_records')
    @patch('app.controllers.hold_info_export_ctrl._connect')
    def test_dedupe_before_limit(self, connect, query, process):
        connect.return_value = Mock()
        query.return_value = [
            _hold('C1-01', 'C1', '2026-01-01 00:00:00', 1),
            _hold('C2-01', 'C2', '2026-01-01 08:00:00', 2),
            _hold('C1-01', 'C1', '2026-01-03 00:00:00', 3),
        ]
        process.side_effect = lambda cursor, hold, caches, extras, hold_time=None: (
            [hold[6]], {'LOT': hold[6], 'ID': hold[8]},
        )
        ok, msg, payload = _collect_rows(
            'P1', '2026-01-01 00:00:00', '2026-01-04 23:59:59', {}, 1,
        )
        self.assertTrue(ok)
        self.assertEqual(payload['total'], 2)
        self.assertTrue(payload['truncated'])
        self.assertEqual(payload['preview_rows'], [{'LOT': 'C2', 'ID': 2}])
        self.assertEqual(process.call_count, 1)

    @patch('app.controllers.hold_info_export_ctrl.lookup_lot_oa', return_value='')
    @patch('app.controllers.hold_info_export_ctrl.lookup_pre_handler', return_value='')
    @patch('app.controllers.hold_info_export_ctrl._query_hold_wafers_by_lot', return_value=set())
    @patch('app.controllers.hold_info_export_ctrl._query_test_program', return_value='')
    def test_excel_hold_time_uses_earliest(self, _program, _wafers, _handler, _oa):
        hold = _hold('C1-09', 'C1', '2026-01-03 00:00:00', 3)
        row, preview = _process_hold(
            Mock(), hold,
            {'merge': {}, 'hold_wafers': {}, 'test_program': {}},
            {},
            hold_time='2026-01-01 00:00:00',
        )
        self.assertEqual(row[4], 'GC_FT')
        self.assertEqual(row[11], 'FT1')
        self.assertEqual(row[18], '2026-01-01 00:00:00')
        self.assertEqual(row[25], '2026-01-03 00:00:00')
        self.assertEqual(preview['HOLD_DATETIME'], '2026-01-01 00:00:00')
        self.assertEqual(row[15], '')
        self.assertEqual(row[26], '')
        self.assertEqual(preview['PRE_HANDLER'], '')
        self.assertEqual(preview['OA_NUMBER'], '')
        _handler.assert_not_called()

    @patch('app.controllers.hold_info_export_ctrl.lookup_lot_oa', return_value='ET.202610218')
    @patch('app.controllers.hold_info_export_ctrl.lookup_pre_handler', return_value='周星辰（A0004655）')
    @patch('app.controllers.hold_info_export_ctrl._query_hold_wafers_by_lot', return_value=set())
    @patch('app.controllers.hold_info_export_ctrl._query_test_program', return_value='')
    def test_engineering_pre_handler_uses_oa(self, _program, _wafers, handler, lot_oa):
        hold = _hold('C1-09', 'C1', '2026-01-03 00:00:00', 3, route='ENGINEERING')
        cursor = Mock()
        _row, preview = _process_hold(
            cursor, hold,
            {'merge': {}, 'hold_wafers': {}, 'test_program': {}},
            {},
        )
        self.assertEqual(preview['PRE_HANDLER'], '周星辰（A0004655）')
        self.assertEqual(preview['OA_NUMBER'], 'ET.202610218')
        self.assertEqual(_row[26], 'ET.202610218')
        self.assertEqual(handler.call_args[0][0], 'C1')
        self.assertEqual(lot_oa.call_args[0][0], 'C1')
        cursor.execute.assert_not_called()

    @patch('app.controllers.hold_info_export_ctrl.lookup_lot_oa', return_value='ET.MP')
    @patch('app.controllers.hold_info_export_ctrl.lookup_pre_handler', return_value='不应调用')
    @patch('app.controllers.hold_info_export_ctrl._query_hold_wafers_by_lot', return_value=set())
    @patch('app.controllers.hold_info_export_ctrl._query_test_program', return_value='')
    def test_mp_pre_handler_uses_newest_pie(self, _program, _wafers, handler, _oa):
        cursor = Mock()
        cursor.fetchall.return_value = [
            ('旧PIE', '2026-01-01', 1),
            ('  张三  ', '2026-02-01', 2),
            ('   ', '2026-03-01', 3),
        ]
        hold = _hold('C1-09', 'C1', '2026-01-03 00:00:00', 3, route='PRODUCTION')
        _row, preview = _process_hold(
            cursor, hold,
            {'merge': {}, 'hold_wafers': {}, 'test_program': {}},
            {},
        )
        self.assertEqual(preview['PRE_HANDLER'], '张三')
        self.assertEqual(preview['OA_NUMBER'], 'ET.MP')
        handler.assert_not_called()
        sql, binds = cursor.execute.call_args[0]
        self.assertIn('PRODUCT_INFO', sql)
        self.assertEqual(set(binds.values()), {'P1', 'P1-3.5', 'P1-2.6'})


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


class QueryHoldInfoRecordsTest(unittest.TestCase):
    @patch('app.controllers.hold_info_export_ctrl._connect')
    @patch('app.controllers.hold_info_export_ctrl._query_hold_records')
    def test_returns_raw_rows(self, query, connect):
        connect.return_value = Mock()
        query.return_value = [
            _hold('C1-01', 'C1', '2026-01-01 00:00:00', 7, route='ENG'),
        ]
        ok, msg, data = query_hold_info_records(
            'P1', '2026-01-01', '2026-01-02', lot_id='C1', route_id='ENG',
        )
        self.assertTrue(ok)
        self.assertEqual(msg, '查询成功')
        self.assertFalse(data['truncated'])
        self.assertEqual(data['shown'], 1)
        self.assertEqual(data['lot_id'], 'C1')
        self.assertEqual(data['route_id'], 'ENG')
        self.assertEqual(data['pie'], '')
        self.assertEqual(data['items'], [{
            'wafer_id': 'C1-01',
            'product_id': 'P1',
            'equip_id': 'EQ',
            'hold_datetime': '2026-01-01 00:00:00',
            'reason': 'reason',
            'route_type': 'ENG',
            'lot_id': 'C1',
            'route_id': 'MP',
            'id': 7,
        }])
        self.assertEqual(
            query.call_args.kwargs['row_limit'],
            RECORDS_MAX_ROWS + 1,
        )

    @patch('app.controllers.hold_info_export_ctrl.RECORDS_MAX_ROWS', 1)
    @patch('app.controllers.hold_info_export_ctrl._connect')
    @patch('app.controllers.hold_info_export_ctrl._query_hold_records')
    def test_truncates_at_safety_cap(self, query, connect):
        connect.return_value = Mock()
        query.return_value = [
            _hold('C1-01', 'C1', '2026-01-01 00:00:00', 1),
            _hold('C1-02', 'C1', '2026-01-02 00:00:00', 2),
        ]
        ok, msg, data = query_hold_info_records('P1', '2026-01-01', '2026-01-02')
        self.assertTrue(ok)
        self.assertTrue(data['truncated'])
        self.assertEqual(data['shown'], 1)
        self.assertEqual(len(data['items']), 1)
        self.assertEqual(data['items'][0]['id'], 1)

    @patch('app.controllers.hold_info_export_ctrl._connect')
    @patch('app.controllers.hold_info_export_ctrl._query_hold_records', return_value=[])
    def test_records_include_family_pie(self, _query, connect):
        cursor = Mock()
        cursor.fetchall.return_value = [('李四', '2026-01-01', 9)]
        connect.return_value.cursor.return_value = cursor
        ok, msg, data = query_hold_info_records(
            'GC1084-3.5', '2026-01-01', '2026-01-02',
        )
        self.assertTrue(ok, msg)
        self.assertEqual(data['pie'], '李四')
        _sql, binds = cursor.execute.call_args[0]
        self.assertEqual(
            set(binds.values()),
            {'GC1084', 'GC1084-3.5', 'GC1084-2.6'},
        )

    @patch('app.controllers.engineer_ctrl.engineer_owns_product', return_value=False)
    @patch('app.controllers.hold_info_export_ctrl._connect')
    def test_engineer_unowned_skips_query(self, connect, _owns):
        ok, msg, data = query_hold_info_records(
            'OTHER-1', '2026-01-01', '2026-01-02', owner_eng_id=12,
        )
        self.assertFalse(ok)
        self.assertIn('不属于', msg)
        self.assertIsNone(data)
        connect.assert_not_called()


class QueryPreHandlersTest(unittest.TestCase):
    @patch(
        'app.controllers.hold_info_export_ctrl.lookup_lot_oa',
        side_effect=lambda lot_id, cache=None: f'ET.{lot_id}',
    )
    @patch(
        'app.controllers.hold_info_export_ctrl.lookup_pre_handler',
        side_effect=lambda lot_id, cache=None, oa_cache=None: f'周星辰（{lot_id}）',
    )
    def test_returns_formatted_handlers(self, lookup, lot_oa):
        ok, msg, data = query_pre_handlers('P1', ['C1', ' C1 ', '', 'C2'])
        self.assertTrue(ok, msg)
        self.assertEqual(data['pre_handlers'], {
            'C1': '周星辰（C1）',
            'C2': '周星辰（C2）',
        })
        self.assertEqual(data['oa_numbers'], {'C1': 'ET.C1', 'C2': 'ET.C2'})
        self.assertEqual(lookup.call_count, 2)
        self.assertEqual(lot_oa.call_count, 2)

    @patch(
        'app.controllers.hold_info_export_ctrl.lookup_lot_oa',
        side_effect=lambda lot_id, cache=None: 'ET.1',
    )
    @patch('app.controllers.hold_info_export_ctrl.lookup_pre_handler')
    def test_mp_lots_skip_pre_handler_chain(self, lookup, _oa):
        ok, msg, data = query_pre_handlers(
            'P1', ['C1'], pre_handler_lot_ids=[],
        )
        self.assertTrue(ok, msg)
        self.assertEqual(data['oa_numbers'], {'C1': 'ET.1'})
        self.assertEqual(data['pre_handlers'], {'C1': ''})
        lookup.assert_not_called()

    @patch('app.controllers.hold_info_export_ctrl.lookup_lot_oa')
    @patch('app.controllers.hold_info_export_ctrl.lookup_pre_handler')
    def test_empty_lots_skip_oa(self, lookup, lot_oa):
        ok, msg, data = query_pre_handlers('P1', [])
        self.assertTrue(ok, msg)
        self.assertEqual(data['pre_handlers'], {})
        self.assertEqual(data['oa_numbers'], {})
        lookup.assert_not_called()
        lot_oa.assert_not_called()

    def test_rejects_too_many_lots(self):
        ok, msg, data = query_pre_handlers(
            'P1', [str(i) for i in range(LOOKUP_MAX_IDS + 1)],
        )
        self.assertFalse(ok)
        self.assertIn('无效', msg)
        self.assertIsNone(data)

    @patch('app.controllers.engineer_ctrl.engineer_owns_product', return_value=False)
    @patch('app.controllers.hold_info_export_ctrl.lookup_lot_oa')
    @patch('app.controllers.hold_info_export_ctrl.lookup_pre_handler')
    def test_engineer_unowned_skips_oa(self, lookup, lot_oa, _owns):
        ok, msg, data = query_pre_handlers('OTHER-1', ['C1'], owner_eng_id=3)
        self.assertFalse(ok)
        self.assertIn('不属于', msg)
        self.assertIsNone(data)
        lookup.assert_not_called()
        lot_oa.assert_not_called()


class QueryLookupTest(unittest.TestCase):
    def test_merge_sources_rejects_too_many_ids(self):
        ok, msg, data = query_merge_sources(
            'P1', [str(i) for i in range(LOOKUP_MAX_IDS + 1)],
        )
        self.assertFalse(ok)
        self.assertIn('无效', msg)
        self.assertIsNone(data)

    def test_empty_lots_skip_database(self):
        with patch('app.controllers.hold_info_export_ctrl._connect') as connect:
            ok, msg, data = query_hold_wafers('P1', [])
        self.assertTrue(ok)
        self.assertEqual(data['wafers'], {})
        connect.assert_not_called()

    def test_missing_product(self):
        ok, msg, data = query_test_programs('', [{'wafer_id': 'C1-01', 'route': 'ENG'}])
        self.assertFalse(ok)
        self.assertIn('型号', msg)
        self.assertIsNone(data)

    @patch('app.controllers.engineer_ctrl.engineer_owns_product', return_value=False)
    @patch('app.controllers.hold_info_export_ctrl._connect')
    def test_lookup_requires_owned_product(self, connect, _owns):
        ok, msg, data = query_merge_sources('OTHER-1', ['C1-001'], owner_eng_id=3)
        self.assertFalse(ok)
        self.assertIn('不属于', msg)
        connect.assert_not_called()

    @patch('app.controllers.hold_info_export_ctrl._record_table', return_value='FT_HOLD_RECORD_TEST')
    @patch('app.controllers.hold_info_export_ctrl._connect')
    def test_hold_wafers_keep_raw_pairs(self, connect, _table):
        cursor = Mock()
        cursor.fetchall.return_value = [('C1', '#01'), ('C1', '#01'), ('C1', 'C1-02')]
        connect.return_value.cursor.return_value = cursor
        ok, msg, data = query_hold_wafers('P1', ['C1'])
        self.assertTrue(ok)
        self.assertEqual(data['wafers']['C1'], [
            {'lot_id': 'C1', 'wafer_id': '#01'},
            {'lot_id': 'C1', 'wafer_id': 'C1-02'},
        ])
        sql, params = cursor.execute.call_args[0]
        self.assertIn('FT_HOLD_RECORD_TEST', sql)
        self.assertEqual(params['pat'], 'C1%')
        self.assertNotIn('C1-01', str(data))

    @patch('app.controllers.hold_info_export_ctrl._connect')
    def test_merge_sources_shape(self, connect):
        cursor = Mock()

        def execute(_sql, params):
            cursor.wafer_id = params['wafer_id']

        def fetchall():
            if cursor.wafer_id == 'C100-001':
                return [('C100-01',), ('C100-02',)]
            return []

        cursor.execute.side_effect = execute
        cursor.fetchall.side_effect = fetchall
        connect.return_value.cursor.return_value = cursor
        ok, msg, data = query_merge_sources('P1', ['C100-001', 'C200-001'])
        self.assertTrue(ok)
        self.assertEqual(data['sources']['C100-001'], ['C100-01', 'C100-02'])
        self.assertEqual(data['sources']['C200-001'], [])

    @patch('app.controllers.hold_info_export_ctrl._connect')
    def test_test_program_shape(self, connect):
        cursor = Mock()
        cursor.fetchone.return_value = ('TP_A',)
        connect.return_value.cursor.return_value = cursor
        ok, msg, data = query_test_programs('P1', [
            {'wafer_id': 'C1-01', 'route': 'PRODUCTION'},
            {'wafer_id': '', 'route': 'ENG'},
        ])
        self.assertTrue(ok)
        self.assertEqual(data['programs'], [{
            'wafer_id': 'C1-01',
            'route': 'PRODUCTION',
            'test_program': 'TP_A',
        }])
        _sql, params = cursor.execute.call_args[0]
        self.assertEqual(params['route_pat'], '%PRODUCTION%')

    @patch('app.controllers.hold_info_export_ctrl._connect')
    def test_engineering_route_matches_eng(self, connect):
        cursor = Mock()
        cursor.fetchone.return_value = ('TP_ENG',)
        connect.return_value.cursor.return_value = cursor
        ok, msg, data = query_test_programs('P1', [
            {'wafer_id': 'C1-01', 'route': 'ENGINEERING'},
        ])
        self.assertTrue(ok, msg)
        self.assertEqual(data['programs'], [{
            'wafer_id': 'C1-01',
            'route': 'ENGINEERING',
            'test_program': 'TP_ENG',
        }])
        _sql, params = cursor.execute.call_args[0]
        self.assertEqual(params['route_pat'], '%ENG%')


class QueryHoldRecordsLimitTest(unittest.TestCase):
    @patch('app.controllers.hold_info_export_ctrl._record_table', return_value='FT_HOLD_RECORD')
    def test_row_limit_wraps_ordered_query(self, _table):
        from app.controllers.hold_info_export_ctrl import _query_hold_records

        cursor = Mock()
        cursor.fetchall.return_value = []
        _query_hold_records(
            cursor, 'P1', '2026-01-01 00:00:00', '2026-01-02 23:59:59',
            route_id='MP', row_limit=3,
        )
        sql, params = cursor.execute.call_args[0]
        self.assertIn('ROWNUM <= :row_limit', sql)
        self.assertIn('ORDER BY', sql)
        self.assertLess(sql.index('ORDER BY'), sql.index('ROWNUM'))
        self.assertEqual(params['row_limit'], 3)
        self.assertEqual(params['route_id'], '%MP%')


if __name__ == '__main__':
    unittest.main()
