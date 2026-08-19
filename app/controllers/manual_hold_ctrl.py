"""手提 Hold Record 创建（后台页 + 外部 API）。"""
from datetime import datetime
import logging

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app import db
from app.config import Config
from app.controllers.hold_report_ctrl import RECORD_TYPE_LABELS, _row_to_dict
from app.utils.annex_util import (
    FT_MANUAL_HOLD_CODES,
    HOLD_CODE_AQL,
    RECORD_TYPE_FT,
    RECORD_TYPE_WLT,
    SOURCE_MANUAL,
    WLT_MANUAL_HOLD_CODES,
    annex_mimetype,
    download_annex_bytes,
    hold_code_is_aql,
    join_annex_ftp_paths,
    parse_annex_ftp_paths,
    upload_annex_files,
)
from app.utils.database_util import (
    insert_manual_hold_record,
    resolve_hold_record_table,
)

logger = logging.getLogger(__name__)

_LINE_FT = 'FT'
_LINE_WLT = 'WLT'


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
    missing = []
    for key, val in (
        ('product_id', product_id),
        ('equip_id', equip_id),
        ('lot_id', lot_id),
        ('wafer_id', wafer_id),
        ('hold_reason', hold_reason),
    ):
        if not val:
            missing.append(key)
    if line == _LINE_FT and not station:
        missing.append('station')
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
    else:
        if not pid.endswith('-2.6'):
            return False, 'WLT 手提要求 PRODUCT_ID 以 -2.6 结尾', None
        if code not in WLT_MANUAL_HOLD_CODES:
            allowed = ' / '.join(sorted(WLT_MANUAL_HOLD_CODES))
            return False, f'WLT 手提 HOLD_CODE 须为 {allowed}', None
        hold_code = code
        record_type = RECORD_TYPE_WLT
        if not station:
            station = 'WOQC'

    annex_paths = []
    annex_raw = _s(raw, 'annex_ftp_path') or _s(raw, 'ANNEX_FTP_PATH')
    if annex_raw:
        annex_paths.extend(parse_annex_ftp_paths(annex_raw))
    extra = raw.get('annex_paths') or raw.get('ANNEX_PATHS')
    if isinstance(extra, list):
        annex_paths.extend(str(p).strip() for p in extra if str(p).strip())
    annex_ftp_path = join_annex_ftp_paths(annex_paths)

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
    ok, msg, record = normalize_manual_hold(raw or {})
    if not ok:
        return False, msg, None

    from app.utils.auth_decorators import ROLE_ENGINEER
    if actor_role == ROLE_ENGINEER:
        if not _engineer_owns_product(actor_user_id, record['PRODUCT_ID']):
            return False, '不属于您负责的型号', None

    files = list(uploaded_files or [])
    if files:
        try:
            uploaded = upload_annex_files(
                files,
                product_id=record['PRODUCT_ID'],
                lot_id=record['LOT_ID'],
            )
        except ValueError as e:
            return False, str(e), None
        merged = parse_annex_ftp_paths(record.get('ANNEX_FTP_PATH')) + uploaded
        record['ANNEX_FTP_PATH'] = join_annex_ftp_paths(merged)

    new_id = insert_manual_hold_record(
        record,
        record_table=resolve_hold_record_table(),
    )
    if new_id is None:
        return False, '写入 Hold Record 失败', None

    paths = parse_annex_ftp_paths(record.get('ANNEX_FTP_PATH'))
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


def get_annex_image(record_id, index=0) -> tuple:
    """
    成功 (True, msg, {'bytes': ..., 'mimetype': ..., 'filename': ...})
    失败 (False, msg, None)
    """
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
                SELECT ID, ANNEX_FTP_PATH
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
    paths = parse_annex_ftp_paths(row._mapping.get('ANNEX_FTP_PATH'))
    if not paths:
        return False, '无附件图片', None
    if idx >= len(paths):
        return False, '附件序号超出范围', None
    ftp_path = paths[idx]
    try:
        data = download_annex_bytes(ftp_path)
    except ValueError as e:
        return False, str(e), None
    name = ftp_path.replace('\\', '/').rsplit('/', 1)[-1] or f'annex_{idx}'
    return True, 'ok', {
        'bytes': data,
        'mimetype': annex_mimetype(ftp_path),
        'filename': name,
        'path': ftp_path,
    }
