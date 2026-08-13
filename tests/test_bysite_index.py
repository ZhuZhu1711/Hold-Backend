"""unittest for compute_bysite_index — 不连库。"""
from __future__ import annotations

import unittest

from app.hold_predict.bysite_index import (
    compute_bysite_index,
    merge_site_bin_matrices,
)


class BysiteIndexTest(unittest.TestCase):
    def test_missing_matrix(self):
        out = compute_bysite_index(None)
        self.assertEqual(out['missing_bysite'], 1)
        self.assertIsNone(out['bysite_index'])

    def test_single_site_degenerate(self):
        out = compute_bysite_index({'1': {'1': 100, '2': 20}})
        self.assertEqual(out['missing_bysite'], 0)
        self.assertEqual(out['bysite_degenerate'], 1)
        self.assertIsNone(out['bysite_index'])

    def test_all_pass_degenerate(self):
        matrix = {
            '1': {'1': 100},
            '2': {'1': 100},
            '3': {'1': 100},
        }
        out = compute_bysite_index(matrix)
        self.assertEqual(out['bysite_degenerate'], 1)
        self.assertIsNone(out['bysite_index'])

    def test_concentrated_fail_high_index(self):
        # site 1 几乎包揽失败 → 像机台误测
        matrix = {
            '1': {'1': 80, '2': 40},
            '2': {'1': 99, '2': 1},
            '3': {'1': 99, '2': 1},
            '4': {'1': 99, '2': 1},
        }
        high = compute_bysite_index(matrix)
        self.assertEqual(high['missing_bysite'], 0)
        self.assertGreater(high['bysite_index'], 0.4)
        self.assertEqual(high['suspect_site'], 1)
        self.assertGreater(high['fail_max_share'], 0.8)

        even = compute_bysite_index({
            '1': {'1': 90, '2': 10},
            '2': {'1': 90, '2': 10},
            '3': {'1': 90, '2': 10},
            '4': {'1': 90, '2': 10},
        })
        self.assertGreater(high['bysite_index'], even['bysite_index'])

    def test_merge_payload_list(self):
        payload = [
            {'bysite': {'1': {'1': 10, '2': 2}, '2': {'1': 8}}},
            {'bysite': {'1': {'2': 3}, '2': {'1': 1}}},
        ]
        merged = merge_site_bin_matrices(payload)
        self.assertEqual(merged['1']['1'], 10)
        self.assertEqual(merged['1']['2'], 5)
        self.assertEqual(merged['2']['1'], 9)


if __name__ == '__main__':
    unittest.main()
