"""合批 exe 只调后端的上报接口。NCMR 登录和抛送留在 Web exe。"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import date, datetime

from app.config import Config, is_test_env
from app.utils.ncmr_import import ncmr_row_from_hold_record

_REPORT_PATH = '/admin/hold/api/hold_info_export/ncmr'
_TOKEN_HEADER = 'X-Hold-Token'
_TIMEOUT = 90


def backend_base_url():
    """与本进程的 HOLD_ENV 同一套后端。HOLD_API_BASE 可改地址。"""
    explicit = str(
        getattr(Config, 'HOLD_API_BASE', '') or ''
    ).strip().rstrip('/')
    if explicit:
        return explicit
    port = 60001 if is_test_env() else 50001
    return f'http://127.0.0.1:{port}'


def _json_cell(value):
    if isinstance(value, datetime):
        return value.strftime('%Y-%m-%d %H:%M:%S')
    if isinstance(value, date):
        return value.isoformat()
    return value


def report_record_via_backend(record_id, record):
    """组装一行，POST 后端上报接口。不调用 NCMR 登录。"""
    token = str(getattr(Config, 'HOLD_API_TOKEN', '') or '').strip()
    if not token:
        return False, '未配置后端接口令牌，无法上报'
    try:
        rid = int(record_id)
    except (TypeError, ValueError):
        return False, '记录无效'
    if rid <= 0:
        return False, '记录无效'
    try:
        row = [_json_cell(cell) for cell in ncmr_row_from_hold_record(record)]
    except ValueError as exc:
        return False, str(exc)
    return post_backend_ncmr([row], [rid])


def post_backend_ncmr(rows, record_ids):
    """POST /admin/hold/api/hold_info_export/ncmr。"""
    token = str(getattr(Config, 'HOLD_API_TOKEN', '') or '').strip()
    if not token:
        return False, '未配置后端接口令牌，无法上报'
    url = backend_base_url() + _REPORT_PATH
    body = json.dumps(
        {'rows': rows, 'record_ids': record_ids},
        ensure_ascii=False,
    ).encode('utf-8')
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            'Content-Type': 'application/json; charset=utf-8',
            _TOKEN_HEADER: token,
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            raw = response.read().decode('utf-8', errors='replace')
            status = response.getcode()
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode('utf-8', errors='replace')
        payload = _loads(raw)
        message = _message(payload, raw) or f'HTTP {exc.code}'
        return False, message
    except urllib.error.URLError as exc:
        return False, f'后端连接失败: {exc.reason}'

    payload = _loads(raw)
    if not isinstance(payload, dict) or payload.get('code') != 200 or (
        status and int(status) >= 400
    ):
        return False, _message(payload, raw) or '后端上报失败'
    return True, _message(payload, raw) or '已上报'


def _loads(raw):
    text = (raw or '').strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _message(payload, raw):
    if isinstance(payload, dict):
        value = payload.get('msg')
        if value not in (None, ''):
            return str(value)
    text = str(raw or '').replace('\n', ' ').strip()
    if len(text) > 800:
        return text[:800] + '...'
    return text
