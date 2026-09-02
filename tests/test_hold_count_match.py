"""WLT / 合批片号格式：hold_count 匹配（无数据库）。"""
from __future__ import annotations

import unittest

from app.controllers.hold_report_ctrl import (
    _hold_count_display_lot_params,
    _hold_count_match_spec,
    _hold_count_suffix_keys,
    _stored_wafer_matches_hold_count,
    hold_count_match_by_lot,
)


class HoldCountMatchSpecTest(unittest.TestCase):
    def test_full_mes_id_infers_lot_and_display_token(self):
        exact_ids, lot_prefix, display_tokens = _hold_count_match_spec('C196721-05')
        self.assertEqual(exact_ids, ['C196721-05'])
        self.assertEqual(lot_prefix, 'C196721')
        self.assertIn('#05', display_tokens)
        self.assertNotIn('#05', exact_ids)

    def test_display_token_without_lot_not_in_exact_ids(self):
        exact_ids, lot_prefix, display_tokens = _hold_count_match_spec('#05')
        self.assertEqual(exact_ids, [])
        self.assertEqual(lot_prefix, '')
        self.assertIn('#05', display_tokens)

    def test_display_token_expands_with_lot(self):
        exact_ids, lot_prefix, display_tokens = _hold_count_match_spec('#05', 'C196721.01')
        self.assertIn('C196721-05', exact_ids)
        self.assertNotIn('#05', exact_ids)
        self.assertEqual(lot_prefix, 'C196721')
        self.assertIn('#05', display_tokens)

    def test_merged_query_keeps_group_token_only(self):
        _exact, lot_prefix, display_tokens = _hold_count_match_spec('#01#02#05', 'C196721')
        self.assertEqual(lot_prefix, 'C196721')
        self.assertIn('#01#02#05', display_tokens)
        self.assertNotIn('#05', display_tokens)


class SuffixKeysTest(unittest.TestCase):
    def test_padded_and_unpadded_same_key(self):
        self.assertEqual(
            _hold_count_suffix_keys('#05'),
            _hold_count_suffix_keys('#5'),
        )
        self.assertEqual(_hold_count_suffix_keys('#05'), {('n', 5)})

    def test_group_string_splits_tokens(self):
        self.assertEqual(
            _hold_count_suffix_keys('#01#02#05'),
            {('n', 1), ('n', 2), ('n', 5)},
        )

    def test_three_digit_not_equal_two_digit(self):
        self.assertFalse(_hold_count_suffix_keys('#050') & _hold_count_suffix_keys('#05'))


class StoredWaferMatchTest(unittest.TestCase):
    def _spec(self, wafer_id, lot_id=None):
        exact_ids, _lot, display_tokens = _hold_count_match_spec(wafer_id, lot_id)
        return set(exact_ids), set(display_tokens)

    def test_wlt_single_display_matches_full_id(self):
        exact, disp = self._spec('C196721-05')
        self.assertTrue(_stored_wafer_matches_hold_count('#05', exact, disp))

    def test_wlt_merged_display_counts_when_contains_wafer(self):
        exact, disp = self._spec('C196721-05')
        self.assertTrue(_stored_wafer_matches_hold_count('#01#02#05', exact, disp))

    def test_wlt_merged_display_with_spaces(self):
        exact, disp = self._spec('C196721-05')
        self.assertTrue(_stored_wafer_matches_hold_count('#01 #02 #05', exact, disp))

    def test_other_wafer_in_same_lot_excluded(self):
        exact, disp = self._spec('C196721-05')
        self.assertFalse(_stored_wafer_matches_hold_count('#13', exact, disp))

    def test_unrelated_group_excluded(self):
        exact, disp = self._spec('C196721-05')
        self.assertFalse(_stored_wafer_matches_hold_count('#01#02#13', exact, disp))

    def test_three_digit_token_not_treated_as_contains(self):
        exact, disp = self._spec('C196721-05')
        self.assertFalse(_stored_wafer_matches_hold_count('#050', exact, disp))

    def test_exact_full_id_still_matches(self):
        exact, disp = self._spec('C196721-05')
        self.assertTrue(_stored_wafer_matches_hold_count('C196721-05', exact, disp))

    def test_query_group_matches_single_member_display(self):
        exact, disp = self._spec('#01#02#05', 'C196721')
        self.assertTrue(_stored_wafer_matches_hold_count('#05', exact, disp))
        self.assertFalse(_stored_wafer_matches_hold_count('#13', exact, disp))

    def test_ft_merged_display_still_matches_source_wafer(self):
        exact, disp = self._spec('C199627-13', 'C199627-1312')
        self.assertTrue(_stored_wafer_matches_hold_count('#13', exact, disp))
        self.assertFalse(_stored_wafer_matches_hold_count('#24', exact, disp))


class HoldCountLotScopeTest(unittest.TestCase):
    @staticmethod
    def _lot_in_scope(stored_lot, lot_prefix):
        stored = str(stored_lot or '').strip()
        prefix = str(lot_prefix or '').strip()
        return (
            stored == prefix
            or stored.startswith(f'{prefix}.')
            or stored.startswith(f'{prefix}-')
        )

    def test_dash_pattern_covers_ft_merged_lot(self):
        params = _hold_count_display_lot_params('C199627')
        self.assertEqual(params['lot_like_dash'], 'C199627-%')
        self.assertTrue(self._lot_in_scope('C199627-1312', 'C199627'))

    def test_dot_and_prefix_still_in_scope(self):
        self.assertTrue(self._lot_in_scope('C199627', 'C199627'))
        self.assertTrue(self._lot_in_scope('C199627.14', 'C199627'))

    def test_other_lot_excluded(self):
        self.assertFalse(self._lot_in_scope('C199628-1312', 'C199627'))
        self.assertFalse(self._lot_in_scope('C1996270-13', 'C199627'))


class HoldCountMatchByLotTest(unittest.TestCase):
    def test_ziyi_bit_matches_lot(self):
        self.assertTrue(hold_count_match_by_lot(2))
        self.assertTrue(hold_count_match_by_lot('2'))
        self.assertTrue(hold_count_match_by_lot(3))

    def test_other_attr_keeps_wafer_match(self):
        self.assertFalse(hold_count_match_by_lot(0))
        self.assertFalse(hold_count_match_by_lot(1))
        self.assertFalse(hold_count_match_by_lot(4))
        self.assertFalse(hold_count_match_by_lot(8))
        self.assertFalse(hold_count_match_by_lot(16))
        self.assertFalse(hold_count_match_by_lot(None))
        self.assertFalse(hold_count_match_by_lot(''))
        self.assertFalse(hold_count_match_by_lot('x'))


if __name__ == '__main__':
    unittest.main()
