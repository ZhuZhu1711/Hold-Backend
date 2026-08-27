"""旧表写回：码映射 / comment / 片号对齐。不连 Oracle。"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from app.utils.legacy_dispose_writeback import (
    build_ate_comment,
    build_wlt_comment,
    map_new_dispose_to_old,
    match_wlt_physical_wafer,
    writeback_after_dispose,
    writeback_enabled,
)


class MapDisposeTest(unittest.TestCase):
    def test_known_codes(self):
        self.assertEqual(map_new_dispose_to_old(1), 0)
        self.assertEqual(map_new_dispose_to_old(2), 1)
        self.assertEqual(map_new_dispose_to_old(3), 2)
        self.assertEqual(map_new_dispose_to_old(5), 4)
        self.assertEqual(map_new_dispose_to_old('1'), 0)

    def test_unmapped(self):
        self.assertIsNone(map_new_dispose_to_old(8))
        self.assertIsNone(map_new_dispose_to_old(99))
        self.assertIsNone(map_new_dispose_to_old(65))
        self.assertIsNone(map_new_dispose_to_old(None))
        self.assertIsNone(map_new_dispose_to_old('x'))


class AteCommentTest(unittest.TestCase):
    def test_release(self):
        self.assertEqual(
            build_ate_comment(1, dispose_manual_note='ok'),
            'SYS\nok',
        )

    def test_analyze_falls_back_to_note(self):
        self.assertEqual(
            build_ate_comment(5, dispose_note='RE'),
            'SYS\nRE',
        )

    def test_empty_note(self):
        self.assertEqual(build_ate_comment(1), 'SYS\n')

    def test_downgrade_from_pairs(self):
        comment = build_ate_comment(
            2,
            downgrades=[{'from': 'HA', 'to': 'F'}, {'from': 'FB', 'to': 'F'}],
            dispose_manual_note='note',
        )
        self.assertEqual(comment, 'SYS\nHA>F;FB>F\nnote')

    def test_downgrade_from_detail_prefix(self):
        comment = build_ate_comment(2, dispose_detail='DG:HA>F')
        self.assertEqual(comment, 'SYS\nHA>F')

    def test_retest_grades(self):
        comment = build_ate_comment(
            3,
            retest_grades=['F', 'HA', 'F'],
            dispose_manual_note='rt',
        )
        self.assertEqual(comment, 'SYS\nF, HA\nrt')

    def test_retest_from_detail(self):
        comment = build_ate_comment(3, dispose_detail='RT:F,HA')
        self.assertEqual(comment, 'SYS\nF,HA')


class WltCommentTest(unittest.TestCase):
    def test_release(self):
        self.assertEqual(build_wlt_comment(1, dispose_manual_note='放行'), 'SYS\n放行')

    def test_downgrade_split(self):
        self.assertEqual(
            build_wlt_comment(2, downgrade_mode='main_split'),
            'SYS\n降级main(拆批)',
        )

    def test_downgrade_nosplit_default(self):
        self.assertEqual(
            build_wlt_comment(2, downgrade_mode='main_nosplit', dispose_manual_note='x'),
            'SYS\n降级main(不拆批)\nx',
        )

    def test_rework(self):
        self.assertEqual(
            build_wlt_comment(3, retest_mode='full', dispose_manual_note='n'),
            'SYS\nRework WLT\nn',
        )

    def test_full_prefers_codes(self):
        self.assertEqual(
            build_wlt_comment(
                3,
                retest_mode='full',
                retest_codes='@1@361',
                dispose_manual_note='ignored',
            ),
            'SYS\nRework WLT\n@1@361',
        )

    def test_fixture_a_prefers_codes(self):
        self.assertEqual(
            build_wlt_comment(
                3,
                retest_mode='fixture_a',
                retest_codes='@1@361',
                dispose_manual_note='ignored',
            ),
            'SYS\nA夹具重测(备注中填code)\n@1@361',
        )

    def test_fixture_b(self):
        self.assertEqual(
            build_wlt_comment(3, retest_mode='fixture_b', retest_codes='@2'),
            'SYS\nB夹具重测(备注中填code)\n@2',
        )

    def test_analyze(self):
        self.assertEqual(build_wlt_comment(5, dispose_manual_note='RE'), 'SYS\nRE')


class MatchWaferTest(unittest.TestCase):
    def test_exact_full_id(self):
        self.assertEqual(
            match_wlt_physical_wafer('#01', 'C123456', ['C123456-01', 'C123456-02']),
            'C123456-01',
        )

    def test_dotted_physical_id(self):
        self.assertEqual(
            match_wlt_physical_wafer('#03', 'C123456', ['C123456.xx-03']),
            'C123456.xx-03',
        )

    def test_display_already_full(self):
        self.assertEqual(
            match_wlt_physical_wafer('C123456-02', 'C123456', ['C123456-01', 'C123456-02']),
            'C123456-02',
        )

    def test_no_match(self):
        self.assertIsNone(
            match_wlt_physical_wafer('#09', 'C123456', ['C123456-01']),
        )

    def test_empty_open_rows(self):
        self.assertIsNone(match_wlt_physical_wafer('#01', 'C123456', []))


class WritebackGuardTest(unittest.TestCase):
    def test_enabled_reads_config_flag(self):
        with patch(
            'app.utils.legacy_dispose_writeback.Config.LEGACY_DISPOSE_WRITEBACK_ENABLED',
            False,
        ):
            self.assertFalse(writeback_enabled())
        with patch(
            'app.utils.legacy_dispose_writeback.Config.LEGACY_DISPOSE_WRITEBACK_ENABLED',
            True,
        ):
            self.assertTrue(writeback_enabled())

    @patch('app.utils.legacy_dispose_writeback.writeback_enabled', return_value=False)
    @patch('app.utils.legacy_dispose_writeback._writeback_ate')
    @patch('app.utils.legacy_dispose_writeback._writeback_wlt')
    def test_disabled_does_not_touch_db(self, wlt, ate, _enabled):
        writeback_after_dispose(
            {'ID': 1, 'LOT_ID': 'C123456', 'RECORD_TYPE': 0},
            dispose=1,
            actor_user_id=10,
        )
        ate.assert_not_called()
        wlt.assert_not_called()

    @patch('app.utils.legacy_dispose_writeback.writeback_enabled', return_value=True)
    @patch('app.utils.legacy_dispose_writeback._writeback_ate')
    def test_unmapped_dispose_skips(self, ate, _enabled):
        writeback_after_dispose(
            {'ID': 1, 'LOT_ID': 'C123456', 'RECORD_TYPE': 0},
            dispose=99,
            actor_user_id=10,
        )
        ate.assert_not_called()

    @patch('app.utils.legacy_dispose_writeback.writeback_enabled', return_value=True)
    @patch('app.utils.legacy_dispose_writeback._writeback_ate')
    def test_empty_lot_skips(self, ate, _enabled):
        writeback_after_dispose(
            {'ID': 1, 'LOT_ID': '', 'RECORD_TYPE': 0},
            dispose=1,
            actor_user_id=10,
        )
        ate.assert_not_called()

    @patch('app.utils.legacy_dispose_writeback.writeback_enabled', return_value=True)
    @patch('app.utils.legacy_dispose_writeback._writeback_ate')
    def test_ate_routes_to_hold_info(self, ate, _enabled):
        writeback_after_dispose(
            {'ID': 1, 'LOT_ID': 'C123456', 'RECORD_TYPE': 0},
            dispose=1,
            actor_user_id=10,
            dispose_manual_note='ok',
        )
        ate.assert_called_once()
        kwargs = ate.call_args.kwargs
        self.assertEqual(kwargs['lot_id'], 'C123456')
        self.assertEqual(kwargs['eng_id'], 10)
        self.assertEqual(kwargs['old_code'], 0)
        self.assertEqual(kwargs['comment'], 'SYS\nok')

    @patch('app.utils.legacy_dispose_writeback.writeback_enabled', return_value=True)
    @patch('app.utils.legacy_dispose_writeback._writeback_wlt')
    def test_wlt_routes_to_wlt_hold_info(self, wlt, _enabled):
        actions = [{'wafer': '#01', 'dispose': 1}]
        writeback_after_dispose(
            {'ID': 2, 'LOT_ID': 'C123456', 'RECORD_TYPE': 2},
            dispose=1,
            actor_user_id=10,
            wafer_actions=actions,
        )
        wlt.assert_called_once()
        self.assertEqual(wlt.call_args.kwargs['lot_id'], 'C123456')
        self.assertEqual(wlt.call_args.kwargs['wafer_actions'], actions)


if __name__ == '__main__':
    unittest.main()
