"""NCMR 批量导入。合批 exe 和 Web exe 分开打包，这里不能导入 app.controllers。

合批进程直接调用 report_eng_future_to_ncmr。网页上报仍由 hold_info_export_ctrl 转发。
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from datetime import date, datetime

from app.config import Config
from app.utils.database_util import (
    logger,
    mark_hold_record_ncmr_reported,
    normalize_lot_id,
)
from app.utils.pre_handler_lookup import lookup_lot_oa, lookup_pre_handler

DEFAULTS = {
    'sub_customer': 'GC',
    'package_type': 'CIS',
    'factory': 'GC_FT',
    'area': 'FT',
    'stage': 'FT1',
}

NCMR_BATCH_TYPE = 'ENCAPSULATION'
NCMR_TIMEOUT = 60
NCMR_ROW_WIDTH = 27
POST_MAX_ROWS = 5000
_NCMR_OK_CODES = (0, 200, '0', '200')
LOT_HOLD_HEADERS = [
    'Yield',
    'OA单号',
    'Top1 Fail Bin',
    'Top 2 Fail Bin',
    'Top 3 Fail Bin',
    'Top 4 Fail Bin',
    'Top 5 Fail Bin',
    '其他备注',
]
_COL_SUB_CUSTOMER = 0
_COL_PRODUCT = 1
_COL_DEVICE = 2
_COL_PACKAGE = 3
_COL_FACTORY = 4
_COL_AREA = 5
_COL_LOT_TYPE = 6
_COL_LOT = 7
_COL_SUBLOT = 8
_COL_WAFER_LOT = 9
_COL_WO = 10
_COL_STAGE = 11
_COL_HOLD_REASON = 14
_COL_PRE_HANDLER = 15
_COL_PART = 16
_COL_TASK = 17
_COL_HOLD_TIME = 18
_COL_HOLD_TYPE = 19
_COL_TEST_PROGRAM = 20
_COL_PROBECARD = 21
_COL_PROBER = 22
_COL_TESTER = 23
_COL_START = 24
_COL_END = 25
_COL_OA = 26


def _is_engineering_route(route):
    """导出路线 ENGINEERING，原始 ROUTE_ID 含 ENG。"""
    return 'ENG' in (route or '').strip().upper()


def _is_mp_route(route):
    """导出路线 PRODUCTION，原始 ROUTE_ID 含 MP。ENG 优先。"""
    text = (route or '').strip().upper()
    if _is_engineering_route(text):
        return False
    return text == 'PRODUCTION' or 'MP' in text


def _ncmr_cell(value):
    """空单元格转为 null。时间写成 YYYY-MM-DD HH:MM:SS。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime('%Y-%m-%d %H:%M:%S')
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        text = value.strip()
        return text or None
    text = str(value).strip()
    return text or None


def _ncmr_cells(row):
    if isinstance(row, (str, bytes)) or not isinstance(row, (list, tuple)):
        raise ValueError('上报行无效')
    cells = list(row[:NCMR_ROW_WIDTH])
    if len(cells) < NCMR_ROW_WIDTH:
        cells.extend([None] * (NCMR_ROW_WIDTH - len(cells)))
    return cells


def _ncmr_item(row):
    cells = _ncmr_cells(row)

    def cell(index):
        return _ncmr_cell(cells[index])

    return {
        'batchId': None,
        'ncmrNum': None,
        'holdTime': cell(_COL_HOLD_TIME),
        'holdType': cell(_COL_HOLD_TYPE),
        'lotId': cell(_COL_LOT),
        'waferLot': cell(_COL_WAFER_LOT),
        'sblotId': cell(_COL_SUBLOT),
        'lotType': cell(_COL_LOT_TYPE),
        'area': cell(_COL_AREA),
        'stage': cell(_COL_STAGE),
        'holdWaferList': None,
        'totalWaferList': None,
        'product': cell(_COL_PRODUCT),
        'deviceId': cell(_COL_DEVICE),
        'factory': cell(_COL_FACTORY),
        'holdReason': cell(_COL_HOLD_REASON),
        'preProcessOperator': cell(_COL_PRE_HANDLER),
        'mrbOrders': None,
        'testProgram': cell(_COL_TEST_PROGRAM),
        'packageType': cell(_COL_PACKAGE),
        'probecardLoadboardId': cell(_COL_PROBECARD),
        'proberHandlerId': cell(_COL_PROBER),
        'workingOrder': cell(_COL_WO),
        'testerName': cell(_COL_TESTER),
        'part': cell(_COL_PART),
        'taskNum': cell(_COL_TASK),
        'startTime': cell(_COL_START),
        'endTime': cell(_COL_END),
        'batchType': NCMR_BATCH_TYPE,
        'attachments': None,
        'lotHoldEntity': {
            'data': [[
                None, cell(_COL_OA), None, None, None, None, None, None,
            ]],
            'headers': list(LOT_HOLD_HEADERS),
        },
        'waferHoldEntity': None,
        'disposalOrderId': None,
        'subCustomer': cell(_COL_SUB_CUSTOMER),
        'lotHoldInfo': None,
        'waferHoldInfo': None,
    }


def lot_key_from_ncmr_row(row):
    """NCMR 一次只收一个 lot。lot 是 LotId 里第一个 '-' 之前的前缀。"""
    _key, _cells = align_ncmr_row_lot(row)
    return _key


def align_ncmr_row_lot(row):
    """返回 (lot 前缀, 27 列)。LotId / WaferLot 写成同一个前缀。"""
    cells = _ncmr_cells(row)
    raw = _ncmr_cell(cells[_COL_LOT]) or ''
    key = normalize_lot_id(raw) or raw
    if key:
        cells[_COL_LOT] = key
        wafer_raw = _ncmr_cell(cells[_COL_WAFER_LOT]) or ''
        if not wafer_raw or wafer_raw == raw or normalize_lot_id(wafer_raw) == key:
            cells[_COL_WAFER_LOT] = key
    return key, cells


def build_ncmr_import_body(rows):
    """导出行 → NCMR batchImport 请求体。Hold/Total Wafer List 固定 null。"""
    return {
        'data': [{
            'data': [_ncmr_item(row) for row in rows],
            'waferHoldInfo': None,
        }],
        'batchType': NCMR_BATCH_TYPE,
    }


def _ncmr_brief(text, limit=800):
    text = str(text or '').replace('\n', ' ').strip()
    if len(text) > limit:
        return text[:limit] + '...'
    return text


def _loads_ncmr_json(raw):
    text = (raw or '').strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _ncmr_payload_message(payload, raw):
    if isinstance(payload, dict):
        for key in ('msg', 'message'):
            value = payload.get(key)
            if value not in (None, ''):
                return _ncmr_brief(value)
    return _ncmr_brief(raw)


def _ncmr_business_failed(payload):
    if not isinstance(payload, dict):
        return False
    if payload.get('success') is False:
        return True
    if 'code' not in payload:
        return False
    return payload.get('code') not in _NCMR_OK_CODES


_NCMR_EXPIRED_MSG = 'NCMR登录已过期，请重新登录'
_NCMR_TOKEN_LOCK = threading.Lock()
_ncmr_token = None


def reset_ncmr_token_cache():
    """清掉进程内登录 token。测试用。"""
    global _ncmr_token
    with _NCMR_TOKEN_LOCK:
        _ncmr_token = None


def _ncmr_auth_failed(status, payload, message):
    """导入接口拒绝的是登录态，而不是业务数据。"""
    try:
        if status is not None and int(status) == 401:
            return True
    except (TypeError, ValueError):
        pass
    if isinstance(payload, dict) and str(payload.get('code')) == '401':
        return True
    text = message or ''
    if any(hint in text for hint in ('过期', '未登录', '认证', '登录超时', '重新登录')):
        return True
    return 'token' in text.lower()


def _ncmr_login():
    """用固定账号登录。成功返回 (True, msg, token)，不记录密码和 token。"""
    login_url = str(Config.NCMR_LOGIN_URL or '').strip()
    username = str(Config.NCMR_USERNAME or '').strip()
    password = Config.NCMR_PASSWORD or ''
    if not login_url or not username or not str(password):
        return False, '未配置 NCMR 登录账号', None
    body = json.dumps(
        {'username': username, 'password': password},
        ensure_ascii=False,
    ).encode('utf-8')
    request = urllib.request.Request(
        login_url,
        data=body,
        headers={'Content-Type': 'application/json; charset=utf-8'},
        method='POST',
    )
    logger.info('NCMR login user=%s', username)
    try:
        with urllib.request.urlopen(request, timeout=NCMR_TIMEOUT) as response:
            raw = response.read().decode('utf-8', errors='replace')
            status = response.getcode()
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode('utf-8', errors='replace')
        payload = _loads_ncmr_json(raw)
        return False, _ncmr_payload_message(payload, raw) or 'NCMR 登录失败', None
    except urllib.error.URLError as exc:
        return False, f'NCMR 连接失败: {exc.reason}', None

    payload = _loads_ncmr_json(raw)
    token = ''
    if isinstance(payload, dict):
        token = str(payload.get('token') or '').strip()
    if (
        not isinstance(payload, dict)
        or (status and int(status) >= 400)
        or payload.get('code') not in _NCMR_OK_CODES
        or not token
    ):
        return False, _ncmr_payload_message(payload, raw) or 'NCMR 登录失败', None
    return True, _ncmr_payload_message(payload, raw) or '操作成功', token


def _login_and_store(discard=None):
    """进程内只保留一份 token。discard 是已知失效的旧 token。"""
    global _ncmr_token
    with _NCMR_TOKEN_LOCK:
        if _ncmr_token and _ncmr_token != discard:
            return True, '', _ncmr_token
        ok, msg, token = _ncmr_login()
        if ok and token:
            _ncmr_token = token
            return True, msg, token
        if _ncmr_token == discard:
            _ncmr_token = None
        return False, msg or 'NCMR 登录失败', None


def _acquire_ncmr_token():
    """已登录的 token，或环境变量里的 token，否则登录一次。"""
    with _NCMR_TOKEN_LOCK:
        if _ncmr_token:
            return True, '', _ncmr_token
    static = str(Config.NCMR_BEARER_TOKEN or '').strip()
    if static:
        return True, '', static
    return _login_and_store()


def _drop_ncmr_token(token):
    global _ncmr_token
    with _NCMR_TOKEN_LOCK:
        if _ncmr_token == token:
            _ncmr_token = None


def _post_ncmr_once(url, token, body):
    """返回 (ok, msg, data, auth_failed)。调用方决定要不要重新登录。"""
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            'Content-Type': 'application/json; charset=utf-8',
            'Authorization': f'Bearer {token}',
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(request, timeout=NCMR_TIMEOUT) as response:
            raw = response.read().decode('utf-8', errors='replace')
            status = response.getcode()
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode('utf-8', errors='replace')
        payload = _loads_ncmr_json(raw)
        message = _ncmr_payload_message(payload, raw) or f'HTTP {exc.code}'
        auth_failed = _ncmr_auth_failed(exc.code, payload, message)
        return False, f'NCMR HTTP {exc.code}: {message}', None, auth_failed
    except urllib.error.URLError as exc:
        return False, f'NCMR 连接失败: {exc.reason}', None, False

    payload = _loads_ncmr_json(raw)
    message = _ncmr_payload_message(payload, raw)
    if status and int(status) >= 400:
        auth_failed = _ncmr_auth_failed(status, payload, message)
        text = message or f'NCMR HTTP {status}'
        return False, text, None, auth_failed
    if _ncmr_business_failed(payload):
        auth_failed = _ncmr_auth_failed(status, payload, message)
        return False, message or 'NCMR 上报失败', None, auth_failed
    count = None
    return True, message, count, False


def post_ncmr_import(rows, url=None, token=None):
    """把导出行 POST 到 NCMR。未带 token 时用进程内登录态，过期只重新登录并重试一次。"""
    if not rows:
        return False, '没有可上报的行', None
    if len(rows) > POST_MAX_ROWS:
        return False, f'一次最多上报 {POST_MAX_ROWS} 条', None
    try:
        payload = build_ncmr_import_body(rows)
    except ValueError as exc:
        return False, str(exc), None
    url = str(url or Config.NCMR_IMPORT_URL or '').strip()
    if not url:
        return False, '未配置 NCMR 上报地址', None
    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    logger.info('NCMR import submit rows=%s', len(rows))

    explicit = token is not None
    if explicit:
        token = str(token or '').strip()
        if not token:
            return False, '未配置 NCMR 上报地址或 Token', None
        ok, msg, _count, _auth = _post_ncmr_once(url, token, body)
        if not ok:
            return False, msg, None
        return True, msg or f'已上报 {len(rows)} 条', {'count': len(rows)}

    ok, msg, token = _acquire_ncmr_token()
    if not ok:
        return False, msg or 'NCMR 登录失败', None
    ok, msg, _count, auth_failed = _post_ncmr_once(url, token, body)
    if ok:
        return True, msg or f'已上报 {len(rows)} 条', {'count': len(rows)}
    if not auth_failed:
        return False, msg, None

    ok, _login_msg, fresh = _login_and_store(discard=token)
    if not ok or not fresh:
        _drop_ncmr_token(token)
        return False, _NCMR_EXPIRED_MSG, None
    ok, msg, _count, auth_failed = _post_ncmr_once(url, fresh, body)
    if ok:
        return True, msg or f'已上报 {len(rows)} 条', {'count': len(rows)}
    if auth_failed:
        _drop_ncmr_token(fresh)
        return False, _NCMR_EXPIRED_MSG, None
    return False, msg, None


def ncmr_row_from_hold_record(record):
    """一条 hold_record → NCMR 导出行。晶圆列表留空，由请求体固定成 null。"""
    if not isinstance(record, dict):
        raise ValueError('上报行无效')
    product_id = str(record.get('PRODUCT_ID') or '').strip()
    product = product_id.split('-')[0] if product_id else ''
    route_id = str(record.get('ROUTE_ID') or '')
    if _is_engineering_route(route_id):
        lot_type = 'ENGINEERING'
    elif _is_mp_route(route_id):
        lot_type = 'PRODUCTION'
    else:
        lot_type = route_id.strip() or None
    lot_id = str(record.get('LOT_ID') or '').strip()
    wafer_id = str(record.get('WAFER_ID') or '').strip()
    hold_time = record.get('HOLD_DTTM')
    oa_lot = normalize_lot_id(lot_id) or lot_id
    oa_number = lookup_lot_oa(oa_lot) if oa_lot else ''
    pre_handler = ''
    if _is_engineering_route(route_id) and oa_lot:
        pre_handler = lookup_pre_handler(oa_lot)
    return [
        DEFAULTS['sub_customer'],
        product,
        product_id,
        DEFAULTS['package_type'],
        DEFAULTS['factory'],
        DEFAULTS['area'],
        lot_type,
        lot_id or None,
        wafer_id or None,
        lot_id or None,
        None,
        DEFAULTS['stage'],
        None,
        None,
        record.get('HOLD_REASON') or None,
        pre_handler or None,
        None,
        None,
        hold_time,
        None,
        None,
        None,
        None,
        None,
        None,
        hold_time,
        oa_number or None,
    ]


def report_eng_future_to_ncmr(record_id, record, record_table='FT_HOLD_RECORD'):
    """把一条仍是创建态的 ENG 预 Hold 上报 NCMR。成功才把 STATUS 改成已上报。"""
    try:
        rid = int(record_id)
    except (TypeError, ValueError):
        return False, '记录无效'
    if rid <= 0:
        return False, '记录无效'
    try:
        row = ncmr_row_from_hold_record(record)
    except ValueError as exc:
        return False, str(exc)
    ok, msg, _data = post_ncmr_import([row])
    if not ok:
        logger.warning('ENG预Hold NCMR 上报失败 id=%s: %s', rid, msg)
        return False, msg
    if not mark_hold_record_ncmr_reported(rid, record_table=record_table):
        logger.error('ENG预Hold NCMR 已上报但回写状态失败 id=%s', rid)
        return False, 'NCMR 已上报，但回写状态失败'
    return True, msg or '已上报'
