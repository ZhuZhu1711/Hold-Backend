"""严重报错邮件（不连真实 SMTP）。"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.utils import mail_alert


class _FakeCfg:
    ALERT_MAIL_ENABLED = True
    ALERT_SMTP_HOST = 'smtp.example.com'
    ALERT_SMTP_PORT = 465
    ALERT_SMTP_SSL = True
    ALERT_SMTP_USER = 'alert@example.com'
    ALERT_SMTP_PASSWORD = 'secret'
    ALERT_MAIL_FROM = 'alert@example.com'
    ALERT_MAIL_TO = ['ops@example.com']
    ALERT_MAIL_COOLDOWN_SECONDS = 1800


class MailAlertTest(unittest.TestCase):
    def setUp(self):
        mail_alert._last_sent.clear()

    def test_skip_when_not_configured(self):
        cfg = _FakeCfg()
        cfg.ALERT_SMTP_HOST = ''
        self.assertFalse(mail_alert.mail_configured(cfg))
        self.assertFalse(mail_alert.send_alert_mail('x', 'y', cfg=cfg))

    @patch('app.utils.mail_alert.smtplib.SMTP_SSL')
    def test_password_login_and_send(self, smtp_ssl_cls):
        smtp = MagicMock()
        smtp_ssl_cls.return_value = smtp
        cfg = _FakeCfg()
        ok = mail_alert.send_alert_mail('subj', 'body', cfg=cfg)
        self.assertTrue(ok)
        smtp.login.assert_called_once_with('alert@example.com', 'secret')
        smtp.sendmail.assert_called_once()
        smtp.quit.assert_called_once()

    @patch('app.utils.mail_alert.smtplib.SMTP_SSL')
    def test_cooldown_skips_duplicate(self, smtp_ssl_cls):
        smtp = MagicMock()
        smtp_ssl_cls.return_value = smtp
        cfg = _FakeCfg()
        cfg.ALERT_MAIL_COOLDOWN_SECONDS = 600
        first = mail_alert.notify_severe_error('db down', 'ora-xxx', cfg=cfg)
        second = mail_alert.notify_severe_error('db down', 'ora-xxx', cfg=cfg)
        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(smtp.login.call_count, 1)

    @patch('app.utils.mail_alert.smtplib.SMTP_SSL')
    def test_notify_includes_traceback(self, smtp_ssl_cls):
        smtp = MagicMock()
        smtp_ssl_cls.return_value = smtp
        try:
            raise RuntimeError('boom')
        except RuntimeError as exc:
            mail_alert.notify_severe_error('crash', 'detail', exc=exc, cfg=_FakeCfg())
        raw = smtp.sendmail.call_args[0][2]
        from email import message_from_string
        payload = message_from_string(raw).get_payload(decode=True).decode('utf-8')
        self.assertIn('RuntimeError', payload)
        self.assertIn('boom', payload)


if __name__ == '__main__':
    unittest.main()
