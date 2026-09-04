"""附件 FTP 路径收口（不连 FTP / 库）。"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from app.controllers.manual_hold_ctrl import normalize_manual_hold
from app.utils.annex_util import (
    annex_relname_for_store,
    canonicalize_annex_path,
    download_annex_bytes,
    ft_manual_stations,
    sanitize_client_annex_paths,
)


class CanonicalizeAnnexPathTest(unittest.TestCase):
    def test_relative_name_joins_ft_root(self):
        path = canonicalize_annex_path('188_1.jpg', line='FT')
        self.assertEqual(path, '/JDY_UPLOAD/FT_MANUAL/188_1.jpg')

    def test_legal_absolute_ft(self):
        path = canonicalize_annex_path('/JDY_UPLOAD/FT_MANUAL/a.jpg', line='FT')
        self.assertEqual(path, '/JDY_UPLOAD/FT_MANUAL/a.jpg')
        self.assertEqual(annex_relname_for_store(path), 'a.jpg')

    def test_legal_absolute_wlt(self):
        path = canonicalize_annex_path('/JDY_UPLOAD/WLT_MANUAL/b.png', line='WLT')
        self.assertEqual(path, '/JDY_UPLOAD/WLT_MANUAL/b.png')

    def test_dotdot_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            canonicalize_annex_path('/JDY_UPLOAD/FT_MANUAL/../../RAW_DATA/x.jpg')
        self.assertIn('允许目录', str(ctx.exception))

    def test_relative_dotdot_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            canonicalize_annex_path('../../RAW_DATA/x.jpg', line='FT')
        self.assertIn('允许目录', str(ctx.exception))

    def test_raw_data_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            canonicalize_annex_path('/RAW_DATA/secret.jpg', line='FT')
        self.assertIn('允许目录', str(ctx.exception))

    def test_no_extension_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            canonicalize_annex_path('foo', line='FT')
        self.assertIn('图片', str(ctx.exception))

    def test_csv_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            canonicalize_annex_path('/JDY_UPLOAD/FT_MANUAL/a.csv', line='FT')
        self.assertIn('图片', str(ctx.exception))

    def test_at_sign_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            canonicalize_annex_path('a@b.jpg', line='FT')
        self.assertIn('无效', str(ctx.exception))

    def test_root_dir_itself_rejected(self):
        with self.assertRaises(ValueError):
            canonicalize_annex_path('/JDY_UPLOAD/FT_MANUAL/', line='FT')


class SanitizeClientAnnexPathsTest(unittest.TestCase):
    def test_stores_relative_names(self):
        out = sanitize_client_annex_paths(
            ['/JDY_UPLOAD/FT_MANUAL/a.jpg', 'b.png'],
            line='FT',
        )
        self.assertEqual(out, ['a.jpg', 'b.png'])

    def test_wlt_path_rejected_on_ft_line(self):
        with self.assertRaises(ValueError):
            sanitize_client_annex_paths(
                ['/JDY_UPLOAD/WLT_MANUAL/a.jpg'],
                line='FT',
            )

    def test_raw_data_rejected(self):
        with self.assertRaises(ValueError):
            sanitize_client_annex_paths(['/RAW_DATA/x.jpg'], line='FT')


class DownloadAnnexBytesGuardTest(unittest.TestCase):
    @patch('app.utils.annex_util.annex_ftp_pool')
    def test_illegal_path_does_not_connect(self, pool):
        with self.assertRaises(ValueError) as ctx:
            download_annex_bytes('/RAW_DATA/secret.csv', line='FT')
        self.assertIn('图片', str(ctx.exception))
        pool.get_conn.assert_not_called()

    @patch('app.utils.annex_util.annex_ftp_pool')
    def test_dotdot_does_not_connect(self, pool):
        with self.assertRaises(ValueError):
            download_annex_bytes(
                '/JDY_UPLOAD/FT_MANUAL/../../RAW_DATA/x.jpg',
                line='FT',
            )
        pool.get_conn.assert_not_called()


class NormalizeManualHoldAnnexTest(unittest.TestCase):
    def _ft_base(self):
        stations = ft_manual_stations()
        self.assertTrue(stations)
        return {
            'line': 'FT',
            'product_id': 'XX-3.5',
            'station': stations[0],
            'equip_id': 'MANUAL',
            'lot_id': 'ABC01',
            'wafer_id': 'ABC01',
            'hold_code': 'AQL_HOLD',
            'hold_reason': 'AQL',
        }

    def test_rejects_raw_data_path(self):
        raw = self._ft_base()
        raw['annex_ftp_path'] = '@/RAW_DATA/x.jpg'
        ok, msg, rec = normalize_manual_hold(raw)
        self.assertFalse(ok)
        self.assertIn('允许目录', msg)
        self.assertIsNone(rec)

    def test_stores_relative_name(self):
        raw = self._ft_base()
        raw['annex_ftp_path'] = '@/JDY_UPLOAD/FT_MANUAL/a.jpg'
        ok, msg, rec = normalize_manual_hold(raw)
        self.assertTrue(ok, msg)
        self.assertEqual(rec['ANNEX_FTP_PATH'], '@a.jpg')


if __name__ == '__main__':
    unittest.main()
