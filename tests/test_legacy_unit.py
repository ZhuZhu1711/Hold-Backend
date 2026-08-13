"""前身 HOLD_INFO 样本构造：无数据库。"""
from __future__ import annotations

import inspect
import unittest
from datetime import datetime

from app.hold_predict.features import extract_features
from app.hold_predict.legacy import (
    LEGACY_TRAIN_ENABLED,
    extract_hold_codes_from_reason,
    legacy_label_y,
    parse_hold_datetime,
    summarize_legacy_records,
    to_pseudo_record,
)


class ParseHoldDatetimeTest(unittest.TestCase):
    def test_iso_space(self):
        dt = parse_hold_datetime('2024-03-15 08:30:00')
        self.assertEqual(dt, datetime(2024, 3, 15, 8, 30, 0))

    def test_slash(self):
        dt = parse_hold_datetime('2024/03/15 08:30:00')
        self.assertEqual(dt, datetime(2024, 3, 15, 8, 30, 0))

    def test_t_separator(self):
        dt = parse_hold_datetime('2024-03-15T08:30:00')
        self.assertEqual(dt, datetime(2024, 3, 15, 8, 30, 0))

    def test_compact(self):
        dt = parse_hold_datetime('20240315083000')
        self.assertEqual(dt, datetime(2024, 3, 15, 8, 30, 0))

    def test_date_only(self):
        dt = parse_hold_datetime('2024-03-15')
        self.assertEqual(dt, datetime(2024, 3, 15))

    def test_passthrough_datetime(self):
        raw = datetime(2024, 1, 1, 12, 0, 0)
        self.assertIs(parse_hold_datetime(raw), raw)

    def test_empty(self):
        self.assertIsNone(parse_hold_datetime(None))
        self.assertIsNone(parse_hold_datetime(''))
        self.assertIsNone(parse_hold_datetime('not-a-date'))


class HoldCodeFromReasonTest(unittest.TestCase):
    def test_single(self):
        self.assertEqual(extract_hold_codes_from_reason('Yield hold 023 bin2'), ['023'])

    def test_multi_order(self):
        self.assertEqual(extract_hold_codes_from_reason('023 then 024 and 027'), ['023', '024', '027'])

    def test_no_false_positive(self):
        self.assertEqual(extract_hold_codes_from_reason('bin 10230 fail'), [])

    def test_empty(self):
        self.assertEqual(extract_hold_codes_from_reason(None), [])
        self.assertEqual(extract_hold_codes_from_reason(''), [])


class LabelAndPseudoRecordTest(unittest.TestCase):
    def test_release_is_zero(self):
        self.assertEqual(legacy_label_y(0), 1)
        self.assertEqual(legacy_label_y('0'), 1)
        self.assertEqual(legacy_label_y(1), 0)
        self.assertEqual(legacy_label_y(2), 0)
        self.assertEqual(legacy_label_y(None), 0)

    def test_pseudo_record(self):
        rec = to_pseudo_record({
            'ID': 42,
            'WAFER_ID': 'C123456-03',
            'PRODUCT_ID': 'ABC-3.5',
            'EQUIP_ID': 'EQ1',
            'HOLD_DATETIME': '2024-03-15 08:30:00',
            'HOLD_REASON': 'FT yield 023 / 025',
            'ROUTE_ID': 'FT-ENG-01',
            'GRADE_NUM': 'F:10,HA:2',
            'SECOND_CODE': 'SC',
            'LABEL_DISPOSE': 0,
            'LABEL_DTTM': datetime(2024, 3, 15, 10, 0, 0),
        })
        self.assertEqual(rec['ID'], 42)
        self.assertEqual(rec['LOT_ID'], 'C123456')
        self.assertEqual(rec['HOLD_CODE'], '023@025')
        self.assertIsNone(rec['STATION'])
        self.assertIsNone(rec['SOURCE'])
        self.assertEqual(rec['LABEL_Y'], 1)
        self.assertEqual(rec['_prior_source'], 'legacy')
        self.assertEqual(rec['HOLD_DTTM'], datetime(2024, 3, 15, 8, 30, 0))

    def test_summarize_warn_rate(self):
        records = [
            to_pseudo_record({
                'ID': 1,
                'WAFER_ID': 'A-01',
                'HOLD_REASON': '023',
                'LABEL_DISPOSE': 0,
                'HOLD_DATETIME': '2024-01-01 00:00:00',
            }),
            to_pseudo_record({
                'ID': 2,
                'WAFER_ID': 'B-01',
                'HOLD_REASON': 'no code',
                'LABEL_DISPOSE': 2,
                'HOLD_DATETIME': '2024-01-02 00:00:00',
            }),
        ]
        stats = summarize_legacy_records(records)
        self.assertEqual(stats['n'], 2)
        self.assertEqual(stats['release_n'], 1)
        self.assertEqual(stats['hold_code_n'], 1)
        self.assertAlmostEqual(stats['hold_code_rate'], 0.5)


class ExtractFeaturesContractTest(unittest.TestCase):
    def test_prior_source_param(self):
        params = inspect.signature(extract_features).parameters
        self.assertIn('prior_source', params)
        self.assertEqual(params['prior_source'].default, 'record')

    def test_legacy_train_disabled(self):
        self.assertFalse(LEGACY_TRAIN_ENABLED)


if __name__ == '__main__':
    unittest.main()
