"""客户端打开 Web 后台用的短时一次性登录票据。"""
from __future__ import annotations

import threading
import time
import uuid

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

SSO_SALT = 'hold-web-sso-v1'

_used_lock = threading.Lock()
_used_jti: dict[str, float] = {}


class TicketError(Exception):
    """票据无效、过期或已使用。"""


def reset_used_tickets() -> None:
    """测试用：清空已消费 jti。"""
    with _used_lock:
        _used_jti.clear()


def _serializer(secret_key: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret_key, salt=SSO_SALT)


def _purge_used(now: float) -> None:
    expired = [jti for jti, exp in _used_jti.items() if exp <= now]
    for jti in expired:
        del _used_jti[jti]


def issue_ticket(secret_key: str, user: dict, max_age: int) -> str:
    payload = {
        'uid': int(user['user_id']),
        'name': user.get('user_name') or '',
        'emp': user.get('employee_no') or '',
        'role': user.get('role'),
        'jti': uuid.uuid4().hex,
        'ttl': int(max_age),
    }
    return _serializer(secret_key).dumps(payload)


def consume_ticket(secret_key: str, ticket: str | None, max_age: int) -> dict:
    raw = (ticket or '').strip()
    if not raw:
        raise TicketError('缺少登录票据')
    try:
        payload = _serializer(secret_key).loads(raw, max_age=int(max_age))
    except SignatureExpired as exc:
        raise TicketError('登录链接已过期，请从客户端重新打开') from exc
    except BadSignature as exc:
        raise TicketError('登录链接无效') from exc

    if not isinstance(payload, dict) or payload.get('uid') is None:
        raise TicketError('登录链接无效')

    jti = str(payload.get('jti') or '')
    if not jti:
        raise TicketError('登录链接无效')

    ttl = int(payload.get('ttl') or max_age)
    now = time.time()
    with _used_lock:
        _purge_used(now)
        if jti in _used_jti:
            raise TicketError('登录链接已使用，请从客户端重新打开')
        _used_jti[jti] = now + max(ttl, int(max_age)) + 5

    return {
        'user_id': int(payload['uid']),
        'user_name': str(payload.get('name') or ''),
        'employee_no': str(payload.get('emp') or ''),
        'role': payload.get('role'),
    }
