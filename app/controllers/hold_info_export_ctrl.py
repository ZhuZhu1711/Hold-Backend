"""
按型号 + 时间从 FT_HOLD_RECORD 导出 NCMR 上报清单（原 HoldInfoExport / FT ATE Hold Lot）。

逻辑：
  1. 主数据取 FT_HOLD_RECORD，且当前仍在工程师节点（最新流转负责人是型号工程师；已关闭不取）
  2. ROUTE_ID 含 ENG → ENGINEERING，否则含 MP → PRODUCTION。ENG 优先。
  3. 合批片（LOT/WAFER 后缀位数>2）查 SPLIT_MERGE_HISTORY 还原源 lot / wafer
  4. 补 TEST_WAFER.TEST_PROGRAM
  5. 同一 wafer 多条 record 只保留最新一条（相同时间取 ID 更大的）；Hold Time 统一用该 lot 最早的 HOLD_DTTM
  6. 预处置人：ROUTE 含 ENG 时按 lot 查 OA（形如「周星辰（A0004655）」）；
     含 MP 的 PRODUCTION 用该型号族 PRODUCT_INFO.PIE，按 USERS 补成同样的「姓名（工号）」
  7. 末列 OA单号按 lot 查 MES get_lot_order 的 data.OA；没有记录留空
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
from app.controllers.product_ctrl import family_product_ids
from app.utils.ncmr_import import (
    DEFAULTS,
    NCMR_TIMEOUT,
    build_ncmr_import_body,
    ncmr_row_from_hold_record,
    post_ncmr_import,
    report_eng_future_to_ncmr,
    reset_ncmr_token_cache,
)
from app.utils.pre_handler_lookup import (
    format_pre_handler,
    format_work_code,
    looks_like_work_code,
    lookup_lot_oa,
    lookup_pre_handler,
    split_pre_handler,
)

EXPORT_MAX_ROWS = 5000
PREVIEW_MAX_ROWS = 100
SHEET_ENG_PQC = 'ENG预Hold PQC'
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


def is_eng_prehold_pqc(route_id, station):
    """ROUTE_ID 含 ENG 且 STATION 含 PQC。大小写不敏感。"""
    route = (route_id or '').strip().upper()
    site = (station or '').strip().upper()
    return 'ENG' in route and 'PQC' in site


def _sheet_key(route_id, station):
    """ENG 且 PQC 进预 Hold 表；不含 ENG 的 MP route 进 MP 工程测试表。其余不进任何表。"""
    if is_eng_prehold_pqc(route_id, station):
        return 'eng_pqc'
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
            END AS NCMR_REPORTED_AT
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


def _newest_pie(rows):
    """同族取更新时间较新的非空 PIE。空白不覆盖更早的有效值。"""
    chosen = ''
    chosen_key = None
    for row in rows:
        if not isinstance(row, (tuple, list)) or len(row) < 3:
            continue
        pie = str(row[0] or '').strip()
        if not pie:
            continue
        updated = str(row[1] or '')
        try:
            ident = int(row[2]) if row[2] is not None else 0
        except (TypeError, ValueError):
            ident = 0
        key = (updated, ident)
        if chosen_key is None or key > chosen_key:
            chosen = pie
            chosen_key = key
    return chosen


def _query_product_pie(cursor, product_id, cache):
    """按型号族查 PRODUCT_INFO.PIE。失败或未绑定返回空字符串，不中断导出。"""
    key = (product_id or '').strip()
    if not key:
        return ''
    if key in cache:
        return cache[key]
    value = ''
    try:
        ids = family_product_ids(key)
        binds = {f'p{index}': pid for index, pid in enumerate(ids)}
        placeholders = ', '.join(f':{name}' for name in binds)
        cursor.execute(
            f"""
            SELECT PIE, TO_CHAR(UPDATE_DTTM, 'YYYY-MM-DD'), ID
            FROM PRODUCT_INFO
            WHERE PRODUCT_ID IN ({placeholders})
            """,
            binds,
        )
        rows = cursor.fetchall()
        if isinstance(rows, list):
            value = _format_mp_pre_handler(cursor, _newest_pie(rows))
    except Exception as exc:
        logger.warning('型号 PIE 查询失败 product=%s: %s', key, exc)
        value = ''
    cache[key] = value
    return value


def _format_mp_pre_handler(cursor, pie: str) -> str:
    """PIE 是工号或姓名。导出成与 ENG 相同的「姓名（A0004655）」。对不上就留原值。"""
    text = (pie or '').strip()
    if not text:
        return ''
    person, code = split_pre_handler(text)
    if person and code:
        return format_pre_handler(person, code)
    if looks_like_work_code(text):
        return format_pre_handler(_lookup_user_name(cursor, text), text)
    employee_no = _lookup_employee_no(cursor, text)
    if not employee_no:
        return text
    return format_pre_handler(text, employee_no)


def _lookup_user_name(cursor, work_code: str) -> str:
    formatted = format_work_code(work_code)
    cursor.execute(
        """
        SELECT NAME
        FROM USERS
        WHERE EMPLOYEE_NO = :raw OR EMPLOYEE_NO = :formatted
        ORDER BY ID
        """,
        {'raw': work_code, 'formatted': formatted},
    )
    names = []
    for row in cursor.fetchall() or []:
        name = str(row[0] or '').strip() if row else ''
        if name and name not in names:
            names.append(name)
    if len(names) != 1:
        return ''
    return names[0]


def _lookup_employee_no(cursor, name: str) -> str:
    cursor.execute(
        """
        SELECT EMPLOYEE_NO
        FROM USERS
        WHERE NAME = :name
        ORDER BY ID
        """,
        {'name': name},
    )
    codes = []
    for row in cursor.fetchall() or []:
        code = str(row[0] or '').strip() if row else ''
        if looks_like_work_code(code) and code not in codes:
            codes.append(code)
    if len(codes) != 1:
        return ''
    return codes[0]


def _pre_handler_for(cursor, route, lot_id, product_id, caches):
    """ENG 走 OA；MP 用型号 PIE。"""
    if _is_engineering_route(route):
        return lookup_pre_handler(
            lot_id,
            caches.setdefault('pre_handler', {}),
            oa_cache=caches.setdefault('lot_oa', {}),
        )
    if _is_mp_route(route):
        return _query_product_pie(
            cursor, product_id, caches.setdefault('product_pie', {}),
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
    reported_at = str(hold[10]).strip() if len(hold) > 10 and hold[10] else ''

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
    oa_number = lookup_lot_oa(lot_id, caches.setdefault('lot_oa', {}))
    pre_handler = _pre_handler_for(cursor, route, lot_id, product_id, caches)
    lot_str = '/'.join(source_lots)
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
        lot_str,
        wafer_id,
        lot_str,
        None,
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
        'SHEET': _sheet_key(route_id, station),
        'LOT_STR': lot_str,
        'HOLD_WAFER_LIST': hold_wafer_list_str,
        'WAFER_LIST': wafer_list_str,
        'TEST_PROGRAM': test_pro,
        'PRE_HANDLER': pre_handler,
        'OA_NUMBER': oa_number,
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
    eng_preview, engineering_preview = _partition_preview(payload['preview_rows'])
    return True, '查询成功', {
        'items': payload['preview_rows'],
        'eng_pqc': eng_preview,
        'engineering': engineering_preview,
        'eng_pqc_total': len(eng_preview),
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


def _partition_preview(preview_rows):
    eng_rows = []
    engineering_rows = []
    for preview in preview_rows or []:
        sheet = preview.get('SHEET')
        if sheet == 'eng_pqc':
            eng_rows.append(preview)
        elif sheet == 'engineering':
            engineering_rows.append(preview)
    return eng_rows, engineering_rows


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
    node_rows = []
    for excel, preview in zip(excel_rows, preview_rows):
        sheet = preview.get('SHEET')
        if sheet == 'eng_pqc':
            reported = preview.get('NCMR_REPORTED_AT') or None
            eng_rows.append(list(excel) + [reported])
            continue
        if sheet != 'engineering':
            continue
        if engineering_ids is None or preview.get('RECORD_ID') in engineering_ids:
            node_rows.append(excel)
    return eng_rows, node_rows


_SHEET_TITLES = {
    'eng_pqc': SHEET_ENG_PQC,
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
    if title == SHEET_ENG_PQC:
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

    eng_rows, node_rows = _select_export_rows(
        payload['excel_rows'], payload['preview_rows'], id_set,
    )
    title = _SHEET_TITLES[sheet_key]
    rows = eng_rows if sheet_key == 'eng_pqc' else node_rows
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


def import_ncmr_rows(rows, record_ids=None):
    """客户端已组装好的当前表。成功后按 record_ids 写「已上报」流转。"""
    if isinstance(rows, (str, bytes)) or not isinstance(rows, list):
        return False, '上报行无效', None
    ok, msg, data = post_ncmr_import(rows)
    if not ok:
        return False, msg, None
    note, marked = _write_ncmr_circulations(record_ids)
    data = dict(data or {})
    data['circulation_marked'] = marked
    return True, f'{msg}{note}', data


def import_hold_info_ncmr(
    product_id, start_dttm, end_dttm, lot_id='', route_id='',
    owner_eng_id=None, engineering_ids=None, sheet='', **extras,
):
    """网页按筛选和勾选重新取当前表，再上报。ENG 预 Hold 只展示，不在这里上报。"""
    ok, msg, sheet_key = _normalize_sheet(sheet)
    if not ok:
        return False, msg, None
    if sheet_key == 'eng_pqc':
        return False, 'ENG预Hold PQC 由系统自动上报，不能手动上报', None
    ok, msg, rows, title, payload = _sheet_export_rows(
        product_id, start_dttm, end_dttm, lot_id=lot_id, route_id=route_id,
        owner_eng_id=owner_eng_id, engineering_ids=engineering_ids,
        sheet=sheet, **extras,
    )
    if not ok:
        return False, msg, None
    if not rows:
        return False, '没有可上报的行', None
    ok, msg, data = post_ncmr_import(rows)
    if not ok:
        return False, msg, None
    note, marked = _write_ncmr_circulations(
        _reported_record_ids(
            (payload or {}).get('preview_rows'), engineering_ids,
        ),
    )
    data = dict(data or {})
    data['circulation_marked'] = marked
    return True, f'{msg}（{len(rows)} 条，{title}）{note}', data


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
        'ncmr_reported_at': (
            str(hold[10]).strip() if len(hold) > 10 and hold[10] else ''
        ),
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
    """按型号和时间返回原始 hold 行，并带上型号 PIE。去重、合批在客户端；ENG 预处置人另查。"""
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
            'pie': _query_product_pie(cursor, product_id, {}),
            'product_id': product_id,
            'lot_id': lot_id,
            'route_id': route_id,
            'start_dttm': start,
            'end_dttm': end,
        }

    return _run_query(work)


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
