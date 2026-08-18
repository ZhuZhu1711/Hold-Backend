"""客户端崩溃上报：校验、截断、限流、入库。"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Any

from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app import db
from app.models.client_error import ClientError

logger = logging.getLogger(__name__)

ALLOWED_EVENT_TYPES = frozenset({'UNCAUGHT', 'THREAD_UNCAUGHT', 'QT_FATAL'})
MAX_STACK_CHARS = 64 * 1024
MAX_MESSAGE_CHARS = 1024
MAX_PER_HOUR = 20
RATE_WINDOW_SECONDS = 3600

_rate_lock = threading.Lock()
_rate_hits: dict[str, list[float]] = {}


def reset_rate_limiter() -> None:
    with _rate_lock:
        _rate_hits.clear()


def _clip(value: Any, max_chars: int) -> str:
    text = '' if value is None else str(value)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + '…'


def _parse_occurred_at(raw: Any) -> datetime | None:
    if raw is None or raw == '':
        return None
    if isinstance(raw, datetime):
        return raw
    text = str(raw).strip()
    for fmt in ('%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S'):
        try:
            return datetime.strptime(text[:26], fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace('Z', '+00:00')).replace(tzinfo=None)
    except ValueError:
        return None


def _as_int(value: Any) -> int | None:
    if value is None or value == '':
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def under_rate_limit(key: str, now_ts: float | None = None) -> bool:
    """同一 hostname（或 IP）一小时内最多 MAX_PER_HOUR 条。返回 True 表示允许写入。"""
    now = datetime.now().timestamp() if now_ts is None else now_ts
    key = (key or '').strip() or 'unknown'
    cutoff = now - RATE_WINDOW_SECONDS
    with _rate_lock:
        hits = [ts for ts in _rate_hits.get(key, []) if ts >= cutoff]
        if len(hits) >= MAX_PER_HOUR:
            _rate_hits[key] = hits
            return False
        hits.append(now)
        _rate_hits[key] = hits
        return True


def normalize_payload(
    body: dict[str, Any],
    *,
    session_user_id: Any = None,
    session_employee_no: Any = None,
    client_ip: str = '',
) -> tuple[bool, str, dict[str, Any] | None]:
    if not isinstance(body, dict):
        return False, '请求体无效', None

    report_id = str(body.get('report_id') or '').strip()
    if not report_id:
        return False, '缺少 report_id', None
    if len(report_id) > 36:
        return False, 'report_id 无效', None

    message = _clip(body.get('message'), MAX_MESSAGE_CHARS)
    stack_trace = _clip(body.get('stack_trace'), MAX_STACK_CHARS)
    if not message and not stack_trace:
        return False, '缺少 message 或 stack_trace', None

    event_type = str(body.get('event_type') or 'UNCAUGHT').strip().upper()
    if event_type not in ALLOWED_EVENT_TYPES:
        return False, 'event_type 无效', None

    user_id = _as_int(session_user_id)
    if user_id is None:
        user_id = _as_int(body.get('user_id'))

    employee_no = str(session_employee_no or '').strip()
    if not employee_no:
        employee_no = str(body.get('employee_no') or '').strip()[:20]

    frozen_raw = body.get('frozen')
    frozen = 1 if frozen_raw in (True, 1, '1', 'true', 'True') else 0

    row = {
        'report_id': report_id,
        'occurred_at': _parse_occurred_at(body.get('occurred_at')),
        'event_type': event_type,
        'exception_type': _clip(body.get('exception_type'), 256),
        'message': message,
        'stack_trace': stack_trace,
        'hostname': _clip(body.get('hostname'), 128),
        'os_user': _clip(body.get('os_user'), 64),
        'employee_no': employee_no,
        'user_id': user_id,
        'app_mode': _clip(body.get('app_mode'), 16),
        'frozen': frozen,
        'client_ip': _clip(client_ip, 64),
    }
    return True, 'ok', row


def save_client_error(
    body: dict[str, Any],
    *,
    session_user_id: Any = None,
    session_employee_no: Any = None,
    client_ip: str = '',
) -> tuple[bool, int, str, dict[str, Any] | None]:
    """返回 (ok, http_status, msg, data)。"""
    ok, msg, row = normalize_payload(
        body,
        session_user_id=session_user_id,
        session_employee_no=session_employee_no,
        client_ip=client_ip,
    )
    if not ok or row is None:
        return False, 400, msg, None

    rate_key = row['hostname'] or client_ip or 'unknown'
    if not under_rate_limit(rate_key):
        logger.warning('客户端错误上报限流: %s', rate_key)
        return False, 429, '上报过于频繁', None

    existing = ClientError.query.filter_by(REPORT_ID=row['report_id']).first()
    if existing is not None:
        return True, 200, 'already exists', existing.to_dict()

    record = ClientError(
        REPORT_ID=row['report_id'],
        OCCURRED_AT=row['occurred_at'] or datetime.now(),
        RECEIVED_AT=datetime.now(),
        EVENT_TYPE=row['event_type'],
        EXCEPTION_TYPE=row['exception_type'] or None,
        MESSAGE=row['message'] or None,
        STACK_TRACE=row['stack_trace'] or None,
        HOSTNAME=row['hostname'] or None,
        OS_USER=row['os_user'] or None,
        EMPLOYEE_NO=row['employee_no'] or None,
        USER_ID=row['user_id'],
        APP_MODE=row['app_mode'] or None,
        FROZEN=row['frozen'],
        CLIENT_IP=row['client_ip'] or None,
    )
    try:
        db.session.add(record)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        dup = ClientError.query.filter_by(REPORT_ID=row['report_id']).first()
        if dup is not None:
            return True, 200, 'already exists', dup.to_dict()
        logger.exception('客户端错误上报唯一约束冲突')
        return False, 500, '保存失败', None
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception('客户端错误上报入库失败')
        return False, 500, '保存失败', None

    return True, 200, 'success', record.to_dict()
