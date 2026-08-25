"""WLT / 合批片号格式：hold_count 匹配（无数据库）。"""
from __future__ import annotations

import unittest

from app.controllers.hold_report_ctrl import (
    _hold_count_match_spec,
    _stored_wafer_matches_hold_count,
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


class StoredWaferMatchTest(unittest.TestCase):
    def _spec(self, wafer_id, lot_id=None):
        exact_ids, _lot, display_tokens = _hold_count_match_spec(wafer_id, lot_id)
        return set(exact_ids), set(display_tokens)

    def test_wlt_single_display_matches_full_id(self):
        exact, disp = self._spec('C196721-05')
        self.assertTrue(_stored_wafer_matches_hold_count('#05', exact, disp))

    def test_wlt_merged_display_does_not_count_as_single_wafer(self):
        exact, disp = self._spec('C196721-05')
        self.assertFalse(_stored_wafer_matches_hold_count('#01#02#05', exact, disp))

    def test_other_wafer_in_same_lot_excluded(self):
        exact, disp = self._spec('C196721-05')
        self.assertFalse(_stored_wafer_matches_hold_count('#13', exact, disp))

    def test_exact_full_id_still_matches(self):
        exact, disp = self._spec('C196721-05')
        self.assertTrue(_stored_wafer_matches_hold_count('C196721-05', exact, disp))


if __name__ == '__main__':
    unittest.main()
