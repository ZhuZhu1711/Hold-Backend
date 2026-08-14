"""
质量部只读报表：全型号物料已处置情况。

仅 CIRCULATION_HISTORY.DISPOSE ∈ {1放行, 2降级, 3重测}，不含未处理及其它流转。
不提供任何处置/改写接口。
"""
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app import db
from app.controllers.dispose_ctrl import (
    DISPOSE_DOWNGRADE,
    DISPOSE_LABELS,
    DISPOSE_RELEASE,
    DISPOSE_RETEST,
    _circ_table,
    _record_table,
    _row_to_dict,
)
from app.controllers.hold_info_export_ctrl import normalize_dttm
from app.controllers.hold_report_ctrl import (
    RECORD_TYPE_LABELS,
    _page_payload,
    _parse_page,
)

QUALITY_DISPOSES = (DISPOSE_RELEASE, DISPOSE_DOWNGRADE, DISPOSE_RETEST)


def _enrich_row(row):
    item = _row_to_dict(row)
    dispose = item.get('DISPOSE')
    try:
        dispose_key = int(dispose)
    except (TypeError, ValueError):
        dispose_key = dispose
    item['DISPOSE_LABEL'] = DISPOSE_LABELS.get(dispose_key, str(dispose))
    try:
        rt_key = int(item.get('RECORD_TYPE'))
    except (TypeError, ValueError):
        rt_key = item.get('RECORD_TYPE')
    item['RECORD_TYPE_NAME'] = RECORD_TYPE_LABELS.get(rt_key, '-')
    return item


def _parse_record_type(record_type):
    if record_type is None or str(record_type).strip() == '':
        return True, None
    try:
        rt = int(record_type)
    except (TypeError, ValueError):
        return False, 'record_type 无效'
    if rt not in RECORD_TYPE_LABELS:
        return False, 'record_type 须为 0/1/2（FT/FVI/WLT）'
    return True, rt


def _parse_dispose(dispose):
    if dispose is None or str(dispose).strip() == '':
        return True, None
    try:
        code = int(dispose)
    except (TypeError, ValueError):
        return False, 'dispose 无效'
    if code not in QUALITY_DISPOSES:
        return False, 'dispose 仅支持 1放行 / 2降级 / 3重测'
    return True, code


def query_quality_disposes(
    start_dttm='',
    end_dttm='',
    product_id='',
    dispose=None,
    record_type=None,
    route='',
    page=1,
    page_size=20,
    max_page_size=200,
):
    """
    已处置物料列表（分页）。
    时间按 CIRCULATION_HISTORY.DISPOSE_DTTM；ROUTE 匹配 FT_HOLD_RECORD.ROUTE_ID。
    """
    try:
        page, page_size, offset = _parse_page(page, page_size, max_page_size=max_page_size)
        record_table = _record_table()

        start_norm = normalize_dttm(start_dttm, is_end=False) if start_dttm else None
        end_norm = normalize_dttm(end_dttm, is_end=True) if end_dttm else None
        if start_dttm and not start_norm:
            return False, '开始时间无效', _page_payload([], 0, page, page_size)
        if end_dttm and not end_norm:
            return False, '结束时间无效', _page_payload([], 0, page, page_size)
        if start_norm and end_norm and start_norm > end_norm:
            return False, '开始时间不能晚于结束时间', _page_payload([], 0, page, page_size)

        ok, dispose_code = _parse_dispose(dispose)
        if not ok:
            return False, dispose_code, _page_payload([], 0, page, page_size)
        ok, rt = _parse_record_type(record_type)
        if not ok:
            return False, rt, _page_payload([], 0, page, page_size)

        where_sql = " WHERE c.DISPOSE IN (1, 2, 3)"
        params = {'offset': offset, 'page_size': page_size}

        if start_norm:
            where_sql += " AND c.DISPOSE_DTTM >= TO_DATE(:start_dttm, 'YYYY-MM-DD HH24:MI:SS')"
            params['start_dttm'] = start_norm
        if end_norm:
            where_sql += " AND c.DISPOSE_DTTM <= TO_DATE(:end_dttm, 'YYYY-MM-DD HH24:MI:SS')"
            params['end_dttm'] = end_norm
        if product_id:
            where_sql += " AND UPPER(r.PRODUCT_ID) LIKE UPPER(:product_id)"
            params['product_id'] = f"%{str(product_id).strip()}%"
        if dispose_code is not None:
            where_sql += " AND c.DISPOSE = :dispose"
            params['dispose'] = dispose_code
        if rt is not None:
            where_sql += " AND r.RECORD_TYPE = :record_type"
            params['record_type'] = rt
        if route:
            where_sql += " AND UPPER(NVL(r.ROUTE_ID, '')) LIKE UPPER(:route)"
            params['route'] = f"%{str(route).strip()}%"

        from_sql = f"""
            FROM {_circ_table()} c
            INNER JOIN {record_table} r
                ON r.ID = c.HOLD_RECORD_ID
            LEFT JOIN USERS u1 ON u1.ID = c.DISPOSED_OWNER_ID
        """

        total = int(db.session.execute(
            text(f"SELECT COUNT(*) AS CNT {from_sql} {where_sql}"),
            params,
        ).scalar() or 0)

        data_sql = f"""
            SELECT
                c.ID, c.HOLD_RECORD_ID, c.DISPOSED_OWNER_ID, c.DISPOSE,
                c.DISPOSE_SOURCE, c.DISPOSE_DTTM,
                c.DISPOSE_DETAIL, c.DISPOSE_NOTE, c.DISPOSE_MANUAL_NOTE,
                u1.NAME AS DISPOSED_OWNER_NAME,
                r.PRODUCT_ID, r.STATION, r.EQUIP_ID, r.LOT_ID, r.WAFER_ID,
                r.HOLD_CODE, r.HOLD_REASON, r.ROUTE_ID, r.RECORD_TYPE,
                r.HOLD_DTTM
            {from_sql}
            {where_sql}
            ORDER BY c.DISPOSE_DTTM DESC NULLS LAST, c.ID DESC
            OFFSET :offset ROWS FETCH NEXT :page_size ROWS ONLY
        """
        rows = db.session.execute(text(data_sql), params).fetchall()
        return True, '获取成功', _page_payload(
            [_enrich_row(r) for r in rows], total, page, page_size
        )
    except ValueError as e:
        return False, str(e), _page_payload([], 0, 1, 20)
    except SQLAlchemyError as e:
        db.session.rollback()
        return False, f'数据库查询异常: {e}', _page_payload([], 0, 1, 20)
    except Exception as e:
        db.session.rollback()
        return False, f'查询失败: {e}', _page_payload([], 0, 1, 20)


QUALITY_EXPORT_HEADERS = [
    '处置时间',
    '型号',
    'Record类型',
    'ROUTE',
    '处置',
    'Lot',
    'Wafer',
    '站点',
    'Hold Code',
    '处置人',
    '处置详情',
    '工程备注',
    '手输备注',
    'Record ID',
]


def quality_export_row(item):
    return [
        item.get('DISPOSE_DTTM') or '',
        item.get('PRODUCT_ID') or '',
        item.get('RECORD_TYPE_NAME') or item.get('RECORD_TYPE') or '',
        item.get('ROUTE_ID') or '',
        item.get('DISPOSE_LABEL') or item.get('DISPOSE') or '',
        item.get('LOT_ID') or '',
        item.get('WAFER_ID') or '',
        item.get('STATION') or '',
        item.get('HOLD_CODE') or '',
        item.get('DISPOSED_OWNER_NAME') or item.get('DISPOSED_OWNER_ID') or '',
        item.get('DISPOSE_DETAIL') or '',
        item.get('DISPOSE_NOTE') or '',
        item.get('DISPOSE_MANUAL_NOTE') or '',
        item.get('HOLD_RECORD_ID'),
    ]


def export_quality_disposes_xlsx(
    start_dttm='',
    end_dttm='',
    product_id='',
    dispose=None,
    record_type=None,
    route='',
):
    """导出质量部已处置物料为 xlsx（筛选条件与列表一致，最多 5000 行）。"""
    from app.utils.excel_export import EXPORT_MAX_ROWS, from_page_payload

    success, msg, payload = query_quality_disposes(
        start_dttm=start_dttm,
        end_dttm=end_dttm,
        product_id=product_id,
        dispose=dispose,
        record_type=record_type,
        route=route,
        page=1,
        page_size=EXPORT_MAX_ROWS,
        max_page_size=EXPORT_MAX_ROWS,
    )
    return from_page_payload(
        success, msg, payload,
        QUALITY_EXPORT_HEADERS, quality_export_row, '物料处置',
    )


def get_quality_product_options(keyword=''):
    """型号下拉：出现过放行/降级/重测的 PRODUCT_ID。"""
    try:
        record_table = _record_table()
        sql = f"""
            SELECT DISTINCT r.PRODUCT_ID
            FROM {record_table} r
            INNER JOIN {_circ_table()} c ON c.HOLD_RECORD_ID = r.ID
            WHERE r.PRODUCT_ID IS NOT NULL
              AND c.DISPOSE IN (1, 2, 3)
        """
        params = {}
        if keyword:
            sql += " AND UPPER(r.PRODUCT_ID) LIKE UPPER(:keyword)"
            params['keyword'] = f"%{keyword.strip()}%"
        sql += " ORDER BY r.PRODUCT_ID"
        rows = db.session.execute(text(sql), params).fetchall()
        return True, '获取成功', [r[0] for r in rows if r[0]]
    except Exception as e:
        db.session.rollback()
        return False, f'查询失败: {e}', []


def get_quality_route_options(keyword=''):
    """ROUTE 下拉：出现过放行/降级/重测的 ROUTE_ID。"""
    try:
        record_table = _record_table()
        sql = f"""
            SELECT DISTINCT r.ROUTE_ID
            FROM {record_table} r
            INNER JOIN {_circ_table()} c ON c.HOLD_RECORD_ID = r.ID
            WHERE r.ROUTE_ID IS NOT NULL
              AND c.DISPOSE IN (1, 2, 3)
        """
        params = {}
        if keyword:
            sql += " AND UPPER(r.ROUTE_ID) LIKE UPPER(:keyword)"
            params['keyword'] = f"%{keyword.strip()}%"
        sql += " ORDER BY r.ROUTE_ID"
        rows = db.session.execute(text(sql), params).fetchall()
        return True, '获取成功', [r[0] for r in rows if r[0]]
    except Exception as e:
        db.session.rollback()
        return False, f'查询失败: {e}', []


def get_quality_record_detail(hold_record_id):
    """只读：record 摘要 + 该单上的放行/降级/重测记录。"""
    try:
        rid = int(hold_record_id)
    except (TypeError, ValueError):
        return False, 'hold_record_id 无效', None

    try:
        record_table = _record_table()
        record_row = db.session.execute(
            text(f"""
                SELECT
                    r.ID, r.PRODUCT_ID, r.STATION, r.EQUIP_ID, r.LOT_ID, r.WAFER_ID,
                    r.HOLD_CODE, r.HOLD_REASON, r.ROUTE_ID, r.RECORD_TYPE,
                    r.HOLD_DTTM
                FROM {record_table} r
                WHERE r.ID = :rid
            """),
            {'rid': rid},
        ).fetchone()
        if not record_row:
            return False, 'hold_record 不存在', None

        record = _row_to_dict(record_row)
        try:
            rt_key = int(record.get('RECORD_TYPE'))
        except (TypeError, ValueError):
            rt_key = record.get('RECORD_TYPE')
        record['RECORD_TYPE_NAME'] = RECORD_TYPE_LABELS.get(rt_key, '-')

        circ_rows = db.session.execute(
            text(f"""
                SELECT
                    c.ID, c.HOLD_RECORD_ID, c.DISPOSED_OWNER_ID, c.DISPOSE,
                    c.DISPOSE_SOURCE, c.DISPOSE_DTTM,
                    c.DISPOSE_DETAIL, c.DISPOSE_NOTE, c.DISPOSE_MANUAL_NOTE,
                    u1.NAME AS DISPOSED_OWNER_NAME
                FROM {_circ_table()} c
                LEFT JOIN USERS u1 ON u1.ID = c.DISPOSED_OWNER_ID
                WHERE c.HOLD_RECORD_ID = :rid
                  AND c.DISPOSE IN (1, 2, 3)
                ORDER BY c.DISPOSE_DTTM ASC NULLS LAST, c.ID ASC
            """),
            {'rid': rid},
        ).fetchall()

        return True, '获取成功', {
            'record': record,
            'disposes': [_enrich_row(r) for r in circ_rows],
        }
    except SQLAlchemyError as e:
        db.session.rollback()
        return False, f'数据库查询异常: {e}', None
    except Exception as e:
        db.session.rollback()
        return False, f'查询失败: {e}', None
