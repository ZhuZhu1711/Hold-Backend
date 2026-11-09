"""NCMR 上报清单导出：筛选校验、分表和工程师仅所属型号。"""
from __future__ import annotations

import io
import json
import unittest
import urllib.error
from datetime import datetime
from unittest.mock import MagicMock, Mock, patch

from app.controllers.dispose_ctrl import DISPOSE_NCMR_REPORTED, dispose_record
from app.controllers.hold_info_export_ctrl import (
    LOOKUP_MAX_IDS,
    NCMR_TIMEOUT,
    RECORDS_MAX_ROWS,
    _collect_rows,
    _format_mp_pre_handler,
    _ensure_owned_product,
    _latest_hold_per_wafer,
    _parse_filters,
    _prepare_export_holds,
    _process_hold,
    build_ncmr_import_body,
    export_hold_info_xlsx,
    ncmr_row_from_hold_record,
    report_eng_future_to_ncmr,
    import_hold_info_ncmr,
    import_ncmr_rows,
    post_ncmr_import,
    preview_hold_info_export,
    reset_ncmr_token_cache,
    query_hold_info_records,
    query_hold_wafers,
    query_merge_sources,
    query_pre_handlers,
    query_test_programs,
)


def _sample_ncmr_row():
    """与 ncmr_engineering_GC20C3-3.5_20261106_150747.xlsx 的数据行一致。"""
    return [
        'GC', 'GC20C3', 'GC20C3-3.5', 'CIS', 'GC_FT', 'FT', 'ENGINEERING',
        '3AL585', '3AL585-20', '3AL585', None, 'FT1',
        '3AL585:#20', '3AL585:#20',
        'Reason:FUTURE HOLDV3版本物料', '杨泽鑫（A0003923）',
        None, None, '2026-11-06 10:00:10', None, '',
        None, None, None, None, '2026-11-06 10:00:10', 'ET.202611060',
    ]


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

    def test_all_route_is_unfiltered(self):
        ok, msg, parsed = _parse_filters(
            'P1', '2026-01-01', '2026-01-02', route_id=' all ',
        )
        self.assertTrue(ok)
        self.assertEqual(parsed[4], '')

    def test_rejects_free_text_route(self):
        ok, msg, parsed = _parse_filters(
            'P1', '2026-01-01', '2026-01-02', route_id='FT-MP',
        )
        self.assertFalse(ok)
        self.assertIn('Route', msg)
        self.assertIsNone(parsed)


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


def _hold(wafer, lot, dttm, hold_id, route='PRODUCTION', route_id='MP', station=''):
    return (
        wafer, 'P1', 'EQ', dttm, 'reason', route, lot, route_id, hold_id, station,
    )


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
        pie_rows = [
            ('旧PIE', '2026-01-01', 1),
            ('  张三  ', '2026-02-01', 2),
            ('   ', '2026-03-01', 3),
        ]

        def fetchall():
            sql = cursor.execute.call_args[0][0]
            if 'USERS' in sql:
                return [('A0004655',)]
            return pie_rows

        cursor.fetchall.side_effect = fetchall
        hold = _hold('C1-09', 'C1', '2026-01-03 00:00:00', 3, route='PRODUCTION')
        _row, preview = _process_hold(
            cursor, hold,
            {'merge': {}, 'hold_wafers': {}, 'test_program': {}},
            {},
        )
        self.assertEqual(preview['PRE_HANDLER'], '张三（A0004655）')
        self.assertEqual(preview['OA_NUMBER'], 'ET.MP')
        handler.assert_not_called()
        product_call = next(
            call for call in cursor.execute.call_args_list
            if 'PRODUCT_INFO' in call[0][0]
        )
        self.assertEqual(set(product_call[0][1].values()), {'P1', 'P1-3.5', 'P1-2.6'})

    def test_mp_pie_work_code_and_existing_display(self):
        cursor = Mock()
        cursor.fetchall.return_value = [('周星辰',)]
        self.assertEqual(
            _format_mp_pre_handler(cursor, '4655'),
            '周星辰（A0004655）',
        )
        cursor.reset_mock()
        self.assertEqual(
            _format_mp_pre_handler(cursor, '周星辰（4655）'),
            '周星辰（A0004655）',
        )
        cursor.execute.assert_not_called()


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
        self.assertLess(sql.find("LIKE '%ENG%'"), sql.find("LIKE '%MP%'"))
        self.assertIn('UPPER(NVL(r.ROUTE_ID', sql)
        self.assertIn("NOT LIKE '%ENG%'", sql)
        self.assertIn('r.STATION', sql)
        self.assertIn('c.NEXT_OWNER_ID = NVL(p.PRO_ENG_ID, :system_id)', sql)
        self.assertIn('NVL(r.STATUS, 0) <> :closed', sql)
        self.assertIn('c.DISPOSE = :reported', sql)
        self.assertEqual(params['closed'], 99)
        self.assertEqual(params['reported'], 9)
        self.assertNotIn('route_id', params)
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
        self.assertNotIn("AND UPPER(NVL(r.ROUTE_ID", sql)
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
            'station': '',
            'id': 7,
            'ncmr_reported_at': '',
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
        product_call = next(
            call for call in cursor.execute.call_args_list
            if 'PRODUCT_INFO' in call[0][0]
        )
        self.assertEqual(
            set(product_call[0][1].values()),
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
        self.assertNotIn('route_id', params)
        self.assertIn("NOT LIKE '%ENG%'", sql)


class NcmrSheetTest(unittest.TestCase):
    def test_eng_and_pqc_only(self):
        from app.controllers.hold_info_export_ctrl import is_eng_prehold_pqc

        self.assertTrue(is_eng_prehold_pqc('ft-eng', 'fpqc'))
        self.assertTrue(is_eng_prehold_pqc('ENG-MP', 'PQC'))
        self.assertFalse(is_eng_prehold_pqc('FT-ENG', 'WOQC'))
        self.assertFalse(is_eng_prehold_pqc('FT-MP', 'FPQC'))

    def test_dedupe_then_latest_station_decides_sheet(self):
        from app.controllers.hold_info_export_ctrl import is_eng_prehold_pqc

        latest = _latest_hold_per_wafer([
            _hold('C1-01', 'C1', '2026-01-01 00:00:00', 1, route_id='FT-ENG', station='WOQC'),
            _hold('C1-01', 'C1', '2026-01-03 00:00:00', 2, route_id='FT-ENG', station='FPQC'),
        ])
        self.assertEqual([row[8] for row in latest], [2])
        self.assertTrue(is_eng_prehold_pqc(latest[0][7], latest[0][9]))

    @patch('app.controllers.hold_info_export_ctrl.lookup_lot_oa', return_value='')
    @patch('app.controllers.hold_info_export_ctrl.lookup_pre_handler', return_value='')
    @patch('app.controllers.hold_info_export_ctrl._query_hold_wafers_by_lot', return_value=set())
    @patch('app.controllers.hold_info_export_ctrl._query_test_program', return_value='')
    def test_preview_marks_eng_pqc_sheet(self, _program, _wafers, _handler, _oa):
        hold = _hold(
            'C1-09', 'C1', '2026-01-03 00:00:00', 3,
            route='ENGINEERING', route_id='FT-ENG', station='FPQC',
        )
        _row, preview = _process_hold(
            Mock(), hold, {'merge': {}, 'hold_wafers': {}, 'test_program': {}}, {},
        )
        self.assertEqual(preview['SHEET'], 'eng_pqc')
        self.assertEqual(preview['STATION'], 'FPQC')
        self.assertEqual(preview['RECORD_ID'], 3)
        self.assertEqual(preview['NCMR_REPORTED_AT'], '')

    def test_sheet_key_keeps_eng_pqc_and_mp_only(self):
        from app.controllers.hold_info_export_ctrl import _sheet_key

        self.assertEqual(_sheet_key('FT-ENG', 'FPQC'), 'eng_pqc')
        self.assertEqual(_sheet_key('ENG-MP', 'PQC'), 'eng_pqc')
        self.assertEqual(_sheet_key('FT-MP', 'FPQC'), 'engineering')
        self.assertEqual(_sheet_key('FT-ENG', 'WOQC'), '')

    @patch('app.controllers.hold_info_export_ctrl.lookup_lot_oa', return_value='')
    @patch('app.controllers.hold_info_export_ctrl.lookup_pre_handler', return_value='')
    @patch('app.controllers.hold_info_export_ctrl._query_hold_wafers_by_lot', return_value=set())
    @patch('app.controllers.hold_info_export_ctrl._query_test_program', return_value='')
    def test_preview_keeps_auto_report_time(self, _program, _wafers, _handler, _oa):
        from app.controllers.hold_info_export_ctrl import _process_hold

        hold = _hold(
            'C1-09', 'C1', '2026-01-03 00:00:00', 3,
            route='ENGINEERING', route_id='FT-ENG', station='FPQC',
        ) + ('2026-11-06 10:05:00',)
        _row, preview = _process_hold(
            Mock(), hold, {'merge': {}, 'hold_wafers': {}, 'test_program': {}}, {},
        )
        self.assertEqual(preview['NCMR_REPORTED_AT'], '2026-11-06 10:05:00')

    @patch('app.controllers.hold_info_export_ctrl._find_template', return_value=None)
    @patch('app.controllers.hold_info_export_ctrl._collect_rows')
    def test_xlsx_exports_only_the_selected_sheet(self, collect, _template):
        from io import BytesIO
        from openpyxl import load_workbook

        collect.return_value = (True, 'ok', {
            'excel_rows': [['eng'], ['keep'], ['drop']],
            'preview_rows': [
                {'SHEET': 'eng_pqc', 'RECORD_ID': 1},
                {'SHEET': 'engineering', 'RECORD_ID': 2},
                {'SHEET': 'engineering', 'RECORD_ID': 3},
            ],
            'total': 3,
            'truncated': False,
        })
        ok, msg, content = export_hold_info_xlsx(
            'P1', '2026-01-01', '2026-01-02', sheet='eng_pqc', engineering_ids=[2],
        )
        self.assertTrue(ok, msg)
        book = load_workbook(BytesIO(content))
        self.assertEqual(book.sheetnames, ['ENG预Hold PQC'])
        self.assertEqual(book.active.max_row, 2)
        self.assertEqual(book.active[2][0].value, 'eng')

        ok, msg, content = export_hold_info_xlsx(
            'P1', '2026-01-01', '2026-01-02',
            sheet='engineering', engineering_ids=[2],
        )
        self.assertTrue(ok, msg)
        node = load_workbook(BytesIO(content))
        self.assertEqual(node.sheetnames, ['MP工程测试Hold'])
        self.assertEqual(node.active.max_row, 2)
        self.assertEqual(node.active[2][0].value, 'keep')

        ok, msg, content = export_hold_info_xlsx(
            'P1', '2026-01-01', '2026-01-02', sheet='engineering', engineering_ids=[],
        )
        self.assertTrue(ok, msg)
        empty_node = load_workbook(BytesIO(content))
        self.assertEqual(empty_node.sheetnames, ['MP工程测试Hold'])
        self.assertEqual(empty_node.active.max_row, 1)

    @patch('app.controllers.hold_info_export_ctrl._collect_rows')
    def test_invalid_engineering_ids_skip_query(self, collect):
        ok, msg, content = export_hold_info_xlsx(
            'P1', '2026-01-01', '2026-01-02',
            sheet='engineering', engineering_ids='2',
        )
        self.assertFalse(ok)
        self.assertIn('无效', msg)
        self.assertIsNone(content)
        collect.assert_not_called()


class _NcmrResponse:
    def __init__(self, status, raw):
        self._status = status
        self._raw = raw

    def read(self):
        return self._raw

    def getcode(self):
        return self._status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class NcmrImportBodyTest(unittest.TestCase):
    def test_sample_row_matches_encapsulation_body(self):
        body = build_ncmr_import_body([_sample_ncmr_row()])
        self.assertEqual(body['batchType'], 'ENCAPSULATION')
        group = body['data'][0]
        self.assertIsNone(group['waferHoldInfo'])
        item = group['data'][0]
        self.assertEqual(item['lotId'], '3AL585')
        self.assertEqual(item['sblotId'], '3AL585-20')
        self.assertEqual(item['waferLot'], '3AL585')
        self.assertEqual(item['lotType'], 'ENGINEERING')
        self.assertEqual(item['product'], 'GC20C3')
        self.assertEqual(item['deviceId'], 'GC20C3-3.5')
        self.assertEqual(item['subCustomer'], 'GC')
        self.assertEqual(item['holdReason'], 'Reason:FUTURE HOLDV3版本物料')
        self.assertEqual(item['preProcessOperator'], '杨泽鑫（A0003923）')
        self.assertEqual(item['holdTime'], '2026-11-06 10:00:10')
        self.assertEqual(item['endTime'], '2026-11-06 10:00:10')
        self.assertEqual(item['batchType'], 'ENCAPSULATION')
        self.assertIsNone(item['holdWaferList'])
        self.assertIsNone(item['totalWaferList'])
        self.assertIsNone(item['testProgram'])
        self.assertIsNone(item['workingOrder'])
        self.assertEqual(
            item['lotHoldEntity']['headers'][1],
            'OA单号',
        )
        self.assertEqual(item['lotHoldEntity']['headers'][2], 'Top1 Fail Bin')
        self.assertEqual(item['lotHoldEntity']['headers'][3], 'Top 2 Fail Bin')
        self.assertEqual(
            item['lotHoldEntity']['data'],
            [[None, 'ET.202611060', None, None, None, None, None, None]],
        )

    def test_datetime_cells_become_text(self):
        row = _sample_ncmr_row()
        stamp = datetime(2026, 11, 6, 10, 0, 10)
        row[18] = stamp
        row[25] = stamp
        item = build_ncmr_import_body([row])['data'][0]['data'][0]
        self.assertEqual(item['holdTime'], '2026-11-06 10:00:10')
        self.assertEqual(item['endTime'], '2026-11-06 10:00:10')


class NcmrImportPostTest(unittest.TestCase):
    URL = 'http://ncmr.example/batchImport'
    LOGIN_URL = 'http://ncmr.example/login'
    TOKEN = 'test-token'

    def setUp(self):
        reset_ncmr_token_cache()

    def _config(self, token=''):
        return patch.multiple(
            'app.controllers.hold_info_export_ctrl.Config',
            NCMR_IMPORT_URL=self.URL,
            NCMR_LOGIN_URL=self.LOGIN_URL,
            NCMR_USERNAME='dongxian_zhu',
            NCMR_PASSWORD='vJMtrdKSCbjkn5E4IXBjkA==',
            NCMR_BEARER_TOKEN=token,
        )

    def _open(self, status, raw, calls):
        def fake_urlopen(request, timeout=None):
            calls.append((request, timeout))
            return _NcmrResponse(status, raw)

        return fake_urlopen

    def test_omitted_url_uses_env_token_without_login(self):
        calls = []
        with self._config(token='env-token'), patch(
            'app.controllers.hold_info_export_ctrl.urllib.request.urlopen',
            self._open(200, b'{"code":200,"msg":"ok"}', calls),
        ):
            ok, msg, _data = post_ncmr_import([_sample_ncmr_row()])
        self.assertTrue(ok, msg)
        self.assertEqual(len(calls), 1)
        request, _timeout = calls[0]
        self.assertEqual(request.full_url, self.URL)
        self.assertEqual(request.get_header('Authorization'), 'Bearer env-token')

    def _login_then_import(self, import_results):
        """import_results 按导入调用顺序给出 (status, raw) 或 HTTPError 用 ('http', code, raw)。"""
        calls = []
        state = {'imports': 0}

        def fake_urlopen(request, timeout=None):
            calls.append(request)
            if request.full_url == self.LOGIN_URL:
                return _NcmrResponse(
                    200,
                    b'{"code":200,"msg":"\xe6\x93\x8d\xe4\xbd\x9c\xe6\x88\x90\xe5\x8a\x9f","token":"fresh-token"}',
                )
            index = state['imports']
            state['imports'] += 1
            status, raw = import_results[index]
            if status == 'http':
                code, body = raw
                raise urllib.error.HTTPError(
                    request.full_url, code, 'denied', hdrs=None,
                    fp=io.BytesIO(body),
                )
            return _NcmrResponse(status, raw)

        return calls, fake_urlopen

    def test_login_then_import_uses_returned_token(self):
        calls, fake = self._login_then_import([
            (200, b'{"code":200,"msg":"imported"}'),
            (200, b'{"code":200,"msg":"imported"}'),
        ])
        with self._config(), patch(
            'app.controllers.hold_info_export_ctrl.urllib.request.urlopen',
            fake,
        ), patch('app.controllers.hold_info_export_ctrl.logger') as log:
            ok, msg, data = post_ncmr_import([_sample_ncmr_row()])
            again, again_msg, _again_data = post_ncmr_import([_sample_ncmr_row()])
        self.assertTrue(ok, msg)
        self.assertEqual(msg, 'imported')
        self.assertEqual(data, {'count': 1})
        self.assertTrue(again, again_msg)
        urls = [request.full_url for request in calls]
        self.assertEqual(urls, [self.LOGIN_URL, self.URL, self.URL])
        self.assertNotIn('172.19.15.11', ''.join(urls))
        login = calls[0]
        posted = json.loads(login.data.decode('utf-8'))
        self.assertEqual(posted['username'], 'dongxian_zhu')
        self.assertEqual(posted['password'], 'vJMtrdKSCbjkn5E4IXBjkA==')
        self.assertFalse(login.has_header('Authorization'))
        self.assertEqual(
            calls[1].get_header('Authorization'),
            'Bearer fresh-token',
        )
        for call in log.info.call_args_list:
            self.assertNotIn('vJMtrdKSCbjkn5E4IXBjkA==', str(call))
            self.assertNotIn('fresh-token', str(call))

    def test_login_failure_returns_msg(self):
        def fake_urlopen(request, timeout=None):
            self.assertEqual(request.full_url, self.LOGIN_URL)
            return _NcmrResponse(200, '{"code":500,"msg":"账号或密码错误"}'.encode('utf-8'))

        with self._config(), patch(
            'app.controllers.hold_info_export_ctrl.urllib.request.urlopen',
            fake_urlopen,
        ):
            ok, msg, data = post_ncmr_import([_sample_ncmr_row()])
        self.assertFalse(ok)
        self.assertEqual(msg, '账号或密码错误')
        self.assertIsNone(data)

    def test_expired_token_relogin_once(self):
        calls, fake = self._login_then_import([
            ('http', (401, b'{"msg":"token expired"}')),
            (200, b'{"code":200,"msg":"imported"}'),
        ])
        with self._config(token='stale-token'), patch(
            'app.controllers.hold_info_export_ctrl.urllib.request.urlopen',
            fake,
        ):
            ok, msg, data = post_ncmr_import([_sample_ncmr_row()])
        self.assertTrue(ok, msg)
        self.assertEqual(data, {'count': 1})
        self.assertEqual(
            [request.full_url for request in calls],
            [self.URL, self.LOGIN_URL, self.URL],
        )
        self.assertEqual(calls[0].get_header('Authorization'), 'Bearer stale-token')
        self.assertEqual(calls[2].get_header('Authorization'), 'Bearer fresh-token')

    def test_expired_retry_still_asks_to_login(self):
        calls, fake = self._login_then_import([
            (200, b'{"code":401,"msg":"\xe7\x99\xbb\xe5\xbd\x95\xe5\xb7\xb2\xe8\xbf\x87\xe6\x9c\x9f"}'),
            (200, b'{"code":401,"msg":"\xe7\x99\xbb\xe5\xbd\x95\xe5\xb7\xb2\xe8\xbf\x87\xe6\x9c\x9f"}'),
        ])
        with self._config(), patch(
            'app.controllers.hold_info_export_ctrl.urllib.request.urlopen',
            fake,
        ):
            ok, msg, data = post_ncmr_import([_sample_ncmr_row()])
        self.assertFalse(ok)
        self.assertEqual(msg, 'NCMR登录已过期，请重新登录')
        self.assertIsNone(data)
        self.assertEqual(
            [request.full_url for request in calls],
            [self.LOGIN_URL, self.URL, self.LOGIN_URL, self.URL],
        )

    def test_business_failure_does_not_relogin(self):
        calls = []

        def fake_urlopen(request, timeout=None):
            calls.append(request.full_url)
            return _NcmrResponse(200, b'{"code":500,"message":"lot duplicated"}')

        with self._config(token='env-token'), patch(
            'app.controllers.hold_info_export_ctrl.urllib.request.urlopen',
            fake_urlopen,
        ):
            ok, msg, data = post_ncmr_import([_sample_ncmr_row()])
        self.assertFalse(ok)
        self.assertEqual(msg, 'lot duplicated')
        self.assertIsNone(data)
        self.assertEqual(calls, [self.URL])

    def test_posts_bearer_body_without_real_host(self):
        calls = []
        with patch(
            'app.controllers.hold_info_export_ctrl.urllib.request.urlopen',
            self._open(200, b'{"code":200,"msg":"imported"}', calls),
        ):
            ok, msg, data = post_ncmr_import(
                [_sample_ncmr_row()], url=self.URL, token=self.TOKEN,
            )
        self.assertTrue(ok, msg)
        self.assertEqual(msg, 'imported')
        self.assertEqual(data, {'count': 1})
        request, timeout = calls[0]
        self.assertEqual(request.full_url, self.URL)
        self.assertNotIn('172.19.15.11', request.full_url)
        self.assertEqual(timeout, NCMR_TIMEOUT)
        self.assertEqual(request.get_header('Authorization'), 'Bearer test-token')
        posted = json.loads(request.data.decode('utf-8'))
        item = posted['data'][0]['data'][0]
        self.assertEqual(item['lotId'], '3AL585')
        self.assertIsNone(item['holdWaferList'])
        self.assertIsNone(item['totalWaferList'])
        self.assertEqual(item['lotHoldEntity']['data'][0][1], 'ET.202611060')

    def test_empty_rows_do_not_call_ncmr(self):
        with patch(
            'app.controllers.hold_info_export_ctrl.urllib.request.urlopen',
        ) as urlopen:
            ok, msg, data = post_ncmr_import([])
        self.assertFalse(ok)
        self.assertIn('没有可上报', msg)
        self.assertIsNone(data)
        urlopen.assert_not_called()

    def test_http_error_is_failure(self):
        def fake_urlopen(request, timeout=None):
            raise urllib.error.HTTPError(
                request.full_url, 401, 'denied', hdrs=None,
                fp=io.BytesIO('{"msg":"bad token"}'.encode('utf-8')),
            )

        with patch(
            'app.controllers.hold_info_export_ctrl.urllib.request.urlopen',
            fake_urlopen,
        ):
            ok, msg, data = post_ncmr_import(
                [_sample_ncmr_row()], url=self.URL, token=self.TOKEN,
            )
        self.assertFalse(ok)
        self.assertIn('401', msg)
        self.assertIn('bad token', msg)
        self.assertIsNone(data)

    def test_business_code_is_failure(self):
        calls = []
        with patch(
            'app.controllers.hold_info_export_ctrl.urllib.request.urlopen',
            self._open(200, b'{"code":500,"message":"lot duplicated"}', calls),
        ):
            ok, msg, data = post_ncmr_import(
                [_sample_ncmr_row()], url=self.URL, token=self.TOKEN,
            )
        self.assertFalse(ok)
        self.assertEqual(msg, 'lot duplicated')
        self.assertIsNone(data)

    def test_success_false_is_failure(self):
        calls = []
        raw = b'{"code":200,"success":false,"msg":"rejected"}'
        with patch(
            'app.controllers.hold_info_export_ctrl.urllib.request.urlopen',
            self._open(200, raw, calls),
        ):
            ok, msg, _data = post_ncmr_import(
                [_sample_ncmr_row()], url=self.URL, token=self.TOKEN,
            )
        self.assertFalse(ok)
        self.assertEqual(msg, 'rejected')

    def test_invalid_client_rows(self):
        ok, msg, data = import_ncmr_rows('3AL585')
        self.assertFalse(ok)
        self.assertIn('无效', msg)
        self.assertIsNone(data)

    @patch('app.controllers.hold_info_export_ctrl.post_ncmr_import')
    @patch('app.controllers.hold_info_export_ctrl._collect_rows')
    def test_sheet_import_skips_ncmr_when_none_selected(self, collect, post):
        collect.return_value = (True, 'ok', {
            'excel_rows': [['eng'], ['node']],
            'preview_rows': [
                {'SHEET': 'eng_pqc', 'RECORD_ID': 1},
                {'SHEET': 'engineering', 'RECORD_ID': 2},
            ],
            'total': 2,
            'truncated': False,
        })
        ok, msg, data = import_hold_info_ncmr(
            'P1', '2026-01-01', '2026-01-02',
            sheet='engineering', engineering_ids=[],
        )
        self.assertFalse(ok)
        self.assertIn('没有可上报', msg)
        self.assertIsNone(data)
        post.assert_not_called()

    @patch('app.controllers.hold_info_export_ctrl.mark_hold_record_ncmr_reported', return_value=True)
    @patch('app.controllers.hold_info_export_ctrl.post_ncmr_import', return_value=(True, 'imported', {'count': 1}))
    @patch('app.controllers.hold_info_export_ctrl._collect_rows')
    def test_sheet_import_posts_selected_rows(self, collect, post, mark):
        sample = _sample_ncmr_row()
        collect.return_value = (True, 'ok', {
            'excel_rows': [sample, ['other']],
            'preview_rows': [
                {'SHEET': 'engineering', 'RECORD_ID': 8},
                {'SHEET': 'engineering', 'RECORD_ID': 9},
            ],
            'total': 2,
            'truncated': False,
        })
        ok, msg, data = import_hold_info_ncmr(
            'GC20C3-3.5', '2026-11-06 00:00:00', '2026-11-06 23:59:59',
            sheet='engineering', engineering_ids=[8],
        )
        self.assertTrue(ok, msg)
        self.assertIn('MP工程测试Hold', msg)
        self.assertIn('流转已写入 1 条', msg)
        self.assertEqual(data['count'], 1)
        self.assertEqual(data['circulation_marked'], 1)
        posted_rows = post.call_args[0][0]
        self.assertEqual(posted_rows, [sample])
        mark.assert_called_once()
        self.assertEqual(mark.call_args.args[0], 8)

    def test_eng_sheet_is_not_posted(self):
        ok, msg, data = import_hold_info_ncmr(
            'P1', '2026-01-01', '2026-01-02', sheet='eng_pqc',
        )
        self.assertFalse(ok)
        self.assertIn('不能手动上报', msg)
        self.assertIsNone(data)


def _eng_future_record():
    return {
        'PRODUCT_ID': 'GC20C3-3.5',
        'LOT_ID': '3AL585',
        'WAFER_ID': '3AL585-20',
        'ROUTE_ID': 'FT-ENG',
        'STATION': 'FPQC',
        'HOLD_REASON': 'Reason:FUTURE HOLDV3版本物料',
        'HOLD_DTTM': datetime(2026, 11, 6, 10, 0, 10),
        'RECORD_TYPE': 3,
        'STATUS': 0,
    }


class EngFutureNcmrTest(unittest.TestCase):
    @patch(
        'app.controllers.hold_info_export_ctrl.lookup_pre_handler',
        return_value='杨泽鑫（A0003923）',
    )
    @patch(
        'app.controllers.hold_info_export_ctrl.lookup_lot_oa',
        return_value='ET.202611060',
    )
    def test_record_becomes_encapsulation_body(self, _oa, _handler):
        body = build_ncmr_import_body([ncmr_row_from_hold_record(_eng_future_record())])
        item = body['data'][0]['data'][0]
        self.assertEqual(body['batchType'], 'ENCAPSULATION')
        self.assertEqual(item['lotId'], '3AL585')
        self.assertEqual(item['sblotId'], '3AL585-20')
        self.assertEqual(item['lotType'], 'ENGINEERING')
        self.assertEqual(item['deviceId'], 'GC20C3-3.5')
        self.assertEqual(item['holdTime'], '2026-11-06 10:00:10')
        self.assertEqual(item['preProcessOperator'], '杨泽鑫（A0003923）')
        self.assertIsNone(item['holdWaferList'])
        self.assertIsNone(item['totalWaferList'])
        self.assertEqual(item['lotHoldEntity']['data'][0][1], 'ET.202611060')
        _oa.assert_called()

    @patch(
        'app.controllers.hold_info_export_ctrl.lookup_pre_handler',
        return_value='',
    )
    @patch(
        'app.controllers.hold_info_export_ctrl.lookup_lot_oa',
        return_value='',
    )
    @patch(
        'app.controllers.hold_info_export_ctrl.mark_hold_record_ncmr_reported',
        return_value=True,
    )
    @patch(
        'app.controllers.hold_info_export_ctrl.post_ncmr_import',
        return_value=(True, 'imported', {'count': 1}),
    )
    def test_success_marks_reported(self, post, mark, _oa, _handler):
        ok, msg = report_eng_future_to_ncmr(8, _eng_future_record())
        self.assertTrue(ok, msg)
        post.assert_called_once()
        mark.assert_called_once_with(8, record_table='FT_HOLD_RECORD')

    @patch(
        'app.controllers.hold_info_export_ctrl.lookup_pre_handler',
        return_value='',
    )
    @patch(
        'app.controllers.hold_info_export_ctrl.lookup_lot_oa',
        return_value='',
    )
    @patch(
        'app.controllers.hold_info_export_ctrl.mark_hold_record_ncmr_reported',
        return_value=True,
    )
    @patch(
        'app.controllers.hold_info_export_ctrl.post_ncmr_import',
        return_value=(False, 'NCMR 连接失败: down', None),
    )
    def test_failure_does_not_mark(self, _post, mark, _oa, _handler):
        ok, msg = report_eng_future_to_ncmr(8, _eng_future_record())
        self.assertFalse(ok)
        self.assertIn('连接失败', msg)
        mark.assert_not_called()


class NcmrReportedStatusTest(unittest.TestCase):
    def _connect(self, cursor):
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cursor
        return conn

    @patch('app.utils.database_util.oracledb.connect')
    def test_mark_writes_status_nine_only_from_create(self, connect):
        from app.utils.database_util import mark_hold_record_ncmr_reported

        cursor = MagicMock()
        cursor.fetchone.side_effect = [(0, 55), (15,), (100,)]
        cursor.rowcount = 1
        connect.return_value = self._connect(cursor)
        self.assertTrue(mark_hold_record_ncmr_reported(8))
        sqls = [call.args[0] for call in cursor.execute.call_args_list]
        update = next(sql for sql in sqls if sql.lstrip().startswith('UPDATE'))
        self.assertIn('NOT IN (:reported, :closed)', update)
        insert_params = next(
            call.args[1] for call in cursor.execute.call_args_list
            if len(call.args) > 1 and isinstance(call.args[1], dict)
            and call.args[1].get('dispose') == DISPOSE_NCMR_REPORTED
        )
        self.assertEqual(insert_params['next_owner_id'], 15)
        self.assertEqual(insert_params['dispose_type'], 9)
        connect.return_value.commit.assert_called_once()

    @patch('app.utils.database_util.oracledb.connect')
    def test_mark_skips_when_not_create(self, connect):
        from app.utils.database_util import mark_hold_record_ncmr_reported

        cursor = MagicMock()
        cursor.fetchone.return_value = (9, 55)
        connect.return_value = self._connect(cursor)
        self.assertFalse(mark_hold_record_ncmr_reported(8))
        connect.return_value.commit.assert_not_called()

    @patch('app.utils.database_util.oracledb.connect')
    def test_mark_writes_when_still_open(self, connect):
        from app.utils.database_util import mark_hold_record_ncmr_reported

        cursor = MagicMock()
        cursor.fetchone.side_effect = [(5, 55), (15,), (100,)]
        cursor.rowcount = 1
        connect.return_value = self._connect(cursor)
        self.assertTrue(mark_hold_record_ncmr_reported(8))
        connect.return_value.commit.assert_called_once()

    @patch('app.utils.database_util.oracledb.connect')
    def test_retry_query_is_type_three_status_zero(self, connect):
        from app.utils.database_util import query_unreported_eng_future_records

        cursor = MagicMock()
        cursor.description = [('ID',)]
        cursor.fetchall.return_value = []
        connect.return_value = self._connect(cursor)
        self.assertEqual(query_unreported_eng_future_records('FT_HOLD_RECORD'), [])
        sql = cursor.execute.call_args.args[0]
        self.assertIn('RECORD_TYPE, -1) = 3', sql)
        self.assertIn('NVL(STATUS, 0) = 0', sql)

    @patch('app.controllers.dispose_ctrl._load_record')
    def test_engineer_cannot_dispose_reported(self, load):
        from app.utils.auth_decorators import ROLE_ENGINEER

        load.return_value = {
            'ID': 8,
            'STATUS': DISPOSE_NCMR_REPORTED,
            'RECORD_TYPE': 0,
            'PRODUCT_ID': 'GC20C3-3.5',
        }
        ok, msg, data = dispose_record(8, 1, 10, ROLE_ENGINEER)
        self.assertFalse(ok)
        self.assertIn('已上报', msg)
        self.assertIsNone(data)


if __name__ == '__main__':
    unittest.main()
