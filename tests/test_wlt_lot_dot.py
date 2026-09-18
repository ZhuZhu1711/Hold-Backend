"""WOQC LOT.起始片号：合批写入保留原 LOT_ID；normalize。"""
from __future__ import annotations

import unittest
from datetime import datetime

from app.backend_schedule.FT_HOLD_MERGE_sche import (
    HoldInfo,
    RoughHoldRecord,
    build_rough_hold_records,
)
from app.utils.database_util import expand_display_wafer_ids, normalize_lot_id


class NormalizeLotDotTest(unittest.TestCase):
    def test_strips_start_wafer_suffix(self):
        self.assertEqual(normalize_lot_id('679PK7.14'), '679PK7')
        self.assertEqual(normalize_lot_id('C196721.01'), 'C196721')

    def test_dash_still_prefix(self):
        self.assertEqual(normalize_lot_id('679PK7-14'), '679PK7')


class WltMergeKeepsLotIdTest(unittest.TestCase):
    def test_to_record_dict_keeps_lot_with_dot(self):
        items = [
            HoldInfo(
                id=1,
                hold_dttm=datetime(2026, 8, 1, 10, 0, 0),
                hold_dttm_raw='2026-08-01 10:00:00',
                station='WOQC',
                equip_id='100',
                product_id='XX-2.6',
                lot_id='679PK7.14',
                wafer_id='679PK7-14',
                hold_code='004',
                hold_reason='t',
                source=0,
            ),
            HoldInfo(
                id=2,
                hold_dttm=datetime(2026, 8, 1, 10, 1, 0),
                hold_dttm_raw='2026-08-01 10:01:00',
                station='WOQC',
                equip_id='100',
                product_id='XX-2.6',
                lot_id='679PK7.14',
                wafer_id='679PK7-15',
                hold_code='004',
                hold_reason='t',
                source=0,
            ),
        ]
        rough = RoughHoldRecord(
            wafer_id='placeholder',
            record_type=2,
            items=items,
            all_source_ids=[1, 2],
            fragmented_merged=True,
            lot_id_override='679PK7',
        )
        row = rough.to_record_dict(status=0)
        self.assertIsNotNone(row)
        self.assertEqual(row['LOT_ID'], '679PK7.14')
        self.assertEqual(row['WAFER_ID'], '#14#15')

    def test_build_rough_groups_by_prefix_keeps_source_lot(self):
        rows = [
            {
                'ID': 1,
                'HOLD_DTTM': '2026-08-01 10:00:00',
                'STATION': 'WOQC',
                'EQUIP_ID': '100',
                'PRODUCT_ID': 'PROD-2.6',
                'LOT_ID': '679PK7.14',
                'WAFER_ID': '679PK7-14',
                'HOLD_CODE': '004',
                'HOLD_REASON': 'r',
                'SOURCE': 0,
            },
            {
                'ID': 2,
                'HOLD_DTTM': '2026-08-01 10:01:00',
                'STATION': 'WOQC',
                'EQUIP_ID': '100',
                'PRODUCT_ID': 'PROD-2.6',
                'LOT_ID': '679PK7.14',
                'WAFER_ID': '679PK7-15',
                'HOLD_CODE': '004',
                'HOLD_REASON': 'r',
                'SOURCE': 0,
            },
        ]
        records, skipped = build_rough_hold_records(rows)
        self.assertEqual(skipped, [])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].lot_id_override, '679PK7')
        row = records[0].to_record_dict()
        self.assertEqual(row['LOT_ID'], '679PK7.14')
        self.assertEqual(row['WAFER_ID'], '#14#15')

    def test_build_rough_keeps_s83209_dot_suffix(self):
        rows = [
            {
                'ID': 1,
                'HOLD_DTTM': '2026-08-01 10:00:00',
                'STATION': 'WOQC',
                'EQUIP_ID': '100',
                'PRODUCT_ID': 'PROD-2.6',
                'LOT_ID': 'S83209.13',
                'WAFER_ID': 'S83209-13',
                'HOLD_CODE': '004',
                'HOLD_REASON': 'r',
                'SOURCE': 0,
            },
            {
                'ID': 2,
                'HOLD_DTTM': '2026-08-01 10:01:00',
                'STATION': 'WOQC',
                'EQUIP_ID': '100',
                'PRODUCT_ID': 'PROD-2.6',
                'LOT_ID': 'S83209.13',
                'WAFER_ID': 'S83209-14',
                'HOLD_CODE': '004',
                'HOLD_REASON': 'r',
                'SOURCE': 0,
            },
        ]
        records, skipped = build_rough_hold_records(rows)
        self.assertEqual(skipped, [])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].lot_id_override, 'S83209')
        row = records[0].to_record_dict()
        self.assertEqual(row['LOT_ID'], 'S83209.13')
        self.assertEqual(row['WAFER_ID'], '#13#14')

    def test_prefers_dotted_lot_when_mixed_with_prefix(self):
        items = [
            HoldInfo(
                id=1,
                hold_dttm=datetime(2026, 8, 1, 10, 0, 0),
                hold_dttm_raw='2026-08-01 10:00:00',
                station='WOQC',
                equip_id='100',
                product_id='XX-2.6',
                lot_id='S83209',
                wafer_id='S83209-13',
                hold_code='004',
                hold_reason='t',
                source=0,
            ),
            HoldInfo(
                id=2,
                hold_dttm=datetime(2026, 8, 1, 10, 1, 0),
                hold_dttm_raw='2026-08-01 10:01:00',
                station='WOQC',
                equip_id='100',
                product_id='XX-2.6',
                lot_id='S83209.13',
                wafer_id='S83209-14',
                hold_code='004',
                hold_reason='t',
                source=0,
            ),
        ]
        rough = RoughHoldRecord(
            wafer_id='placeholder',
            record_type=2,
            items=items,
            all_source_ids=[1, 2],
            fragmented_merged=True,
            lot_id_override='S83209',
        )
        row = rough.to_record_dict(status=0)
        self.assertEqual(row['LOT_ID'], 'S83209.13')

    def test_empty_info_lot_falls_back_to_prefix(self):
        items = [
            HoldInfo(
                id=1,
                hold_dttm=datetime(2026, 8, 1, 10, 0, 0),
                hold_dttm_raw='2026-08-01 10:00:00',
                station='WOQC',
                equip_id='100',
                product_id='XX-2.6',
                lot_id='',
                wafer_id='S83209-13',
                hold_code='004',
                hold_reason='t',
                source=0,
            ),
        ]
        rough = RoughHoldRecord(
            wafer_id='#13',
            record_type=2,
            items=items,
            all_source_ids=[1],
            fragmented_merged=True,
            lot_id_override='S83209',
        )
        row = rough.to_record_dict(status=0)
        self.assertEqual(row['LOT_ID'], 'S83209')


class ExpandDisplayWltLotTest(unittest.TestCase):
    def test_strips_dot_suffix_before_join(self):
        self.assertEqual(
            expand_display_wafer_ids('#13#14', 'S83209.13'),
            ['S83209-13', 'S83209-14'],
        )


if __name__ == '__main__':
    unittest.main()
