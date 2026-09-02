"""FT 028 重码：入 RECORD_TYPE=0，并与同片良率/缺陷率分列。"""
from __future__ import annotations

import unittest

from app.backend_schedule.FT_HOLD_MERGE_sche import (
    RECORD_TYPE_FT,
    build_rough_hold_records,
    resolve_record_type,
)


def _row(info_id, hold_code, wafer_id='ABC01', lot_id='ABC01', station='FATE-FA'):
    return {
        'ID': info_id,
        'HOLD_DTTM': f'2026-08-01 10:00:{info_id:02d}',
        'STATION': station,
        'EQUIP_ID': 'EQ1',
        'PRODUCT_ID': 'PROD-3.5',
        'LOT_ID': lot_id,
        'WAFER_ID': wafer_id,
        'HOLD_CODE': hold_code,
        'HOLD_REASON': f'reason-{hold_code}',
        'SOURCE': 0,
    }


class FtDupcode028MergeTest(unittest.TestCase):
    def test_028_is_ft_record_type(self):
        self.assertEqual(
            resolve_record_type('PROD-3.5', '028', 'FATE-FA'),
            RECORD_TYPE_FT,
        )

    def test_028_splits_from_yield_and_defect(self):
        records, skipped = build_rough_hold_records([
            _row(1, '023'),
            _row(2, '025'),
            _row(3, '028'),
        ])
        self.assertEqual(skipped, [])
        self.assertEqual(len(records), 2)
        self.assertTrue(all(r.record_type == RECORD_TYPE_FT for r in records))

        codes = {tuple(sorted(i.hold_code for i in r.items)) for r in records}
        self.assertEqual(codes, {('023', '025'), ('028',)})

        for rec in records:
            row = rec.to_record_dict()
            self.assertEqual(row['RECORD_TYPE'], RECORD_TYPE_FT)
            self.assertEqual(row['WAFER_ID'], 'ABC01')
            if '028' in row['HOLD_CODE']:
                self.assertEqual(row['HOLD_CODE'], '028')
            else:
                self.assertIn('023', row['HOLD_CODE'])
                self.assertIn('025', row['HOLD_CODE'])
                self.assertNotIn('028', row['HOLD_CODE'])

    def test_028_only_stays_one_record(self):
        records, skipped = build_rough_hold_records([_row(1, '028')])
        self.assertEqual(skipped, [])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].to_record_dict()['HOLD_CODE'], '028')


class FpqcFutureHoldSkipTest(unittest.TestCase):
    def test_fpqc_025_future_hold_skipped(self):
        rows = [
            _row(1, '023'),
            {
                **_row(2, '025', station='FPQC'),
                'HOLD_REASON': 'defect FUTURE HOLD pending',
            },
        ]
        records, skipped = build_rough_hold_records(rows)
        self.assertEqual(skipped, [2])
        self.assertEqual(len(records), 1)
        self.assertEqual(
            tuple(sorted(i.hold_code for i in records[0].items)),
            ('023',),
        )

    def test_fpqc_025_without_future_hold_still_merges(self):
        rows = [
            _row(1, '023'),
            _row(2, '025', station='FPQC'),
        ]
        records, skipped = build_rough_hold_records(rows)
        self.assertEqual(skipped, [])
        self.assertEqual(len(records), 1)
        codes = tuple(sorted(i.hold_code for i in records[0].items))
        self.assertEqual(codes, ('023', '025'))

    def test_future_hold_other_station_still_merges(self):
        rows = [{
            **_row(1, '025', station='FATE-FA'),
            'HOLD_REASON': 'FUTURE HOLD',
        }]
        records, skipped = build_rough_hold_records(rows)
        self.assertEqual(skipped, [])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].items[0].hold_code, '025')


if __name__ == '__main__':
    unittest.main()
