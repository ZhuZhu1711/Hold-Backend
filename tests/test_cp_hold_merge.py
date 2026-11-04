"""CP 合批：按 lot 等齐后写入 CP 表（不连库）。"""
from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from app.backend_schedule.CP_HOLD_MERGE_sche import (
    CP_PRODUCTION_EMPLOYEE_NO,
    CP_PRODUCTION_ROLE,
    CpHoldMergeScheduler,
    build_cp_rough_records,
    evaluate_cp_lot_quiet,
    hold_code_in_sql,
    insert_cp_hold_record_and_link,
    query_cp_online_hold_info,
)
from app.utils.auth_decorators import (
    ROLE_CP_PRODUCTION,
    ROLE_PRODUCTION,
    home_endpoint_for_role,
)


def _row(**kwargs):
    base = {
        'ID': 1,
        'HOLD_DTTM': '2026-10-28 10:00:00',
        'STATION': 'CP1',
        'EQUIP_ID': 'E1',
        'PRODUCT_ID': 'P1',
        'LOT_ID': 'LOT1',
        'WAFER_ID': 'LOT1-01',
        'HOLD_CODE': '027',
        'HOLD_REASON': 'yield',
        'SOURCE': 0,
        'SECOND_CODE': None,
        'ROUTE_ID': None,
        'GRADE_NUM': 'A',
    }
    base.update(kwargs)
    return base


def _cursor():
    cursor = MagicMock()
    cursor.description = [('ID',)]
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = (1,)
    cursor.rowcount = 1
    return cursor


def _connect(cursor):
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor
    return conn


class CpMergeRulesTest(unittest.TestCase):
    def test_hold_code_sql_is_whitelist_only(self):
        sql = hold_code_in_sql()
        for code in ('SYSTEM', '027', '01', '004', '015', '016', 'CP_FUTURE_HOLD'):
            self.assertIn(f"'{code}'", sql)
        self.assertNotIn('023', sql)

    def test_same_lot_multiple_reasons_one_record(self):
        records, dirty = build_cp_rough_records([
            _row(ID=1, HOLD_CODE='027', HOLD_REASON='yield'),
            _row(
                ID=2, HOLD_CODE='004', HOLD_REASON='bin',
                HOLD_DTTM='2026-10-28 10:05:00', GRADE_NUM='B',
            ),
        ], window=timedelta(hours=1))
        self.assertEqual(dirty, [])
        self.assertEqual(len(records), 1)
        body = records[0].to_record_dict()
        self.assertEqual(body['RECORD_TYPE'], 0)
        self.assertEqual(body['LOT_ID'], 'LOT1')
        self.assertEqual(body['HOLD_CODE'], '027@004')
        self.assertEqual(body['HOLD_REASON'], 'yield@bin')
        self.assertEqual(body['WAFER_ID'], 'LOT1-01')
        self.assertEqual(body['GRADE_NUM'], 'B')
        self.assertNotIn('HOLD_WAFER_ATTR', body)
        self.assertEqual(records[0].source_ids, [1, 2])

    def test_different_lots_stay_apart(self):
        records, dirty = build_cp_rough_records([
            _row(ID=1, LOT_ID='LOT1'),
            _row(ID=2, LOT_ID='LOT2', HOLD_CODE='SYSTEM'),
        ])
        self.assertEqual(dirty, [])
        self.assertEqual([r.lot_id for r in records], ['LOT1', 'LOT2'])

    def test_multiple_wafers_use_display(self):
        records, _dirty = build_cp_rough_records([
            _row(ID=1, WAFER_ID='LOT1-01'),
            _row(ID=2, WAFER_ID='LOT1-02', HOLD_CODE='015', HOLD_DTTM='2026-10-28 10:06:00'),
        ])
        body = records[0].to_record_dict()
        self.assertEqual(body['WAFER_ID'], '#01#02')

    def test_empty_lot_is_dirty_other_codes_are_left(self):
        records, dirty = build_cp_rough_records([
            _row(ID=8, LOT_ID='  '),
            _row(ID=9, HOLD_CODE='999'),
        ])
        self.assertEqual(records, [])
        self.assertEqual(dirty, [8])

    def test_first_sight_defers(self):
        now = datetime(2026, 10, 28, 12, 0, 0)
        status, mark = evaluate_cp_lot_quiet(
            None, 5, now, timedelta(minutes=30), timedelta(minutes=120),
        )
        self.assertEqual(status, 'defer')
        self.assertEqual(mark['max_id'], 5)

    def test_quiet_elapsed_is_ready(self):
        now = datetime(2026, 10, 28, 12, 0, 0)
        _status, mark = evaluate_cp_lot_quiet(
            None, 5, now, timedelta(minutes=30), timedelta(minutes=120),
        )
        later = now + timedelta(minutes=30)
        status, cleared = evaluate_cp_lot_quiet(
            mark, 5, later, timedelta(minutes=30), timedelta(minutes=120),
        )
        self.assertEqual(status, 'ready')
        self.assertIsNone(cleared)

    def test_cap_uses_rows_already_seen(self):
        first = datetime(2026, 10, 28, 9, 0, 0)
        _status, mark = evaluate_cp_lot_quiet(
            None, 5, first, timedelta(minutes=30), timedelta(minutes=120),
        )
        later = first + timedelta(minutes=121)
        status, cleared = evaluate_cp_lot_quiet(
            mark, 9, later, timedelta(minutes=30), timedelta(minutes=120),
        )
        self.assertEqual(status, 'cap')
        self.assertIsNone(cleared)


class CpMergeSqlTest(unittest.TestCase):
    @patch('app.backend_schedule.CP_HOLD_MERGE_sche._connect')
    def test_query_is_area_one_holding_zero(self, connect):
        cursor = _cursor()
        connect.return_value = _connect(cursor)
        rows = query_cp_online_hold_info('FT_HOLD_INFO_TEST')
        self.assertEqual(rows, [])
        sql = cursor.execute.call_args.args[0]
        self.assertIn('FROM FT_HOLD_INFO_TEST', sql)
        self.assertEqual(sql.count('AREA = 1'), 1)
        self.assertIn('HOLDING = 0', sql)
        self.assertNotIn('HOLDING = 1', sql)
        self.assertIn('NVL(HOLD_RECORD_ID, 0) = 0', sql)
        self.assertIn("'CP_FUTURE_HOLD'", sql)
        self.assertNotIn('FT_HOLD_RECORD', sql)

    @patch('app.backend_schedule.CP_HOLD_MERGE_sche.mark_cp_infos_dirty')
    @patch('app.backend_schedule.CP_HOLD_MERGE_sche._connect')
    def test_insert_writes_cp_tables_and_area_one(self, connect, mark_dirty):
        cursor = _cursor()
        cursor.fetchone.side_effect = [(11,), (22,)]
        cursor.rowcount = 2
        connect.return_value = _connect(cursor)
        body = build_cp_rough_records([_row()])[0][0].to_record_dict()
        new_id = insert_cp_hold_record_and_link(
            body,
            [1],
            7,
            info_table='FT_HOLD_INFO_TEST',
            record_table='CP_HOLD_RECORD_TEST',
            circ_table='CP_CIRCULATION_HISTORY_TEST',
        )
        self.assertEqual(new_id, 11)
        mark_dirty.assert_not_called()
        sqls = [call.args[0] for call in cursor.execute.call_args_list]
        joined = '\n'.join(sqls)
        self.assertIn('INSERT INTO CP_HOLD_RECORD_TEST', joined)
        self.assertIn('INSERT INTO CP_CIRCULATION_HISTORY_TEST', joined)
        self.assertIn('UPDATE FT_HOLD_INFO_TEST', joined)
        self.assertIn('AREA = 1', joined)
        self.assertNotIn('FT_HOLD_RECORD', joined)
        self.assertNotIn('HOLD_WAFER_ATTR', joined)
        self.assertNotIn('CIRCULATION_HISTORY', joined.replace('CP_CIRCULATION_HISTORY_TEST', ''))
        circ_params = [
            call.args[1]
            for call in cursor.execute.call_args_list
            if len(call.args) > 1 and isinstance(call.args[1], dict)
            and 'next_owner_id' in call.args[1]
        ]
        self.assertEqual(len(circ_params), 1)
        self.assertEqual(circ_params[0]['next_owner_id'], 7)


class CpMergeJobTest(unittest.TestCase):
    def _scheduler(self, directory):
        sched = CpHoldMergeScheduler()
        sched.hold_info_table = 'FT_HOLD_INFO_TEST'
        sched.hold_record_table = 'CP_HOLD_RECORD_TEST'
        sched.circ_table = 'CP_CIRCULATION_HISTORY_TEST'
        sched._quiet_state_path = lambda: os.path.join(directory, 'quiet.json')
        return sched

    @patch('app.backend_schedule.CP_HOLD_MERGE_sche.insert_cp_hold_record_and_link')
    @patch('app.backend_schedule.CP_HOLD_MERGE_sche.find_cp_production_user_id', return_value=None)
    @patch('app.backend_schedule.CP_HOLD_MERGE_sche.query_cp_online_hold_info')
    def test_missing_owner_does_not_create(self, query, _find_user, insert):
        query.return_value = [_row()]
        with tempfile.TemporaryDirectory() as directory:
            self._scheduler(directory)._run_job()
        insert.assert_not_called()

    @patch('app.backend_schedule.CP_HOLD_MERGE_sche.insert_cp_hold_record_and_link')
    @patch('app.backend_schedule.CP_HOLD_MERGE_sche.find_cp_production_user_id', return_value=7)
    @patch('app.backend_schedule.CP_HOLD_MERGE_sche.query_cp_online_hold_info')
    def test_new_lot_waits_for_quiet(self, query, _find_user, insert):
        query.return_value = [_row()]
        with tempfile.TemporaryDirectory() as directory:
            self._scheduler(directory)._run_job()
            self.assertTrue(os.path.isfile(os.path.join(directory, 'quiet.json')))
        insert.assert_not_called()


class CpProductionAccountTest(unittest.TestCase):
    def test_role_two_does_not_enter_ft_production(self):
        self.assertEqual(ROLE_CP_PRODUCTION, CP_PRODUCTION_ROLE)
        self.assertEqual(CP_PRODUCTION_EMPLOYEE_NO, '88888')
        self.assertEqual(
            home_endpoint_for_role(ROLE_CP_PRODUCTION),
            'auth.change_password_page',
        )
        self.assertEqual(home_endpoint_for_role(ROLE_PRODUCTION), 'production.dashboard')
        self.assertNotEqual(
            home_endpoint_for_role(ROLE_CP_PRODUCTION),
            home_endpoint_for_role(ROLE_PRODUCTION),
        )


if __name__ == '__main__':
    unittest.main()
