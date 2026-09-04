"""进程内滑动窗口限流（单进程多线程）。"""
from __future__ import annotations

import math
import threading
import time


class SlidingWindowLimiter:
    def __init__(self, max_hits: int, window_seconds: float):
        if max_hits < 1:
            raise ValueError('max_hits must be >= 1')
        if window_seconds <= 0:
            raise ValueError('window_seconds must be > 0')
        self.max_hits = max_hits
        self.window_seconds = float(window_seconds)
        self._lock = threading.Lock()
        self._hits: dict[str, list[float]] = {}

    def clear(self) -> None:
        with self._lock:
            self._hits.clear()

    def reset(self, key: str) -> None:
        key = (key or '').strip() or 'unknown'
        with self._lock:
            self._hits.pop(key, None)

    def would_allow(self, key: str, now_ts: float | None = None) -> bool:
        now = time.time() if now_ts is None else now_ts
        key = (key or '').strip() or 'unknown'
        with self._lock:
            hits = self._pruned(key, now)
            return len(hits) < self.max_hits

    def hit(self, key: str, now_ts: float | None = None) -> bool:
        """记录一次访问。已达上限则不写入并返回 False。"""
        now = time.time() if now_ts is None else now_ts
        key = (key or '').strip() or 'unknown'
        with self._lock:
            hits = self._pruned(key, now)
            if len(hits) >= self.max_hits:
                return False
            hits.append(now)
            self._hits[key] = hits
            return True

    def retry_after(self, key: str, now_ts: float | None = None) -> int:
        now = time.time() if now_ts is None else now_ts
        key = (key or '').strip() or 'unknown'
        with self._lock:
            hits = self._pruned(key, now)
            if len(hits) < self.max_hits:
                return 0
            oldest = hits[0]
            wait = oldest + self.window_seconds - now
            return max(1, int(math.ceil(wait)))

    def _pruned(self, key: str, now: float) -> list[float]:
        cutoff = now - self.window_seconds
        hits = [ts for ts in self._hits.get(key, []) if ts >= cutoff]
        if hits:
            self._hits[key] = hits
        else:
            self._hits.pop(key, None)
        return hits


LOGIN_IP_MAX = 10
LOGIN_IP_WINDOW = 60
LOGIN_EMP_FAIL_MAX = 5
LOGIN_EMP_FAIL_WINDOW = 15 * 60
LOGIN_TOO_FREQUENT_MSG = '请求过于频繁，请稍后再试'

login_ip_limiter = SlidingWindowLimiter(LOGIN_IP_MAX, LOGIN_IP_WINDOW)
login_emp_fail_limiter = SlidingWindowLimiter(LOGIN_EMP_FAIL_MAX, LOGIN_EMP_FAIL_WINDOW)


def reset_login_limiters() -> None:
    login_ip_limiter.clear()
    login_emp_fail_limiter.clear()


def client_ip_from_request(req) -> str:
    forwarded = ''
    try:
        forwarded = (req.headers.get('X-Forwarded-For') or '').strip()
    except Exception:
        forwarded = ''
    if forwarded:
        return forwarded.split(',')[0].strip() or 'unknown'
    remote = ''
    try:
        remote = (req.remote_addr or '').strip()
    except Exception:
        remote = ''
    return remote or 'unknown'


def _emp_key(employee_no: str | None) -> str:
    return (employee_no or '').strip().lower() or 'unknown'


def check_login_rate(ip: str, employee_no: str | None, now_ts: float | None = None) -> tuple[bool, int]:
    """
    登录前检查。True 表示应返回 429。
    IP 窗口会计入本次请求；工号失败窗口只读、不记成功/失败。
    """
    ip_key = f'ip:{(ip or "").strip() or "unknown"}'
    emp_key = f'emp:{_emp_key(employee_no)}'
    if not login_ip_limiter.hit(ip_key, now_ts=now_ts):
        return True, login_ip_limiter.retry_after(ip_key, now_ts=now_ts)
    if not login_emp_fail_limiter.would_allow(emp_key, now_ts=now_ts):
        return True, login_emp_fail_limiter.retry_after(emp_key, now_ts=now_ts)
    return False, 0


def record_login_failure(employee_no: str | None, now_ts: float | None = None) -> None:
    login_emp_fail_limiter.hit(f'emp:{_emp_key(employee_no)}', now_ts=now_ts)


def clear_login_failures(employee_no: str | None) -> None:
    login_emp_fail_limiter.reset(f'emp:{_emp_key(employee_no)}')
