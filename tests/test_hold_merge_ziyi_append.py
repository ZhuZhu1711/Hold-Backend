"""梓一合批跨周期追加：不连 Oracle。"""
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
    build_ziyi_append_updates,
    is_ziyi_append_candidate,
)
from app.utils.database_util import (
    HOLD_WAFER_ATTR_IQC_ATE,
    HOLD_WAFER_ATTR_ZIYI,
    hold_code_is_028_bucket,
    record_row_is_ziyi,
    select_earliest_ziyi_record,
)


def _info(
    info_id,
    wafer_suffix,
    hold_code='023',
    equip='FATE203',
    lot='C123456-033',
    station='FATE-FA',
    product='PROD-3.5',
):
    return HoldInfo(
        id=info_id,
        hold_dttm=datetime(2026, 8, 1, 10, 0, info_id),
        hold_dttm_raw=f'2026-08-01 10:00:{info_id:02d}',
        station=station,
        equip_id=equip,
        product_id=product,
        lot_id=lot,
        wafer_id=f'C123456-{wafer_suffix}',
        hold_code=hold_code,
        hold_reason=f'reason-{hold_code}',
        source=0,
    )


def _ziyi_rec(*infos, bucket=''):
    return RoughHoldRecord(
        wafer_id='#01',
        record_type=RECORD_TYPE_FT,
        items=list(infos),
        all_source_ids=[i.id for i in infos],
        fragmented_merged=True,
        merge_code_bucket=bucket,
    )


def _info_row(info: HoldInfo) -> dict:
    return {
        'ID': info.id,
        'HOLD_DTTM': info.hold_dttm_raw,
        'STATION': info.station,
        'EQUIP_ID': info.equip_id,
        'PRODUCT_ID': info.product_id,
        'LOT_ID': info.lot_id,
        'WAFER_ID': info.wafer_id,
        'HOLD_CODE': info.hold_code,
        'HOLD_REASON': info.hold_reason,
        'SOURCE': info.source,
        'SECOND_CODE': info.second_code,
        'ROUTE_ID': info.route_id,
        'GRADE_NUM': info.grade_num,
        'HOLD_RECORD_ID': 10,
        'HOLDING': 0,
        'REMARK': None,
    }


_REF = datetime(2026, 8, 1, 10, 5, 0)
_WIN = timedelta(minutes=30)
_IN_WINDOW = datetime(2026, 8, 1, 10, 0, 0)
_OUT_WINDOW = datetime(2026, 8, 1, 9, 30, 0)


def _pick(rows, is_028=False, ref=_REF, window=_WIN):
    return select_earliest_ziyi_record(
        rows, is_028_bucket=is_028, ref_dttm=ref, window=window
    )


def _cand(**kwargs):
    row = {
        'SOURCE': 0,
        'STATUS': 0,
        'HOLD_CODE': '023',
        'HOLD_WAFER_ATTR': HOLD_WAFER_ATTR_ZIYI,
        'HOLD_DTTM': _IN_WINDOW,
    }
    row.update(kwargs)
    return row


class ZiyiCandidateTest(unittest.TestCase):
    def test_ziyi_fragmented_is_candidate(self):
        rec = _ziyi_rec(_info(1, '01'))
        self.assertTrue(is_ziyi_append_candidate(rec))

    def test_iqc_ate_is_not_candidate(self):
        rec = _ziyi_rec(_info(1, '01', equip='ATE015'))
        self.assertFalse(is_ziyi_append_candidate(rec))

    def test_wlt_is_not_candidate(self):
        rec = RoughHoldRecord(
            wafer_id='#14#15',
            record_type=RECORD_TYPE_WLT,
            items=[_info(1, '14', hold_code='004', product='XX-2.6', station='WOQC')],
            all_source_ids=[1],
            fragmented_merged=True,
            lot_id_override='C123456',
        )
        self.assertFalse(is_ziyi_append_candidate(rec))

    def test_single_wafer_not_fragmented(self):
        rec = RoughHoldRecord(
            wafer_id='C123456-01',
            record_type=RECORD_TYPE_FT,
            items=[_info(1, '01')],
            all_source_ids=[1],
            fragmented_merged=False,
        )
        self.assertFalse(is_ziyi_append_candidate(rec))


class HoldCode028BucketTest(unittest.TestCase):
    def test_028_tokens(self):
        self.assertTrue(hold_code_is_028_bucket('028'))
        self.assertTrue(hold_code_is_028_bucket('028@028'))
        self.assertFalse(hold_code_is_028_bucket('023'))
        self.assertFalse(hold_code_is_028_bucket('023@028'))
        self.assertFalse(hold_code_is_028_bucket(''))


class RecordRowIsZiyiTest(unittest.TestCase):
    def test_attr_bit(self):
        self.assertTrue(record_row_is_ziyi({'HOLD_WAFER_ATTR': HOLD_WAFER_ATTR_ZIYI}))
        self.assertFalse(record_row_is_ziyi({'HOLD_WAFER_ATTR': HOLD_WAFER_ATTR_IQC_ATE}))

    def test_attr_zero_falls_back_to_equip(self):
        self.assertTrue(record_row_is_ziyi({
            'HOLD_WAFER_ATTR': 0,
            'LOT_ID': 'C123456-033',
            'EQUIP_ID': 'FATE203',
            'STATION': 'FATE-FA',
        }))
        self.assertFalse(record_row_is_ziyi({
            'HOLD_WAFER_ATTR': 0,
            'LOT_ID': 'C123456-033',
            'EQUIP_ID': 'ATE015',
            'STATION': 'FIQC',
        }))


class SelectEarliestZiyiRecordTest(unittest.TestCase):
    def test_picks_min_id_ziyi_same_bucket(self):
        rows = [
            _cand(ID=20),
            _cand(ID=11, HOLD_CODE='023@025'),
        ]
        picked = _pick(rows)
        self.assertEqual(picked['ID'], 11)

    def test_028_does_not_match_yield_record(self):
        self.assertIsNone(_pick([_cand(ID=11)], is_028=True))

    def test_yield_does_not_match_028_record(self):
        self.assertIsNone(_pick([_cand(ID=11, HOLD_CODE='028')]))

    def test_skips_manual_and_iqc_ate(self):
        rows = [
            _cand(ID=1, SOURCE=1),
            _cand(ID=2, HOLD_WAFER_ATTR=HOLD_WAFER_ATTR_IQC_ATE),
        ]
        self.assertIsNone(_pick(rows))

    def test_028_matches_028_record(self):
        picked = _pick([_cand(ID=9, HOLD_CODE='028')], is_028=True)
        self.assertEqual(picked['ID'], 9)

    def test_window_includes_retest_status(self):
        rows = [
            _cand(ID=3, STATUS=3),
            _cand(ID=99, STATUS=99),
        ]
        picked = _pick(rows)
        self.assertEqual(picked['ID'], 3)

    def test_skips_outside_window_even_status_0(self):
        rows = [_cand(ID=1, STATUS=0, HOLD_DTTM=_OUT_WINDOW)]
        self.assertIsNone(_pick(rows))

    def test_missing_ref_matches_nothing(self):
        self.assertIsNone(
            select_earliest_ziyi_record(
                [_cand(ID=1)], is_028_bucket=False, ref_dttm=None
            )
        )

    def test_boundary_exactly_30_minutes_included(self):
        rows = [_cand(ID=4, HOLD_DTTM=_REF - _WIN)]
        picked = _pick(rows)
        self.assertEqual(picked['ID'], 4)


class BuildZiyiAppendUpdatesTest(unittest.TestCase):
    def test_merges_wafer_display(self):
        existing = [_info_row(_info(1, '01'))]
        incoming = _ziyi_rec(_info(2, '02'))
        updates = build_ziyi_append_updates(
            existing, incoming, window=timedelta(hours=1)
        )
        self.assertIsNotNone(updates)
        self.assertEqual(updates['WAFER_ID'], '#01#02')
        self.assertIn('023', updates['HOLD_CODE'])


class BuildRoughSetsBucketTest(unittest.TestCase):
    def test_fragmented_ziyi_groups_by_lot(self):
        rows = [
            {
                'ID': 1,
                'HOLD_DTTM': '2026-08-01 10:00:01',
                'STATION': 'FATE-FA',
                'EQUIP_ID': 'FATE203',
                'PRODUCT_ID': 'PROD-3.5',
                'LOT_ID': 'C123456-033',
                'WAFER_ID': 'C123456-01',
                'HOLD_CODE': '023',
                'HOLD_REASON': 'r',
                'SOURCE': 0,
            },
            {
                'ID': 2,
                'HOLD_DTTM': '2026-08-01 10:00:02',
                'STATION': 'FATE-FA',
                'EQUIP_ID': 'FATE203',
                'PRODUCT_ID': 'PROD-3.5',
                'LOT_ID': 'C123456-033',
                'WAFER_ID': 'C123456-02',
                'HOLD_CODE': '023',
                'HOLD_REASON': 'r',
                'SOURCE': 0,
            },
        ]
        records, skipped = build_rough_hold_records(rows)
        self.assertEqual(skipped, [])
        self.assertEqual(len(records), 1)
        self.assertTrue(records[0].fragmented_merged)
        self.assertEqual(records[0].merge_code_bucket, '')
        self.assertEqual(records[0].to_record_dict()['WAFER_ID'], '#01#02')


class PersistZiyiAppendTest(unittest.TestCase):
    def setUp(self):
        self.scheduler = HoldMergeScheduler()

    @patch('app.backend_schedule.FT_HOLD_MERGE_sche.mark_hold_infos_dirty')
    @patch('app.backend_schedule.FT_HOLD_MERGE_sche.insert_hold_record_and_link')
    @patch('app.backend_schedule.FT_HOLD_MERGE_sche.append_hold_infos_to_record')
    @patch('app.backend_schedule.FT_HOLD_MERGE_sche.query_hold_infos_by_record_id')
    @patch('app.backend_schedule.FT_HOLD_MERGE_sche.find_existing_ziyi_hold_record')
    def test_existing_ziyi_appends(
        self, find_existing, query_infos, append_fn, insert_fn, _dirty
    ):
        find_existing.return_value = {
            'ID': 88,
            'STATUS': 0,
            'HOLD_CODE': '023',
            'HOLD_WAFER_ATTR': HOLD_WAFER_ATTR_ZIYI,
        }
        query_infos.return_value = [_info_row(_info(1, '01'))]
        append_fn.return_value = 88
        rec = _ziyi_rec(_info(2, '02'))

        result = self.scheduler._persist_rough_record(rec)

        self.assertEqual(result, 88)
        find_existing.assert_called_once()
        self.assertFalse(find_existing.call_args.kwargs['is_028_bucket'])
        self.assertEqual(
            find_existing.call_args.kwargs['ref_dttm'],
            rec.to_record_dict()['HOLD_DTTM'],
        )
        self.assertEqual(
            find_existing.call_args.kwargs['window_minutes'],
            self.scheduler.interval_minutes,
        )
        append_fn.assert_called_once()
        insert_fn.assert_not_called()
        updates = append_fn.call_args[0][1]
        self.assertEqual(updates['WAFER_ID'], '#01#02')

    @patch('app.backend_schedule.FT_HOLD_MERGE_sche.mark_hold_infos_dirty')
    @patch('app.backend_schedule.FT_HOLD_MERGE_sche.insert_hold_record_and_link')
    @patch('app.backend_schedule.FT_HOLD_MERGE_sche.append_hold_infos_to_record')
    @patch('app.backend_schedule.FT_HOLD_MERGE_sche.query_hold_infos_by_record_id')
    @patch('app.backend_schedule.FT_HOLD_MERGE_sche.find_existing_ziyi_hold_record')
    def test_disposed_within_window_appends(
        self, find_existing, query_infos, append_fn, insert_fn, _dirty
    ):
        find_existing.return_value = {
            'ID': 88,
            'STATUS': 3,
            'HOLD_CODE': '023',
            'HOLD_WAFER_ATTR': HOLD_WAFER_ATTR_ZIYI,
        }
        query_infos.return_value = [_info_row(_info(1, '01'))]
        append_fn.return_value = 88
        rec = _ziyi_rec(_info(2, '02'))

        result = self.scheduler._persist_rough_record(rec)

        self.assertEqual(result, 88)
        append_fn.assert_called_once()
        insert_fn.assert_not_called()

    @patch('app.backend_schedule.FT_HOLD_MERGE_sche.mark_hold_infos_dirty')
    @patch('app.backend_schedule.FT_HOLD_MERGE_sche.insert_hold_record_and_link')
    @patch('app.backend_schedule.FT_HOLD_MERGE_sche.append_hold_infos_to_record')
    @patch('app.backend_schedule.FT_HOLD_MERGE_sche.find_existing_ziyi_hold_record')
    def test_no_existing_inserts(
        self, find_existing, append_fn, insert_fn, _dirty
    ):
        find_existing.return_value = None
        insert_fn.return_value = 99
        rec = _ziyi_rec(_info(1, '01'))

        result = self.scheduler._persist_rough_record(rec)

        self.assertEqual(result, 99)
        insert_fn.assert_called_once()
        append_fn.assert_not_called()

    @patch('app.backend_schedule.FT_HOLD_MERGE_sche.mark_hold_infos_dirty')
    @patch('app.backend_schedule.FT_HOLD_MERGE_sche.insert_hold_record_and_link')
    @patch('app.backend_schedule.FT_HOLD_MERGE_sche.append_hold_infos_to_record')
    @patch('app.backend_schedule.FT_HOLD_MERGE_sche.find_existing_ziyi_hold_record')
    def test_iqc_ate_inserts_without_lookup(
        self, find_existing, append_fn, insert_fn, _dirty
    ):
        insert_fn.return_value = 77
        rec = _ziyi_rec(_info(1, '01', equip='ATE015'))

        result = self.scheduler._persist_rough_record(rec)

        self.assertEqual(result, 77)
        find_existing.assert_not_called()
        append_fn.assert_not_called()
        insert_fn.assert_called_once()

    @patch('app.backend_schedule.FT_HOLD_MERGE_sche.mark_hold_infos_dirty')
    @patch('app.backend_schedule.FT_HOLD_MERGE_sche.insert_hold_record_and_link')
    @patch('app.backend_schedule.FT_HOLD_MERGE_sche.append_hold_infos_to_record')
    @patch('app.backend_schedule.FT_HOLD_MERGE_sche.query_hold_infos_by_record_id')
    @patch('app.backend_schedule.FT_HOLD_MERGE_sche.find_existing_ziyi_hold_record')
    def test_028_looks_up_028_bucket(
        self, find_existing, query_infos, append_fn, insert_fn, _dirty
    ):
        find_existing.return_value = {
            'ID': 5,
            'STATUS': 0,
            'HOLD_CODE': '028',
            'HOLD_WAFER_ATTR': HOLD_WAFER_ATTR_ZIYI,
        }
        query_infos.return_value = [_info_row(_info(1, '01', hold_code='028'))]
        append_fn.return_value = 5
        rec = _ziyi_rec(_info(2, '02', hold_code='028'), bucket='028')

        result = self.scheduler._persist_rough_record(rec)

        self.assertEqual(result, 5)
        self.assertTrue(find_existing.call_args.kwargs['is_028_bucket'])
        insert_fn.assert_not_called()

    @patch('app.backend_schedule.FT_HOLD_MERGE_sche.mark_hold_infos_dirty')
    @patch('app.backend_schedule.FT_HOLD_MERGE_sche.insert_hold_record_and_link')
    @patch('app.backend_schedule.FT_HOLD_MERGE_sche.append_hold_infos_to_record')
    @patch('app.backend_schedule.FT_HOLD_MERGE_sche.find_existing_ziyi_hold_record')
    def test_outside_window_inserts_new(
        self, find_existing, append_fn, insert_fn, _dirty
    ):
        # HOLD_DTTM 超出回看窗口时 find_existing 无命中，走新建
        find_existing.return_value = None
        insert_fn.return_value = 101
        rec = _ziyi_rec(_info(2, '02'))

        result = self.scheduler._persist_rough_record(rec)

        self.assertEqual(result, 101)
        insert_fn.assert_called_once()
        append_fn.assert_not_called()


if __name__ == '__main__':
    unittest.main()
