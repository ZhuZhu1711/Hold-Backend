"""按 lot 查预处置人：OA 单号 → 申请人工号 → 姓名。

调用链（默认 http://127.0.0.1:6969，可用环境变量 HOLD_OA_API_BASE 覆盖）：
  1. POST /api/v1/query          operation=get_lot_order，category=["ft"]
  2. POST /api/v1/jdy/query      operation=engineering_query，category=["common"]
  3. POST /api/v1/bpm/query      operation=get_user_name，category=["common"]

返回形如「周星辰（A0004655）」。字段取 data.OA、data.applicant_id、data.SHOW_NAME。
解析不到时记一条日志并留空，不中断导出。
"""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
from typing import NamedTuple

logger = logging.getLogger(__name__)

DEFAULT_BASE = 'http://172.18.203.43:6969'
DEFAULT_APP_KEY = 'huL5aYsLWGokiap1'
REQUEST_TIMEOUT = 8
ENGINEERING_CATEGORY = 'FT'

_DOC_KEYS = (
    'oa', 'document_number', 'document_no', 'doc_no', 'order_no', 'order_number',
    'oa_no', 'oa_number', 'bill_no', 'oa_bill_no',
)
_WORK_KEYS = (
    'applicant_id', 'work_code', 'workcode', 'job_number', 'job_no', 'emp_no',
    'employee_no', 'employee_id', 'applicant_code', 'apply_code', 'user_code',
    'staff_code', '工号', '申请人工号',
)
_NAME_KEYS = (
    'show_name', 'user_name', 'username', 'real_name', 'realname', 'full_name',
    '姓名', 'name',
)
_ENVELOPE_KEYS = ('data', 'result', 'results', 'payload', 'content')
_VALUE_KEYS = ('value', 'text', 'name', 'label')
_DOC_RE = re.compile(r'ET\.\d+', re.IGNORECASE)
_A_CODE_RE = re.compile(r'^[Aa]0*(\d+)$')


class LotOrder(NamedTuple):
    """get_lot_order 的结果。ok 为假是请求失败；oa、wo 为空仍是查到了。"""

    ok: bool
    oa: str = ''
    wo: str = ''
    error: str = ''


def lot_order_base() -> str:
    """合批建单查 OA/WO。未设 HOLD_OA_API_BASE 时打本机 6969。"""
    override = (os.environ.get('HOLD_OA_API_BASE') or '').strip()
    if override:
        return override.rstrip('/')
    return 'http://127.0.0.1:6969'


def lookup_ncmr_owner(product_id, cache=None) -> str:
    """按型号查 MP 预处置人。line_type 固定 FT。

    POST /api/v1/rpt/query，operation=get_ncmr_owner。
    没有记录、或 data.OWNER 为 null / 空，都是成功，返回空字符串。
    连接失败或 code 不是 0 / 200 时记日志并留空，不中断导出。
    """
    key = (product_id or '').strip()
    if not key:
        return ''
    if cache is not None and key in cache:
        return cache[key]
    try:
        value = _ncmr_owner(key)
    except Exception as exc:
        logger.warning('NCMR 预处置人查询失败 product=%s: %s', key, exc)
        value = ''
    if cache is not None:
        cache[key] = value
    return value


def _ncmr_owner(product_id: str) -> str:
    payload = _post('/api/v1/rpt/query', {
        'category': ['common'],
        'operation': 'get_ncmr_owner',
        'params': {
            'line_type': 'FT',
            'product_id': product_id,
        },
    }, base_url=lot_order_base())
    if not isinstance(payload, dict) or not _owner_response_ok(payload):
        logger.warning(
            'NCMR 预处置人查询失败 product=%s body=%s',
            product_id, _brief(payload),
        )
        return ''
    value = extract_ncmr_owner(payload)
    if value:
        logger.info('NCMR 预处置人 product=%s -> %s', product_id, value)
    return value


def extract_ncmr_owner(payload) -> str:
    """读 data.OWNER。data 为 null、空列表，或 OWNER 为 null，都是空字符串。"""
    if not isinstance(payload, dict) or not _owner_response_ok(payload):
        return ''
    data = payload.get('data') if 'data' in payload else None
    if isinstance(data, list):
        for item in data:
            text = _owner_text(item)
            if text:
                return text
        return ''
    return _owner_text(data)


def _owner_text(node) -> str:
    if not isinstance(node, dict):
        return ''
    return _field(node, 'OWNER')


def _owner_response_ok(payload) -> bool:
    """这个接口成功码是 0；调用方描述的 HTTP/业务 200 也算成功。"""
    if not isinstance(payload, dict) or 'code' not in payload:
        return True
    code = payload.get('code')
    if isinstance(code, str) and code.strip().lstrip('-').isdigit():
        code = int(code.strip())
    return code in (0, 200)


def lookup_lot_order(lot_id) -> LotOrder:
    """POST get_lot_order，返回 data.OA 与 data.WO。

    连接失败、非 JSON、code 非 0 时 ok=False。
    code 为 0 且 OA/WO 缺失或为 null 时 ok=True，对应字段为空字符串。
    """
    key = (lot_id or '').strip()
    if not key:
        return LotOrder(False, error='缺少 LOT_ID')
    try:
        payload = _post('/api/v1/query', {
            'category': ['ft'],
            'operation': 'get_lot_order',
            'params': {'lot_id': key},
        }, base_url=lot_order_base())
    except Exception as exc:
        logger.warning('OA/WO 查询失败 lot=%s: %s', key, exc)
        return LotOrder(False, error=str(exc))
    if not isinstance(payload, dict) or not _response_ok(payload):
        logger.warning('OA/WO 查询失败 lot=%s body=%s', key, _brief(payload))
        message = ''
        if isinstance(payload, dict):
            message = _scalar(payload.get('message')) or _scalar(payload.get('msg'))
        return LotOrder(False, error=message or 'get_lot_order 返回失败')
    data = payload.get('data')
    if data is None:
        return LotOrder(True, '', '')
    if not isinstance(data, dict):
        logger.warning('OA/WO 查询失败 lot=%s body=%s', key, _brief(payload))
        return LotOrder(False, error='get_lot_order data 不是对象')
    return LotOrder(True, _field(data, 'OA'), _field(data, 'WO'))


def _field(data: dict, name: str) -> str:
    for key, value in data.items():
        if str(key).lower() == name.lower():
            return _scalar(value)
    return ''


def lookup_lot_oa(lot_id, cache=None) -> str:
    """按 lot 查 MES get_lot_order，返回 OA 单号。没有记录时为空字符串。"""
    key = (lot_id or '').strip()
    if not key:
        return ''
    if cache is not None and key in cache:
        return cache[key]
    try:
        value = _document_number(key)
    except Exception as exc:
        logger.warning('OA单号查询失败 lot=%s: %s', key, exc)
        value = ''
    if cache is not None:
        cache[key] = value
    return value


def lookup_pre_handler(lot_id, cache=None, oa_cache=None) -> str:
    """同一 lot 在 cache 里只打一轮接口。失败或解析不到时返回空字符串。

    oa_cache 与 lookup_lot_oa 共用时，批次查询不会再打第二次。
    """
    key = (lot_id or '').strip()
    if not key:
        return ''
    if cache is not None and key in cache:
        return cache[key]
    try:
        value = _resolve(key, oa_cache)
    except Exception as exc:
        logger.warning('预处置人查询失败 lot=%s: %s', key, exc)
        value = ''
    if cache is not None:
        cache[key] = value
    return value


_DISPLAY_RE = re.compile(r'^(.+?)[（(]([^）)]+)[）)]$')


def looks_like_work_code(value) -> bool:
    return bool(_digits(value))


def format_pre_handler(name, work_code) -> str:
    """周星辰 + 4655 → 周星辰（A0004655）。工号按 A + 7 位数字展示。"""
    person = (name or '').strip()
    code = format_work_code(work_code) if looks_like_work_code(work_code) else ''
    if person and code:
        return f'{person}（{code}）'
    return person or code or ''


def split_pre_handler(text) -> tuple:
    """已是「姓名（工号）」时拆开。工号不像 A 码则不算已格式化。"""
    matched = _DISPLAY_RE.fullmatch((text or '').strip())
    if not matched or not looks_like_work_code(matched.group(2)):
        return '', ''
    return matched.group(1).strip(), matched.group(2).strip()


def format_work_code(work_code) -> str:
    digits = _digits(work_code)
    if digits:
        return 'A' + digits.zfill(7)
    return (str(work_code).strip() if work_code is not None else '')


def work_code_for_query(work_code) -> str:
    """第三步示例用工号 4655，不带 A 前缀。"""
    digits = _digits(work_code)
    if digits:
        return digits
    return (str(work_code).strip() if work_code is not None else '')


def _digits(work_code) -> str:
    text = str(work_code).strip() if work_code is not None else ''
    if not text:
        return ''
    matched = _A_CODE_RE.fullmatch(text)
    if matched:
        return matched.group(1)
    if text.isdigit():
        return text.lstrip('0') or '0'
    return ''


def _resolve(lot_id: str, oa_cache=None) -> str:
    if oa_cache is None:
        document_number = _document_number(lot_id)
    else:
        document_number = lookup_lot_oa(lot_id, oa_cache)
    if not document_number:
        return ''
    work_code = _work_code(document_number)
    if not work_code:
        return ''
    name = _user_name(work_code_for_query(work_code))
    value = format_pre_handler(name, work_code)
    if value:
        logger.info(
            '预处置人 lot=%s oa=%s work_code=%s -> %s',
            lot_id, document_number, work_code, value,
        )
    return value


def _document_number(lot_id: str) -> str:
    payload = _post('/api/v1/query', {
        'category': ['ft'],
        'operation': 'get_lot_order',
        'params': {'lot_id': lot_id},
    })
    document_number = extract_document_number(payload)
    if not document_number:
        logger.warning(
            '未解析到 OA 单号 lot=%s body=%s',
            lot_id, _brief(payload),
        )
    return document_number


def _work_code(document_number: str) -> str:
    payload = _post('/api/v1/jdy/query', {
        'category': ['common'],
        'operation': 'engineering_query',
        'params': {
            'document_number': document_number,
            'category': ENGINEERING_CATEGORY,
        },
    })
    work_code = extract_work_code(payload)
    if not work_code:
        logger.warning(
            '预处置人未解析到工号 oa=%s body=%s',
            document_number, _brief(payload),
        )
    return work_code


def _user_name(work_code: str) -> str:
    if not work_code:
        return ''
    payload = _post('/api/v1/bpm/query', {
        'category': ['common'],
        'operation': 'get_user_name',
        'params': {'work_code': work_code},
    })
    name = extract_user_name(payload)
    if not name:
        logger.warning(
            '预处置人未解析到姓名 work_code=%s body=%s',
            work_code, _brief(payload),
        )
    return name


def extract_document_number(payload) -> str:
    if not _response_ok(payload):
        return ''
    text = _first_text(payload, _DOC_KEYS)
    if text:
        matched = _DOC_RE.search(text)
        return matched.group(0) if matched else text
    for value in _scalars(payload):
        matched = _DOC_RE.search(value)
        if matched:
            return matched.group(0)
    return ''


def extract_work_code(payload) -> str:
    if not _response_ok(payload):
        return ''
    text = _first_text(payload, _WORK_KEYS)
    if text:
        return text
    for value in _envelope_scalars(payload):
        if _digits(value) or _A_CODE_RE.fullmatch(value):
            return value
    return ''


def extract_user_name(payload) -> str:
    if not _response_ok(payload):
        return ''
    text = _first_text(payload, _NAME_KEYS)
    if text:
        return text
    for value in _envelope_scalars(payload):
        if value and not _digits(value) and not _DOC_RE.search(value):
            return value
    return ''


def _response_ok(payload) -> bool:
    if not isinstance(payload, dict) or 'code' not in payload:
        return True
    return payload.get('code') == 0


def _first_text(payload, keys) -> str:
    for key in keys:
        found = _find_key(payload, key)
        if found:
            return found
    return ''


def _find_key(node, key, depth=0):
    if depth > 6 or node is None:
        return ''
    wanted = key.lower()
    if isinstance(node, dict):
        for name, value in node.items():
            if str(name).lower() == wanted:
                text = _scalar(value)
                if text:
                    return text
        for value in node.values():
            found = _find_key(value, key, depth + 1)
            if found:
                return found
    elif isinstance(node, list):
        for item in node[:20]:
            found = _find_key(item, key, depth + 1)
            if found:
                return found
    return ''


def _scalar(value) -> str:
    if isinstance(value, bool) or value is None:
        return ''
    if isinstance(value, (str, int, float)):
        return str(value).strip()
    if isinstance(value, dict):
        for key in _VALUE_KEYS:
            if key in value:
                text = _scalar(value[key])
                if text:
                    return text
    if isinstance(value, list) and value:
        return _scalar(value[0])
    return ''


def _scalars(node, depth=0):
    if depth > 6 or node is None:
        return
    text = _scalar(node) if not isinstance(node, (dict, list)) else ''
    if text:
        yield text
        return
    if isinstance(node, dict):
        for value in node.values():
            yield from _scalars(value, depth + 1)
    elif isinstance(node, list):
        for item in node[:20]:
            yield from _scalars(item, depth + 1)


def _envelope_scalars(payload):
    if not isinstance(payload, dict):
        text = _scalar(payload)
        if text:
            yield text
        return
    for key in _ENVELOPE_KEYS:
        if key not in payload:
            continue
        text = _scalar(payload[key])
        if text:
            yield text


def _base_url() -> str:
    return (os.environ.get('HOLD_OA_API_BASE') or DEFAULT_BASE).strip().rstrip('/')


def _app_key() -> str:
    return (os.environ.get('HOLD_OA_APP_KEY') or DEFAULT_APP_KEY).strip()


def _post(path: str, body: dict, base_url: str | None = None):
    url = (base_url or _base_url()).rstrip('/') + path
    data = json.dumps(body, ensure_ascii=False).encode('utf-8')
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            'Content-Type': 'application/json; charset=utf-8',
            'X-App-Key': _app_key(),
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            raw = response.read().decode('utf-8', errors='replace')
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode('utf-8', errors='replace')
        raise RuntimeError(f'{path} HTTP {exc.code}: {_brief(detail)}') from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f'{path} 连接失败: {exc.reason}') from exc
    if not raw.strip():
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f'{path} 返回不是 JSON: {_brief(raw)}') from exc


def _brief(payload) -> str:
    if isinstance(payload, str):
        text = payload
    else:
        try:
            text = json.dumps(payload, ensure_ascii=False)
        except TypeError:
            text = str(payload)
    text = text.replace('\n', ' ')
    if len(text) > 800:
        return text[:800] + '...'
    return text
