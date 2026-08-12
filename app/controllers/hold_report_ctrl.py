"""
Hold 报表业务逻辑（root 全量数据）。

1. holding_record：当前仍在 hold 的 FT_HOLD_RECORD
   - 通过 FT_HOLD_INFO 关联字段 + HOLDING=0 过滤已解 hold
   - 注意：HOLDING=0 表示正在 hold（命名反直觉）

2. hold 历史：按型号 + 月份/周聚合 hold 数量，供柱状图使用
"""
from calendar import monthrange
from datetime import date, datetime, timedelta
import logging

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app import db
from app.config import Config
from app.utils.database_util import (
    expand_display_wafer_ids,
    format_wafer_id_display,
    is_merged_wafer_id,
    lot_id_digit_suffix_len,
    normalize_lot_id,
    query_fvi_defect_details,
    query_split_merge_history,
    logger as sql_file_logger,
)
from app.controllers.dispose_ctrl import (
    DISPOSE_LABELS,
    DISPOSE_CLOSE,
    format_grade_num_display,
    parse_grade_num,
)
from app.controllers.rawdata_ctrl import (
    get_latest_defect_bincodes_for_wafers,
    query_same_lot_bincodes_by_prefixes,
)
from app.controllers import testlog_ctrl

logger = logging.getLogger(__name__)



_ALLOWED_HOLD_INFO_TABLES = {'FT_HOLD_INFO', 'FT_HOLD_INFO_TEST'}
_ALLOWED_HOLD_RECORD_TABLES = {'FT_HOLD_RECORD'}
_ALLOWED_LINK_COLUMNS = {'HOLD_RECORD_ID', 'PROCESSED'}

# dispose_api.md「处置单划分」处置单大类 ↔ RECORD_TYPE
RECORD_TYPE_LABELS = {
    0: 'FT异常反馈单',
    1: 'FVI异常反馈单',
    2: 'WLT异常反馈单',
}


def _table_names():
    info_table = (getattr(Config, 'HOLD_INFO_TABLE', None) or 'FT_HOLD_INFO_TEST').upper()
    record_table = (getattr(Config, 'HOLD_RECORD_TABLE', None) or 'FT_HOLD_RECORD').upper()
    link_col = (getattr(Config, 'HOLD_INFO_LINK_COLUMN', None) or 'HOLD_RECORD_ID').upper()

    if info_table not in _ALLOWED_HOLD_INFO_TABLES:
        raise ValueError(f'非法 HOLD_INFO 表名: {info_table}')
    if record_table not in _ALLOWED_HOLD_RECORD_TABLES:
        raise ValueError(f'非法 HOLD_RECORD 表名: {record_table}')
    if link_col not in _ALLOWED_LINK_COLUMNS:
        raise ValueError(f'非法关联字段: {link_col}')
    return info_table, record_table, link_col


def _row_to_dict(row):
    raw = dict(row._mapping)
    data = {}
    for key, value in raw.items():
        out_key = str(key).upper()
        if isinstance(value, datetime):
            data[out_key] = value.strftime('%Y-%m-%d %H:%M:%S')
        elif isinstance(value, date):
            data[out_key] = value.strftime('%Y-%m-%d')
        else:
            data[out_key] = value
    if 'WAFER_ID' in data:
        data['WAFER_ID'] = format_wafer_id_display(data['WAFER_ID'])
    return data


def _parse_page(page=1, page_size=20, max_page_size=200):
    """解析分页参数，返回 (page, page_size, offset)。"""
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


def get_holding_records(
    product_id='',
    station='',
    keyword='',
    record_type=None,
    page=1,
    page_size=20,
    owner_eng_id=None,
    product_ids=None,
    current_owner_id=None,
    limit=None,
):
    """
    查询当前仍在 hold 的 hold_record 列表（分页）。
    HOLDING=0 才是在线 hold；用 INFO 关联字段踢掉已解 hold 的 record。
    record_type：按处置单大类筛选（0=FT / 1=FVI / 2=WLT），空则不过滤。
    owner_eng_id：仅返回 PRODUCT_INFO.PRO_ENG_ID 等于该工程师的型号。
    product_ids：精确匹配型号列表（与 product_id 模糊可叠加）。
    current_owner_id：仅返回最新流转 NEXT_OWNER_ID 等于该用户的记录（待办）。
    limit：兼容旧参数，等价于 page_size（仅第 1 页）。
    成功返回 (True, msg, page_payload)。
    """
    try:
        info_table, record_table, link_col = _table_names()
        if limit is not None and (page is None or str(page) in ('', '1')):
            # 旧调用：limit 当作 page_size，固定第 1 页
            page, page_size, offset = _parse_page(1, limit)
        else:
            page, page_size, offset = _parse_page(page, page_size)

        where_sql = " WHERE 1 = 1"
        params = {'offset': offset, 'page_size': page_size}

        if owner_eng_id is not None:
            where_sql += """
                AND r.PRODUCT_ID IN (
                    SELECT p.PRODUCT_ID
                    FROM PRODUCT_INFO p
                    WHERE p.PRO_ENG_ID = :owner_eng_id
                )
            """
            params['owner_eng_id'] = int(owner_eng_id)

        if current_owner_id is not None and str(current_owner_id).strip() != '':
            where_sql += " AND c.NEXT_OWNER_ID = :current_owner_id"
            params['current_owner_id'] = int(current_owner_id)

        exact_pids = [
            str(p).strip() for p in (product_ids or []) if p is not None and str(p).strip()
        ]
        if exact_pids:
            ph = ', '.join(f':pid{i}' for i in range(len(exact_pids)))
            where_sql += f" AND r.PRODUCT_ID IN ({ph})"
            for i, pid in enumerate(exact_pids):
                params[f'pid{i}'] = pid

        if product_id:
            where_sql += " AND UPPER(r.PRODUCT_ID) LIKE UPPER(:product_id)"
            params['product_id'] = f"%{product_id.strip()}%"
        if station:
            where_sql += " AND UPPER(r.STATION) LIKE UPPER(:station)"
            params['station'] = f"%{station.strip()}%"
        if keyword:
            where_sql += """
                AND (
                    UPPER(r.WAFER_ID) LIKE UPPER(:keyword)
                    OR UPPER(r.LOT_ID) LIKE UPPER(:keyword)
                    OR UPPER(r.HOLD_CODE) LIKE UPPER(:keyword)
                    OR UPPER(r.HOLD_REASON) LIKE UPPER(:keyword)
                )
            """
            params['keyword'] = f"%{keyword.strip()}%"
        if record_type is not None and str(record_type).strip() != '':
            try:
                rt = int(record_type)
            except (TypeError, ValueError):
                return False, 'record_type 无效', _page_payload([], 0, page, page_size)
            if rt not in RECORD_TYPE_LABELS:
                return False, 'record_type 须为 0/1/2（FT/FVI/WLT）', _page_payload([], 0, page, page_size)
            where_sql += " AND r.RECORD_TYPE = :record_type"
            params['record_type'] = rt

        from_sql = f"""
            FROM {record_table} r
            INNER JOIN {info_table} i
                ON i.{link_col} = r.ID
               AND NVL(i.HOLDING, 1) = 0
            LEFT JOIN CIRCULATION_HISTORY c
                ON c.ID = r.LAST_CIRCULATION_ID
            LEFT JOIN USERS u
                ON u.ID = c.NEXT_OWNER_ID
            LEFT JOIN USERS u_disp
                ON u_disp.ID = c.DISPOSED_OWNER_ID
        """

        count_sql = f"""
            SELECT COUNT(DISTINCT r.ID) AS CNT
            {from_sql}
            {where_sql}
        """
        total = int(
            db.session.execute(text(count_sql), params).scalar() or 0
        )

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
                r.SOURCE,
                r.SECOND_CODE,
                r.ROUTE_ID,
                r.GRADE_NUM,
                r.RECORD_TYPE,
                r.STATUS,
                r.LAST_CIRCULATION_ID,
                r.HOLD_DTTM,
                c.NEXT_OWNER_ID AS CURRENT_OWNER_ID,
                c.DISPOSE AS LAST_DISPOSE,
                c.DISPOSE_DETAIL AS LAST_DISPOSE_DETAIL,
                c.DISPOSE_NOTE AS LAST_DISPOSE_NOTE,
                c.DISPOSE_MANUAL_NOTE AS LAST_DISPOSE_MANUAL_NOTE,
                c.DISPOSE_DTTM AS LAST_DISPOSE_DTTM,
                c.DISPOSED_OWNER_ID AS LAST_DISPOSED_OWNER_ID,
                u.NAME AS CURRENT_OWNER_NAME,
                u_disp.NAME AS LAST_DISPOSED_OWNER_NAME,
                COUNT(i.ID) AS INFO_CNT
            {from_sql}
            {where_sql}
            GROUP BY
                r.ID, r.PRODUCT_ID, r.STATION, r.EQUIP_ID, r.LOT_ID, r.WAFER_ID,
                r.HOLD_CODE, r.HOLD_REASON, r.SOURCE, r.SECOND_CODE, r.ROUTE_ID,
                r.GRADE_NUM, r.RECORD_TYPE, r.STATUS, r.LAST_CIRCULATION_ID, r.HOLD_DTTM,
                c.NEXT_OWNER_ID, c.DISPOSE, c.DISPOSE_DETAIL, c.DISPOSE_NOTE,
                c.DISPOSE_MANUAL_NOTE, c.DISPOSE_DTTM,
                c.DISPOSED_OWNER_ID, u.NAME, u_disp.NAME
            ORDER BY r.HOLD_DTTM DESC NULLS LAST, r.ID DESC
            OFFSET :offset ROWS FETCH NEXT :page_size ROWS ONLY
        """

        rows = db.session.execute(text(data_sql), params).fetchall()
        data = []
        for r in rows:
            item = _row_to_dict(r)
            rt = item.get('RECORD_TYPE')
            try:
                rt_key = int(rt) if rt is not None else None
            except (TypeError, ValueError):
                rt_key = None
            item['RECORD_TYPE_NAME'] = RECORD_TYPE_LABELS.get(rt_key, '-')
            last_dispose = item.get('LAST_DISPOSE')
            try:
                ld = int(last_dispose) if last_dispose is not None else None
            except (TypeError, ValueError):
                ld = None
            item['LAST_DISPOSE_LABEL'] = DISPOSE_LABELS.get(
                ld, str(last_dispose) if last_dispose is not None else '-'
            )
            try:
                status_val = int(item.get('STATUS')) if item.get('STATUS') is not None else 0
            except (TypeError, ValueError):
                status_val = 0
            item['IS_CLOSED'] = status_val == DISPOSE_CLOSE
            item['GRADE_NUM_DISPLAY'] = format_grade_num_display(item.get('GRADE_NUM')) or ''
            item['GRADES'] = parse_grade_num(item.get('GRADE_NUM'))
            data.append(item)
        return True, '获取成功', _page_payload(data, total, page, page_size)
    except ValueError as e:
        return False, str(e), _page_payload([], 0, 1, 20)
    except SQLAlchemyError as e:
        db.session.rollback()
        return False, f'数据库查询异常: {e}', _page_payload([], 0, 1, 20)
    except Exception as e:
        db.session.rollback()
        return False, f'查询失败: {e}', _page_payload([], 0, 1, 20)


def get_hold_count_by_wafer(wafer_id):
    """
    按 wafer_id 统计 FT_HOLD_RECORD 中的 hold 次数（记录条数）。
    """
    if wafer_id is None or not str(wafer_id).strip():
        return False, '请指定 wafer_id', None

    wafer_id = str(wafer_id).strip()
    try:
        _, record_table, _ = _table_names()
        row = db.session.execute(
            text(f"""
                SELECT COUNT(*) AS CNT
                FROM {record_table}
                WHERE WAFER_ID = :wafer_id
            """),
            {'wafer_id': wafer_id},
        ).fetchone()
        count = int(row[0] or 0) if row else 0
        return True, '获取成功', {
            'wafer_id': wafer_id,
            'hold_count': count,
        }
    except ValueError as e:
        return False, str(e), None
    except SQLAlchemyError as e:
        db.session.rollback()
        return False, f'数据库查询异常: {e}', None
    except Exception as e:
        db.session.rollback()
        return False, f'查询失败: {e}', None


def get_hold_product_options(keyword=''):
    """报表筛选用：从 hold_record 取型号列表。"""
    try:
        _, record_table, _ = _table_names()
        sql = f"""
            SELECT DISTINCT PRODUCT_ID
            FROM {record_table}
            WHERE PRODUCT_ID IS NOT NULL
        """
        params = {}
        if keyword:
            sql += " AND UPPER(PRODUCT_ID) LIKE UPPER(:keyword)"
            params['keyword'] = f"%{keyword.strip()}%"
        sql += " ORDER BY PRODUCT_ID"

        rows = db.session.execute(text(sql), params).fetchall()
        return True, '获取成功', [r[0] for r in rows]
    except Exception as e:
        db.session.rollback()
        return False, f'查询失败: {e}', []


def _month_range(year, month):
    last_day = monthrange(year, month)[1]
    start = date(year, month, 1)
    end = date(year, month, last_day) + timedelta(days=1)
    return start, end, last_day


def _iso_week_range(year, week):
    # ISO: 周一为一周起始
    start = date.fromisocalendar(year, week, 1)
    end = start + timedelta(days=7)
    return start, end


def get_hold_history(product_id, period_type, year, month=None, week=None):
    """
    Hold 历史簇状柱状图数据（按处置单 RECORD_TYPE 拆分）。
    period_type=month: 按天聚合（该月每天一组柱）
    period_type=week:  按天聚合（该 ISO 周 Mon~Sun）
    """
    try:
        if not product_id or not str(product_id).strip():
            return False, '请指定型号 product_id', None

        period_type = (period_type or '').strip().lower()
        if period_type not in ('month', 'week'):
            return False, 'period_type 必须为 month 或 week', None

        try:
            year = int(year)
        except (TypeError, ValueError):
            return False, 'year 无效', None

        _, record_table, _ = _table_names()
        product_id = str(product_id).strip()

        if period_type == 'month':
            try:
                month = int(month)
            except (TypeError, ValueError):
                return False, 'month 无效', None
            if month < 1 or month > 12:
                return False, 'month 须为 1-12', None
            start, end, last_day = _month_range(year, month)
            labels = [f'{year:04d}-{month:02d}-{d:02d}' for d in range(1, last_day + 1)]
            period_label = f'{year:04d}-{month:02d}'
        else:
            try:
                week = int(week)
            except (TypeError, ValueError):
                return False, 'week 无效', None
            if week < 1 or week > 53:
                return False, 'week 须为 1-53', None
            try:
                start, end = _iso_week_range(year, week)
            except ValueError:
                return False, f'{year} 年不存在第 {week} 周', None
            labels = [(start + timedelta(days=i)).strftime('%Y-%m-%d') for i in range(7)]
            period_label = f'{year:04d}-W{week:02d}'

        sql = f"""
            SELECT
                TO_CHAR(HOLD_DTTM, 'YYYY-MM-DD') AS DAY_KEY,
                RECORD_TYPE,
                COUNT(*) AS CNT
            FROM {record_table}
            WHERE PRODUCT_ID = :product_id
              AND HOLD_DTTM >= :start_dt
              AND HOLD_DTTM < :end_dt
            GROUP BY TO_CHAR(HOLD_DTTM, 'YYYY-MM-DD'), RECORD_TYPE
            ORDER BY DAY_KEY, RECORD_TYPE
        """
        rows = db.session.execute(
            text(sql),
            {
                'product_id': product_id,
                'start_dt': datetime.combine(start, datetime.min.time()),
                'end_dt': datetime.combine(end, datetime.min.time()),
            },
        ).fetchall()

        # (day, record_type) -> count
        count_map = {}
        for day_key, rtype, cnt in rows:
            try:
                rt = int(rtype) if rtype is not None else None
            except (TypeError, ValueError):
                rt = None
            if rt is None:
                continue
            count_map[(day_key, rt)] = int(cnt or 0)

        series = []
        total = 0
        totals_by_type = {}
        for rt, name in RECORD_TYPE_LABELS.items():
            values = [count_map.get((label, rt), 0) for label in labels]
            type_total = sum(values)
            totals_by_type[rt] = type_total
            total += type_total
            series.append({
                'record_type': rt,
                'name': name,
                'values': values,
                'total': type_total,
            })

        return True, '获取成功', {
            'product_id': product_id,
            'period_type': period_type,
            'period_label': period_label,
            'labels': labels,
            'series': series,
            'totals_by_type': totals_by_type,
            'total': total,
        }
    except ValueError as e:
        return False, str(e), None
    except SQLAlchemyError as e:
        db.session.rollback()
        return False, f'数据库查询异常: {e}', None
    except Exception as e:
        db.session.rollback()
        return False, f'查询失败: {e}', None


def get_fvi_defect_details(lot_id, line_type='FT'):
    """
    FVI 异常反馈单缺陷明细。
    返回:
      items: [{defect_code, defect_code_raw, defect_desc, qty, grade, ratio}, ...]
      summary: 组合展示文案
      grade_num / grade_num_display / grades / grade_num_total / defect_ratio
    行比率、总缺陷占比分母均为 grade_num 数量合计。
    """
    if lot_id is None or not str(lot_id).strip():
        return False, '请指定 lot_id', None

    lot_id = str(lot_id).strip()
    rows = query_fvi_defect_details(lot_id, line_type=line_type)
    if rows is None:
        return False, '查询 FVI 缺陷明细失败', None

    grade_num = _lookup_fvi_grade_num(lot_id)
    grades = parse_grade_num(grade_num)
    grade_num_total = _sum_grade_qtys(grades)
    total_qty = sum(int(i.get('qty') or 0) for i in rows)

    items = []
    for item in rows:
        row = dict(item) if isinstance(item, dict) else {}
        try:
            qty = int(row.get('qty') or 0)
        except (TypeError, ValueError):
            qty = 0
        row['qty'] = qty
        row['ratio'] = _format_pct(qty, grade_num_total)
        items.append(row)

    parts = []
    for item in items:
        code = item.get('defect_code') or '-'
        desc = item.get('defect_desc') or ''
        qty = item.get('qty') if item.get('qty') is not None else 0
        if desc:
            parts.append(f'{code} {desc}×{qty}')
        else:
            parts.append(f'{code}×{qty}')

    return True, '获取成功', {
        'lot_id': lot_id,
        'line_type': (line_type or 'FT').strip() or 'FT',
        'items': items,
        'summary': '；'.join(parts) if parts else '',
        'total_qty': total_qty,
        'count': len(items),
        'grade_num': grade_num or '',
        'grade_num_display': format_grade_num_display(grade_num) or '',
        'grades': grades,
        'grade_num_total': grade_num_total,
        'defect_ratio': _format_pct(total_qty, grade_num_total),
    }


def _lookup_fvi_grade_num(lot_id: str):
    """按 lot_id 取最新一条 FVI（RECORD_TYPE=1）Hold 的 GRADE_NUM。"""
    lot_id = (lot_id or '').strip()
    if not lot_id:
        return ''
    try:
        _, record_table, _ = _table_names()
        row = db.session.execute(
            text(f"""
                SELECT GRADE_NUM
                FROM {record_table}
                WHERE LOT_ID = :lot_id
                  AND RECORD_TYPE = 1
                ORDER BY HOLD_DTTM DESC NULLS LAST, ID DESC
                FETCH FIRST 1 ROWS ONLY
            """),
            {'lot_id': lot_id},
        ).fetchone()
        if not row:
            return ''
        raw = row[0]
        return str(raw).strip() if raw is not None else ''
    except Exception as e:
        logger.warning('lookup FVI GRADE_NUM failed lot_id=%s: %s', lot_id, e)
        try:
            db.session.rollback()
        except Exception:
            pass
        return ''


def _sum_grade_qtys(grades) -> int:
    total = 0
    for item in grades or []:
        qty = item.get('qty') if isinstance(item, dict) else None
        try:
            total += int(qty) if qty not in (None, '') else 0
        except (TypeError, ValueError):
            continue
    return total


def _format_pct(numerator, denominator) -> str:
    try:
        num = int(numerator or 0)
        den = int(denominator or 0)
    except (TypeError, ValueError):
        return '-'
    if den <= 0:
        return '-'
    return f'{(num / den) * 100:.2f}%'


def get_split_merge_history(wafer_id):
    """
    查询合批 wafer 的来源 lot 列表（MES SPLIT_MERGE_HISTORY）。
    合批 wafer_id 通常含 '-' 且 '-' 后数字位数 > 2。
    """
    if wafer_id is None or not str(wafer_id).strip():
        return False, '请指定 wafer_id', None

    wafer_id = str(wafer_id).strip()
    rows = query_split_merge_history(wafer_id)
    if rows is None:
        return False, '查询合批记录失败', None

    return True, '获取成功', {
        'wafer_id': wafer_id,
        'is_merged_candidate': is_merged_wafer_id(wafer_id),
        'source_lot_ids': rows,
        'count': len(rows),
    }


ALLOWED_ANALYSIS_STATIONS = frozenset({'WLT2', 'FATE-FA', 'VBOX-FA'})


def _resolve_analysis_params(station=None):
    """
    由标准化 station 推断 bysite step 组与 raw_data operation_id。
    station 仅允许：WLT2 / FATE-FA / VBOX-FA。
    """
    station = (station or '').strip()
    if station == 'WLT2':
        return 'WLT', 'WLT2'
    if station in ('FATE-FA', 'VBOX-FA'):
        return 'ATE', station
    return None, None


def _station_to_bysite_step_list(station=None):
    """
    标准化 station → FT_WLT_TESTLOG.STEP 列表（bysite 查询用）。

      WLT2     → ['WLTA', 'WLTB']
      FATE-FA  → ['FA']
      VBOX-FA  → ['FA']
    """
    station = (station or '').strip()
    if station == 'WLT2':
        return ['WLTA', 'WLTB']
    if station in ('FATE-FA', 'VBOX-FA'):
        return ['FA']
    return []


def get_hold_analysis(wafer_id, record_type=None, station=None, lot_id=None):
    """
    Hold Record 数据分析：bysite + raw_data（qty 降序）+ 同 lot 片列表。

    wafer_id 可为完整片号，或展示串（#03 / #01#02 / #03 #04）；
    展示串需配合 lot_id 还原真实 MES wafer id。

    station 仅允许 WLT2 / FATE-FA / VBOX-FA（由调用方按 record_type + lot_id 推导）。

    同 lot（same_lot_rows）按 record_type / lot_id 后缀分情况：
      - record_type=1（FVI）：仅当前片
      - record_type=2（WLT），或 FT 且 lot 后缀位数≠>2：lot 前缀 LIKE TEST_WAFER
      - record_type=0（FT）且 lot 后缀位数>2：合批源片 → 各源 lot 前缀 LIKE，并标注合批源
    """
    if wafer_id is None or not str(wafer_id).strip():
        return False, '请指定 wafer_id', None

    station = (station or '').strip()
    if station not in ALLOWED_ANALYSIS_STATIONS:
        return False, 'station 仅支持 WLT2/FATE-FA/VBOX-FA', None

    sql_trace = []

    wafer_id = str(wafer_id).strip()
    raw_lot_id = str(lot_id).strip() if lot_id is not None else ''
    lot_id_norm = normalize_lot_id(raw_lot_id) if raw_lot_id else ''
    display_wafers = expand_display_wafer_ids(wafer_id, raw_lot_id or lot_id_norm)

    # 展示串无法还原时直接报错（缺 lot_id）
    if wafer_id.startswith('#') and not display_wafers:
        return False, '展示串 Wafer 需同时指定 lot_id', None

    resolved_ids = display_wafers or [wafer_id]
    primary_wafer = resolved_ids[0]
    fragmented_multi = len(resolved_ids) > 1
    current_set = {str(x).strip() for x in resolved_ids if str(x).strip()}

    try:
        rt = int(record_type) if record_type is not None and str(record_type).strip() != '' else None
    except (TypeError, ValueError):
        rt = None

    step, operation_id = _resolve_analysis_params(station)
    step_list = _station_to_bysite_step_list(station)

    # 1) bysite（主片；多片时仍以第一片为主展示）
    bysite = None
    bysite_msg = ''
    try:
        bysite_resp = testlog_ctrl.get_testlog_bysite_str(primary_wafer, step_list)
        if isinstance(bysite_resp, Exception):
            bysite_msg = f'bysite 查询异常: {bysite_resp}'
            bysite = None
        elif bysite_resp is None:
            bysite_msg = '无 bysite 数据'
            bysite = None
        else:
            bysite = bysite_resp
            bysite_msg = '获取成功'
    except Exception as e:
        bysite_msg = f'bysite 查询失败: {e}'
        bysite = None

    # 2) 同 lot / 合批：批量查 BIN，避免 N+1
    source_lot_ids = []
    source_raw_data = {}
    is_merged = False
    same_lot_rows = []
    raw_by_wafer = {}
    die_by_wafer = {}
    wafer_ids_ordered = []
    merge_source_set = set()

    def _lot_prefix_of(wid):
        return normalize_lot_id(wid) or lot_id_norm or None

    def _ensure_raw_for_missing(extra_ids):
        """对批量结果中缺失的片再打一次 IN 查询；仅写入 TEST_WAFER 命中的片。"""
        missing = [
            w for w in extra_ids
            if w and w not in raw_by_wafer
        ]
        if not missing or not operation_id:
            return
        filled_raw, filled_die = get_latest_defect_bincodes_for_wafers(
            missing, operation_id, sql_trace=sql_trace,
        )
        for wid, raw_map in filled_raw.items():
            raw_by_wafer[wid] = raw_map if isinstance(raw_map, dict) else {}
        for wid, die_num in filled_die.items():
            die_by_wafer[wid] = die_num

    def _build_rows(ordered_ids, *, require_test_wafer=True):
        """
        组装 same_lot_rows。
        require_test_wafer=True 时：TEST_WAFER 未命中的片不展示（当前片除外）。
        """
        rows = []
        seen = set()
        for wid in ordered_ids:
            wid = str(wid or '').strip()
            if not wid or wid in seen:
                continue
            is_current = wid in current_set
            if require_test_wafer and wid not in raw_by_wafer and not is_current:
                continue
            seen.add(wid)
            rows.append({
                'wafer_id': wid,
                'is_current': is_current,
                'is_merge_source': wid in merge_source_set,
                'lot_prefix': _lot_prefix_of(wid),
                'raw_data': raw_by_wafer.get(wid) or {},
                'die_num': die_by_wafer.get(wid),
            })
        return rows

    def _ordered_from_query(must_have=None):
        """以 TEST_WAFER 查询结果为主；当前片始终保留。"""
        ordered = list(wafer_ids_ordered)
        seen = set(ordered)
        for wid in list(raw_by_wafer.keys()):
            if wid not in seen:
                ordered.append(wid)
                seen.add(wid)
        for wid in must_have or []:
            wid = str(wid or '').strip()
            if not wid or wid in seen:
                continue
            # 仅当前片在未命中 TEST_WAFER 时仍占位；其它片不展示
            if wid in current_set:
                ordered.append(wid)
                seen.add(wid)
        return ordered

    def _apply_batch_result(ids, raw_map, die_map):
        wafer_ids_ordered[:] = list(ids or [])
        raw_by_wafer.clear()
        raw_by_wafer.update(raw_map or {})
        die_by_wafer.clear()
        die_by_wafer.update(die_map or {})

    if fragmented_multi:
        is_merged = True
        source_lot_ids = list(resolved_ids[1:])

    if rt == 1:
        # FVI：仅当前片
        if operation_id:
            filled_raw, filled_die = get_latest_defect_bincodes_for_wafers(
                [primary_wafer], operation_id, sql_trace=sql_trace,
            )
            raw_by_wafer.update(filled_raw)
            die_by_wafer.update(filled_die)
            if primary_wafer not in raw_by_wafer:
                raw_by_wafer[primary_wafer] = {}
            raw_msg = '获取成功'
        else:
            raw_msg = '无法确定 operation_id，跳过 raw_data'
        same_lot_rows = _build_rows([primary_wafer], require_test_wafer=False)
    elif rt == 0 and lot_id_digit_suffix_len(raw_lot_id) > 2:
        # FT 合批：MES 源片 → 多前缀一次 LIKE + BIN
        is_merged = True
        sources = query_split_merge_history(raw_lot_id, sql_trace=sql_trace)
        if sources is None:
            sources = []
        if not sources and primary_wafer != raw_lot_id:
            sources = query_split_merge_history(primary_wafer, sql_trace=sql_trace) or []
        source_lot_ids = list(sources)
        merge_source_set = {str(s).strip() for s in source_lot_ids if str(s).strip()}

        prefixes = []
        seen_prefix = set()
        for src in source_lot_ids:
            pref = normalize_lot_id(src)
            if pref and pref not in seen_prefix:
                seen_prefix.add(pref)
                prefixes.append(pref)

        raw_msg = ''
        if prefixes and operation_id:
            ids, raw_map, die_map = query_same_lot_bincodes_by_prefixes(
                prefixes, operation_id, sql_trace=sql_trace,
            )
            _apply_batch_result(ids, raw_map, die_map)
            raw_msg = '获取成功'
        elif not operation_id:
            raw_msg = '无法确定 operation_id，跳过 raw_data'
        else:
            raw_msg = '无合批源前缀，降级当前片'

        # 仅补查仍可能存在于 TEST_WAFER 的片；查不到的不展示
        must_have = list(source_lot_ids) + [primary_wafer]
        _ensure_raw_for_missing(must_have)
        same_lot_rows = _build_rows(_ordered_from_query(must_have=[primary_wafer]))
        if not same_lot_rows:
            same_lot_rows = _build_rows([primary_wafer], require_test_wafer=False)
    else:
        # WLT / FT 普通 lot
        if rt != 1 and is_merged_wafer_id(primary_wafer) and not fragmented_multi:
            is_merged = True
            sources = query_split_merge_history(primary_wafer, sql_trace=sql_trace) or []
            source_lot_ids = list(sources)
            merge_source_set = {str(s).strip() for s in source_lot_ids if str(s).strip()}

        prefix = lot_id_norm or normalize_lot_id(primary_wafer)
        raw_msg = ''
        if prefix and operation_id:
            ids, raw_map, die_map = query_same_lot_bincodes_by_prefixes(
                [prefix], operation_id, sql_trace=sql_trace,
            )
            _apply_batch_result(ids, raw_map, die_map)
            raw_msg = '获取成功'
        elif not operation_id:
            raw_msg = '无法确定 operation_id，跳过 raw_data'
        else:
            raw_msg = '无 lot 前缀'

        must_have = [primary_wafer] + list(source_lot_ids)
        if fragmented_multi:
            must_have.extend(resolved_ids)
        _ensure_raw_for_missing(must_have)
        same_lot_rows = _build_rows(_ordered_from_query(must_have=[primary_wafer]))
        if not same_lot_rows:
            same_lot_rows = _build_rows([primary_wafer], require_test_wafer=False)

    raw_data = raw_by_wafer.get(primary_wafer) or {}
    # 兼容字段：仅保留 TEST_WAFER 命中的源片 raw
    for src in source_lot_ids:
        src = str(src or '').strip()
        if src and src in raw_by_wafer:
            source_raw_data[src] = raw_by_wafer.get(src) or {}

    # 当前片优先，其次合批源，再按 wafer_id
    same_lot_rows.sort(
        key=lambda r: (
            0 if r.get('is_current') else 1,
            0 if r.get('is_merge_source') else 1,
            str(r.get('wafer_id') or ''),
        )
    )

    # 请求级 SQL 日志（写入 logs/test_log.log，与 database_util 共用 handler）
    sql_file_logger.info(
        'analysis SQL wafer=%s lot_raw=%s record_type=%s op=%s sql_count=%s',
        primary_wafer, raw_lot_id or '-', rt, operation_id or '-', len(sql_trace),
    )
    for i, entry in enumerate(sql_trace, start=1):
        sql_file_logger.info(
            'analysis SQL[%s/%s] tag=%s params=%s sql=%s',
            i, len(sql_trace),
            entry.get('tag') or '-',
            entry.get('params'),
            entry.get('sql'),
        )
    logger.info(
        'analysis done wafer=%s same_lot=%s sql_count=%s',
        primary_wafer, len(same_lot_rows), len(sql_trace),
    )

    return True, '获取成功', {
        'wafer_id': primary_wafer,
        'wafer_id_display': format_wafer_id_display(wafer_id) if wafer_id.startswith('#')
        else format_wafer_id_display(primary_wafer),
        'lot_id': lot_id_norm or None,
        'lot_id_raw': raw_lot_id or None,
        'resolved_wafer_ids': resolved_ids,
        'record_type': record_type if rt is None else rt,
        'station': (station or '').strip() or None,
        'step': step,
        'step_list': step_list,
        'operation_id': operation_id,
        'bysite': bysite,
        'bysite_msg': bysite_msg,
        'raw_data': raw_data or {},
        'raw_msg': raw_msg,
        'is_merged': is_merged,
        'source_lot_ids': source_lot_ids,
        'source_raw_data': source_raw_data,
        'same_lot_rows': same_lot_rows,
    }
