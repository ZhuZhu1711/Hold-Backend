"""无数据库的特征小函数测试。"""
from __future__ import annotations

import unittest

from app.hold_predict.features import extract_grade_features, route_is_eng, split_hold_codes
from app.hold_predict.eval import auc_roc, brier_score


class RouteAndGradeTest(unittest.TestCase):
    def test_route_is_eng(self):
        self.assertEqual(route_is_eng('FT-ENG-01'), (1, 0))
        self.assertEqual(route_is_eng('FT_MP_MAIN'), (0, 0))
        self.assertEqual(route_is_eng(None), (0, 1))
        self.assertEqual(route_is_eng('  '), (0, 1))

    def test_split_hold_codes(self):
        self.assertEqual(split_hold_codes('023@024'), ['023', '024'])
        self.assertEqual(split_hold_codes('025'), ['025'])

    def test_grade_features(self):
        feats = extract_grade_features('F:100,HA:20,TA:80')
        self.assertEqual(feats['missing_grade_num'], 0)
        self.assertEqual(feats['qty_F'], 100)
        self.assertEqual(feats['qty_HA'], 20)
        self.assertEqual(feats['qty_TA'], 80)
        self.assertEqual(feats['total_qty'], 200)
        self.assertAlmostEqual(feats['ratio_F'], 0.5)
        self.assertAlmostEqual(feats['ratio_passA'], 0.5)

    def test_missing_grade(self):
        feats = extract_grade_features('')
        self.assertEqual(feats['missing_grade_num'], 1)
        self.assertIsNone(feats['qty_F'])


class MetricsTest(unittest.TestCase):
    def test_auc_perfect(self):
        y = [0, 0, 1, 1]
        p = [0.1, 0.2, 0.8, 0.9]
        self.assertAlmostEqual(auc_roc(y, p), 1.0)

    def test_brier(self):
        y = [1, 0]
        p = [1.0, 0.0]
        self.assertAlmostEqual(brier_score(y, p), 0.0)


if __name__ == '__main__':
    unittest.main()
