"""VW_WAFER_YIELD 按站点取良率（mock SQL，不连库）。"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.controllers.hold_report_ctrl import (
    _lookup_yield,
    _normalize_yield_station,
    _pack_yield_payload,
    _query_vw_wafer_yields,
    get_wafer_yield_batch,
)


class NormalizeYieldStationTest(unittest.TestCase):
    def test_wlt_aliases(self):
        self.assertEqual(_normalize_yield_station('WLT'), 'WLT')
        self.assertEqual(_normalize_yield_station('WLT2'), 'WLT')
        self.assertEqual(_normalize_yield_station('wlt2'), 'WLT')

    def test_fa_aliases(self):
        self.assertEqual(_normalize_yield_station('FA'), 'FA')
        self.assertEqual(_normalize_yield_station('FATE-FA'), 'FA')
        self.assertEqual(_normalize_yield_station('VBOX-FA'), 'FA')

    def test_record_type(self):
        self.assertEqual(_normalize_yield_station(record_type=2), 'WLT')
        self.assertEqual(_normalize_yield_station(record_type=0), 'FA')
        self.assertIsNone(_normalize_yield_station(record_type=1))
        self.assertIsNone(_normalize_yield_station())

    def test_station_wins_over_record_type(self):
        self.assertEqual(
            _normalize_yield_station('WLT2', record_type=0),
            'WLT',
        )


class LookupYieldTest(unittest.TestCase):
    def setUp(self):
        self.yield_map = {
            ('P', 'W1', 'FA'): 80.0,
            ('P', 'W1', 'WLT'): 90.0,
        }

    def test_explicit_station_no_fallback(self):
        self.assertEqual(_lookup_yield(self.yield_map, 'P', 'W1', 'WLT'), 90.0)
        self.assertIsNone(_lookup_yield(
            {('P', 'W1', 'FA'): 80.0}, 'P', 'W1', 'WLT',
        ))

    def test_unspecified_prefers_fa(self):
        self.assertEqual(_lookup_yield(self.yield_map, 'P', 'W1', None), 80.0)


class PackYieldPayloadTest(unittest.TestCase):
    def test_wlt_empty_not_filled_by_fa(self):
        yield_map = {('P', 'W1', 'FA'): 80.0}
        payload = _pack_yield_payload(
            'P', 'L', 'W1', ['W1'], yield_map, station='WLT',
        )
        self.assertEqual(payload['station'], 'WLT')
        self.assertIsNone(payload['items'][0]['yield'])
        self.assertEqual(payload['items'][0]['station'], 'WLT')


class QueryVwWaferYieldsTest(unittest.TestCase):
    def test_keys_include_station(self):
        rows = [
            ('W1', 'WLT', 90.1),
            ('W1', 'FA', 80.2),
        ]
        mock_session = MagicMock()
        mock_session.execute.return_value.fetchall.return_value = rows
        with patch('app.controllers.hold_report_ctrl.db') as mock_db:
            mock_db.session = mock_session
            result = _query_vw_wafer_yields([('P', 'W1')])
        self.assertEqual(result[('P', 'W1', 'WLT')], 90.1)
        self.assertEqual(result[('P', 'W1', 'FA')], 80.2)


class GetWaferYieldBatchTest(unittest.TestCase):
    def test_batch_picks_requested_station(self):
        yield_map = {
            ('PROD', 'W1', 'WLT'): None,
            ('PROD', 'W1', 'FA'): 88.5,
        }
        with patch(
            'app.controllers.hold_report_ctrl._query_vw_wafer_yields',
            return_value=yield_map,
        ):
            ok, msg, data = get_wafer_yield_batch([
                {
                    'key': 'W1|WLT',
                    'product_id': 'PROD',
                    'lot_id': 'LOT',
                    'wafer_id': 'W1',
                    'station': 'WLT',
                },
                {
                    'key': 'W1|FA',
                    'product_id': 'PROD',
                    'lot_id': 'LOT',
                    'wafer_id': 'W1',
                    'station': 'FA',
                },
            ])
        self.assertTrue(ok)
        self.assertEqual(msg, '获取成功')
        by_key = {item['key']: item for item in data['items']}
        self.assertIsNone(by_key['W1|WLT']['items'][0]['yield'])
        self.assertEqual(by_key['W1|FA']['items'][0]['yield'], 88.5)
        self.assertEqual(by_key['W1|WLT']['station'], 'WLT')
        self.assertEqual(by_key['W1|FA']['station'], 'FA')
