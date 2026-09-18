"""同 lot 当前片只加粗当前站；缺 rawdata 时标 pending（mock，不连库）。"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from app.controllers.hold_report_ctrl import (
    _same_lot_rawdata_pending,
    _same_lot_row_is_current,
    get_hold_analysis,
)


class SameLotCurrentRowTest(unittest.TestCase):
    def test_same_wafer_other_station_not_current(self):
        current = {'C123-01'}
        self.assertTrue(
            _same_lot_row_is_current('C123-01', 'FA', current, 'FA'),
        )
        self.assertFalse(
            _same_lot_row_is_current('C123-01', 'WLT', current, 'FA'),
        )
        self.assertFalse(
            _same_lot_row_is_current('C123-02', 'FA', current, 'FA'),
        )

    def test_pending_flag(self):
        rows = [
            {'is_current': False, 'rawdata_pending': True},
            {'is_current': True, 'rawdata_pending': False},
        ]
        self.assertFalse(_same_lot_rawdata_pending(rows))
        rows[1]['rawdata_pending'] = True
        self.assertTrue(_same_lot_rawdata_pending(rows))


class GetHoldAnalysisStationTest(unittest.TestCase):
    def _lot_records(self):
        return [
            {
                'wafer_id': 'C123-01',
                'station': 'WLT',
                'operation_id': 'WLT2',
                'test_time': '2026-09-01 10:00:00',
                'die_num': 1000,
                'raw_data': {'1': 5},
            },
            {
                'wafer_id': 'C123-02',
                'station': 'FA',
                'operation_id': 'FATE-FA',
                'test_time': '2026-09-02 11:00:00',
                'die_num': 1000,
                'raw_data': {'1': 8},
            },
        ]

    def _run(self, *, station, record_type, lot_id, wafer_id='C123-01', records=None):
        lot_records = self._lot_records() if records is None else records
        with patch(
            'app.controllers.hold_report_ctrl.testlog_ctrl.get_testlog_bysite_str',
            return_value=None,
        ), patch(
            'app.controllers.hold_report_ctrl.query_same_lot_bincodes_by_prefixes',
            return_value=lot_records,
        ), patch(
            'app.controllers.hold_report_ctrl.get_latest_defect_bincodes_for_wafers',
            return_value=[],
        ):
            return get_hold_analysis(
                wafer_id,
                record_type=record_type,
                station=station,
                lot_id=lot_id,
            )

    def test_ft_hold_does_not_mark_wlt_as_current(self):
        ok, _msg, data = self._run(
            station='FATE-FA', record_type=0, lot_id='C123-01',
        )
        self.assertTrue(ok)
        rows = { (r['wafer_id'], r['station']): r for r in data['same_lot_rows'] }
        self.assertFalse(rows[('C123-01', 'WLT')]['is_current'])
        fa = rows[('C123-01', 'FA')]
        self.assertTrue(fa['is_current'])
        self.assertTrue(fa['rawdata_pending'])
        self.assertEqual(fa.get('raw_data') or {}, {})
        self.assertTrue(data['raw_data_pending'])
        self.assertEqual(data['raw_data'], {})

    def test_wlt_hold_does_not_fallback_raw_data_to_fa(self):
        records = [
            {
                'wafer_id': 'C123-01',
                'station': 'FA',
                'operation_id': 'FATE-FA',
                'test_time': '2026-09-02 11:00:00',
                'die_num': 1000,
                'raw_data': {'12': 3},
            },
        ]
        ok, _msg, data = self._run(
            station='WLT2',
            record_type=2,
            lot_id='C123.01',
            records=records,
        )
        self.assertTrue(ok)
        rows = { (r['wafer_id'], r['station']): r for r in data['same_lot_rows'] }
        self.assertFalse(rows[('C123-01', 'FA')]['is_current'])
        wlt = rows[('C123-01', 'WLT')]
        self.assertTrue(wlt['is_current'])
        self.assertTrue(wlt['rawdata_pending'])
        self.assertTrue(data['raw_data_pending'])
        self.assertEqual(data['raw_data'], {})

    def test_matching_station_not_pending(self):
        records = [
            {
                'wafer_id': 'C123-01',
                'station': 'FA',
                'operation_id': 'FATE-FA',
                'test_time': '2026-09-02 11:00:00',
                'die_num': 1000,
                'raw_data': {'1': 9},
            },
            {
                'wafer_id': 'C123-01',
                'station': 'WLT',
                'operation_id': 'WLT2',
                'test_time': '2026-09-01 10:00:00',
                'die_num': 1000,
                'raw_data': {'1': 5},
            },
        ]
        ok, _msg, data = self._run(
            station='FATE-FA',
            record_type=0,
            lot_id='C123-01',
            records=records,
        )
        self.assertTrue(ok)
        rows = { (r['wafer_id'], r['station']): r for r in data['same_lot_rows'] }
        self.assertTrue(rows[('C123-01', 'FA')]['is_current'])
        self.assertFalse(rows[('C123-01', 'FA')].get('rawdata_pending'))
        self.assertFalse(rows[('C123-01', 'WLT')]['is_current'])
        self.assertFalse(data['raw_data_pending'])
        self.assertEqual(data['raw_data'], {'1': 9})
