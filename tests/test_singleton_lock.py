"""合批单实例：模拟 DBMS_LOCK，不连库。"""
from __future__ import annotations

import logging
import unittest
from unittest.mock import MagicMock

from app.backend_schedule import singleton_lock
from app.backend_schedule.singleton_lock import (
    LOCK_CP_HOLD_MERGE,
    LOCK_FT_HOLD_MERGE,
    hold_singleton,
)


class _Cursor:
    def __init__(self, status):
        self.status = status
        self.sql = ''

    def var(self, typ):
        del typ
        holder = MagicMock()
        holder.getvalue.return_value = self.status
        return holder

    def execute(self, sql, **kwargs):
        self.sql = sql
        self.kwargs = kwargs

    def fetchone(self):
        return (1,)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _Conn:
    def __init__(self, status):
        self.cursor_obj = _Cursor(status)
        self.closed = False

    def cursor(self):
        return self.cursor_obj

    def close(self):
        self.closed = True


class SingletonLockTest(unittest.TestCase):
    def setUp(self):
        self.logger = logging.getLogger('test_singleton_lock')
        self.logger.handlers.clear()
        self.logger.addHandler(logging.NullHandler())

    def test_first_process_keeps_the_session_lock(self):
        conn = _Conn(0)
        hold_singleton(
            LOCK_FT_HOLD_MERGE,
            self.logger,
            connect=lambda: conn,
            keep_alive=False,
        )
        self.assertIn('DBMS_LOCK.REQUEST', conn.cursor_obj.sql)
        self.assertIn('FALSE', conn.cursor_obj.sql)
        self.assertEqual(conn.cursor_obj.kwargs['id'], 88001001)
        self.assertFalse(conn.closed)
        singleton_lock._GUARDS[-1].release()
        self.assertTrue(conn.closed)

    def test_second_process_exits(self):
        conn = _Conn(1)
        with self.assertRaises(SystemExit) as caught:
            hold_singleton(
                LOCK_CP_HOLD_MERGE,
                self.logger,
                connect=lambda: conn,
                keep_alive=False,
            )
        self.assertEqual(caught.exception.code, 1)
        self.assertTrue(conn.closed)
        self.assertEqual(conn.cursor_obj.kwargs['id'], 88001002)

    def test_missing_privilege_refuses_start(self):
        def connect():
            raise RuntimeError('ORA-01031: insufficient privileges')

        with self.assertRaises(SystemExit) as caught:
            hold_singleton(LOCK_FT_HOLD_MERGE, self.logger, connect=connect, keep_alive=False)
        self.assertEqual(caught.exception.code, 1)

    def test_ft_and_cp_use_different_lock_ids(self):
        self.assertNotEqual(
            singleton_lock._LOCK_IDS[LOCK_FT_HOLD_MERGE],
            singleton_lock._LOCK_IDS[LOCK_CP_HOLD_MERGE],
        )


if __name__ == '__main__':
    unittest.main()
