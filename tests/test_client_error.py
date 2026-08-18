"""客户端崩溃上报：校验、截断、限流（不连真实库）。"""
from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

from app.controllers import client_error_ctrl
from app.controllers.client_error_ctrl import (
    MAX_PER_HOUR,
    MAX_STACK_CHARS,
    normalize_payload,
    reset_rate_limiter,
    under_rate_limit,
)


class NormalizePayloadTest(unittest.TestCase):
    def test_missing_report_id(self):
        ok, msg, row = normalize_payload({'message': 'boom'})
        self.assertFalse(ok)
        self.assertIn('report_id', msg)
        self.assertIsNone(row)

    def test_missing_message_and_stack(self):
        ok, msg, row = normalize_payload({'report_id': 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'})
        self.assertFalse(ok)
        self.assertIn('message', msg)
        self.assertIsNone(row)

    def test_invalid_event_type(self):
        ok, msg, _ = normalize_payload({
            'report_id': 'rid-1',
            'message': 'x',
            'event_type': 'LOGOUT',
        })
        self.assertFalse(ok)
        self.assertIn('event_type', msg)

    def test_session_overrides_body_user(self):
        ok, msg, row = normalize_payload(
            {
                'report_id': 'rid-2',
                'message': 'boom',
                'user_id': 9,
                'employee_no': 'FROM_CLIENT',
            },
            session_user_id=42,
            session_employee_no='A0001',
            client_ip='10.0.0.8',
        )
        self.assertTrue(ok, msg)
        self.assertEqual(row['user_id'], 42)
        self.assertEqual(row['employee_no'], 'A0001')
        self.assertEqual(row['client_ip'], '10.0.0.8')

    def test_truncate_stack(self):
        ok, msg, row = normalize_payload({
            'report_id': 'rid-3',
            'stack_trace': 'A' * (MAX_STACK_CHARS + 50),
        })
        self.assertTrue(ok, msg)
        self.assertEqual(len(row['stack_trace']), MAX_STACK_CHARS)
        self.assertTrue(row['stack_trace'].endswith('…'))

    def test_parse_occurred_at(self):
        ok, msg, row = normalize_payload({
            'report_id': 'rid-4',
            'message': 'x',
            'occurred_at': '2026-08-18T15:00:00',
            'frozen': True,
            'event_type': 'qt_fatal',
        })
        self.assertTrue(ok, msg)
        self.assertEqual(row['event_type'], 'QT_FATAL')
        self.assertEqual(row['frozen'], 1)
        self.assertEqual(row['occurred_at'], datetime(2026, 8, 18, 15, 0, 0))


class RateLimitTest(unittest.TestCase):
    def setUp(self):
        reset_rate_limiter()

    def tearDown(self):
        reset_rate_limiter()

    def test_allows_then_blocks(self):
        now = 1_000_000.0
        for i in range(MAX_PER_HOUR):
            self.assertTrue(under_rate_limit('host-a', now_ts=now + i))
        self.assertFalse(under_rate_limit('host-a', now_ts=now + MAX_PER_HOUR))
        self.assertTrue(under_rate_limit('host-b', now_ts=now))

    def test_window_expires(self):
        now = 1_000_000.0
        for i in range(MAX_PER_HOUR):
            under_rate_limit('host-c', now_ts=now + i)
        later = now + client_error_ctrl.RATE_WINDOW_SECONDS + 1
        self.assertTrue(under_rate_limit('host-c', now_ts=later))


class SaveClientErrorTest(unittest.TestCase):
    def setUp(self):
        reset_rate_limiter()

    def tearDown(self):
        reset_rate_limiter()

    def test_duplicate_report_id_returns_ok(self):
        existing = MagicMock()
        existing.to_dict.return_value = {'id': 1, 'report_id': 'dup-1', 'event_type': 'UNCAUGHT'}
        model = MagicMock()
        model.query.filter_by.return_value.first.return_value = existing
        with patch.object(client_error_ctrl, 'ClientError', model):
            ok, status, msg, data = client_error_ctrl.save_client_error({
                'report_id': 'dup-1',
                'message': 'boom',
                'hostname': 'unique-host-dup',
            })
        self.assertTrue(ok)
        self.assertEqual(status, 200)
        self.assertEqual(msg, 'already exists')
        self.assertEqual(data['report_id'], 'dup-1')

    def test_insert_commit(self):
        model = MagicMock()
        model.query.filter_by.return_value.first.return_value = None
        instance = MagicMock()
        instance.to_dict.return_value = {
            'id': 2,
            'report_id': 'new-1',
            'event_type': 'UNCAUGHT',
        }
        model.return_value = instance
        session = MagicMock()
        with patch.object(client_error_ctrl, 'ClientError', model), \
             patch.object(client_error_ctrl.db, 'session', session):
            ok, status, msg, data = client_error_ctrl.save_client_error({
                'report_id': 'new-1',
                'message': 'boom',
                'hostname': 'unique-host-insert',
                'event_type': 'UNCAUGHT',
            })
        self.assertTrue(ok)
        self.assertEqual(status, 200)
        self.assertEqual(msg, 'success')
        session.add.assert_called_once_with(instance)
        session.commit.assert_called_once()
        self.assertEqual(data['report_id'], 'new-1')


if __name__ == '__main__':
    unittest.main()
