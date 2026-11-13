"""
按型号 + 时间从 FT_HOLD_RECORD 导出 NCMR 上报清单（原 HoldInfoExport / FT ATE Hold Lot）。

逻辑：
  1. 主数据取 FT_HOLD_RECORD，且当前仍在工程师节点（最新流转负责人是型号工程师；已关闭不取）
  2. ROUTE_ID 含 ENG → ENGINEERING，否则含 MP → PRODUCTION。ENG 优先。
  3. 合批片（LOT/WAFER 后缀位数>2）查 SPLIT_MERGE_HISTORY 还原源 lot / wafer
  4. 补 TEST_WAFER.TEST_PROGRAM
  5. 同一 wafer 多条 record 只保留最新一条（相同时间取 ID 更大的）；Hold Time 统一用该 lot 最早的 HOLD_DTTM
  6. 预处置人：ROUTE 含 ENG 时按 OA 查姓名（形如「周星辰（A0004655）」）；
     含 MP 的 PRODUCTION 调 get_ncmr_owner（line_type 固定 FT，product_id 为实际型号）。
     没有记录或 OWNER 为 null 时留空
  7. OA、WO 优先用 FT_HOLD_RECORD 已有值。没有工单号时才按 lot 请求
     get_lot_order（默认 http://127.0.0.1:6969）。OA 允许为空。查询失败的行不能上报。
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from io import BytesIO
import os
import re

import oracledb
from flask import current_app
from openpyxl import Workbook, load_workbook

from app.config import Config
from app.utils.database_util import (
    DISPOSE_CLOSE, DISPOSE_NCMR_REPORTED, USER, PWD, DSN,
    expand_display_wafer_ids, is_merged_wafer_id, logger,
    mark_hold_record_ncmr_reported, normalize_lot_id,
    resolve_circulation_table, resolve_hold_record_table,
)
from app.backend_schedule.hold_merge_rules import is_eng_pqc_future_hold
from app.utils.ncmr_import import (
    DEFAULTS,
    NCMR_TIMEOUT,
    align_lot_hold_times,
    align_ncmr_row_lot,
    build_ncmr_import_body,
    ncmr_row_from_hold_record,
    post_ncmr_import,
    report_eng_future_to_ncmr,
    reset_ncmr_token_cache,
)
from app.utils.pre_handler_lookup import (
    lookup_lot_oa,
    lookup_lot_order,
    lookup_ncmr_owner,
    lookup_pre_handler,
)

EXPORT_MAX_ROWS = 5000
PREVIEW_MAX_ROWS = 100
SHEET_ENG_PQC = 'ENG预Hold PQC'
SHEET_ENG_OTHER = 'ENG其他Hold'
SHEET_ENGINEERING = 'MP工程测试Hold'
_ROUTE_CHOICES = ('', 'ENG', 'MP')
# 客户端拉原始行后再去重；先截断会和网页导出（先去重再截断）不一致。
RECORDS_MAX_ROWS = 20000
# 合批 / HoldWafer / 测试程序一次提交的 id 上限，避免单次打出过多 LIKE。
LOOKUP_MAX_IDS = 2000

EXCEL_HEADERS = [
    'SubCustomer', 'Product', 'Device', 'PackageType', 'Factory', 'Area',
    'RouteType', 'LotId', 'CustLotId', 'WaferId', '', 'Stage',
    'HoldWaferList', 'WaferList', 'HoldReason', '预处置人', '', '',
    'HoldDateTime', '', 'TestProgram', '', '', '', '', 'HoldDateTime2',
    'OA单号',
]


def is_eng_prehold_pqc(route_id, station, reason=''):
    """ENG 路线 + 站点 FPQC/PQC + 原因含 FUTURE。大小写不敏感。"""
    return is_eng_pqc_future_hold(route_id, station, reason)


def _sheet_key(route_id, station, reason=''):
    """三条都满足进预 Hold 表；其余 ENG 进其他 Hold；不含 ENG 的 MP 进工程测试表。"""
    if is_eng_prehold_pqc(route_id, station, reason):
        return 'eng_pqc'
    if _is_engineering_route(route_id):
        return 'eng_other'
    if _is_mp_route(route_id):
        return 'engineering'
    return ''

_DTTM_RE = re.compile(
    r'^(\d{4}-\d{2}-\d{2})(?:[ T](\d{2}:\d{2})(?::(\d{2}))?)?$'
)


def _connect():
    return oracledb.connect(user=USER, password=PWD, dsn=DSN)


def _record_table():
    return resolve_hold_record_table()


def normalize_dttm(raw, is_end=False):
    """
    接受 YYYY-MM-DD / YYYY-MM-DD HH:MM / YYYY-MM-DDTHH:MM[:SS]。
    仅日期时：开始补 00:00:00，结束补 23:59:59。
    成功返回 'YYYY-MM-DD HH:MM:SS'，失败返回 None。
    """
    text = (raw or '').strip().replace('T', ' ')
    if not text:
        return None
    m = _DTTM_RE.match(text)
    if not m:
        return None
    day, hm, sec = m.group(1), m.group(2), m.group(3)
    if not hm:
        hm = '23:59' if is_end else '00:00'
        sec = '59' if is_end else '00'
    elif not sec:
        sec = '59' if is_end else '00'
    return f'{day} {hm}:{sec}'


def format_wafer_list(wafer_list):
    """LOT:#01#02/LOT2:#03"""
    groups = []
    current_lot = None
    current_nos = []
    for wafer in sorted(str(w).strip() for w in wafer_list if w):
        if '-' in wafer:
            lot = wafer.split('-')[0]
            wafer_no = wafer.split('-')[-1]
        else:
            lot, wafer_no = wafer, ''
        if lot != current_lot:
            if current_lot is not None:
                groups.append(current_lot + ':' + ''.join('#' + n for n in current_nos))
            current_lot = lot
            current_nos = [wafer_no] if wafer_no else []
        elif wafer_no:
            current_nos.append(wafer_no)
    if current_lot is not None:
        groups.append(current_lot + ':' + ''.join('#' + n for n in current_nos))
    return '/'.join(groups)


def _query_hold_records(
    cursor, product_id, start_dttm, end_dttm, lot_id='', route_id='',
    row_limit=None,
):
    record_table = _record_table()
    circ_table = resolve_circulation_table(record_table=record_table)
    sql = f"""
        SELECT
            r.WAFER_ID,
            r.PRODUCT_ID,
            r.EQUIP_ID,
            TO_CHAR(r.HOLD_DTTM, 'YYYY-MM-DD HH24:MI:SS') AS HOLD_DATETIME,
            r.HOLD_REASON AS REASON,
            CASE
                WHEN UPPER(NVL(r.ROUTE_ID, '')) LIKE '%ENG%' THEN 'ENGINEERING'
                WHEN UPPER(NVL(r.ROUTE_ID, '')) LIKE '%MP%' THEN 'PRODUCTION'
            END AS ROUTE_TYPE,
            r.LOT_ID,
            r.ROUTE_ID,
            r.ID,
            r.STATION,
            CASE
                WHEN c.DISPOSE = :reported
                THEN TO_CHAR(c.DISPOSE_DTTM, 'YYYY-MM-DD HH24:MI:SS')
            END AS NCMR_REPORTED_AT,
            r.OA,
            r.WO
        FROM {record_table} r
        INNER JOIN {circ_table} c
            ON c.ID = r.LAST_CIRCULATION_ID
        INNER JOIN PRODUCT_INFO p
            ON p.PRODUCT_ID = r.PRODUCT_ID
        WHERE r.PRODUCT_ID = :device
          AND r.HOLD_DTTM >= TO_DATE(:start_dttm, 'YYYY-MM-DD HH24:MI:SS')
          AND r.HOLD_DTTM <= TO_DATE(:end_dttm, 'YYYY-MM-DD HH24:MI:SS')
          AND NVL(r.STATUS, 0) <> :closed
          AND c.NEXT_OWNER_ID = NVL(p.PRO_ENG_ID, :system_id)
    """
    params = {
        'start_dttm': start_dttm,
        'end_dttm': end_dttm,
        'device': product_id,
        'closed': DISPOSE_CLOSE,
        'system_id': int(getattr(Config, 'SYSTEM_USER_ID', 1) or 1),
        'reported': DISPOSE_NCMR_REPORTED,
    }
    if lot_id:
        sql += " AND UPPER(r.LOT_ID) LIKE UPPER(:lot_id)"
        params['lot_id'] = f'%{lot_id}%'
    route_key = (route_id or '').strip().upper()
    if route_key == 'ALL':
        route_key = ''
    if route_key == 'ENG':
        sql += " AND UPPER(NVL(r.ROUTE_ID, '')) LIKE '%ENG%'"
    elif route_key == 'MP':
        sql += (
            " AND UPPER(NVL(r.ROUTE_ID, '')) LIKE '%MP%'"
            " AND UPPER(NVL(r.ROUTE_ID, '')) NOT LIKE '%ENG%'"
        )
    sql += " ORDER BY r.WAFER_ID, r.HOLD_DTTM, r.ID"
    if row_limit:
        sql = f"SELECT * FROM ({sql}) WHERE ROWNUM <= :row_limit"
        params['row_limit'] = int(row_limit)
    cursor.execute(sql, params)
    return cursor.fetchall()


def _query_merge_sources(cursor, wafer_id, cache):
    if wafer_id in cache:
        return cache[wafer_id]
    cursor.execute(
        """
        SELECT source_lot_id
        FROM mesprod.SPLIT_MERGE_HISTORY@MES16019 s
        WHERE s.TARGET_LOT_ID = :wafer_id
        ORDER BY source_lot_id ASC
        """,
        {'wafer_id': wafer_id},
    )
    sources = [str(r[0]).strip() for r in cursor.fetchall() if r and r[0]]
    cache[wafer_id] = sources
    return sources


def _query_hold_wafers_by_lot(cursor, lot, cache):
    if lot in cache:
        return cache[lot]
    record_table = _record_table()
    cursor.execute(
        f"""
        SELECT LOT_ID, WAFER_ID
        FROM {record_table}
        WHERE LOT_ID LIKE :pat OR WAFER_ID LIKE :pat
        """,
        {'pat': f'{lot}%'},
    )
    wafers = set()
    for lot_id, wafer_id in cursor.fetchall():
        for w in expand_display_wafer_ids(wafer_id, lot_id):
            if w:
                wafers.add(w)
    cache[lot] = wafers
    return wafers


def _is_engineering_route(route):
    """导出路线 ENGINEERING，原始 ROUTE_ID 含 ENG。"""
    return 'ENG' in (route or '').strip().upper()


def _is_mp_route(route):
    """导出路线 PRODUCTION，原始 ROUTE_ID 含 MP。ENG 优先，避免和 MP 叠在一起。"""
    text = (route or '').strip().upper()
    if _is_engineering_route(text):
        return False
    return text == 'PRODUCTION' or 'MP' in text


def _hold_text(hold, index):
    if len(hold) <= index or hold[index] is None:
        return ''
    return str(hold[index]).strip()


def _remember_oa(caches, lot_id, oa):
    lot = (lot_id or '').strip()
    if not lot:
        return
    caches.setdefault('lot_oa', {})[lot] = oa or ''


def _resolve_lot_order(lot_id, stored_oa, stored_wo, caches):
    """表里已有工单号则不再请求。失败返回错误文案，空 OA 不算失败。"""
    lot = (lot_id or '').strip()
    if stored_wo:
        _remember_oa(caches, lot, stored_oa)
        return stored_oa or '', stored_wo, ''
    cache = caches.setdefault('lot_order', {})
    if lot not in cache:
        cache[lot] = lookup_lot_order(lot)
    order = cache[lot]
    if not order.ok:
        caches.setdefault('lot_oa', {}).setdefault(lot, '')
        return '', '', order.error or 'OA/WO 查询失败'
    _remember_oa(caches, lot, order.oa)
    return order.oa or '', order.wo or '', ''


def _pre_handler_for(_cursor, route, lot_id, product_id, caches):
    """ENG 走 OA；MP 按型号调 get_ncmr_owner。同一型号只请求一次。"""
    if _is_engineering_route(route):
        return lookup_pre_handler(
            lot_id,
            caches.setdefault('pre_handler', {}),
            oa_cache=caches.setdefault('lot_oa', {}),
        )
    if _is_mp_route(route):
        return lookup_ncmr_owner(
            product_id, caches.setdefault('ncmr_owner', {}),
        )
    return ''


def _test_program_route_pat(route):
    """TEST_WAFER.ROUTE 仍是 ENG；导出路线类型已改为 ENGINEERING。"""
    text = (route or '').strip()
    if text.upper() == 'ENGINEERING':
        return '%ENG%'
    return f'%{text}%'


def _query_test_program(cursor, wafer_id, route, cache):
    key = (wafer_id, route or '')
    if key in cache:
        return cache[key]
    if not wafer_id or not route:
        cache[key] = ''
        return ''
    cursor.execute(
        """
        SELECT TEST_PROGRAM
        FROM TEST_WAFER
        WHERE WAFER_ID = :wafer_id AND ROUTE LIKE :route_pat
        ORDER BY ID DESC
        """,
        {'wafer_id': wafer_id, 'route_pat': _test_program_route_pat(route)},
    )
    row = cursor.fetchone()
    value = str(row[0]).strip() if row and row[0] else ''
    cache[key] = value
    return value


def _merge_target(lot_id, wafer_id, expanded):
    if is_merged_wafer_id(lot_id):
        return lot_id
    if is_merged_wafer_id(wafer_id):
        return wafer_id
    for w in expanded:
        if is_merged_wafer_id(w):
            return w
    return None


def _process_hold(cursor, hold, caches, extras, hold_time=None):
    wafer_id = str(hold[0] or '').strip()
    product_id = str(hold[1] or '').strip()
    hold_date = hold[3]
    if hold_time in (None, ''):
        hold_time = hold_date
    reason = hold[4] or ''
    route = hold[5]
    lot_id = str(hold[6] or '').strip()
    route_id = str(hold[7] or '').strip() if len(hold) > 7 else ''
    record_id = hold[8] if len(hold) > 8 else None
    try:
        record_id = int(record_id) if record_id not in (None, '') else None
    except (TypeError, ValueError):
        record_id = None
    station = str(hold[9] or '').strip() if len(hold) > 9 else ''
    reported_at = _hold_text(hold, 10)
    stored_oa = _hold_text(hold, 11)
    stored_wo = _hold_text(hold, 12)

    product = product_id.split('-')[0] if product_id else ''
    expanded = expand_display_wafer_ids(wafer_id, lot_id)
    lookup_wafer = expanded[0] if expanded else wafer_id
    test_pro = _query_test_program(cursor, lookup_wafer, route, caches['test_program'])

    source_lots_set = set()
    source_wafers_set = set()
    merge_key = _merge_target(lot_id, wafer_id, expanded)
    if merge_key:
        for src in _query_merge_sources(cursor, merge_key, caches['merge']):
            source_wafers_set.add(src)
            source_lots_set.add(src.split('-')[0] if '-' in src else src)
    if not source_wafers_set:
        source_wafers_set.update(expanded or ([wafer_id] if wafer_id else []))
        if lot_id:
            source_lots_set.add(lot_id.split('-')[0] if '-' in lot_id else lot_id)
        elif wafer_id and '-' in wafer_id and not wafer_id.startswith('#'):
            source_lots_set.add(wafer_id.split('-')[0])

    hold_wafers_set = set(expanded)
    for lot in list(source_lots_set):
        for wafer in _query_hold_wafers_by_lot(cursor, lot, caches['hold_wafers']):
            if wafer in source_wafers_set:
                hold_wafers_set.add(wafer)
    if not hold_wafers_set:
        hold_wafers_set.update(source_wafers_set)

    source_lots = sorted(source_lots_set)
    source_wafers = sorted(source_wafers_set)
    hold_wafers = sorted(hold_wafers_set)
    oa_number, wo, order_error = _resolve_lot_order(
        lot_id, stored_oa, stored_wo, caches,
    )
    pre_handler = _pre_handler_for(cursor, route, lot_id, product_id, caches)
    lot_str = '/'.join(source_lots)
    lot_key = normalize_lot_id(lot_id)
    if not lot_key and source_lots:
        lot_key = source_lots[0]
    ncmr_lot = lot_key or lot_str
    hold_wafer_list_str = format_wafer_list(hold_wafers)
    wafer_list_str = format_wafer_list(source_wafers)
    if not hold_wafer_list_str:
        hold_wafer_list_str = wafer_list_str

    row = [
        extras.get('sub_customer') or DEFAULTS['sub_customer'],
        product,
        product_id,
        extras.get('package_type') or DEFAULTS['package_type'],
        DEFAULTS['factory'],
        extras.get('area') or DEFAULTS['area'],
        route or '',
        ncmr_lot,
        wafer_id,
        ncmr_lot,
        wo or None,
        DEFAULTS['stage'],
        hold_wafer_list_str,
        wafer_list_str,
        reason,
        pre_handler,
        None,
        None,
        hold_time,
        None,
        test_pro,
        None,
        None,
        None,
        None,
        hold_date,
        oa_number,
    ]
    preview = {
        'WAFER_ID': wafer_id,
        'PRODUCT_ID': product_id,
        'EQUIP_ID': str(hold[2] or '').strip(),
        'HOLD_DATETIME': hold_time,
        'REASON': reason,
        'ROUTE_TYPE': route or '',
        'ROUTE_ID': route_id,
        'STATION': station,
        'RECORD_ID': record_id,
        'SHEET': _sheet_key(route_id, station, reason),
        'LOT_STR': lot_str,
        'LOT_KEY': ncmr_lot,
        'HOLD_WAFER_LIST': hold_wafer_list_str,
        'WAFER_LIST': wafer_list_str,
        'TEST_PROGRAM': test_pro,
        'PRE_HANDLER': pre_handler,
        'OA_NUMBER': oa_number,
        'WO': wo,
        'ORDER_ERROR': order_error,
        'NCMR_REPORTED_AT': reported_at,
    }
    return row, preview


def _export_lot_key(hold, index):
    """Hold Time 按 lot 对齐。片号后缀和 WLT 的 .NO 先剥掉；lot 为空时用 wafer 前缀。"""
    lot_id = str(hold[6] or '').strip() if len(hold) > 6 else ''
    wafer_id = str(hold[0] or '').strip()
    key = normalize_lot_id(lot_id) or normalize_lot_id(wafer_id)
    if key:
        return key.upper()
    return f'\0lot{index}'


def _export_wafer_key(hold, index):
    """去重用的 wafer。#01 会按 lot 还原成 LOT-01；没有 wafer 时各行单独保留。"""
    wafer_id = str(hold[0] or '').strip()
    lot_id = str(hold[6] or '').strip() if len(hold) > 6 else ''
    expanded = [
        str(w).strip().upper()
        for w in expand_display_wafer_ids(wafer_id, lot_id)
        if w and str(w).strip()
    ]
    if expanded:
        return '/'.join(sorted(set(expanded)))
    if wafer_id:
        return wafer_id.upper()
    return f'\0{index}'


def _hold_recency(hold):
    hold_dttm = str(hold[3] or '') if len(hold) > 3 else ''
    hold_id = hold[8] if len(hold) > 8 and hold[8] is not None else 0
    return hold_dttm, hold_id


def _prepare_export_holds(holds):
    """每个 wafer 保留最新 record；Hold Time 用该 lot 最早的 HOLD_DTTM。"""
    earliest_by_lot = {}
    best_index = {}
    wafer_keys = []
    lot_keys = []
    for index, hold in enumerate(holds):
        wafer_key = _export_wafer_key(hold, index)
        lot_key = _export_lot_key(hold, index)
        wafer_keys.append(wafer_key)
        lot_keys.append(lot_key)
        dttm = str(hold[3] or '') if len(hold) > 3 else ''
        if dttm and (lot_key not in earliest_by_lot or dttm < earliest_by_lot[lot_key]):
            earliest_by_lot[lot_key] = dttm
        prev = best_index.get(wafer_key)
        if prev is None or _hold_recency(hold) >= _hold_recency(holds[prev]):
            best_index[wafer_key] = index
    keep = set(best_index.values())
    prepared = []
    for index, hold in enumerate(holds):
        if index not in keep:
            continue
        hold_time = earliest_by_lot.get(lot_keys[index])
        if hold_time in (None, ''):
            hold_time = hold[3] if len(hold) > 3 else ''
        prepared.append((hold, hold_time))
    return prepared


def _latest_hold_per_wafer(holds):
    """同一 wafer 只留最新一条，其余行的相对顺序不变。"""
    return [hold for hold, _hold_time in _prepare_export_holds(holds)]


def _parse_filters(product_id, start_dttm, end_dttm, lot_id='', route_id=''):
    product_id = (product_id or '').strip()
    lot_id = (lot_id or '').strip()
    route_id = (route_id or '').strip().upper()
    if route_id == 'ALL':
        route_id = ''
    if route_id not in _ROUTE_CHOICES:
        return False, 'Route 无效，只能选 ALL、ENG 或 MP', None
    start = normalize_dttm(start_dttm, is_end=False)
    end = normalize_dttm(end_dttm, is_end=True)
    if not product_id:
        return False, '请指定型号 PRODUCT_ID', None
    if not start or not end:
        return False, '请指定有效的开始/结束时间', None
    try:
        start_dt = datetime.strptime(start, '%Y-%m-%d %H:%M:%S')
        end_dt = datetime.strptime(end, '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return False, '时间格式无效', None
    if end_dt < start_dt:
        return False, '结束时间不能早于开始时间', None
    return True, 'ok', (product_id, start, end, lot_id, route_id)


def _ensure_owned_product(product_id, owner_eng_id=None):
    """root: owner_eng_id=None 不限制；工程师必须所属型号。"""
    if owner_eng_id is None:
        return True, 'ok'
    from app.controllers.engineer_ctrl import engineer_owns_product
    if engineer_owns_product(owner_eng_id, product_id):
        return True, 'ok'
    return False, '不属于您负责的型号'


def _collect_rows(
    product_id, start_dttm, end_dttm, extras, limit, lot_id='', route_id='',
):
    conn = _connect()
    try:
        cursor = conn.cursor()
        holds = _query_hold_records(
            cursor, product_id, start_dttm, end_dttm,
            lot_id=lot_id, route_id=route_id,
        )
        prepared = _prepare_export_holds(holds)
        total = len(prepared)
        truncated = total > limit
        prepared = prepared[:limit]
        caches = {'merge': {}, 'hold_wafers': {}, 'test_program': {}}
        excel_rows = []
        preview_rows = []
        for hold, hold_time in prepared:
            excel_row, preview = _process_hold(
                cursor, hold, caches, extras, hold_time=hold_time,
            )
            excel_rows.append(excel_row)
            preview_rows.append(preview)
        return True, 'ok', {
            'excel_rows': excel_rows,
            'preview_rows': preview_rows,
            'total': total,
            'truncated': truncated,
            'product_id': product_id,
            'lot_id': lot_id,
            'route_id': route_id,
            'start_dttm': start_dttm,
            'end_dttm': end_dttm,
        }
    except Exception as e:
        logger.error(f'NCMR 上报清单导出失败: {e}', exc_info=True)
        return False, f'导出失败: {e}', None
    finally:
        conn.close()


def preview_hold_info_export(
    product_id, start_dttm, end_dttm, lot_id='', route_id='',
    owner_eng_id=None, **extras,
):
    ok, msg, parsed = _parse_filters(
        product_id, start_dttm, end_dttm, lot_id=lot_id, route_id=route_id,
    )
    if not ok:
        return False, msg, None
    product_id, start, end, lot_id, route_id = parsed
    ok, msg = _ensure_owned_product(product_id, owner_eng_id)
    if not ok:
        return False, msg, None
    ok, msg, payload = _collect_rows(
        product_id, start, end, extras, EXPORT_MAX_ROWS,
        lot_id=lot_id, route_id=route_id,
    )
    if not ok:
        return False, msg, None
    eng_preview, other_preview, engineering_preview = _partition_preview(
        payload['preview_rows'],
    )
    eng_groups, other_groups, engineering_groups, missing_wo, order_failed = (
        _build_ncmr_groups(payload['preview_rows'])
    )
    return True, '查询成功', {
        'items': payload['preview_rows'],
        'eng_pqc': eng_preview,
        'eng_other': other_preview,
        'engineering': engineering_preview,
        'eng_groups': eng_groups,
        'eng_other_groups': other_groups,
        'engineering_groups': engineering_groups,
        'missing_wo_total': missing_wo,
        'order_failed_total': order_failed,
        'eng_pqc_total': len(eng_preview),
        'eng_other_total': len(other_preview),
        'engineering_total': len(engineering_preview),
        'total': payload['total'],
        'shown': len(payload['preview_rows']),
        'truncated': payload['truncated'],
        'product_id': product_id,
        'lot_id': lot_id,
        'route_id': route_id,
        'start_dttm': start,
        'end_dttm': end,
    }


def _find_template():
    name = 'FT_ATE_HOLD_LOT.xlsx'
    root = current_app.root_path
    backend_root = os.path.dirname(root)
    workspace = os.path.dirname(backend_root)
    candidates = [
        os.path.join(root, 'static', 'templates', name),
        os.path.join(workspace, 'HoldInfoExport', 'template', name),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def _preview_lot_key(preview):
    key = str(preview.get('LOT_KEY') or '').strip()
    if key:
        return key
    text = str(preview.get('LOT_STR') or '').strip()
    if '/' in text:
        text = text.split('/', 1)[0].strip()
    return normalize_lot_id(text) or text


def _reportable_record_id(preview):
    """未上报、有工单号、有 lot 的记录才能进入上报按钮。"""
    if not isinstance(preview, dict):
        return None
    if preview.get('NCMR_REPORTED_AT') or preview.get('ORDER_ERROR'):
        return None
    if not str(preview.get('WO') or '').strip():
        return None
    if not _preview_lot_key(preview):
        return None
    try:
        rid = int(preview.get('RECORD_ID'))
    except (TypeError, ValueError):
        return None
    if rid <= 0:
        return None
    return rid


def _lot_nodes(lot_map):
    lots = []
    for lot in sorted(lot_map):
        wafers = lot_map[lot]
        ids = []
        for preview in wafers:
            rid = _reportable_record_id(preview)
            if rid is not None:
                ids.append(rid)
        lots.append({
            'lot': lot,
            'record_ids': ids,
            'count': len(wafers),
            'wafers': wafers,
        })
    return lots


def _report_ids_and_lots(lots):
    ids = []
    seen_lots = []
    for lot in lots:
        lot_ids = lot.get('record_ids') or []
        if not lot_ids:
            continue
        ids.extend(lot_ids)
        name = lot.get('lot') or ''
        if name and name not in seen_lots:
            seen_lots.append(name)
    return ids, seen_lots


def _nest_eng_groups(rows, oa_report=True):
    """ENG：OA → 工单 → lot → wafer。预 Hold 的上报按钮挂在 OA 上。

    其他 Hold 按片上报，OA 上不挂 record_ids。
    """
    tree = {}
    for preview in rows:
        oa = str(preview.get('OA_NUMBER') or '').strip()
        wo = str(preview.get('WO') or '').strip()
        lot = _preview_lot_key(preview) or '（无 lot）'
        tree.setdefault(oa, {}).setdefault(wo, {}).setdefault(lot, []).append(preview)
    groups = []
    for oa in sorted(tree):
        wos = []
        for wo in sorted(tree[oa]):
            wos.append({'wo': wo, 'lots': _lot_nodes(tree[oa][wo])})
        flat_lots = [lot for wo in wos for lot in wo['lots']]
        record_ids, lot_names = _report_ids_and_lots(flat_lots)
        if not oa_report:
            record_ids = []
            lot_names = []
        groups.append({
            'oa': oa,
            'wos': wos,
            'record_ids': record_ids,
            'count': len(record_ids),
            'lot_count': len(lot_names),
            'can_report': bool(record_ids),
        })
    return groups


def _nest_mp_groups(rows):
    """MP：工单 → lot → wafer。网页不展示这层结构，按片平铺，每行一个上报按钮。"""
    tree = {}
    for preview in rows:
        wo = str(preview.get('WO') or '').strip()
        lot = _preview_lot_key(preview) or '（无 lot）'
        tree.setdefault(wo, {}).setdefault(lot, []).append(preview)
    groups = []
    for wo in sorted(tree):
        lots = _lot_nodes(tree[wo])
        record_ids, lot_names = _report_ids_and_lots(lots)
        groups.append({
            'wo': wo,
            'lots': lots,
            'record_ids': record_ids,
            'count': len(record_ids),
            'lot_count': len(lot_names),
            'can_report': bool(record_ids),
        })
    return groups


def _build_ncmr_groups(preview_rows):
    """ENG 按 OA 分层，MP 按工单分层。缺工单或查询失败的行只展示，不进上报按钮。"""
    eng_rows = []
    other_rows = []
    mp_rows = []
    missing = 0
    failed = 0
    for preview in preview_rows or []:
        sheet = preview.get('SHEET')
        if sheet == 'eng_pqc':
            eng_rows.append(preview)
        elif sheet == 'eng_other':
            other_rows.append(preview)
        elif sheet == 'engineering':
            mp_rows.append(preview)
        else:
            continue
        if preview.get('NCMR_REPORTED_AT'):
            continue
        wo = str(preview.get('WO') or '').strip()
        if preview.get('ORDER_ERROR') or not wo:
            missing += 1
            if preview.get('ORDER_ERROR'):
                failed += 1
    return (
        _nest_eng_groups(eng_rows),
        _nest_eng_groups(other_rows, oa_report=False),
        _nest_mp_groups(mp_rows),
        missing,
        failed,
    )


def _partition_preview(preview_rows):
    eng_rows = []
    other_rows = []
    engineering_rows = []
    for preview in preview_rows or []:
        sheet = preview.get('SHEET')
        if sheet == 'eng_pqc':
            eng_rows.append(preview)
        elif sheet == 'eng_other':
            other_rows.append(preview)
        elif sheet == 'engineering':
            engineering_rows.append(preview)
    return eng_rows, other_rows, engineering_rows


def _normalize_engineering_ids(engineering_ids):
    """None 表示 MP 工程测试表全量。空列表表示这张表不写数据行。"""
    if engineering_ids is None:
        return True, 'ok', None
    if isinstance(engineering_ids, (str, bytes)) or not isinstance(
        engineering_ids, (list, tuple),
    ):
        return False, 'engineering_ids无效', None
    cleaned = set()
    for raw in engineering_ids:
        if raw is None or str(raw).strip() == '':
            continue
        try:
            cleaned.add(int(raw))
        except (TypeError, ValueError):
            return False, 'engineering_ids无效', None
    if len(cleaned) > EXPORT_MAX_ROWS:
        return False, f'勾选条数无效，一次最多 {EXPORT_MAX_ROWS} 条', None
    return True, 'ok', cleaned


def _select_export_rows(excel_rows, preview_rows, engineering_ids):
    eng_rows = []
    other_rows = []
    node_rows = []
    for excel, preview in zip(excel_rows, preview_rows):
        sheet = preview.get('SHEET')
        if sheet in ('eng_pqc', 'eng_other'):
            reported = preview.get('NCMR_REPORTED_AT') or None
            target = eng_rows if sheet == 'eng_pqc' else other_rows
            target.append(list(excel) + [reported])
            continue
        if sheet != 'engineering':
            continue
        if engineering_ids is None or preview.get('RECORD_ID') in engineering_ids:
            node_rows.append(excel)
    return eng_rows, other_rows, node_rows


_SHEET_TITLES = {
    'eng_pqc': SHEET_ENG_PQC,
    'eng_other': SHEET_ENG_OTHER,
    'engineering': SHEET_ENGINEERING,
}


def _normalize_sheet(sheet):
    key = (sheet or '').strip().lower()
    if key not in _SHEET_TITLES:
        return False, '导出类型无效', None
    return True, 'ok', key


def _build_export_workbook(rows, title):
    """只写当前选项卡对应的一张表。"""
    template = _find_template()
    if template:
        wb = load_workbook(template)
        sheet = wb.active
        sheet.title = title
        for extra in list(wb.worksheets):
            if extra is not sheet:
                wb.remove(extra)
    else:
        wb = Workbook()
        sheet = wb.active
        sheet.title = title
        sheet.append(list(EXCEL_HEADERS))
    if title in (SHEET_ENG_PQC, SHEET_ENG_OTHER):
        sheet.cell(1, len(EXCEL_HEADERS) + 1, '上报时间')
    for row in rows:
        sheet.append(row)
    bio = BytesIO()
    wb.save(bio)
    bio.seek(0)
    return bio.getvalue()


def _sheet_export_rows(
    product_id, start_dttm, end_dttm, lot_id='', route_id='',
    owner_eng_id=None, engineering_ids=None, sheet='', **extras,
):
    """按当前选项卡取出导出行。返回 (ok, msg, rows, title, payload)。"""
    ok, msg, parsed = _parse_filters(
        product_id, start_dttm, end_dttm, lot_id=lot_id, route_id=route_id,
    )
    if not ok:
        return False, msg, None, None, None
    product_id, start, end, lot_id, route_id = parsed
    ok, msg = _ensure_owned_product(product_id, owner_eng_id)
    if not ok:
        return False, msg, None, None, None
    ok, msg, sheet_key = _normalize_sheet(sheet)
    if not ok:
        return False, msg, None, None, None
    if sheet_key == 'engineering':
        ok, msg, id_set = _normalize_engineering_ids(engineering_ids)
        if not ok:
            return False, msg, None, None, None
    else:
        id_set = None
    ok, msg, payload = _collect_rows(
        product_id, start, end, extras, EXPORT_MAX_ROWS,
        lot_id=lot_id, route_id=route_id,
    )
    if not ok:
        return False, msg, None, None, None

    eng_rows, other_rows, node_rows = _select_export_rows(
        payload['excel_rows'], payload['preview_rows'], id_set,
    )
    title = _SHEET_TITLES[sheet_key]
    rows = {
        'eng_pqc': eng_rows,
        'eng_other': other_rows,
        'engineering': node_rows,
    }[sheet_key]
    return True, 'ok', rows, title, payload


def export_hold_info_xlsx(
    product_id, start_dttm, end_dttm, lot_id='', route_id='',
    owner_eng_id=None, engineering_ids=None, sheet='', **extras,
):
    ok, msg, rows, title, payload = _sheet_export_rows(
        product_id, start_dttm, end_dttm, lot_id=lot_id, route_id=route_id,
        owner_eng_id=owner_eng_id, engineering_ids=engineering_ids,
        sheet=sheet, **extras,
    )
    if not ok:
        return False, msg, None
    content = _build_export_workbook(rows, title)
    note = f'导出 {len(rows)} 条（{title}）'
    if payload['truncated']:
        note = (
            f"共 {payload['total']} 条，已处理前 {len(payload['excel_rows'])} 条，"
            f"写入 {len(rows)} 条（{title}）"
        )
    return True, note, content


def _reported_record_ids(preview_rows, engineering_ids):
    """本次实际上报的 MP 记录。engineering_ids 为 None 时取该表全部。"""
    selected = None if engineering_ids is None else set()
    if selected is not None:
        for raw in engineering_ids:
            try:
                selected.add(int(raw))
            except (TypeError, ValueError):
                continue
    ids = []
    seen = set()
    for preview in preview_rows or []:
        if preview.get('SHEET') != 'engineering':
            continue
        try:
            rid = int(preview.get('RECORD_ID'))
        except (TypeError, ValueError):
            continue
        if rid <= 0 or rid in seen:
            continue
        if selected is not None and rid not in selected:
            continue
        seen.add(rid)
        ids.append(rid)
    return ids


def _clean_record_ids(record_ids):
    if record_ids is None:
        return []
    if isinstance(record_ids, (str, bytes)) or not isinstance(record_ids, (list, tuple)):
        return []
    ids = []
    seen = set()
    for raw in record_ids:
        try:
            rid = int(raw)
        except (TypeError, ValueError):
            continue
        if rid <= 0 or rid in seen:
            continue
        seen.add(rid)
        ids.append(rid)
    return ids


def _write_ncmr_circulations(record_ids):
    """上报已经成功之后补流转。失败只记在结果里，不再次调用 NCMR。"""
    ids = _clean_record_ids(record_ids)
    if not ids:
        return '，未写入流转', 0
    table = resolve_hold_record_table()
    marked = 0
    for rid in ids:
        if mark_hold_record_ncmr_reported(rid, record_table=table):
            marked += 1
    skipped = len(ids) - marked
    note = f'，流转已写入 {marked} 条'
    if skipped:
        note += f'，{skipped} 条未写入（已关闭或已上报）'
    return note, marked


def _post_ncmr_by_lot(pairs):
    """按 lot 分批调用 NCMR。某一 lot 失败时，已经成功的 lot 仍然回写流转。

    同一 lot 的多行合成一单，holdTime 统一成该 lot 最晚的非空时间。
    pairs 是 (导出行, record_id 或 None)。返回 (全部成功, 说明, data)。
    """
    if not pairs:
        return False, '没有可上报的行', None
    buckets = {}
    order = []
    try:
        for row, rid in pairs:
            key, cells = align_ncmr_row_lot(row)
            label = key or ''
            if label not in buckets:
                buckets[label] = []
                order.append(label)
            buckets[label].append((cells, rid))
    except ValueError as exc:
        return False, str(exc), None

    lots_ok = []
    lots_failed = []
    reported_ids = []
    marked = 0
    posted_rows = 0
    notes = []
    for label in order:
        items = buckets[label]
        rows = [cells for cells, _rid in items]
        ids = []
        for _cells, rid in items:
            if rid is None:
                continue
            try:
                rid = int(rid)
            except (TypeError, ValueError):
                continue
            if rid > 0 and rid not in ids:
                ids.append(rid)
        if not label:
            lots_failed.append({'lot': '', 'msg': '缺少 lot'})
            notes.append('缺少 lot，未上报')
            continue
        rows = align_lot_hold_times(rows)
        ok, msg, _data = post_ncmr_import(rows)
        if not ok:
            lots_failed.append({'lot': label, 'msg': msg})
            notes.append(f'lot {label} 失败：{msg}')
            continue
        note, count = _write_ncmr_circulations(ids)
        marked += count
        reported_ids.extend(ids)
        posted_rows += len(rows)
        lots_ok.append(label)
        notes.append(f'lot {label} 已上报 {len(rows)} 条{note}')
    data = {
        'count': posted_rows,
        'lots_ok': lots_ok,
        'lots_failed': lots_failed,
        'reported_ids': reported_ids,
        'circulation_marked': marked,
    }
    summary = '；'.join(notes)
    if lots_failed:
        prefix = '部分 lot 未上报。' if lots_ok else ''
        return False, f'{prefix}{summary}', data
    return True, summary or f'已上报 {posted_rows} 条', data


def import_ncmr_rows(rows, record_ids=None):
    """客户端已组装好的行。按 lot 分批上报，成功的 lot 写「已上报」流转。"""
    if isinstance(rows, (str, bytes)) or not isinstance(rows, list):
        return False, '上报行无效', None
    if record_ids is None:
        pairs = [(row, None) for row in rows]
    else:
        if isinstance(record_ids, (str, bytes)) or not isinstance(record_ids, (list, tuple)):
            return False, 'record_ids无效', None
        if len(record_ids) != len(rows):
            return False, '上报行与记录条数不一致', None
        pairs = []
        seen = set()
        for row, raw in zip(rows, record_ids):
            try:
                rid = int(raw)
            except (TypeError, ValueError):
                return False, 'record_ids无效', None
            if rid <= 0 or rid in seen:
                return False, 'record_ids无效', None
            seen.add(rid)
            pairs.append((row, rid))
    return _post_ncmr_by_lot(pairs)


def import_hold_info_ncmr(
    product_id, start_dttm, end_dttm, lot_id='', route_id='',
    owner_eng_id=None, engineering_ids=None, sheet='', **extras,
):
    """网页按筛选和勾选重新取 MP 表，再按 lot 分批上报。

    ENG 预 Hold 由定时任务上报。ENG 其他 Hold 只按单片走 import_ncmr_group。
    """
    ok, msg, sheet_key = _normalize_sheet(sheet)
    if not ok:
        return False, msg, None
    if sheet_key == 'eng_pqc':
        return False, 'ENG预Hold PQC 由系统自动上报，不能手动上报', None
    if sheet_key == 'eng_other':
        return False, 'ENG其他Hold 按单片上报，不能整表上报', None
    ok, msg, rows, title, payload = _sheet_export_rows(
        product_id, start_dttm, end_dttm, lot_id=lot_id, route_id=route_id,
        owner_eng_id=owner_eng_id, engineering_ids=engineering_ids,
        sheet=sheet, **extras,
    )
    if not ok:
        return False, msg, None
    if not rows:
        return False, '没有可上报的行', None
    wanted = set(_reported_record_ids(
        (payload or {}).get('preview_rows'), engineering_ids,
    ))
    pairs = []
    for excel, preview in zip(payload['excel_rows'], payload['preview_rows']):
        try:
            rid = int(preview.get('RECORD_ID'))
        except (TypeError, ValueError):
            continue
        if rid not in wanted:
            continue
        pairs.append((excel, rid))
    ok, msg, data = _post_ncmr_by_lot(pairs)
    if data is None:
        return False, msg, None
    return ok, f'{msg}（{title}）', data


def import_ncmr_group(
    record_ids, product_id, start_dttm, end_dttm, lot_id='', route_id='',
    owner_eng_id=None, **extras,
):
    """按一组 record 重新取仍在工程师节点上的行。

    ENG 预 Hold 必须是同一个 OA，MP 必须是同一个工单。
    ENG 其他 Hold 一次只上报一片。提交时再按 lot 拆开。
    """
    ids = _clean_record_ids(record_ids)
    if not ids:
        return False, '没有可上报的记录', None
    if len(ids) > EXPORT_MAX_ROWS:
        return False, f'一次最多 {EXPORT_MAX_ROWS} 条', None
    ok, msg, parsed = _parse_filters(
        product_id, start_dttm, end_dttm, lot_id=lot_id, route_id=route_id,
    )
    if not ok:
        return False, msg, None
    product_id, start, end, lot_id, route_id = parsed
    ok, msg = _ensure_owned_product(product_id, owner_eng_id)
    if not ok:
        return False, msg, None
    ok, msg, payload = _collect_rows(
        product_id, start, end, extras, EXPORT_MAX_ROWS,
        lot_id=lot_id, route_id=route_id,
    )
    if not ok:
        return False, msg, None
    wanted = set(ids)
    pairs = []
    found = set()
    sheets = set()
    wos = set()
    oas = set()
    only_preview = None
    for excel, preview in zip(payload['excel_rows'], payload['preview_rows']):
        try:
            rid = int(preview.get('RECORD_ID'))
        except (TypeError, ValueError):
            continue
        if rid not in wanted:
            continue
        sheet = preview.get('SHEET')
        if sheet not in ('eng_pqc', 'eng_other', 'engineering'):
            return False, '包含不能上报的记录，请重新预览', None
        if preview.get('NCMR_REPORTED_AT'):
            return False, '包含已上报记录，请重新预览', None
        if preview.get('ORDER_ERROR'):
            return False, f"OA/WO 查询失败，不能上报：{preview.get('ORDER_ERROR')}", None
        wo = str(preview.get('WO') or '').strip()
        if not wo:
            return False, '缺少工单号，不能上报', None
        found.add(rid)
        pairs.append((excel, rid))
        sheets.add(sheet)
        wos.add(wo)
        oas.add(str(preview.get('OA_NUMBER') or '').strip())
        only_preview = preview
    if found != wanted:
        return False, '部分记录已不在可上报范围，请重新预览', None
    if len(sheets) != 1:
        return False, '不能把 ENG 和 MP 放在同一次上报', None
    sheet = next(iter(sheets))
    if sheet == 'eng_other':
        if len(found) != 1:
            return False, 'ENG其他Hold 一次只能上报一片', None
        if not _preview_lot_key(only_preview):
            return False, '缺少 lot，不能上报', None
        wafer = str((only_preview or {}).get('WAFER_ID') or '').strip()
        label = wafer or '该片'
    elif sheet == 'eng_pqc':
        if len(oas) != 1:
            return False, '组内 OA 不一致', None
        label = f"OA {next(iter(oas)) or '无 OA'}"
    else:
        if len(wos) != 1:
            return False, '组内工单号不一致', None
        label = f"工单 {next(iter(wos))}"
    ok, msg, data = _post_ncmr_by_lot(pairs)
    if data is None:
        return False, msg, None
    return ok, f'{label}：{msg}', data


def _eng_pqc_where():
    """尚未上报的 ENG 预 Hold：创建态、ENG 路线、FPQC/PQC、原因含 FUTURE。"""
    return """
        NVL(r.STATUS, 0) = 0
        AND UPPER(NVL(r.ROUTE_ID, '')) LIKE '%ENG%'
        AND UPPER(NVL(r.STATION, '')) IN ('FPQC', 'PQC')
        AND UPPER(NVL(r.HOLD_REASON, '')) LIKE '%FUTURE%'
    """


def _query_unreported_eng_pqc_products(cursor):
    """有待上报记录的型号，按型号分批时先取这一份。"""
    record_table = _record_table()
    cursor.execute(
        f"""
        SELECT DISTINCT r.PRODUCT_ID
        FROM {record_table} r
        WHERE {_eng_pqc_where()}
        ORDER BY r.PRODUCT_ID
        """
    )
    products = []
    for row in cursor.fetchall() or []:
        product_id = str(row[0] or '').strip() if row else ''
        if product_id and product_id not in products:
            products.append(product_id)
    return products


def _query_unreported_eng_pqc(cursor, product_id=None):
    """状态仍是创建、ENG + FPQC/PQC + 原因含 FUTURE 的记录。可只查一个型号。"""
    record_table = _record_table()
    sql = f"""
        SELECT
            r.WAFER_ID,
            r.PRODUCT_ID,
            r.EQUIP_ID,
            TO_CHAR(r.HOLD_DTTM, 'YYYY-MM-DD HH24:MI:SS') AS HOLD_DATETIME,
            r.HOLD_REASON AS REASON,
            CASE
                WHEN UPPER(NVL(r.ROUTE_ID, '')) LIKE '%ENG%' THEN 'ENGINEERING'
                WHEN UPPER(NVL(r.ROUTE_ID, '')) LIKE '%MP%' THEN 'PRODUCTION'
            END AS ROUTE_TYPE,
            r.LOT_ID,
            r.ROUTE_ID,
            r.ID,
            r.STATION,
            NULL AS NCMR_REPORTED_AT,
            r.OA,
            r.WO
        FROM {record_table} r
        WHERE {_eng_pqc_where()}
    """
    params = {}
    product_id = (product_id or '').strip()
    if product_id:
        sql += " AND r.PRODUCT_ID = :product_id"
        params['product_id'] = product_id
    sql += " ORDER BY r.WAFER_ID, r.HOLD_DTTM, r.ID"
    cursor.execute(sql, params)
    return cursor.fetchall()


def _with_cursor(work):
    conn = _connect()
    try:
        return work(conn.cursor())
    finally:
        conn.close()


def _assemble_unreported_eng_pqc(cursor, product_id, extras):
    """组装一个型号的可上报行。返回 (pairs, skipped)。"""
    holds = _query_unreported_eng_pqc(cursor, product_id)
    caches = {'merge': {}, 'hold_wafers': {}, 'test_program': {}}
    pairs = []
    skipped = 0
    for hold in holds:
        excel, preview = _process_hold(cursor, hold, caches, extras)
        rid = preview.get('RECORD_ID')
        wo = str(preview.get('WO') or '').strip()
        sheet = preview.get('SHEET')
        if sheet and sheet != 'eng_pqc':
            skipped += 1
            logger.warning(
                'ENG预Hold PQC 跳过 id=%s wafer=%s：不属于预 Hold',
                rid,
                preview.get('WAFER_ID'),
            )
            continue
        if preview.get('ORDER_ERROR') or not wo or not preview.get('LOT_KEY'):
            skipped += 1
            logger.warning(
                'ENG预Hold PQC 跳过 id=%s wafer=%s：%s',
                rid,
                preview.get('WAFER_ID'),
                preview.get('ORDER_ERROR') or '缺少工单号或 lot',
            )
            continue
        pairs.append((excel, rid))
    return pairs, skipped


def _eng_pqc_result(count, product_count, lots_ok, lots_failed, reported_ids, marked, skipped, products_failed):
    return {
        'count': count,
        'product_count': product_count,
        'lots_ok': lots_ok,
        'lots_failed': lots_failed,
        'products_failed': products_failed,
        'reported_ids': reported_ids,
        'circulation_marked': marked,
        'skipped': skipped,
    }


def _eng_pqc_message(data):
    """给页面的短摘要。逐 lot 的长句留在 lots_failed，不拼进这句话。"""
    bits = []
    if data['count']:
        bits.append(f"已上报 {data['product_count']} 个型号、{data['count']} 条")
    if data['lots_failed']:
        bits.append(f"{len(data['lots_failed'])} 个 lot 失败")
    if data['products_failed']:
        bits.append(f"{len(data['products_failed'])} 个型号准备失败")
    if data['skipped']:
        bits.append(f"跳过 {data['skipped']} 条")
    if not bits:
        return '没有可上报的 ENG预Hold PQC'
    return '，'.join(bits)


def report_unreported_eng_pqc(**extras):
    """按型号分批：查一个型号、组装并上报，再处理下一个。没有工单号的跳过。"""
    try:
        products = _with_cursor(_query_unreported_eng_pqc_products)
    except Exception as exc:
        logger.error(f'ENG预Hold PQC 上报准备失败: {exc}', exc_info=True)
        return False, f'上报准备失败: {exc}', None
    skipped = 0
    posted_rows = 0
    product_count = 0
    lots_ok = []
    lots_failed = []
    reported_ids = []
    marked = 0
    products_failed = []
    for product_id in products:
        logger.info('ENG预Hold PQC 开始型号 %s', product_id)
        try:
            pairs, product_skipped = _with_cursor(
                lambda cursor, product_id=product_id: _assemble_unreported_eng_pqc(
                    cursor, product_id, extras,
                )
            )
        except Exception as exc:
            logger.error(
                'ENG预Hold PQC 型号 %s 准备失败: %s', product_id, exc, exc_info=True,
            )
            products_failed.append({'product_id': product_id, 'msg': str(exc)})
            continue
        skipped += product_skipped
        if not pairs:
            continue
        ok, msg, data = _post_ncmr_by_lot(pairs)
        logger.info('ENG预Hold PQC 型号 %s：%s', product_id, msg)
        if not ok and not data:
            products_failed.append({'product_id': product_id, 'msg': msg})
            continue
        if data:
            posted_rows += data.get('count') or 0
            lots_ok.extend(data.get('lots_ok') or [])
            for item in data.get('lots_failed') or []:
                failed = dict(item)
                failed['product_id'] = product_id
                lots_failed.append(failed)
            reported_ids.extend(data.get('reported_ids') or [])
            marked += data.get('circulation_marked') or 0
        if ok:
            product_count += 1
        elif data and data.get('lots_ok'):
            product_count += 1
    result = _eng_pqc_result(
        posted_rows, product_count, lots_ok, lots_failed,
        reported_ids, marked, skipped, products_failed,
    )
    message = _eng_pqc_message(result)
    if lots_failed or products_failed:
        return False, message, result
    return True, message, result


def _record_item(hold):
    """原始 FT_HOLD_RECORD 行，不做合批 / 测试程序 / 预处置人整合。"""
    record_id = hold[8] if len(hold) > 8 else None
    if record_id is not None:
        try:
            record_id = int(record_id)
        except (TypeError, ValueError):
            pass
    route_type = hold[5] if len(hold) > 5 else None
    return {
        'wafer_id': str(hold[0] or '').strip(),
        'product_id': str(hold[1] or '').strip(),
        'equip_id': str(hold[2] or '').strip(),
        'hold_datetime': hold[3] or '',
        'reason': hold[4] or '',
        'route_type': route_type or '',
        'lot_id': str(hold[6] or '').strip() if len(hold) > 6 else '',
        'route_id': str(hold[7] or '').strip() if len(hold) > 7 else '',
        'station': str(hold[9] or '').strip() if len(hold) > 9 else '',
        'id': record_id,
        'ncmr_reported_at': _hold_text(hold, 10),
        'oa': _hold_text(hold, 11),
        'wo': _hold_text(hold, 12),
    }


def _run_query(work):
    conn = _connect()
    try:
        return work(conn.cursor())
    except Exception as e:
        logger.error(f'Hold Info 查询失败: {e}', exc_info=True)
        return False, f'查询失败: {e}', None
    finally:
        conn.close()


def _owned_product(product_id, owner_eng_id=None):
    product_id = (product_id or '').strip()
    if not product_id:
        return False, '请指定型号 PRODUCT_ID', None
    ok, msg = _ensure_owned_product(product_id, owner_eng_id)
    if not ok:
        return False, msg, None
    return True, 'ok', product_id


def _clean_id_list(values, label):
    if values is None:
        values = []
    if not isinstance(values, list):
        return False, f'{label}无效', None
    cleaned = []
    seen = set()
    for raw in values:
        text = str(raw).strip() if raw is not None else ''
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    if len(cleaned) > LOOKUP_MAX_IDS:
        return False, f'{label}无效，一次最多 {LOOKUP_MAX_IDS} 个', None
    return True, 'ok', cleaned


def query_hold_info_records(
    product_id, start_dttm, end_dttm, lot_id='', route_id='',
    owner_eng_id=None,
):
    """按型号和时间返回原始 hold 行。去重、合批在客户端；ENG 预处置人另查。

    pie 字段现在是 MP 预处置人：非纯 ENG 查询时按型号调 get_ncmr_owner。
    """
    ok, msg, parsed = _parse_filters(
        product_id, start_dttm, end_dttm, lot_id=lot_id, route_id=route_id,
    )
    if not ok:
        return False, msg, None
    product_id, start, end, lot_id, route_id = parsed
    ok, msg = _ensure_owned_product(product_id, owner_eng_id)
    if not ok:
        return False, msg, None

    def work(cursor):
        holds = _query_hold_records(
            cursor, product_id, start, end,
            lot_id=lot_id, route_id=route_id,
            row_limit=RECORDS_MAX_ROWS + 1,
        )
        truncated = len(holds) > RECORDS_MAX_ROWS
        holds = holds[:RECORDS_MAX_ROWS]
        return True, '查询成功', {
            'items': [_record_item(hold) for hold in holds],
            'shown': len(holds) if not truncated else RECORDS_MAX_ROWS,
            'truncated': truncated,
            'pie': '',
            'product_id': product_id,
            'lot_id': lot_id,
            'route_id': route_id,
            'start_dttm': start,
            'end_dttm': end,
        }

    ok, msg, data = _run_query(work)
    if ok and isinstance(data, dict) and route_id != 'ENG':
        data['pie'] = lookup_ncmr_owner(product_id)
    return ok, msg, data


def query_pre_handlers(
    product_id, lot_ids, owner_eng_id=None, pre_handler_lot_ids=None,
):
    """按 lot 查 MES OA 单号。pre_handler_lot_ids 里的 lot 再拼预处置人。不连 Oracle。

    pre_handler_lot_ids 为 None 时，每个 lot 都拼预处置人。
    """
    ok, msg, product_id = _owned_product(product_id, owner_eng_id)
    if not ok:
        return False, msg, None
    ok, msg, lot_ids = _clean_id_list(lot_ids, 'lot_ids')
    if not ok:
        return False, msg, None
    if pre_handler_lot_ids is None:
        name_ids = list(lot_ids)
    else:
        ok, msg, name_ids = _clean_id_list(pre_handler_lot_ids, 'pre_handler_lot_ids')
        if not ok:
            return False, msg, None
    if not lot_ids:
        return True, '查询成功', {
            'product_id': product_id,
            'pre_handlers': {},
            'oa_numbers': {},
        }
    name_set = set(name_ids)

    def _one(lot_id):
        oa_cache = {}
        oa_number = lookup_lot_oa(lot_id, oa_cache)
        name = ''
        if lot_id in name_set:
            name = lookup_pre_handler(lot_id, oa_cache=oa_cache) or ''
        return oa_number, name

    workers = min(8, len(lot_ids))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        values = list(pool.map(_one, lot_ids))
    return True, '查询成功', {
        'product_id': product_id,
        'pre_handlers': {lot_id: name for lot_id, (_oa, name) in zip(lot_ids, values)},
        'oa_numbers': {lot_id: oa for lot_id, (oa, _name) in zip(lot_ids, values)},
    }


def query_merge_sources(product_id, wafer_ids, owner_eng_id=None):
    """按目标片查 SPLIT_MERGE_HISTORY，不还原、不和 hold 行合并。"""
    ok, msg, product_id = _owned_product(product_id, owner_eng_id)
    if not ok:
        return False, msg, None
    ok, msg, wafer_ids = _clean_id_list(wafer_ids, 'wafer_ids')
    if not ok:
        return False, msg, None
    if not wafer_ids:
        return True, '查询成功', {'product_id': product_id, 'sources': {}}

    def work(cursor):
        cache = {}
        sources = {}
        for wafer_id in wafer_ids:
            sources[wafer_id] = list(_query_merge_sources(cursor, wafer_id, cache))
        return True, '查询成功', {'product_id': product_id, 'sources': sources}

    return _run_query(work)


def _query_hold_wafer_pairs(cursor, lot):
    """原始 LOT_ID / WAFER_ID，不展开 # 片号，也不和源片取交集。"""
    record_table = _record_table()
    cursor.execute(
        f"""
        SELECT LOT_ID, WAFER_ID
        FROM {record_table}
        WHERE LOT_ID LIKE :pat OR WAFER_ID LIKE :pat
        """,
        {'pat': f'{lot}%'},
    )
    pairs = []
    seen = set()
    for lot_id, wafer_id in cursor.fetchall():
        item = (
            str(lot_id or '').strip(),
            str(wafer_id or '').strip(),
        )
        if item in seen:
            continue
        seen.add(item)
        pairs.append({'lot_id': item[0], 'wafer_id': item[1]})
    return pairs


def query_hold_wafers(product_id, lots, owner_eng_id=None):
    """按 lot 前缀返回原始片号行。SQL 不按型号过滤。"""
    ok, msg, product_id = _owned_product(product_id, owner_eng_id)
    if not ok:
        return False, msg, None
    ok, msg, lots = _clean_id_list(lots, 'lots')
    if not ok:
        return False, msg, None
    if not lots:
        return True, '查询成功', {'product_id': product_id, 'wafers': {}}

    def work(cursor):
        wafers = {}
        for lot in lots:
            wafers[lot] = _query_hold_wafer_pairs(cursor, lot)
        return True, '查询成功', {'product_id': product_id, 'wafers': wafers}

    return _run_query(work)


def _clean_program_items(items):
    if items is None:
        items = []
    if not isinstance(items, list):
        return False, 'items无效', None
    cleaned = []
    seen = set()
    for raw in items:
        if not isinstance(raw, dict):
            return False, 'items无效', None
        wafer_id = str(raw.get('wafer_id') or '').strip()
        route = str(raw.get('route') or '').strip()
        if not wafer_id or not route:
            continue
        key = (wafer_id, route)
        if key in seen:
            continue
        seen.add(key)
        cleaned.append({'wafer_id': wafer_id, 'route': route})
    if len(cleaned) > LOOKUP_MAX_IDS:
        return False, f'items无效，一次最多 {LOOKUP_MAX_IDS} 个', None
    return True, 'ok', cleaned


def query_test_programs(product_id, items, owner_eng_id=None):
    """按片号 + ROUTE（PRODUCTION / ENGINEERING）查 TEST_WAFER.TEST_PROGRAM。"""
    ok, msg, product_id = _owned_product(product_id, owner_eng_id)
    if not ok:
        return False, msg, None
    ok, msg, items = _clean_program_items(items)
    if not ok:
        return False, msg, None
    if not items:
        return True, '查询成功', {'product_id': product_id, 'programs': []}

    def work(cursor):
        cache = {}
        programs = []
        for item in items:
            program = _query_test_program(
                cursor, item['wafer_id'], item['route'], cache,
            )
            programs.append({
                'wafer_id': item['wafer_id'],
                'route': item['route'],
                'test_program': program,
            })
        return True, '查询成功', {
            'product_id': product_id,
            'programs': programs,
        }

    return _run_query(work)
