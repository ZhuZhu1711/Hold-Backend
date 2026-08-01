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


_ALLOWED_HOLD_INFO_TABLES = {'FT_HOLD_INFO', 'FT_HOLD_INFO_TEST'}
_ALLOWED_HOLD_RECORD_TABLES = {'FT_HOLD_RECORD'}
_ALLOWED_LINK_COLUMNS = {'HOLD_RECORD_ID', 'PROCESSED'}


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
    return data


def get_holding_records(product_id='', station='', keyword='', limit=500):
    """
    查询当前仍在 hold 的 hold_record 列表（root 全量）。
    HOLDING=0 才是在线 hold；用 INFO 关联字段踢掉已解 hold 的 record。
    """
    try:
        info_table, record_table, link_col = _table_names()
        limit = max(1, min(int(limit or 500), 5000))

        sql = f"""
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
                COUNT(i.ID) AS INFO_CNT
            FROM {record_table} r
            INNER JOIN {info_table} i
                ON i.{link_col} = r.ID
               AND NVL(i.HOLDING, 1) = 0
            WHERE 1 = 1
        """
        params = {'limit': limit}

        if product_id:
            sql += " AND UPPER(r.PRODUCT_ID) LIKE UPPER(:product_id)"
            params['product_id'] = f"%{product_id.strip()}%"
        if station:
            sql += " AND UPPER(r.STATION) LIKE UPPER(:station)"
            params['station'] = f"%{station.strip()}%"
        if keyword:
            sql += """
                AND (
                    UPPER(r.WAFER_ID) LIKE UPPER(:keyword)
                    OR UPPER(r.LOT_ID) LIKE UPPER(:keyword)
                    OR UPPER(r.HOLD_CODE) LIKE UPPER(:keyword)
                    OR UPPER(r.HOLD_REASON) LIKE UPPER(:keyword)
                )
            """
            params['keyword'] = f"%{keyword.strip()}%"

        sql += """
            GROUP BY
                r.ID, r.PRODUCT_ID, r.STATION, r.EQUIP_ID, r.LOT_ID, r.WAFER_ID,
                r.HOLD_CODE, r.HOLD_REASON, r.SOURCE, r.SECOND_CODE, r.ROUTE_ID,
                r.RECORD_TYPE, r.STATUS, r.LAST_CIRCULATION_ID, r.HOLD_DTTM
            ORDER BY r.HOLD_DTTM DESC NULLS LAST, r.ID DESC
            FETCH FIRST :limit ROWS ONLY
        """

        rows = db.session.execute(text(sql), params).fetchall()
        data = [_row_to_dict(r) for r in rows]
        return True, '获取成功', data
    except ValueError as e:
        return False, str(e), []
    except SQLAlchemyError as e:
        db.session.rollback()
        return False, f'数据库查询异常: {e}', []
    except Exception as e:
        db.session.rollback()
        return False, f'查询失败: {e}', []


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
    Hold 历史柱状图数据。
    period_type=month: 按天聚合（该月每天一根柱）
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
            SELECT TO_CHAR(HOLD_DTTM, 'YYYY-MM-DD') AS DAY_KEY, COUNT(*) AS CNT
            FROM {record_table}
            WHERE PRODUCT_ID = :product_id
              AND HOLD_DTTM >= :start_dt
              AND HOLD_DTTM < :end_dt
            GROUP BY TO_CHAR(HOLD_DTTM, 'YYYY-MM-DD')
            ORDER BY DAY_KEY
        """
        rows = db.session.execute(
            text(sql),
            {
                'product_id': product_id,
                'start_dt': datetime.combine(start, datetime.min.time()),
                'end_dt': datetime.combine(end, datetime.min.time()),
            },
        ).fetchall()
        count_map = {r[0]: int(r[1]) for r in rows}
        values = [count_map.get(label, 0) for label in labels]

        return True, '获取成功', {
            'product_id': product_id,
            'period_type': period_type,
            'period_label': period_label,
            'labels': labels,
            'values': values,
            'total': sum(values),
        }
    except ValueError as e:
        return False, str(e), None
    except SQLAlchemyError as e:
        db.session.rollback()
        return False, f'数据库查询异常: {e}', None
    except Exception as e:
        db.session.rollback()
        return False, f'查询失败: {e}', None
