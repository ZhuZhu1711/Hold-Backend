"""跨服务器单实例。

两台机器连的是同一套库。这里占住库里的一把会话锁，另一台就进不来。
进程退出、连接断开，锁自动释放。测试库和正式库是两套连接，互不影响。
FT 与 CP 各一把锁，可以同时跑。
"""
from __future__ import annotations

import atexit
import os
import threading
import time

LOCK_FT_HOLD_MERGE = 'FT_HOLD_MERGE'
LOCK_CP_HOLD_MERGE = 'CP_HOLD_MERGE'

# DBMS_LOCK 用户锁号。只在当前连接的那套库里有效。
_LOCK_IDS = {
    LOCK_FT_HOLD_MERGE: 88001001,
    LOCK_CP_HOLD_MERGE: 88001002,
}

# 0 成功，4 本会话已持有。1 表示别的会话占着。
_HELD_OK = frozenset({0, 4})
_BUSY = 1
_TOUCH_SECONDS = 60

_GUARDS = []


def _connect():
    import oracledb

    from app.config import Config

    return oracledb.connect(user=Config.DB_USER, password=Config.DB_PASSWORD, dsn=Config.DB_DSN)


def _request(conn, lock_id: int) -> int:
    # release_on_commit 必须为 FALSE，否则本会话一旦提交，锁就没了。
    with conn.cursor() as cursor:
        status = cursor.var(int)
        cursor.execute(
            'BEGIN :status := DBMS_LOCK.REQUEST(:id, 6, 0, FALSE); END;',
            status=status,
            id=int(lock_id),
        )
        value = status.getvalue()
    if value is None:
        raise RuntimeError('DBMS_LOCK.REQUEST 没有返回状态')
    return int(value)


def _exit(logger, message: str) -> None:
    logger.error(message)
    for handler in list(getattr(logger, 'handlers', []) or []):
        try:
            handler.flush()
        except Exception:
            pass
    raise SystemExit(1)


class _Guard:
    def __init__(self, conn, lock_id: int, name: str, logger):
        self.conn = conn
        self.lock_id = lock_id
        self.name = name
        self.logger = logger
        self.released = False
        self._stop = threading.Event()
        atexit.register(self.release)

    def release(self) -> None:
        if self.released:
            return
        self.released = True
        self._stop.set()
        try:
            with self.conn.cursor() as cursor:
                status = cursor.var(int)
                cursor.execute(
                    'BEGIN :status := DBMS_LOCK.RELEASE(:id); END;',
                    status=status,
                    id=self.lock_id,
                )
        except Exception as exc:
            self.logger.warning(f'释放单实例锁 {self.name} 失败，会话断开后会自动放开: {exc}')
        try:
            self.conn.close()
        except Exception:
            pass

    def keep_alive(self) -> None:
        """空闲会话被库杀掉时锁会丢掉，这里定期摸一下这条专用连接。"""
        while not self._stop.wait(_TOUCH_SECONDS):
            try:
                with self.conn.cursor() as cursor:
                    cursor.execute('SELECT 1 FROM DUAL')
                    cursor.fetchone()
            except Exception as exc:
                self.logger.error(f'{self.name} 单实例会话断开，本进程退出: {exc}')
                for handler in list(self.logger.handlers):
                    try:
                        handler.flush()
                    except Exception:
                        pass
                os._exit(1)


def hold_singleton(lock_name: str, logger, *, connect=None, keep_alive: bool = True) -> None:
    """占住当前库上的这把锁。已被占用或拿不到锁时退出，不开始合批。"""
    name = (lock_name or '').strip().upper()
    lock_id = _LOCK_IDS.get(name)
    if lock_id is None:
        raise ValueError(f'未知单实例锁: {lock_name!r}')
    connect_fn = connect or _connect
    try:
        conn = connect_fn()
    except Exception as exc:
        _exit(logger, f'{name} 连接数据库失败，拒绝启动: {exc}')
    try:
        status = _request(conn, lock_id)
    except Exception as exc:
        try:
            conn.close()
        except Exception:
            pass
        _exit(
            logger,
            f'{name} 无法取得单实例锁。'
            f'若提示权限不足，请在当前环境的库执行 GRANT EXECUTE ON SYS.DBMS_LOCK TO 当前账号。'
            f'原因: {exc}',
        )
    if status == _BUSY:
        try:
            conn.close()
        except Exception:
            pass
        _exit(logger, f'{name} 已在其他进程运行，本进程退出')
    if status not in _HELD_OK:
        try:
            conn.close()
        except Exception:
            pass
        _exit(logger, f'{name} 取得单实例锁失败，状态码 {status}，本进程退出')
    guard = _Guard(conn, lock_id, name, logger)
    _GUARDS.append(guard)
    if keep_alive:
        threading.Thread(
            target=guard.keep_alive,
            name=f'singleton-{name}',
            daemon=True,
        ).start()
    logger.info(f'已取得单实例锁 {name}。进程退出后锁自动释放')
