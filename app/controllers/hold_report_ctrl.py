"""
Hold 报表业务逻辑（root 全量数据）。

1. holding_record：当前仍在 hold 的 FT_HOLD_RECORD
   - 通过 FT_HOLD_INFO 关联字段 + HOLDING=0 过滤已解 hold
   - 注意：HOLDING=0 表示正在 hold（命名反直觉）

2. hold 历史：按型号 + 月份/周聚合 hold 数量，供柱状图使用
"""
from calendar import monthrange
from datetime import date, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app import db
from app.config import Config
from app.utils.database_util import (
    is_merged_wafer_id,
    normalize_lot_id,
    query_fvi_defect_details,
    query_split_merge_history,
)
from app.controllers.dispose_ctrl import DISPOSE_LABELS, DISPOSE_CLOSE
from app.controllers.rawdata_ctrl import get_latest_defect_bincodes
from app.controllers import testlog_ctrl



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
    if 'LOT_ID' in data:
        data['LOT_ID'] = normalize_lot_id(data['LOT_ID'])
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
                r.RECORD_TYPE,
                r.STATUS,
                r.LAST_CIRCULATION_ID,
                r.HOLD_DTTM,
                c.NEXT_OWNER_ID AS CURRENT_OWNER_ID,
                c.DISPOSE AS LAST_DISPOSE,
                u.NAME AS CURRENT_OWNER_NAME,
                COUNT(i.ID) AS INFO_CNT
            {from_sql}
            {where_sql}
            GROUP BY
                r.ID, r.PRODUCT_ID, r.STATION, r.EQUIP_ID, r.LOT_ID, r.WAFER_ID,
                r.HOLD_CODE, r.HOLD_REASON, r.SOURCE, r.SECOND_CODE, r.ROUTE_ID,
                r.RECORD_TYPE, r.STATUS, r.LAST_CIRCULATION_ID, r.HOLD_DTTM,
                c.NEXT_OWNER_ID, c.DISPOSE, u.NAME
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
      items: [{defect_code, defect_code_raw, defect_desc, qty}, ...]
      summary: 组合展示文案，如 "A01 Scratch×3；B02 Particle×1"
    """
    if lot_id is None or not str(lot_id).strip():
        return False, '请指定 lot_id', None

    lot_id = str(lot_id).strip()
    rows = query_fvi_defect_details(lot_id, line_type=line_type)
    if rows is None:
        return False, '查询 FVI 缺陷明细失败', None

    parts = []
    for item in rows:
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
        'items': rows,
        'summary': '；'.join(parts) if parts else '',
        'total_qty': sum(int(i.get('qty') or 0) for i in rows),
        'count': len(rows),
    }


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


def _resolve_analysis_params(record_type=None, station=None):
    """
    由 hold record 的 RECORD_TYPE / STATION 推断 bysite step 与 raw_data operation_id。
    FT/FVI → ATE + FATE-FA；WLT → WLT + station。
    """
    station = (station or '').strip()
    try:
        rt = int(record_type) if record_type is not None and str(record_type).strip() != '' else None
    except (TypeError, ValueError):
        rt = None

    if rt == 2:
        return 'WLT', station or None

    if station.upper().startswith('FATE') if station else False:
        operation_id = station
    else:
        operation_id = 'FATE-FA'
    return 'ATE', operation_id


def _station_to_bysite_step_list(station=None, record_type=None, step_group=None):
    """
    Hold STATION → FT_WLT_TESTLOG.STEP 列表（bysite 查询用）。

    数据设计不一致：station 如 FATE-FA，testlog step 为 FA。
      FATE-FA  → ['FA']
      FATE-xx  → ['xx']（取 '-' 后段）
      WLT/WOQC → ['WLTA', 'WLTB']
      其它     → 按 step_group（ATE→FA；WLT→WLTA/WLTB）回退
    """
    station = (station or '').strip()
    sta_u = station.upper()

    try:
        rt = int(record_type) if record_type is not None and str(record_type).strip() != '' else None
    except (TypeError, ValueError):
        rt = None

    if '-' in station and sta_u.startswith('FATE-'):
        suffix = station.split('-', 1)[1].strip()
        if suffix:
            return [suffix]

    if rt == 2 or sta_u == 'WOQC' or (step_group or '').upper() == 'WLT':
        return ['WLTA', 'WLTB']

    return ['FA']


def get_hold_analysis(wafer_id, record_type=None, station=None):
    """
    Hold Record 数据分析：bysite + raw_data（qty 降序）。
    合批 wafer 额外附带各源 wafer 的 raw_data。
    """
    if wafer_id is None or not str(wafer_id).strip():
        return False, '请指定 wafer_id', None

    wafer_id = str(wafer_id).strip()
    step, operation_id = _resolve_analysis_params(record_type, station)
    step_list = _station_to_bysite_step_list(
        station=station,
        record_type=record_type,
        step_group=step,
    )

    # 1) bysite
    bysite = None
    bysite_msg = ''
    try:
        bysite_resp = testlog_ctrl.get_testlog_bysite_str(wafer_id, step_list)
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

    # 2) raw_data（当前 wafer）
    raw_data = {}
    raw_msg = ''
    if operation_id:
        ok, raw_msg, raw_data = get_latest_defect_bincodes(wafer_id, operation_id)
        if not ok:
            raw_data = {}
    else:
        raw_msg = '无法确定 operation_id，跳过 raw_data'

    # 3) 合批源 wafer raw_data
    is_merged = is_merged_wafer_id(wafer_id)
    source_lot_ids = []
    source_raw_data = {}
    if is_merged:
        sources = query_split_merge_history(wafer_id)
        if sources is None:
            sources = []
        source_lot_ids = sources
        if operation_id:
            for src in source_lot_ids:
                ok, _, src_raw = get_latest_defect_bincodes(src, operation_id)
                source_raw_data[src] = src_raw if ok and src_raw else {}

    return True, '获取成功', {
        'wafer_id': wafer_id,
        'record_type': record_type,
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
    }
