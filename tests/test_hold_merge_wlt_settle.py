"""WLT 最新 HOLD 未满 10 分钟则本轮不建单：不连 Oracle。"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from app.backend_schedule.FT_HOLD_MERGE_sche import (
    RECORD_TYPE_FT,
    RECORD_TYPE_WLT,
    HoldInfo,
    HoldMergeScheduler,
    RoughHoldRecord,
    build_rough_hold_records,
    should_defer_wlt_record,
)

_NOW = datetime(2026, 9, 18, 12, 0, 0)
_SETTLE = timedelta(minutes=10)


def _wlt_info(info_id, wafer_suffix, hold_dttm, lot='S83209.13'):
    return HoldInfo(
        id=info_id,
        hold_dttm=hold_dttm,
        hold_dttm_raw=hold_dttm.strftime('%Y-%m-%d %H:%M:%S'),
        station='WOQC',
        equip_id='100',
        product_id='PROD-2.6',
        lot_id=lot,
        wafer_id=f'S83209-{wafer_suffix}',
        hold_code='004',
        hold_reason='r',
        source=0,
    )


def _wlt_rec(*infos):
    return RoughHoldRecord(
        wafer_id='#13',
        record_type=RECORD_TYPE_WLT,
        items=list(infos),
        all_source_ids=[i.id for i in infos],
        fragmented_merged=True,
        lot_id_override='S83209',
    )


def _wlt_row(info_id, wafer_suffix, hold_dttm, lot='S83209.13'):
    return {
        'ID': info_id,
        'HOLD_DTTM': hold_dttm.strftime('%Y-%m-%d %H:%M:%S'),
        'STATION': 'WOQC',
        'EQUIP_ID': '100',
        'PRODUCT_ID': 'PROD-2.6',
        'LOT_ID': lot,
        'WAFER_ID': f'S83209-{wafer_suffix}',
        'HOLD_CODE': '004',
        'HOLD_REASON': 'r',
        'SOURCE': 0,
    }


class ShouldDeferWltRecordTest(unittest.TestCase):
    def test_latest_within_10_minutes_defers(self):
        rec = _wlt_rec(_wlt_info(1, '13', _NOW - timedelta(minutes=5)))
        self.assertTrue(should_defer_wlt_record(rec, now=_NOW, settle=_SETTLE))

    def test_latest_exactly_10_minutes_does_not_defer(self):
        rec = _wlt_rec(_wlt_info(1, '13', _NOW - timedelta(minutes=10)))
        self.assertFalse(should_defer_wlt_record(rec, now=_NOW, settle=_SETTLE))

    def test_latest_older_than_10_minutes_does_not_defer(self):
        rec = _wlt_rec(_wlt_info(1, '13', _NOW - timedelta(minutes=11)))
        self.assertFalse(should_defer_wlt_record(rec, now=_NOW, settle=_SETTLE))

    def test_mixed_lot_defers_if_any_wafer_is_recent(self):
        rec = _wlt_rec(
            _wlt_info(1, '13', _NOW - timedelta(minutes=20)),
            _wlt_info(2, '14', _NOW - timedelta(minutes=3)),
        )
        self.assertTrue(should_defer_wlt_record(rec, now=_NOW, settle=_SETTLE))

    def test_ft_record_never_defers(self):
        rec = RoughHoldRecord(
            wafer_id='C123456-01',
            record_type=RECORD_TYPE_FT,
            items=[
                HoldInfo(
                    id=1,
                    hold_dttm=_NOW - timedelta(minutes=1),
                    hold_dttm_raw='2026-09-18 11:59:00',
                    station='FATE-FA',
                    equip_id='FATE100',
                    product_id='PROD-3.5',
                    lot_id='C123456-01',
                    wafer_id='C123456-01',
                    hold_code='023',
                    hold_reason='r',
                    source=0,
                )
            ],
            all_source_ids=[1],
        )
        self.assertFalse(should_defer_wlt_record(rec, now=_NOW, settle=_SETTLE))

    def test_unparseable_hold_dttm_does_not_defer(self):
        rec = _wlt_rec(_wlt_info(1, '13', _NOW))
        rec.items[0].hold_dttm = None
        self.assertFalse(should_defer_wlt_record(rec, now=_NOW, settle=_SETTLE))


class RunJobDefersWltTest(unittest.TestCase):
    def setUp(self):
        self.scheduler = HoldMergeScheduler()

    @patch('app.backend_schedule.FT_HOLD_MERGE_sche.mark_hold_infos_dirty')
    @patch('app.backend_schedule.FT_HOLD_MERGE_sche.insert_hold_record_and_link')
    @patch('app.backend_schedule.FT_HOLD_MERGE_sche.query_online_hold_info')
    def test_recent_wlt_lot_skipped_until_next_cycle(
        self, query_fn, insert_fn, dirty_fn
    ):
        now = datetime.now()
        query_fn.return_value = [
            _wlt_row(1, '13', now - timedelta(minutes=20)),
            _wlt_row(2, '14', now - timedelta(minutes=3)),
        ]
        insert_fn.return_value = 99

        self.scheduler._run_job()

        insert_fn.assert_not_called()
        dirty_fn.assert_not_called()

    @patch('app.backend_schedule.FT_HOLD_MERGE_sche.mark_hold_infos_dirty')
    @patch('app.backend_schedule.FT_HOLD_MERGE_sche.insert_hold_record_and_link')
    @patch('app.backend_schedule.FT_HOLD_MERGE_sche.query_online_hold_info')
    def test_settled_wlt_lot_is_persisted(
        self, query_fn, insert_fn, dirty_fn
    ):
        now = datetime.now()
        query_fn.return_value = [
            _wlt_row(1, '13', now - timedelta(minutes=20)),
            _wlt_row(2, '14', now - timedelta(minutes=11)),
        ]
        insert_fn.return_value = 99

        self.scheduler._run_job()

        insert_fn.assert_called_once()
        dirty_fn.assert_not_called()

    def test_build_rough_still_groups_same_lot(self):
        rows = [
            _wlt_row(1, '13', _NOW - timedelta(minutes=20)),
            _wlt_row(2, '14', _NOW - timedelta(minutes=3)),
        ]
        records, skipped = build_rough_hold_records(rows)
        self.assertEqual(skipped, [])
        self.assertEqual(len(records), 1)
        self.assertTrue(should_defer_wlt_record(records[0], now=_NOW, settle=_SETTLE))


if __name__ == '__main__':
    unittest.main()
