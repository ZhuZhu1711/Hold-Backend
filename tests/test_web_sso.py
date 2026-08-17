"""短时一次性 Web SSO 票据。"""
from __future__ import annotations

import unittest

from app.utils.web_sso import (
    TicketError,
    consume_ticket,
    issue_ticket,
    reset_used_tickets,
)

SECRET = 'test-web-sso-secret'
USER = {
    'user_id': 42,
    'user_name': '张三',
    'employee_no': 'A0001',
    'role': 1,
}


class WebSsoTicketTest(unittest.TestCase):
    def setUp(self):
        reset_used_tickets()

    def test_roundtrip(self):
        ticket = issue_ticket(SECRET, USER, max_age=60)
        payload = consume_ticket(SECRET, ticket, max_age=60)
        self.assertEqual(payload['user_id'], 42)
        self.assertEqual(payload['user_name'], '张三')
        self.assertEqual(payload['employee_no'], 'A0001')
        self.assertEqual(payload['role'], 1)

    def test_single_use(self):
        ticket = issue_ticket(SECRET, USER, max_age=60)
        consume_ticket(SECRET, ticket, max_age=60)
        with self.assertRaises(TicketError) as ctx:
            consume_ticket(SECRET, ticket, max_age=60)
        self.assertIn('已使用', str(ctx.exception))

    def test_wrong_secret(self):
        ticket = issue_ticket(SECRET, USER, max_age=60)
        with self.assertRaises(TicketError):
            consume_ticket('other-secret', ticket, max_age=60)

    def test_expired(self):
        ticket = issue_ticket(SECRET, USER, max_age=60)
        with self.assertRaises(TicketError) as ctx:
            consume_ticket(SECRET, ticket, max_age=-1)
        self.assertIn('过期', str(ctx.exception))

    def test_empty_ticket(self):
        with self.assertRaises(TicketError):
            consume_ticket(SECRET, '', max_age=60)
        with self.assertRaises(TicketError):
            consume_ticket(SECRET, None, max_age=60)


if __name__ == '__main__':
    unittest.main()
