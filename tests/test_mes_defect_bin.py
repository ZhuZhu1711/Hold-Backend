"""MES 缺陷 BIN：按 TRANSACTION_TIME 只留最新一遍测试的 CODE 列表（不连库）。"""
from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import patch

from app.controllers.rawdata_ctrl import get_mes_defect_bin_qty


class MesDefectBinDedupTest(unittest.TestCase):
    @patch('app.controllers.rawdata_ctrl.query_mes_defect_bin_qty')
    def test_keeps_only_latest_run(self, query):
        latest = datetime(2026, 10, 11, 9, 0, 0)
        query.return_value = [
            {
                'defect_code': 'A01',
                'qty': 9,
                'transaction_time': datetime(2026, 10, 1, 8, 0, 0),
            },
            {'defect_code': 'A01', 'qty': 3, 'transaction_time': latest},
            {'defect_code': 'C03', 'qty': 4, 'transaction_time': latest},
            {
                'defect_code': 'B02',
                'qty': 5,
                'transaction_time': datetime(2026, 10, 2, 8, 0, 0),
            },
        ]

        ok, _msg, data = get_mes_defect_bin_qty('C200161-027')

        self.assertTrue(ok)
        self.assertEqual(
            data['items'],
            [
                {'defect_code': 'C03', 'qty': 4, 'bin_name': ''},
                {'defect_code': 'A01', 'qty': 3, 'bin_name': ''},
            ],
        )
        self.assertEqual(data['qty'], 7)
        self.assertEqual(data['fail_qty'], 0)
        self.assertFalse(data['has_duplicate'])
        self.assertEqual(data['duplicate_codes'], [])

    @patch('app.controllers.rawdata_ctrl.query_mes_defect_bin_qty')
    def test_duplicate_inside_latest_run_keeps_first(self, query):
        same = datetime(2026, 10, 11, 9, 0, 0)
        query.return_value = [
            {'defect_code': 'A01', 'qty': 4, 'transaction_time': same},
            {'defect_code': 'A01', 'qty': 8, 'transaction_time': same},
            {
                'defect_code': 'B02',
                'qty': 1,
                'transaction_time': datetime(2026, 10, 1, 0, 0, 0),
            },
        ]

        ok, _msg, data = get_mes_defect_bin_qty('C200161-027')

        self.assertTrue(ok)
        self.assertEqual(
            data['items'],
            [{'defect_code': 'A01', 'qty': 4, 'bin_name': ''}],
        )
        self.assertTrue(data['has_duplicate'])
        self.assertEqual(data['duplicate_codes'], ['A01'])

    @patch('app.controllers.rawdata_ctrl.query_mes_defect_bin_qty')
    def test_real_time_beats_empty_time(self, query):
        query.return_value = [
            {'defect_code': 'A01', 'qty': 9, 'transaction_time': None},
            {
                'defect_code': 'B02',
                'qty': 2,
                'transaction_time': datetime(2026, 10, 1, 0, 0, 0),
            },
        ]

        ok, _msg, data = get_mes_defect_bin_qty('C200161-027')

        self.assertTrue(ok)
        self.assertEqual(
            data['items'],
            [{'defect_code': 'B02', 'qty': 2, 'bin_name': ''}],
        )

    @patch('app.controllers.rawdata_ctrl.query_mes_defect_bin_qty')
    def test_all_empty_time_keeps_that_list(self, query):
        query.return_value = [
            {'defect_code': 'A01', 'qty': 2, 'transaction_time': None},
            {'defect_code': 'B02', 'qty': 6, 'transaction_time': None},
        ]

        ok, _msg, data = get_mes_defect_bin_qty('C200161-027')

        self.assertTrue(ok)
        self.assertEqual(
            data['items'],
            [
                {'defect_code': 'B02', 'qty': 6, 'bin_name': ''},
                {'defect_code': 'A01', 'qty': 2, 'bin_name': ''},
            ],
        )

    @patch('app.controllers.rawdata_ctrl.query_mes_defect_bin_qty')
    def test_latest_run_keeps_non_f_bin(self, query):
        latest = datetime(2026, 10, 11, 20, 15, 43)
        query.return_value = [
            {'defect_code': '102', 'qty': 2, 'transaction_time': latest, 'bin_name': 'F'},
            {'defect_code': '157', 'qty': 8, 'transaction_time': latest, 'bin_name': 'MA'},
            {
                'defect_code': '157',
                'qty': 132,
                'transaction_time': datetime(2026, 10, 11, 18, 12, 44),
                'bin_name': 'RT',
            },
        ]

        ok, _msg, data = get_mes_defect_bin_qty('A086191-016')

        self.assertTrue(ok)
        self.assertEqual(
            data['items'],
            [
                {'defect_code': '157', 'qty': 8, 'bin_name': 'MA'},
                {'defect_code': '102', 'qty': 2, 'bin_name': 'F'},
            ],
        )
        self.assertEqual(data['qty'], 10)
        self.assertEqual(data['fail_qty'], 2)
        self.assertFalse(data['has_duplicate'])
