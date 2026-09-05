"""手提 Hold Record 创建（后台页 + 外部 API）。

当前已下架：创建 / 附件 FTP 上传下载接口返回 410。
探活 `GET /api/common_data/ftp/status` 不受影响。
"""
from datetime import datetime
import io
import logging
import zipfile

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app import db
from app.config import Config
from app.controllers.hold_report_ctrl import RECORD_TYPE_LABELS, _row_to_dict
from app.utils.annex_util import (
    ANNEX_FTP_PATH_MAX,
    ANNEX_MAX_FILES,
    FT_MANUAL_HOLD_CODES,
    HOLD_CODE_AQL,
    RECORD_TYPE_FT,
    RECORD_TYPE_WLT,
    SOURCE_MANUAL,
    WLT_MANUAL_HOLD_CODES,
    WLT_MANUAL_STATION,
    annex_line_from_record,
    annex_mimetype,
    download_annex_bytes,
    format_wlt_lot_id,
    format_wlt_wafer_id,
    ft_manual_stations,
    hold_code_is_aql,
    join_annex_ftp_paths,
    parse_annex_ftp_paths,
    parse_wlt_wafer_nos,
    product_suffix_for_line,
    sanitize_client_annex_paths,
    upload_annex_files,
)
from app.utils.database_util import (
    insert_manual_hold_record,
    resolve_hold_record_table,
    update_manual_hold_annex_path,
    compute_hold_wafer_attr,
)

logger = logging.getLogger(__name__)

# 手提 Hold / 附件 FTP 上传下载已下架；探活接口不走这里。
TAKEN_DOWN = True
TAKEN_DOWN_MSG = '手提 Hold 功能已下架'
ANNEX_FTP_TAKEN_DOWN_MSG = '附件 FTP 上传/下载已关闭'

_LINE_FT = 'FT'
_LINE_WLT = 'WLT'


def gone_response(msg=None):
    """HTTP 410 JSON，供路由直接返回。"""
    from flask import jsonify
    text = msg or TAKEN_DOWN_MSG
    return jsonify({'code': 410, 'msg': text, 'data': None}), 410


def _s(raw, key, default=None):
    val = raw.get(key, default) if isinstance(raw, dict) else default
    if val is None:
        return None
    text = str(val).strip()
    return text if text != '' else None


def _default_status():
    try:
        return int(getattr(Config, 'HOLD_RECORD_STATUS', 0) or 0)
    except (TypeError, ValueError):
        return 0


def _parse_hold_dttm(raw):
    if isinstance(raw, datetime):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    for fmt in (
        '%Y-%m-%d %H:%M:%S',
        '%Y/%m/%d %H:%M:%S',
        '%Y-%m-%dT%H:%M:%S',
        '%Y-%m-%d',
    ):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _product_candidates(line, keyword='', owner_eng_id=None) -> list:
    suffix = product_suffix_for_line(line)
    sql = """
        SELECT PRODUCT_ID
        FROM PRODUCT_INFO
        WHERE PRODUCT_ID IS NOT NULL
          AND PRODUCT_ID LIKE :suffix
    """
    params = {'suffix': f'%{suffix}'}
    if owner_eng_id is not None:
        sql += " AND PRO_ENG_ID = :eid"
        params['eid'] = int(owner_eng_id)
    kw = (keyword or '').strip()
    if kw:
        sql += " AND UPPER(PRODUCT_ID) LIKE UPPER(:kw)"
        params['kw'] = f'%{kw}%'
    sql += " ORDER BY PRODUCT_ID"
    rows = db.session.execute(text(sql), params).fetchall()
    return [str(r[0]).strip() for r in rows if r and r[0]]


def list_manual_hold_products(line, keyword='', owner_eng_id=None) -> tuple:
    if TAKEN_DOWN:
        return False, TAKEN_DOWN_MSG, []
    line_u = (line or '').strip().upper()
    if line_u not in (_LINE_FT, _LINE_WLT):
        return False, 'line 须为 FT 或 WLT', []
    try:
        return True, '获取成功', _product_candidates(line_u, keyword, owner_eng_id)
    except (TypeError, ValueError, SQLAlchemyError) as e:
        db.session.rollback()
        return False, f'查询型号失败: {e}', []


def resolve_manual_product_id(line, raw, owner_eng_id=None) -> tuple:
    """
    精确匹配，否则唯一包含/前缀命中则采用。
    成功 (True, product_id)；失败 (False, msg)。
    """
    text = (raw or '').strip()
    if not text:
        return False, '缺少必填字段: product_id'
    suffix = product_suffix_for_line(line)
    try:
        candidates = _product_candidates(line, owner_eng_id=owner_eng_id)
    except (TypeError, ValueError, SQLAlchemyError) as e:
        db.session.rollback()
        return False, f'查询型号失败: {e}'

    upper = text.upper()
    exact = [p for p in candidates if p.upper() == upper]
    if exact:
        return True, exact[0]
    prefix = [p for p in candidates if p.upper().startswith(upper)]
    if len(prefix) == 1:
        return True, prefix[0]
    contains = [p for p in candidates if upper in p.upper()]
    if len(contains) == 1:
        return True, contains[0]
    if len(prefix) > 1 or len(contains) > 1:
        return False, '匹配到多个型号，请选择完整 PRODUCT_ID'
    if text.endswith(suffix):
        return True, text
    return False, f'未匹配到{line}型号（须 {suffix}）'


def normalize_manual_hold(raw: dict) -> tuple:
    """
    规范化手提入参。
    成功 (True, '', record_dict)；失败 (False, msg, None)。
    """
    if not isinstance(raw, dict):
        return False, '请求体须为对象', None

    line = (_s(raw, 'line') or _s(raw, 'LINE') or '').upper()
    product_id = _s(raw, 'product_id') or _s(raw, 'PRODUCT_ID')
    station = _s(raw, 'station') or _s(raw, 'STATION')
    equip_id = _s(raw, 'equip_id') or _s(raw, 'EQUIP_ID')
    lot_id = _s(raw, 'lot_id') or _s(raw, 'LOT_ID')
    wafer_id = _s(raw, 'wafer_id') or _s(raw, 'WAFER_ID')
    hold_reason = _s(raw, 'hold_reason') or _s(raw, 'HOLD_REASON')
    hold_code = _s(raw, 'hold_code') or _s(raw, 'HOLD_CODE')
    second_code = _s(raw, 'second_code') or _s(raw, 'SECOND_CODE')
    route_id = _s(raw, 'route_id') or _s(raw, 'ROUTE_ID')
    grade_num = _s(raw, 'grade_num') or _s(raw, 'GRADE_NUM')

    if line not in (_LINE_FT, _LINE_WLT):
        return False, 'line 须为 FT 或 WLT', None

    extra_nos = raw.get('wafer_nos') or raw.get('WAFER_NOS') or raw.get('wafer_no')
    if extra_nos is not None and not isinstance(extra_nos, (list, tuple)):
        extra_nos = [extra_nos]
    wlt_nos = parse_wlt_wafer_nos(wafer_id, extra_nos) if line == _LINE_WLT else []
    if line == _LINE_WLT and wlt_nos:
        wafer_id = format_wlt_wafer_id(wlt_nos)

    missing = []
    for key, val in (
        ('product_id', product_id),
        ('equip_id', equip_id),
        ('hold_reason', hold_reason),
    ):
        if not val:
            missing.append(key)
    if line == _LINE_FT:
        if not lot_id and not wafer_id:
            missing.append('lot_id')
        if not station:
            missing.append('station')
    elif not lot_id:
        missing.append('lot_id')
    if missing:
        return False, f'缺少必填字段: {", ".join(missing)}', None

    pid = product_id.strip()
    code = (hold_code or '').strip()
    if line == _LINE_FT:
        if not pid.endswith('-3.5'):
            return False, 'FT 手提要求 PRODUCT_ID 以 -3.5 结尾', None
        if not code:
            code = HOLD_CODE_AQL
        if code not in FT_MANUAL_HOLD_CODES:
            allowed = ' / '.join(sorted(FT_MANUAL_HOLD_CODES))
            return False, f'FT 手提 HOLD_CODE 须为 {allowed}', None
        hold_code = code
        record_type = RECORD_TYPE_FT
        allowed_st = ft_manual_stations()
        if station not in allowed_st:
            return False, f'FT 手提 STATION 须为 {" / ".join(allowed_st)}', None
        if not wafer_id:
            wafer_id = lot_id
        elif not lot_id:
            lot_id = wafer_id
        elif lot_id != wafer_id:
            return False, 'FT 手提 LOT_ID 须与 WAFER_ID 相同', None
    else:
        if not pid.endswith('-2.6'):
            return False, 'WLT 手提要求 PRODUCT_ID 以 -2.6 结尾', None
        if code not in WLT_MANUAL_HOLD_CODES:
            allowed = ' / '.join(sorted(WLT_MANUAL_HOLD_CODES))
            return False, f'WLT 手提 HOLD_CODE 须为 {allowed}', None
        hold_code = code
        record_type = RECORD_TYPE_WLT
        station = WLT_MANUAL_STATION
        if not wlt_nos:
            return False, 'WLT 手提须勾选片号（1~25）', None
        wafer_id = format_wlt_wafer_id(wlt_nos)
        lot_id = format_wlt_lot_id(lot_id, wlt_nos[0])
        if not lot_id:
            return False, 'WLT 手提 LOT_ID 格式为 LOT.NO（NO 为本 lot 第一片）', None

    annex_paths = []
    annex_raw = _s(raw, 'annex_ftp_path') or _s(raw, 'ANNEX_FTP_PATH')
    if annex_raw:
        annex_paths.extend(parse_annex_ftp_paths(annex_raw))
    extra = raw.get('annex_paths') or raw.get('ANNEX_PATHS')
    if isinstance(extra, list):
        annex_paths.extend(str(p).strip() for p in extra if str(p).strip())
    try:
        annex_paths = sanitize_client_annex_paths(annex_paths, line=line)
    except ValueError as e:
        return False, str(e), None
    annex_ftp_path = join_annex_ftp_paths(annex_paths)
    if len(annex_paths) > ANNEX_MAX_FILES:
        return False, f'图片最多 {ANNEX_MAX_FILES} 张', None

    record = {
        'PRODUCT_ID': pid,
        'STATION': station,
        'EQUIP_ID': equip_id,
        'LOT_ID': lot_id,
        'WAFER_ID': wafer_id,
        'HOLD_CODE': hold_code,
        'HOLD_REASON': hold_reason[:512],
        'SOURCE': SOURCE_MANUAL,
        'SECOND_CODE': second_code,
        'ROUTE_ID': route_id,
        'GRADE_NUM': grade_num,
        'RECORD_TYPE': record_type,
        'STATUS': _default_status(),
        'HOLD_DTTM': _parse_hold_dttm(raw.get('hold_dttm') or raw.get('HOLD_DTTM')),
        'ANNEX_FTP_PATH': annex_ftp_path,
        'HOLD_WAFER_ATTR': compute_hold_wafer_attr(lot_id, equip_id, station),
        '_LINE': line,
    }
    return True, '', record


def _engineer_owns_product(eng_user_id, product_id) -> bool:
    if not eng_user_id or not product_id:
        return False
    try:
        row = db.session.execute(
            text("""
                SELECT 1
                FROM PRODUCT_INFO
                WHERE PRODUCT_ID = :pid
                  AND PRO_ENG_ID = :eid
                  AND ROWNUM = 1
            """),
            {'pid': str(product_id).strip(), 'eid': int(eng_user_id)},
        ).fetchone()
        return row is not None
    except (TypeError, ValueError, SQLAlchemyError):
        db.session.rollback()
        return False


def create_manual_hold(raw: dict, uploaded_files=None, operator='', actor_role=None, actor_user_id=None) -> tuple:
    """
    uploaded_files: list[(filename, bytes)]
    成功 (True, msg, data)；失败 (False, msg, None)。
    """
    if TAKEN_DOWN:
        return False, TAKEN_DOWN_MSG, None
    payload = dict(raw or {})
    line = (_s(payload, 'line') or _s(payload, 'LINE') or '').upper()
    owner_eng_id = None
    from app.utils.auth_decorators import ROLE_ENGINEER
    if actor_role == ROLE_ENGINEER:
        owner_eng_id = actor_user_id
    ok, resolved = resolve_manual_product_id(
        line,
        _s(payload, 'product_id') or _s(payload, 'PRODUCT_ID'),
        owner_eng_id=owner_eng_id,
    )
    if not ok:
        return False, resolved, None
    payload['product_id'] = resolved

    ok, msg, record = normalize_manual_hold(payload)
    if not ok:
        return False, msg, None

    if actor_role == ROLE_ENGINEER:
        if not _engineer_owns_product(actor_user_id, record['PRODUCT_ID']):
            return False, '不属于您负责的型号', None

    files = list(uploaded_files or [])
    existing = parse_annex_ftp_paths(record.get('ANNEX_FTP_PATH'))
    if len(existing) + len(files) > ANNEX_MAX_FILES:
        return False, f'图片最多 {ANNEX_MAX_FILES} 张', None
    existing_joined = join_annex_ftp_paths(existing)
    if existing_joined and len(existing_joined) > ANNEX_FTP_PATH_MAX:
        return False, f'附件路径超过 {ANNEX_FTP_PATH_MAX} 字符', None
    record['ANNEX_FTP_PATH'] = existing_joined

    table = resolve_hold_record_table()
    new_id = insert_manual_hold_record(record, record_table=table)
    if new_id is None:
        return False, '写入 Hold Record 失败', None

    paths = existing
    if files:
        try:
            uploaded = upload_annex_files(
                files,
                line=record.get('_LINE'),
                record_id=new_id,
            )
        except ValueError as e:
            return False, f'Hold Record 已创建(ID={new_id})，但附件上传失败: {e}', None
        merged = existing + uploaded
        joined = join_annex_ftp_paths(merged)
        if joined and len(joined) > ANNEX_FTP_PATH_MAX:
            return False, f'Hold Record 已创建(ID={new_id})，但附件路径超过 {ANNEX_FTP_PATH_MAX} 字符', None
        if not update_manual_hold_annex_path(new_id, joined, record_table=table):
            return False, f'Hold Record 已创建(ID={new_id})，但附件路径回写失败', None
        record['ANNEX_FTP_PATH'] = joined
        paths = merged
    data = {
        'ID': new_id,
        'PRODUCT_ID': record['PRODUCT_ID'],
        'STATION': record['STATION'],
        'LOT_ID': record['LOT_ID'],
        'WAFER_ID': record['WAFER_ID'],
        'HOLD_CODE': record['HOLD_CODE'],
        'HOLD_REASON': record['HOLD_REASON'],
        'SOURCE': SOURCE_MANUAL,
        'RECORD_TYPE': record['RECORD_TYPE'],
        'RECORD_TYPE_NAME': RECORD_TYPE_LABELS.get(record['RECORD_TYPE'], ''),
        'ANNEX_FTP_PATH': record.get('ANNEX_FTP_PATH'),
        'ANNEX_COUNT': len(paths),
    }
    op = f'，操作者={operator}' if operator else ''
    logger.info(f"手提 Hold 创建成功 id={new_id}{op}")
    return True, '创建成功', data


def list_recent_manual_holds(limit=20, owner_eng_id=None) -> tuple:
    if TAKEN_DOWN:
        return False, TAKEN_DOWN_MSG, []
    try:
        limit_n = max(1, min(int(limit or 20), 100))
    except (TypeError, ValueError):
        limit_n = 20
    record_table = resolve_hold_record_table()
    where_sql = "WHERE NVL(r.SOURCE, 0) = :src"
    params = {'src': SOURCE_MANUAL, 'lim': limit_n}
    if owner_eng_id is not None:
        where_sql += """
            AND r.PRODUCT_ID IN (
                SELECT p.PRODUCT_ID
                FROM PRODUCT_INFO p
                WHERE p.PRO_ENG_ID = :owner_eng_id
            )
        """
        params['owner_eng_id'] = int(owner_eng_id)
    try:
        rows = db.session.execute(
            text(f"""
                SELECT
                    r.ID, r.PRODUCT_ID, r.STATION, r.EQUIP_ID, r.LOT_ID, r.WAFER_ID,
                    r.HOLD_CODE, r.HOLD_REASON, r.SOURCE, r.RECORD_TYPE, r.STATUS,
                    r.HOLD_DTTM, r.ANNEX_FTP_PATH
                FROM {record_table} r
                {where_sql}
                ORDER BY r.ID DESC
                FETCH FIRST :lim ROWS ONLY
            """),
            params,
        ).fetchall()
        items = []
        for row in rows:
            item = _row_to_dict(row)
            rt = item.get('RECORD_TYPE')
            try:
                rt_key = int(rt) if rt is not None else None
            except (TypeError, ValueError):
                rt_key = None
            item['RECORD_TYPE_NAME'] = RECORD_TYPE_LABELS.get(rt_key, '-')
            item['IS_AQL_HOLD'] = hold_code_is_aql(item.get('HOLD_CODE'))
            item['ANNEX_COUNT'] = len(parse_annex_ftp_paths(item.get('ANNEX_FTP_PATH')))
            items.append(item)
        return True, '获取成功', items
    except SQLAlchemyError as e:
        db.session.rollback()
        return False, f'数据库查询异常: {e}', []
    except Exception as e:
        db.session.rollback()
        return False, f'查询失败: {e}', []


def manual_hold_page_options() -> dict:
    return {
        'ft_stations': list(ft_manual_stations()),
        'wlt_station': WLT_MANUAL_STATION,
        'annex_max_files': ANNEX_MAX_FILES,
    }


def get_annex_image(record_id, index=0) -> tuple:
    """
    成功 (True, msg, {'bytes': ..., 'mimetype': ..., 'filename': ...})
    失败 (False, msg, None)
    """
    if TAKEN_DOWN:
        return False, ANNEX_FTP_TAKEN_DOWN_MSG, None
    try:
        rid = int(record_id)
    except (TypeError, ValueError):
        return False, 'record_id 无效', None
    try:
        idx = int(index if index is not None else 0)
    except (TypeError, ValueError):
        return False, 'index 须为整数', None
    if idx < 0:
        return False, 'index 无效', None

    record_table = resolve_hold_record_table()
    try:
        row = db.session.execute(
            text(f"""
                SELECT ID, PRODUCT_ID, RECORD_TYPE, ANNEX_FTP_PATH
                FROM {record_table}
                WHERE ID = :rid
            """),
            {'rid': rid},
        ).fetchone()
    except SQLAlchemyError as e:
        db.session.rollback()
        return False, f'数据库查询异常: {e}', None
    if not row:
        return False, '记录不存在', None
    rec = _row_to_dict(row)
    paths = parse_annex_ftp_paths(rec.get('ANNEX_FTP_PATH'))
    if not paths:
        return False, '无附件图片', None
    if idx >= len(paths):
        return False, '附件序号超出范围', None
    ftp_path = paths[idx]
    line = annex_line_from_record(rec)
    try:
        data = download_annex_bytes(ftp_path, line=line)
    except ValueError as e:
        return False, str(e), None
    name = ftp_path.replace('\\', '/').rsplit('/', 1)[-1] or f'annex_{idx}'
    mime = annex_mimetype(name)
    if not mime.startswith('image/'):
        return False, '附件须为图片', None
    return True, 'ok', {
        'bytes': data,
        'mimetype': mime,
        'filename': name,
        'path': ftp_path,
    }


def get_annex_zip(record_id) -> tuple:
    """打包全部附件。成功 (True, msg, {'bytes','filename'})。"""
    if TAKEN_DOWN:
        return False, ANNEX_FTP_TAKEN_DOWN_MSG, None
    try:
        rid = int(record_id)
    except (TypeError, ValueError):
        return False, 'record_id 无效', None
    record_table = resolve_hold_record_table()
    try:
        row = db.session.execute(
            text(f"""
                SELECT ID, PRODUCT_ID, RECORD_TYPE, ANNEX_FTP_PATH
                FROM {record_table}
                WHERE ID = :rid
            """),
            {'rid': rid},
        ).fetchone()
    except SQLAlchemyError as e:
        db.session.rollback()
        return False, f'数据库查询异常: {e}', None
    if not row:
        return False, '记录不存在', None
    rec = _row_to_dict(row)
    paths = parse_annex_ftp_paths(rec.get('ANNEX_FTP_PATH'))
    if not paths:
        return False, '无附件图片', None
    line = annex_line_from_record(rec)
    buf = io.BytesIO()
    try:
        with zipfile.ZipFile(buf, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
            used = set()
            for idx, ftp_path in enumerate(paths):
                data = download_annex_bytes(ftp_path, line=line)
                name = str(ftp_path).replace('\\', '/').rsplit('/', 1)[-1] or f'annex_{idx}'
                if not annex_mimetype(name).startswith('image/'):
                    raise ValueError('附件须为图片')
                if name in used:
                    stem, dot, ext = name.rpartition('.')
                    name = f'{stem or name}_{idx}{dot}{ext}'
                used.add(name)
                zf.writestr(name, data)
    except ValueError as e:
        return False, str(e), None
    return True, 'ok', {
        'bytes': buf.getvalue(),
        'filename': f'hold_{rid}_annex.zip',
        'mimetype': 'application/zip',
    }
