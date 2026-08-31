"""WOQC LOT.起始片号：合批写入保留原 LOT_ID；normalize；回写默认关。"""
from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import patch

from app.backend_schedule.FT_HOLD_MERGE_sche import (
    HoldInfo,
    RoughHoldRecord,
    build_rough_hold_records,
)
from app.config import Config
from app.utils.database_util import normalize_lot_id
from app.utils.legacy_dispose_writeback import writeback_enabled


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


class LegacyWritebackDefaultOffTest(unittest.TestCase):
    def test_config_flag_false(self):
        self.assertFalse(Config.LEGACY_DISPOSE_WRITEBACK)

    def test_writeback_enabled_follows_config(self):
        with patch.object(Config, 'LEGACY_DISPOSE_WRITEBACK_ENABLED', False):
            self.assertFalse(writeback_enabled())


if __name__ == '__main__':
    unittest.main()
