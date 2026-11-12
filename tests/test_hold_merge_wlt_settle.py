"""梓一 / WLT 同 lot 等插入安静后再建单：不连 Oracle。"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from app.backend_schedule.FT_HOLD_MERGE_sche import (
    HoldMergeScheduler,
    build_rough_hold_records,
    decide_lot_quiet,
    evaluate_lot_quiet,
    pick_latest_wafer_id,
    quiet_state_key_for_record,
    quiet_state_key_for_row,
    rows_for_merge,
)
from app.utils.pre_handler_lookup import LotOrder

_NOW = datetime(2026, 10, 26, 18, 0, 0)
_QUIET = timedelta(minutes=30)
_MAX_WAIT = timedelta(minutes=120)


def _mark(max_id, *, growth_minutes_ago, first_minutes_ago=None):
    first_ago = growth_minutes_ago if first_minutes_ago is None else first_minutes_ago
    return {
        'max_id': max_id,
        'last_growth_at': (_NOW - timedelta(minutes=growth_minutes_ago)).strftime(
            '%Y-%m-%dT%H:%M:%S'
        ),
        'first_seen_at': (_NOW - timedelta(minutes=first_ago)).strftime(
            '%Y-%m-%dT%H:%M:%S'
        ),
    }


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


def _ft_row(
    info_id,
    wafer_suffix,
    hold_dttm,
    *,
    equip='FATE203',
    lot='C123456-033',
    wafer=None,
    station='FATE-FA',
    product='PROD-3.5',
    hold_code='023',
):
    return {
        'ID': info_id,
        'HOLD_DTTM': hold_dttm.strftime('%Y-%m-%d %H:%M:%S'),
        'STATION': station,
        'EQUIP_ID': equip,
        'PRODUCT_ID': product,
        'LOT_ID': lot,
        'WAFER_ID': wafer or f'C123456-{wafer_suffix}',
        'HOLD_CODE': hold_code,
        'HOLD_REASON': 'r',
        'SOURCE': 0,
    }


class EvaluateLotQuietTest(unittest.TestCase):
    def test_first_sight_defers(self):
        status, mark = evaluate_lot_quiet(None, 10, _NOW, _QUIET, _MAX_WAIT)
        self.assertEqual(status, 'defer')
        self.assertEqual(mark['max_id'], 10)
        self.assertEqual(mark['first_seen_at'], mark['last_growth_at'])

    def test_larger_id_defers_and_keeps_first_seen(self):
        status, mark = evaluate_lot_quiet(
            _mark(8, growth_minutes_ago=40, first_minutes_ago=50),
            12,
            _NOW,
            _QUIET,
            _MAX_WAIT,
        )
        self.assertEqual(status, 'defer')
        self.assertEqual(mark['max_id'], 12)
        self.assertEqual(
            mark['first_seen_at'],
            (_NOW - timedelta(minutes=50)).strftime('%Y-%m-%dT%H:%M:%S'),
        )

    def test_unchanged_id_after_quiet_is_ready(self):
        status, mark = evaluate_lot_quiet(
            _mark(8, growth_minutes_ago=30),
            8,
            _NOW,
            _QUIET,
            _MAX_WAIT,
        )
        self.assertEqual(status, 'ready')
        self.assertIsNone(mark)

    def test_unchanged_id_inside_quiet_still_defers(self):
        status, mark = evaluate_lot_quiet(
            _mark(8, growth_minutes_ago=29),
            8,
            _NOW,
            _QUIET,
            _MAX_WAIT,
        )
        self.assertEqual(status, 'defer')
        self.assertEqual(mark['max_id'], 8)

    def test_growth_past_max_wait_caps(self):
        status, mark = evaluate_lot_quiet(
            _mark(8, growth_minutes_ago=10, first_minutes_ago=120),
            12,
            _NOW,
            _QUIET,
            _MAX_WAIT,
        )
        self.assertEqual(status, 'cap')
        self.assertIsNone(mark)

    def test_smaller_id_uses_quiet_window(self):
        status, mark = evaluate_lot_quiet(
            _mark(20, growth_minutes_ago=5),
            8,
            _NOW,
            _QUIET,
            _MAX_WAIT,
        )
        self.assertEqual(status, 'defer')
        self.assertEqual(mark['max_id'], 20)


class LatestWaferHoldbackTest(unittest.TestCase):
    def test_pick_latest_uses_hold_dttm_then_id(self):
        early = _ft_row(1, '01', _NOW - timedelta(minutes=5), lot='C1-01', wafer='C1-01')
        late_small_id = _ft_row(
            2, '02', _NOW, lot='C2-02', wafer='C2-02',
        )
        late_big_id = _ft_row(
            9, '02', _NOW, lot='C2-02', wafer='C2-02',
        )
        self.assertEqual(pick_latest_wafer_id([early, late_small_id]), 'C2-02')
        same_time = [
            _ft_row(3, '01', _NOW, lot='A-01', wafer='A-01'),
            _ft_row(4, '02', _NOW, lot='B-02', wafer='B-02'),
        ]
        self.assertEqual(pick_latest_wafer_id(same_time), 'B-02')
        self.assertEqual(pick_latest_wafer_id([late_big_id, late_small_id]), 'C2-02')

    def test_ziyi_keeps_global_latest_wafer(self):
        older = _ft_row(1, '01', _NOW - timedelta(hours=2))
        latest = _ft_row(2, '02', _NOW)
        kept = rows_for_merge([older, latest])
        self.assertEqual([row['ID'] for row in kept], [1, 2])
        self.assertTrue(quiet_state_key_for_row(latest))

    def test_wlt_keeps_global_latest_wafer(self):
        older = _wlt_row(1, '13', _NOW - timedelta(hours=1))
        latest = _wlt_row(2, '14', _NOW)
        kept = rows_for_merge([older, latest])
        self.assertEqual({row['ID'] for row in kept}, {1, 2})
        records, skipped = build_rough_hold_records(kept)
        self.assertEqual(skipped, [])
        self.assertEqual(len(records), 1)
        self.assertEqual(
            quiet_state_key_for_record(records[0]),
            quiet_state_key_for_row(latest),
        )

    def test_single_wafer_latest_is_held_back(self):
        older = _ft_row(1, '01', _NOW - timedelta(hours=1), lot='C1-01', wafer='C1-01')
        latest = _ft_row(2, '02', _NOW, lot='C2-02', wafer='C2-02')
        kept = rows_for_merge([older, latest])
        self.assertEqual([row['ID'] for row in kept], [1])

    def test_iqc_lot_drops_only_latest_wafer(self):
        older = _ft_row(1, '01', _NOW - timedelta(hours=1), equip='ATE015')
        latest = _ft_row(2, '02', _NOW, equip='ATE015')
        kept = rows_for_merge([older, latest])
        self.assertEqual([row['ID'] for row in kept], [1])
        self.assertIsNone(quiet_state_key_for_row(older))


class DecideLotQuietTest(unittest.TestCase):
    def test_unseen_stale_key_is_pruned(self):
        old = _mark(3, growth_minutes_ago=121)
        _actions, deferred, capped, state = decide_lot_quiet(
            [], {'lot|GONE|0|': old}, _NOW, _QUIET, _MAX_WAIT,
        )
        self.assertEqual(deferred, [])
        self.assertEqual(capped, [])
        self.assertEqual(state, {})

    def test_recent_unseen_key_is_kept(self):
        recent = _mark(3, growth_minutes_ago=10)
        _actions, _deferred, _capped, state = decide_lot_quiet(
            [], {'wlt_lot|S83209|2|': recent}, _NOW, _QUIET, _MAX_WAIT,
        )
        self.assertIn('wlt_lot|S83209|2|', state)


class RunJobLotQuietTest(unittest.TestCase):
    def setUp(self):
        self.scheduler = HoldMergeScheduler()
        self.scheduler.lot_quiet_minutes = 30
        self.scheduler.lot_quiet_max_minutes = 120
        self.scheduler.lot_quiet = timedelta(minutes=30)
        self.scheduler.lot_quiet_max = timedelta(minutes=120)
        self._dir = tempfile.TemporaryDirectory()
        self._path = os.path.join(self._dir.name, 'quiet.json')
        self.scheduler._quiet_state_path = lambda: self._path
        self._order = patch(
            'app.backend_schedule.FT_HOLD_MERGE_sche.lookup_lot_order',
            return_value=LotOrder(ok=True, oa='ET.1', wo='WO-1'),
        )
        self._order.start()

    def tearDown(self):
        self._order.stop()
        self._dir.cleanup()

    def _age_watermark(self, minutes):
        with open(self._path, encoding='utf-8') as handle:
            data = json.load(handle)
        stamp = (datetime.now() - timedelta(minutes=minutes)).strftime(
            '%Y-%m-%dT%H:%M:%S'
        )
        for entry in data.values():
            entry['last_growth_at'] = stamp
            entry['first_seen_at'] = stamp
        with open(self._path, 'w', encoding='utf-8') as handle:
            json.dump(data, handle)

    @patch('app.backend_schedule.FT_HOLD_MERGE_sche.mark_hold_infos_dirty')
    @patch('app.backend_schedule.FT_HOLD_MERGE_sche.insert_hold_record_and_link')
    @patch('app.backend_schedule.FT_HOLD_MERGE_sche.query_online_hold_info')
    def test_first_sight_wlt_does_not_insert(self, query_fn, insert_fn, dirty_fn):
        now = datetime.now()
        query_fn.return_value = [
            _wlt_row(1, '13', now - timedelta(hours=3)),
            _wlt_row(2, '14', now - timedelta(minutes=1)),
        ]
        self.scheduler._run_job()
        insert_fn.assert_not_called()
        dirty_fn.assert_not_called()
        with open(self._path, encoding='utf-8') as handle:
            state = json.load(handle)
        self.assertEqual(len(state), 1)
        self.assertEqual(next(iter(state.values()))['max_id'], 2)

    @patch('app.backend_schedule.FT_HOLD_MERGE_sche.mark_hold_infos_dirty')
    @patch('app.backend_schedule.FT_HOLD_MERGE_sche.insert_hold_record_and_link')
    @patch('app.backend_schedule.FT_HOLD_MERGE_sche.query_online_hold_info')
    def test_quiet_wlt_inserts_both_wafers_including_latest(
        self, query_fn, insert_fn, dirty_fn
    ):
        now = datetime.now()
        query_fn.return_value = [
            _wlt_row(1, '13', now - timedelta(hours=3)),
            _wlt_row(2, '14', now),
        ]
        insert_fn.return_value = 99
        self.scheduler._run_job()
        self._age_watermark(31)
        self.scheduler._run_job()
        insert_fn.assert_called_once()
        row = insert_fn.call_args.args[0]
        source_ids = insert_fn.call_args.args[1]
        self.assertEqual(row['WAFER_ID'], '#13#14')
        self.assertCountEqual(source_ids, [1, 2])
        dirty_fn.assert_not_called()

    @patch('app.backend_schedule.FT_HOLD_MERGE_sche.mark_hold_infos_dirty')
    @patch('app.backend_schedule.FT_HOLD_MERGE_sche.insert_hold_record_and_link')
    @patch('app.backend_schedule.FT_HOLD_MERGE_sche.query_online_hold_info')
    def test_new_wlt_id_resets_wait(self, query_fn, insert_fn, dirty_fn):
        now = datetime.now()
        first = [
            _wlt_row(1, '13', now - timedelta(hours=3)),
            _wlt_row(2, '14', now - timedelta(hours=2)),
        ]
        query_fn.return_value = first
        self.scheduler._run_job()
        self._age_watermark(31)
        query_fn.return_value = first + [
            _wlt_row(3, '15', now - timedelta(hours=2)),
        ]
        self.scheduler._run_job()
        insert_fn.assert_not_called()
        with open(self._path, encoding='utf-8') as handle:
            state = json.load(handle)
        self.assertEqual(next(iter(state.values()))['max_id'], 3)

    @patch('app.backend_schedule.FT_HOLD_MERGE_sche.find_existing_ziyi_hold_record')
    @patch('app.backend_schedule.FT_HOLD_MERGE_sche.mark_hold_infos_dirty')
    @patch('app.backend_schedule.FT_HOLD_MERGE_sche.insert_hold_record_and_link')
    @patch('app.backend_schedule.FT_HOLD_MERGE_sche.query_online_hold_info')
    def test_ziyi_waits_then_inserts_one_record(
        self, query_fn, insert_fn, dirty_fn, find_existing
    ):
        now = datetime.now()
        query_fn.return_value = [
            _ft_row(11, '01', now - timedelta(hours=5)),
            _ft_row(12, '02', now),
        ]
        insert_fn.return_value = 77
        find_existing.return_value = None
        self.scheduler._run_job()
        insert_fn.assert_not_called()
        self._age_watermark(31)
        self.scheduler._run_job()
        insert_fn.assert_called_once()
        row = insert_fn.call_args.args[0]
        self.assertEqual(row['WAFER_ID'], '#01#02')
        self.assertEqual(row['LOT_ID'], 'C123456-033')
        self.assertCountEqual(insert_fn.call_args.args[1], [11, 12])
        dirty_fn.assert_not_called()

    @patch('app.backend_schedule.FT_HOLD_MERGE_sche.find_existing_ziyi_hold_record')
    @patch('app.backend_schedule.FT_HOLD_MERGE_sche.mark_hold_infos_dirty')
    @patch('app.backend_schedule.FT_HOLD_MERGE_sche.insert_hold_record_and_link')
    @patch('app.backend_schedule.FT_HOLD_MERGE_sche.query_online_hold_info')
    def test_ziyi_caps_when_rows_keep_arriving(
        self, query_fn, insert_fn, dirty_fn, find_existing
    ):
        now = datetime.now()
        query_fn.return_value = [
            _ft_row(1, '01', now - timedelta(hours=5)),
        ]
        insert_fn.return_value = 77
        find_existing.return_value = None
        self.scheduler._run_job()
        with open(self._path, encoding='utf-8') as handle:
            data = json.load(handle)
        stamp = (datetime.now() - timedelta(minutes=121)).strftime(
            '%Y-%m-%dT%H:%M:%S'
        )
        for entry in data.values():
            entry['first_seen_at'] = stamp
            entry['last_growth_at'] = stamp
        with open(self._path, 'w', encoding='utf-8') as handle:
            json.dump(data, handle)
        query_fn.return_value = [
            _ft_row(1, '01', now - timedelta(hours=5)),
            _ft_row(9, '02', now),
        ]
        self.scheduler._run_job()
        insert_fn.assert_called_once()
        self.assertCountEqual(insert_fn.call_args.args[1], [1, 9])

    @patch('app.backend_schedule.FT_HOLD_MERGE_sche.mark_hold_infos_dirty')
    @patch('app.backend_schedule.FT_HOLD_MERGE_sche.insert_hold_record_and_link')
    @patch('app.backend_schedule.FT_HOLD_MERGE_sche.query_online_hold_info')
    def test_single_wafer_latest_is_not_persisted(
        self, query_fn, insert_fn, dirty_fn
    ):
        now = datetime.now()
        query_fn.return_value = [
            _ft_row(1, '01', now - timedelta(hours=2), lot='C1-01', wafer='C1-01'),
            _ft_row(2, '02', now, lot='C2-02', wafer='C2-02'),
        ]
        insert_fn.return_value = 5
        self.scheduler._run_job()
        insert_fn.assert_called_once()
        self.assertEqual(insert_fn.call_args.args[1], [1])
        dirty_fn.assert_not_called()

    @patch('app.backend_schedule.FT_HOLD_MERGE_sche.mark_hold_infos_dirty')
    @patch('app.backend_schedule.FT_HOLD_MERGE_sche.insert_hold_record_and_link')
    @patch('app.backend_schedule.FT_HOLD_MERGE_sche.query_online_hold_info')
    def test_iqc_does_not_wait(self, query_fn, insert_fn, dirty_fn):
        now = datetime.now()
        query_fn.return_value = [
            _ft_row(1, '01', now - timedelta(hours=1), equip='ATE015'),
            _ft_row(2, '02', now - timedelta(minutes=1), equip='ATE015'),
        ]
        insert_fn.return_value = 6
        self.scheduler._run_job()
        insert_fn.assert_called_once()
        self.assertEqual(insert_fn.call_args.args[1], [1])
        with open(self._path, encoding='utf-8') as handle:
            self.assertEqual(json.load(handle), {})


if __name__ == '__main__':
    unittest.main()
