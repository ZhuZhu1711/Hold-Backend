"""异常反馈：按处置单提交上游，成功后记下反馈单号。"""
import logging

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app import db
from app.utils.abnormal_feedback import post_abnormal_feedback
from app.utils.database_util import resolve_hold_record_table

logger = logging.getLogger(__name__)

_NOT_DISPOSED = 0


def submit_abnormal_feedback(source=None, environ=None):
    """校验单据后提交上游。成功返回 (True, 200, msg, {hold_record_id, ncmr_no})。"""
    record_id, err = _record_id(source)
    if err:
        return False, 400, err, None

    try:
        record = _load_feedback_record(record_id)
    except SQLAlchemyError:
        logger.exception('读取处置单失败 record_id=%s', record_id)
        return False, 500, '读取处置单失败', None
    if record is None:
        return False, 404, 'hold_record 不存在', None
    if record['ncmr_no']:
        return False, 409, '已反馈上游，等待上游指令', None
    if record['status'] != _NOT_DISPOSED:
        return False, 400, '仅未处置的单据可反馈上游', None

    ok, http_status, msg, data = post_abnormal_feedback(source=source, environ=environ)
    if not ok:
        return ok, http_status, msg, data

    ncmr_no = str((data or {}).get('ncmr_no') or '').strip()
    if not ncmr_no:
        return False, 502, '上游未返回反馈单号', data

    if not _save_ncmr_no(record_id, ncmr_no):
        return False, 500, '反馈单号写入失败', {
            'hold_record_id': record_id,
            'ncmr_no': ncmr_no,
        }
    return True, 200, '已提交上游', {
        'hold_record_id': record_id,
        'ncmr_no': ncmr_no,
    }


def _record_id(source):
    body = source if isinstance(source, dict) else {}
    raw = body.get('hold_record_id')
    try:
        record_id = int(raw)
    except (TypeError, ValueError):
        return None, 'hold_record_id 无效'
    if record_id <= 0:
        return None, 'hold_record_id 无效'
    return record_id, None


def _load_feedback_record(record_id):
    table = resolve_hold_record_table()
    row = db.session.execute(
        text(f"""
            SELECT r.ID, r.STATUS, r.NCMR_NO
            FROM {table} r
            WHERE r.ID = :rid
        """),
        {'rid': record_id},
    ).fetchone()
    if not row:
        return None
    mapping = {str(key).upper(): value for key, value in row._mapping.items()}
    status_raw = mapping.get('STATUS')
    try:
        status = int(status_raw) if status_raw is not None else 0
    except (TypeError, ValueError):
        status = -1
    ncmr = mapping.get('NCMR_NO')
    return {
        'id': int(mapping['ID']),
        'status': status,
        'ncmr_no': '' if ncmr is None else str(ncmr).strip(),
    }


def _save_ncmr_no(record_id, ncmr_no) -> bool:
    table = resolve_hold_record_table()
    try:
        result = db.session.execute(
            text(f"""
                UPDATE {table}
                SET NCMR_NO = :ncmr_no
                WHERE ID = :rid
                  AND NVL(STATUS, 0) = 0
                  AND NCMR_NO IS NULL
            """),
            {'ncmr_no': ncmr_no, 'rid': record_id},
        )
        if not result.rowcount:
            db.session.rollback()
            logger.error(
                '异常反馈单号未写入 record_id=%s ncmr_no=%s',
                record_id, ncmr_no,
            )
            return False
        db.session.commit()
        return True
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception(
            '异常反馈单号写入失败 record_id=%s ncmr_no=%s',
            record_id, ncmr_no,
        )
        return False
