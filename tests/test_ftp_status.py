"""FTP 探活 payload（不连真实 FTP）。"""
from __future__ import annotations

import unittest

from app.utils.FtpPool import FTP_DOWN_IMPACT, build_ftp_status_payload


class FtpStatusPayloadTest(unittest.TestCase):
    def test_available(self):
        data = build_ftp_status_payload('172.18.200.250', True, 12)
        self.assertTrue(data['available'])
        self.assertEqual(data['host'], '172.18.200.250')
        self.assertEqual(data['latency_ms'], 12)
        self.assertIsNone(data['impact'])
        self.assertNotIn('password', data)

    def test_unavailable(self):
        data = build_ftp_status_payload('172.18.200.250', False, 800)
        self.assertFalse(data['available'])
        self.assertEqual(data['impact'], FTP_DOWN_IMPACT)
        self.assertIn('数据分析', data['impact'])


if __name__ == '__main__':
    unittest.main()
