"""WLT 按片编码：重测可填 @code；不支持可靠性分析。"""
from __future__ import annotations

import unittest

from app.controllers.dispose_ctrl import (
    DISPOSE_ANALYZE,
    DISPOSE_RETEST,
    _encode_one_wafer_action,
    build_wlt_wafer_dispose_detail,
)


class WltEncodeTest(unittest.TestCase):
    def test_full_retest_codes_optional(self):
        ok, frag, disp = _encode_one_wafer_action(
            {'wafer': '#01', 'dispose': DISPOSE_RETEST, 'retest_mode': 'full'}
        )
        self.assertTrue(ok)
        self.assertEqual(disp, DISPOSE_RETEST)
        self.assertEqual(frag, '#01，重测，整片重测')

    def test_invalid_codes_rejected(self):
        ok, err, disp = _encode_one_wafer_action(
            {
                'wafer': '#01',
                'dispose': DISPOSE_RETEST,
                'retest_mode': 'full',
                'retest_codes': '361',
            }
        )
        self.assertFalse(ok)
        self.assertIsNone(disp)
        self.assertIn('重测 code 须为', err)

    def test_full_retest_with_codes(self):
        ok, frag, disp = _encode_one_wafer_action(
            {
                'wafer': '#03',
                'dispose': DISPOSE_RETEST,
                'retest_mode': 'full',
                'retest_codes': '@1@361',
            }
        )
        self.assertTrue(ok)
        self.assertEqual(disp, DISPOSE_RETEST)
        self.assertEqual(frag, '#03，重测，整片重测，@1@361')

    def test_analyze_rejected(self):
        ok, err, disp = _encode_one_wafer_action(
            {'wafer': '#01', 'dispose': DISPOSE_ANALYZE}
        )
        self.assertFalse(ok)
        self.assertIsNone(disp)
        self.assertIn('WLT 不支持可靠性分析', err)

    def test_detail_joins_full_codes(self):
        ok, detail, summarized = build_wlt_wafer_dispose_detail(
            [
                {'wafer': '#01', 'dispose': 1},
                {
                    'wafer': '#03',
                    'dispose': 3,
                    'retest_mode': 'full',
                    'retest_codes': '@1@361',
                },
            ],
            ['#01', '#03'],
        )
        self.assertTrue(ok)
        self.assertEqual(summarized, DISPOSE_RETEST)
        self.assertEqual(detail, '#01，放行;#03，重测，整片重测，@1@361')


if __name__ == '__main__':
    unittest.main()
