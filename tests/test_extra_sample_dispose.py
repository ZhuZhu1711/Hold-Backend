"""加抽（dispose=4）门禁与详情编码。不连 Oracle。"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from app.controllers.dispose_ctrl import (
    DISPOSE_ANALYZE,
    DISPOSE_DOWNGRADE,
    DISPOSE_EXTRA_SAMPLE,
    DISPOSE_RELEASE,
    DISPOSE_RETEST,
    allowed_disposes_for_record,
    build_dispose_detail,
    dispose_record,
)


class ExtraSampleDetailTest(unittest.TestCase):
    def test_empty_samples_leave_detail_blank(self):
        ok, detail = build_dispose_detail(DISPOSE_EXTRA_SAMPLE, extra_samples=[])
        self.assertTrue(ok)
        self.assertIsNone(detail)

    def test_encodes_grade_and_qty(self):
        ok, detail = build_dispose_detail(
            DISPOSE_EXTRA_SAMPLE,
            extra_samples=[{'grade': 'F', 'qty': '10'}, {'grade': 'HA', 'qty': 5}],
        )
        self.assertTrue(ok)
        self.assertEqual(detail, 'AS:F:10;HA:5')

    def test_skips_blank_rows_and_rejects_half_filled(self):
        ok, detail = build_dispose_detail(
            DISPOSE_EXTRA_SAMPLE,
            extra_samples=[{'grade': '', 'qty': ''}, {'grade': 'F', 'qty': '2'}],
        )
        self.assertTrue(ok)
        self.assertEqual(detail, 'AS:F:2')
        ok, err = build_dispose_detail(
            DISPOSE_EXTRA_SAMPLE,
            extra_samples=[{'grade': 'F', 'qty': ''}],
        )
        self.assertFalse(ok)
        self.assertIn('等级和数量', err)

    def test_rejects_bad_qty_and_duplicate_grade(self):
        ok, err = build_dispose_detail(
            DISPOSE_EXTRA_SAMPLE,
            extra_samples=[{'grade': 'F', 'qty': '0'}],
        )
        self.assertFalse(ok)
        self.assertIn('正整数', err)
        ok, err = build_dispose_detail(
            DISPOSE_EXTRA_SAMPLE,
            extra_samples=[{'grade': 'F', 'qty': '1'}, {'grade': 'f', 'qty': '2'}],
        )
        self.assertFalse(ok)
        self.assertIn('一次', err)


class ExtraSampleGateTest(unittest.TestCase):
    def test_only_aql_hold_includes_extra_sample(self):
        aql = allowed_disposes_for_record('AQL_HOLD', 0, False)
        self.assertIn(DISPOSE_EXTRA_SAMPLE, aql)
        self.assertIn(DISPOSE_RELEASE, aql)
        self.assertIn(DISPOSE_RETEST, aql)
        self.assertIn(DISPOSE_ANALYZE, aql)

        normal = allowed_disposes_for_record('023', 0, False)
        self.assertNotIn(DISPOSE_EXTRA_SAMPLE, normal)
        self.assertIn(DISPOSE_DOWNGRADE, normal)

    def test_hidden_after_analyze_and_on_wlt(self):
        after = allowed_disposes_for_record('AQL_HOLD', 0, True)
        self.assertEqual(after, [DISPOSE_RELEASE, DISPOSE_DOWNGRADE])
        wlt = allowed_disposes_for_record('AQL_HOLD', 2, False)
        self.assertNotIn(DISPOSE_EXTRA_SAMPLE, wlt)
        self.assertNotIn(DISPOSE_ANALYZE, wlt)

    @patch('app.controllers.dispose_ctrl.attach_reliability_followup', side_effect=lambda rec: rec)
    @patch('app.controllers.dispose_ctrl._load_record')
    def test_submit_rejected_when_hold_code_is_not_aql(self, load_record, _attach):
        load_record.return_value = {
            'ID': 1,
            'STATUS': 0,
            'HOLD_CODE': '023',
            'RECORD_TYPE': 0,
            'PRODUCT_ID': 'P-3.5',
            'LAST_CIRCULATION_ID': 9,
        }
        ok, msg, data = dispose_record(
            hold_record_id=1,
            dispose=DISPOSE_EXTRA_SAMPLE,
            actor_user_id=8,
            actor_role=1,
        )
        self.assertFalse(ok)
        self.assertIsNone(data)
        self.assertIn('AQL_HOLD', msg)


if __name__ == '__main__':
    unittest.main()
