"""CP Hold 列表（只读）。不写 CP_HOLD_RECORD，也不走 FT 处置。"""
from datetime import date, datetime
import logging

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app import db
from app.config import Config

logger = logging.getLogger(__name__)

DISPOSE_CLOSE = 99

_ALLOWED_RECORD_TABLES = frozenset({'CP_HOLD_RECORD', 'CP_HOLD_RECORD_TEST'})
_ALLOWED_CIRC_TABLES = frozenset({
    'CP_CIRCULATION_HISTORY',
    'CP_CIRCULATION_HISTORY_TEST',
})

# 仅用于列表展示。转上海尚无行为码。
_DISPOSE_LABELS = {
    0: '创建',
    1: '放行',
    2: '降级',
    3: '重测',
    4: '加抽',
    5: '可靠性分析',
    7: '转交',
    8: '回退',
    65: '留样完成',
    99: '关闭',
}


def cp_table_names():
    """返回白名单内的 (record_table, circulation_table)。"""
    record = (getattr(Config, 'CP_HOLD_RECORD_TABLE', None) or 'CP_HOLD_RECORD').upper()
    circ = (
        getattr(Config, 'CP_CIRCULATION_TABLE', None) or 'CP_CIRCULATION_HISTORY'
    ).upper()
    if record not in _ALLOWED_RECORD_TABLES:
        raise ValueError(f'非法 CP_HOLD_RECORD 表名: {record}')
    if circ not in _ALLOWED_CIRC_TABLES:
        raise ValueError(f'非法 CP 流转表名: {circ}')
    return record, circ


def _parse_page(page=1, page_size=20, max_page_size=200):
    try:
        page = int(page if page is not None else 1)
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = int(page_size if page_size is not None else 20)
    except (TypeError, ValueError):
        page_size = 20
    page = max(1, page)
    page_size = max(1, min(page_size, int(max_page_size or 200)))
    offset = (page - 1) * page_size
    return page, page_size, offset


def _page_payload(items, total, page, page_size):
    total = max(0, int(total or 0))
    pages = max(1, (total + page_size - 1) // page_size) if total > 0 else 1
    page = min(page, pages)
    return {
        'items': items,
        'total': total,
        'page': page,
        'page_size': page_size,
        'pages': pages,
    }


def _as_int(value):
    if value is None or value == '':
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _format_dt(value):
    if isinstance(value, datetime):
        return value.strftime('%Y-%m-%d %H:%M:%S')
    if isinstance(value, date):
        return value.strftime('%Y-%m-%d')
    if value is None:
        return ''
    return str(value)


def _dispose_label(code):
    if code is None:
        return ''
    return _DISPOSE_LABELS.get(code, str(code))


def _row_item(row):
    raw = {str(key).upper(): value for key, value in dict(row._mapping).items()}
    last_dispose = _as_int(raw.get('LAST_DISPOSE'))
    status = _as_int(raw.get('STATUS'))
    return {
        'id': _as_int(raw.get('ID')),
        'product_id': raw.get('PRODUCT_ID') or '',
        'lot_id': raw.get('LOT_ID') or '',
        'wafer_id': raw.get('WAFER_ID') or '',
        'station': raw.get('STATION') or '',
        'equip_id': raw.get('EQUIP_ID') or '',
        'hold_code': raw.get('HOLD_CODE') or '',
        'hold_reason': raw.get('HOLD_REASON') or '',
        'grade_num': raw.get('GRADE_NUM') or '',
        'status': 0 if status is None else status,
        'hold_dttm': _format_dt(raw.get('HOLD_DTTM')),
        'last_dispose': last_dispose,
        'last_dispose_label': _dispose_label(last_dispose),
    }


def get_cp_holding_records(product_id='', keyword='', page=1, page_size=20):
    """
    查询尚未关闭的 CP hold（STATUS<>99），按 HOLD_DTTM 倒序。
    不按 FT 的 RECORD_TYPE、工程师型号或 AREA 过滤。
    成功返回 (True, msg, page_payload)。
    """
    page, page_size, offset = _parse_page(page, page_size)
    try:
        record_table, circ_table = cp_table_names()
        where_sql = ' WHERE NVL(r.STATUS, 0) <> :closed'
        params = {'closed': DISPOSE_CLOSE}

        product_id = (product_id or '').strip()
        keyword = (keyword or '').strip()
        if product_id:
            where_sql += ' AND r.PRODUCT_ID = :product_id'
            params['product_id'] = product_id
        if keyword:
            where_sql += """
                AND (
                    UPPER(r.WAFER_ID) LIKE UPPER(:keyword)
                    OR UPPER(r.LOT_ID) LIKE UPPER(:keyword)
                    OR UPPER(r.HOLD_CODE) LIKE UPPER(:keyword)
                    OR UPPER(r.HOLD_REASON) LIKE UPPER(:keyword)
                )
            """
            params['keyword'] = f'%{keyword}%'

        from_sql = f"""
            FROM {record_table} r
            LEFT JOIN {circ_table} c
                ON c.ID = r.LAST_CIRCULATION_ID
        """
        count_sql = f"""
            SELECT COUNT(*) AS CNT
            {from_sql}
            {where_sql}
        """
        total = int(db.session.execute(text(count_sql), params).scalar() or 0)
        pages = max(1, (total + page_size - 1) // page_size) if total else 1
        if page > pages:
            page = pages
            offset = (page - 1) * page_size

        data_params = dict(params)
        data_params['offset'] = offset
        data_params['page_size'] = page_size
        data_sql = f"""
            SELECT
                r.ID,
                r.PRODUCT_ID,
                r.STATION,
                r.EQUIP_ID,
                r.LOT_ID,
                r.WAFER_ID,
                r.HOLD_CODE,
                r.HOLD_REASON,
                r.GRADE_NUM,
                r.STATUS,
                r.HOLD_DTTM,
                c.DISPOSE AS LAST_DISPOSE
            {from_sql}
            {where_sql}
            ORDER BY r.HOLD_DTTM DESC NULLS LAST, r.ID DESC
            OFFSET :offset ROWS FETCH NEXT :page_size ROWS ONLY
        """
        rows = db.session.execute(text(data_sql), data_params).fetchall()
        items = []
        for row in rows:
            item = _row_item(row)
            if item['id'] is not None:
                items.append(item)
        return True, '获取成功', _page_payload(items, total, page, page_size)
    except ValueError as exc:
        return False, str(exc), _page_payload([], 0, 1, 20)
    except SQLAlchemyError as exc:
        db.session.rollback()
        logger.exception('CP hold 列表查询失败')
        return False, f'数据库查询异常: {exc}', _page_payload([], 0, 1, 20)
    except Exception as exc:
        db.session.rollback()
        logger.exception('CP hold 列表查询失败')
        return False, f'查询失败: {exc}', _page_payload([], 0, 1, 20)
