"""HOLD_WAFER_ATTR 比特位判定。"""

import unittest

from app.utils.database_util import (
    HOLD_WAFER_ATTR_ATE,
    HOLD_WAFER_ATTR_FVI,
    HOLD_WAFER_ATTR_IQC_ATE,
    HOLD_WAFER_ATTR_VACUUM,
    HOLD_WAFER_ATTR_ZIYI,
    compute_hold_wafer_attr,
)


class ComputeHoldWaferAttrTest(unittest.TestCase):
    def test_vacuum_vsh_tsh(self):
        self.assertEqual(
            compute_hold_wafer_attr('VSH123', '100', 'FIQC_MERGE'),
            HOLD_WAFER_ATTR_VACUUM,
        )
        self.assertEqual(
            compute_hold_wafer_attr('tsh999', '100', 'FIQC'),
            HOLD_WAFER_ATTR_VACUUM,
        )

    def test_vacuum_excluded_on_wlt_station(self):
        self.assertEqual(compute_hold_wafer_attr('VSH123', '100', 'WLT2'), 0)
        self.assertEqual(compute_hold_wafer_attr('VSH123', '100', 'WOQC'), 0)

    def test_ziyi_and_equip_200(self):
        self.assertEqual(
            compute_hold_wafer_attr('C123456-033', '201', 'FIQC'),
            HOLD_WAFER_ATTR_ZIYI,
        )
        self.assertEqual(
            compute_hold_wafer_attr('C123456-033', '200', 'FIQC'),
            HOLD_WAFER_ATTR_ZIYI,
        )

    def test_iqc_ate(self):
        self.assertEqual(
            compute_hold_wafer_attr('C123456-033', '199', 'FIQC'),
            HOLD_WAFER_ATTR_IQC_ATE,
        )
        self.assertEqual(
            compute_hold_wafer_attr('C123456-033', '1', 'FIQC'),
            HOLD_WAFER_ATTR_IQC_ATE,
        )

    def test_fragment_suffix_not_long_enough(self):
        self.assertEqual(compute_hold_wafer_attr('C123456-03', '250', 'FIQC'), 0)

    def test_non_numeric_equip_skips_merge_bits(self):
        self.assertEqual(compute_hold_wafer_attr('C123456-033', 'ATE201', 'FIQC'), 0)
        self.assertEqual(compute_hold_wafer_attr('C123456-033', '', 'FIQC'), 0)

    def test_ate_and_fvi(self):
        self.assertEqual(
            compute_hold_wafer_attr('A12345', '100', 'FIQC'),
            HOLD_WAFER_ATTR_ATE,
        )
        self.assertEqual(
            compute_hold_wafer_attr('I98765', '100', 'FFVI'),
            HOLD_WAFER_ATTR_FVI,
        )

    def test_empty_lot(self):
        self.assertEqual(compute_hold_wafer_attr('', '200', 'FIQC'), 0)
        self.assertEqual(compute_hold_wafer_attr(None, '200', 'FIQC'), 0)


if __name__ == '__main__':
    unittest.main()
