"""
晶圆测试数据查询控制器
"""
from app import db
from sqlalchemy import bindparam, desc, text
from app.models.rawdata import TestWafer, TestBincode
import json


def _compact_sql(sql: str) -> str:
    return ' '.join((sql or '').split())


def _trace_sql(sql_trace, sql, params=None, *, tag=''):
    """将本次执行的 SQL 追加到请求级 trace 列表（供 analysis 记日志）。"""
    if sql_trace is None:
        return
    entry = {
        'tag': tag or '',
        'sql': _compact_sql(sql),
        'params': dict(params or {}),
    }
    sql_trace.append(entry)


def get_wafer_yield_and_bin(wafer_id, operation_id):
    """
    根据 wafer_id 和 operation_id 查询最新一条测试记录
    返回良率和BIN码比率

    Args:
        wafer_id: 晶圆ID
        operation_id: 工序ID

    Returns:
        dict: 包含良率和BIN码比率的数据
    """
    # 1. 查询最新一条 wafer 记录（ID最大）
    wafer = (
        TestWafer.query
        .filter(
            TestWafer.WAFER_ID == wafer_id,
            TestWafer.OPERATION_ID == operation_id
        )
        .order_by(desc(TestWafer.ID))
        .first()
    )

    if not wafer:
        return None

    # 2. 计算良率：从 GRADES_QTY 中统计含'A'的等级数量 / GROSS_DIE
    yield_result = _calculate_yield(wafer)

    # 3. 查询 BIN码数据
    bincodes = (
        TestBincode.query
        .filter(TestBincode.TEST_WAFER_SEQ == wafer.ID)
        .all()
    )

    # 4. 计算BIN码比率
    bin_ratio = _calculate_bin_ratio(bincodes)

    # 5. 组装返回数据
    return {
        'wafer_id': wafer.WAFER_ID,
        'operation_id': wafer.OPERATION_ID,
        'ft_time': str(wafer.FT_TIME) if wafer.FT_TIME else None,
        'product_id': wafer.PRODUCT_ID,
        'yield': yield_result,
        'bin_ratio': bin_ratio
    }


def get_latest_defect_bincodes(wafer_id, operation_id, sql_trace=None):
    """
    查询指定 wafer 在某工序下最新一次测试的缺陷 BIN_CODE / BIN_CODE_QTY。

    Args:
        wafer_id: 晶圆 ID
        operation_id: 工序 ID（如 FATE-FA）
        sql_trace: 可选 list，追加本次 SQL

    Returns:
        (True, msg, data) 或 (False, msg, None)
        data: {bin_code: bin_code_qty, ...}
    """
    if wafer_id is None or not str(wafer_id).strip():
        return False, '请指定 wafer_id', None
    if operation_id is None or not str(operation_id).strip():
        return False, '请指定 operation_id', None

    wafer_id = str(wafer_id).strip()
    operation_id = str(operation_id).strip()
    params = {'wafer_id': wafer_id, 'operation_id': operation_id}

    sql = """
        SELECT
            atb.BIN_CODE,
            atb.BIN_CODE_QTY
        FROM TEST_BINCODE atb
        INNER JOIN (
            SELECT atw.id, atw.product_id
            FROM TEST_WAFER atw
            WHERE atw.WAFER_ID = :wafer_id
              AND atw.operation_id = :operation_id
            ORDER BY atw.id DESC
            FETCH FIRST 1 ROW ONLY
        ) latest_wafer ON atb.TEST_WAFER_SEQ = latest_wafer.id
        INNER JOIN PRODUCT_INFO pi2 ON latest_wafer.product_id = pi2.product_id
        GROUP BY
            atb.BIN_CODE,
            atb.BIN_CODE_QTY
        ORDER BY atb.BIN_CODE_QTY DESC NULLS LAST, atb.bin_code
    """
    _trace_sql(sql_trace, sql, params, tag='get_latest_defect_bincodes')

    try:
        rows = db.session.execute(text(sql), params).fetchall()
    except Exception as e:
        db.session.rollback()
        return False, f'查询失败: {e}', None

    # 按 qty 从高到低插入，保留顺序（Py3.7+ dict）
    data = {}
    for bin_code, bin_code_qty in rows:
        if bin_code is None:
            continue
        data[str(int(bin_code))] = int(bin_code_qty) if bin_code_qty is not None else 0

    return True, '获取成功', data


def query_wafer_ids_by_prefix(prefix, operation_id=None, sql_trace=None):
    """
    按 WAFER_ID 前缀枚举 TEST_WAFER 中的片号（DISTINCT）。

    Args:
        prefix: lot 前缀，如 C123456；匹配 WAFER_ID LIKE 'prefix%'
        operation_id: 可选；有则限制 OPERATION_ID
        sql_trace: 可选 list，追加本次 SQL

    Returns:
        list[str]；失败或空前缀返回 []
    """
    prefix = str(prefix).strip() if prefix is not None else ''
    if not prefix:
        return []

    params = {'prefix': f'{prefix}%'}
    sql = """
        SELECT DISTINCT atw.WAFER_ID
        FROM TEST_WAFER atw
        WHERE atw.WAFER_ID LIKE :prefix
    """
    if operation_id is not None and str(operation_id).strip():
        sql += " AND atw.OPERATION_ID = :operation_id"
        params['operation_id'] = str(operation_id).strip()
    sql += " ORDER BY atw.WAFER_ID"
    _trace_sql(sql_trace, sql, params, tag='query_wafer_ids_by_prefix')

    try:
        rows = db.session.execute(text(sql), params).fetchall()
    except Exception:
        db.session.rollback()
        return []

    result = []
    for (wafer_id,) in rows:
        if wafer_id is None:
            continue
        text_id = str(wafer_id).strip()
        if text_id:
            result.append(text_id)
    return result


def query_same_lot_bincodes_by_prefixes(prefixes, operation_id, sql_trace=None):
    """
    一次查出多个 lot 前缀下、指定工序的最新缺陷 BIN 与 GROSS_DIE。

    用 ROW_NUMBER 取每片最新 TEST_WAFER，再 LEFT JOIN TEST_BINCODE。
    去掉 PRODUCT_INFO 内连接以降低开销。

    Returns:
        (wafer_ids_ordered, raw_by_wafer, die_by_wafer)
        wafer_ids_ordered: list[str]
        raw_by_wafer: {wafer_id: {bin_code: qty}}
        die_by_wafer: {wafer_id: gross_die|None}
        失败返回 ([], {}, {})
    """
    seen = set()
    clean_prefixes = []
    for p in prefixes or []:
        text_p = str(p).strip() if p is not None else ''
        if not text_p or text_p in seen:
            continue
        seen.add(text_p)
        clean_prefixes.append(text_p)

    if not clean_prefixes:
        return [], {}, {}
    if operation_id is None or not str(operation_id).strip():
        return [], {}, {}

    operation_id = str(operation_id).strip()
    params = {'operation_id': operation_id}
    like_parts = []
    for i, pref in enumerate(clean_prefixes):
        key = f'p{i}'
        like_parts.append(f'atw.WAFER_ID LIKE :{key}')
        params[key] = f'{pref}%'
    like_sql = ' OR '.join(like_parts)

    sql = f"""
        SELECT
            latest.WAFER_ID,
            latest.GROSS_DIE,
            atb.BIN_CODE,
            atb.BIN_CODE_QTY
        FROM (
            SELECT
                atw.WAFER_ID,
                atw.ID AS id,
                atw.GROSS_DIE,
                ROW_NUMBER() OVER (
                    PARTITION BY atw.WAFER_ID
                    ORDER BY atw.ID DESC
                ) AS rn
            FROM TEST_WAFER atw
            WHERE atw.OPERATION_ID = :operation_id
              AND ({like_sql})
        ) latest
        LEFT JOIN TEST_BINCODE atb ON atb.TEST_WAFER_SEQ = latest.id
        WHERE latest.rn = 1
        ORDER BY latest.WAFER_ID, atb.BIN_CODE_QTY DESC NULLS LAST, atb.BIN_CODE
    """
    _trace_sql(
        sql_trace, sql, params,
        tag='query_same_lot_bincodes_by_prefixes',
    )

    try:
        rows = db.session.execute(text(sql), params).fetchall()
    except Exception:
        db.session.rollback()
        return [], {}, {}

    raw_by_wafer = {}
    die_by_wafer = {}
    wafer_ids_ordered = []
    for wafer_id, gross_die, bin_code, bin_code_qty in rows:
        if wafer_id is None:
            continue
        wid = str(wafer_id).strip()
        if not wid:
            continue
        if wid not in raw_by_wafer:
            raw_by_wafer[wid] = {}
            wafer_ids_ordered.append(wid)
            if gross_die is not None:
                try:
                    die_by_wafer[wid] = int(gross_die)
                except (TypeError, ValueError):
                    die_by_wafer[wid] = None
            else:
                die_by_wafer[wid] = None
        if bin_code is None:
            continue
        raw_by_wafer[wid][str(int(bin_code))] = (
            int(bin_code_qty) if bin_code_qty is not None else 0
        )

    return wafer_ids_ordered, raw_by_wafer, die_by_wafer


def get_latest_defect_bincodes_for_wafers(wafer_ids, operation_id, sql_trace=None):
    """
    批量查询多片最新缺陷 BIN（IN 列表）及 GROSS_DIE。

    Returns:
        (raw_by_wafer, die_by_wafer)
        raw_by_wafer: {wafer_id: {bin_code: qty}}
        die_by_wafer: {wafer_id: gross_die|None}
        失败或空输入返回 ({}, {})
    """
    ids = []
    seen = set()
    for w in wafer_ids or []:
        text_w = str(w).strip() if w is not None else ''
        if not text_w or text_w in seen:
            continue
        seen.add(text_w)
        ids.append(text_w)

    if not ids:
        return {}, {}
    if operation_id is None or not str(operation_id).strip():
        return {}, {}

    operation_id = str(operation_id).strip()
    sql = """
        SELECT
            latest.WAFER_ID,
            latest.GROSS_DIE,
            atb.BIN_CODE,
            atb.BIN_CODE_QTY
        FROM (
            SELECT
                atw.WAFER_ID,
                atw.ID AS id,
                atw.GROSS_DIE,
                ROW_NUMBER() OVER (
                    PARTITION BY atw.WAFER_ID
                    ORDER BY atw.ID DESC
                ) AS rn
            FROM TEST_WAFER atw
            WHERE atw.OPERATION_ID = :operation_id
              AND atw.WAFER_ID IN :wafer_ids
        ) latest
        LEFT JOIN TEST_BINCODE atb ON atb.TEST_WAFER_SEQ = latest.id
        WHERE latest.rn = 1
        ORDER BY latest.WAFER_ID, atb.BIN_CODE_QTY DESC NULLS LAST, atb.BIN_CODE
    """
    params = {'operation_id': operation_id, 'wafer_ids': ids}
    _trace_sql(
        sql_trace, sql, params,
        tag='get_latest_defect_bincodes_for_wafers',
    )

    stmt = text(sql).bindparams(bindparam('wafer_ids', expanding=True))
    try:
        rows = db.session.execute(stmt, params).fetchall()
    except Exception:
        db.session.rollback()
        return {}, {}

    raw_by_wafer = {}
    die_by_wafer = {}
    for wafer_id, gross_die, bin_code, bin_code_qty in rows:
        if wafer_id is None:
            continue
        wid = str(wafer_id).strip()
        if not wid:
            continue
        if wid not in raw_by_wafer:
            raw_by_wafer[wid] = {}
            if gross_die is not None:
                try:
                    die_by_wafer[wid] = int(gross_die)
                except (TypeError, ValueError):
                    die_by_wafer[wid] = None
            else:
                die_by_wafer[wid] = None
        if bin_code is None:
            continue
        raw_by_wafer[wid][str(int(bin_code))] = (
            int(bin_code_qty) if bin_code_qty is not None else 0
        )
    return raw_by_wafer, die_by_wafer


def _calculate_yield(wafer):
    """
    计算良率
    从 GRADES_QTY JSON中，把所有含字母'A'的等级数量求和，除以 GROSS_DIE

    Args:
        wafer: TestWafer 对象

    Returns:
        dict: 良率计算详情
    """
    if not wafer.GRADES_QTY or not wafer.GROSS_DIE or wafer.GROSS_DIE == 0:
        return {
            'yield_rate': 0.0,
            'pass_die': 0,
            'gross_die': wafer.GROSS_DIE or 0
        }

    try:
        grades = json.loads(wafer.GRADES_QTY)
    except (json.JSONDecodeError, TypeError):
        return {
            'yield_rate': 0.0,
            'pass_die': 0,
            'gross_die': wafer.GROSS_DIE
        }

    # 统计含'A'的等级数量
    pass_die = sum(qty for grade, qty in grades.items() if 'A' in grade.upper())

    # 计算良率百分比，保留2位小数
    yield_rate = round(pass_die / wafer.GROSS_DIE * 100, 2)

    return {
        'yield_rate': yield_rate,
        'pass_die': pass_die,
        'gross_die': wafer.GROSS_DIE
    }


def _calculate_bin_ratio(bincodes):
    """
    计算BIN码比率
    每个BIN_CODE的数量占总数量的百分比

    Args:
        bincodes: TestBincode 对象列表

    Returns:
        dict: BIN码比率，key为BIN_CODE，value为百分比
    """
    if not bincodes:
        return {}

    # 计算总数量
    total_qty = sum(b.BIN_CODE_QTY or 0 for b in bincodes)
    
    if total_qty == 0:
        return {}
    
    # 计算每个BIN的比率
    bin_ratio = {}
    for b in bincodes:
        if b.BIN_CODE is not None:
            qty = b.BIN_CODE_QTY or 0
            ratio = round(qty / total_qty * 100, 2)
            bin_ratio[str(b.BIN_CODE)] = ratio
    
    return bin_ratio
