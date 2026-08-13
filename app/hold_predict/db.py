"""Oracle 访问：FT 预测打分 / 标签回填 / 历史率。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Optional

import oracledb

from app.utils.database_util import DSN, PWD, USER

ENGINEER_DISPOSES = (1, 2, 3, 5)
RECORD_TYPE_FT = 0


def connect():
    return oracledb.connect(user=USER, password=PWD, dsn=DSN)


def rows_as_dicts(cursor) -> list[dict]:
    if cursor.description is None:
        return []
    cols = [d[0].upper() for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def fetch_one_dict(cursor) -> Optional[dict]:
    rows = rows_as_dicts(cursor)
    return rows[0] if rows else None


def _to_int(value, default=None):
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def query_ft_record(cursor, record_id: int) -> Optional[dict]:
    cursor.execute(
        """
        SELECT
            ID, PRODUCT_ID, STATION, EQUIP_ID, LOT_ID, WAFER_ID,
            HOLD_CODE, SOURCE, SECOND_CODE, ROUTE_ID, GRADE_NUM,
            RECORD_TYPE, STATUS, HOLD_DTTM
        FROM FT_HOLD_RECORD
        WHERE ID = :id AND RECORD_TYPE = :rt
        """,
        {'id': record_id, 'rt': RECORD_TYPE_FT},
    )
    return fetch_one_dict(cursor)


def query_pending_ft_records(cursor, model_version: str, batch_size: int) -> list[dict]:
    cursor.execute(
        """
        SELECT * FROM (
            SELECT
                r.ID, r.PRODUCT_ID, r.STATION, r.EQUIP_ID, r.LOT_ID, r.WAFER_ID,
                r.HOLD_CODE, r.SOURCE, r.SECOND_CODE, r.ROUTE_ID, r.GRADE_NUM,
                r.RECORD_TYPE, r.STATUS, r.HOLD_DTTM
            FROM FT_HOLD_RECORD r
            WHERE r.RECORD_TYPE = :rt
              AND NOT EXISTS (
                    SELECT 1 FROM FT_HOLD_PREDICT p
                    WHERE p.HOLD_RECORD_ID = r.ID
                      AND p.MODEL_VERSION = :mv
              )
            ORDER BY r.HOLD_DTTM ASC NULLS LAST, r.ID ASC
        )
        WHERE ROWNUM <= :n
        """,
        {'rt': RECORD_TYPE_FT, 'mv': model_version, 'n': int(batch_size)},
    )
    return rows_as_dicts(cursor)


def query_labeled_ft_records(cursor, limit: Optional[int] = None) -> list[dict]:
    sql = """
        SELECT * FROM (
            SELECT
                r.ID, r.PRODUCT_ID, r.STATION, r.EQUIP_ID, r.LOT_ID, r.WAFER_ID,
                r.HOLD_CODE, r.SOURCE, r.SECOND_CODE, r.ROUTE_ID, r.GRADE_NUM,
                r.RECORD_TYPE, r.STATUS, r.HOLD_DTTM,
                f.DISPOSE AS LABEL_DISPOSE,
                f.DISPOSE_DTTM AS LABEL_DTTM
            FROM FT_HOLD_RECORD r
            JOIN (
                SELECT
                    c.HOLD_RECORD_ID,
                    c.DISPOSE,
                    c.DISPOSE_DTTM,
                    ROW_NUMBER() OVER (
                        PARTITION BY c.HOLD_RECORD_ID
                        ORDER BY c.DISPOSE_DTTM, c.ID
                    ) AS rn
                FROM CIRCULATION_HISTORY c
                WHERE c.DISPOSE IN (1, 2, 3, 5)
            ) f ON f.HOLD_RECORD_ID = r.ID AND f.rn = 1
            WHERE r.RECORD_TYPE = :rt
            ORDER BY r.HOLD_DTTM ASC NULLS LAST, r.ID ASC
        )
    """
    params: dict[str, Any] = {'rt': RECORD_TYPE_FT}
    if limit is not None:
        sql += " WHERE ROWNUM <= :n"
        params['n'] = int(limit)
    cursor.execute(sql, params)
    return rows_as_dicts(cursor)


def query_latest_test_wafer(
    cursor,
    wafer_ids: list[str],
    operation_id: str,
    hold_dttm: Optional[datetime],
) -> Optional[dict]:
    ids = [w for w in wafer_ids if w]
    if not ids or not operation_id:
        return None
    binds = {f'w{i}': w for i, w in enumerate(ids)}
    in_clause = ', '.join(f':w{i}' for i in range(len(ids)))
    params = dict(binds)
    params['op'] = operation_id
    time_filter = ''
    if hold_dttm is not None:
        time_filter = 'AND NVL(tw.FT_TIME, tw.RECORD_DTTM) <= :hold_dttm'
        params['hold_dttm'] = hold_dttm
    cursor.execute(
        f"""
        SELECT * FROM (
            SELECT
                tw.ID, tw.WAFER_ID, tw.OPERATION_ID, tw.FT_TIME, tw.PRODUCT_ID,
                tw.SECOND_CODE, tw.LOT_ID, tw.GROSS_DIE, tw.ROUTE, tw.EQUIP_ID,
                tw.PASS_DIE, tw.NG_NUM, tw.GRADES_QTY, tw.RECORD_DTTM,
                ROW_NUMBER() OVER (
                    PARTITION BY tw.WAFER_ID
                    ORDER BY NVL(tw.FT_TIME, tw.RECORD_DTTM) DESC, tw.ID DESC
                ) AS rn
            FROM TEST_WAFER tw
            WHERE tw.WAFER_ID IN ({in_clause})
              AND tw.OPERATION_ID = :op
              {time_filter}
        )
        WHERE rn = 1
        ORDER BY NVL(FT_TIME, RECORD_DTTM) DESC, ID DESC
        """,
        params,
    )
    rows = rows_as_dicts(cursor)
    return rows[0] if rows else None


def query_test_bincodes(cursor, test_wafer_id: int) -> list[dict]:
    cursor.execute(
        """
        SELECT BIN_CODE, BIN_CODE_QTY
        FROM TEST_BINCODE
        WHERE TEST_WAFER_SEQ = :id
        ORDER BY BIN_CODE_QTY DESC NULLS LAST
        """,
        {'id': int(test_wafer_id)},
    )
    return rows_as_dicts(cursor)


def query_same_lot_yields(
    cursor,
    lot_prefix: str,
    operation_id: str,
    hold_dttm: Optional[datetime],
) -> list[dict]:
    if not lot_prefix or not operation_id:
        return []
    params: dict[str, Any] = {
        'prefix': f'{lot_prefix}%',
        'op': operation_id,
    }
    time_filter = ''
    if hold_dttm is not None:
        time_filter = 'AND NVL(tw.FT_TIME, tw.RECORD_DTTM) <= :hold_dttm'
        params['hold_dttm'] = hold_dttm
    cursor.execute(
        f"""
        SELECT WAFER_ID, GROSS_DIE, PASS_DIE, NG_NUM, GRADES_QTY FROM (
            SELECT
                tw.WAFER_ID, tw.GROSS_DIE, tw.PASS_DIE, tw.NG_NUM, tw.GRADES_QTY,
                ROW_NUMBER() OVER (
                    PARTITION BY tw.WAFER_ID
                    ORDER BY NVL(tw.FT_TIME, tw.RECORD_DTTM) DESC, tw.ID DESC
                ) AS rn
            FROM TEST_WAFER tw
            WHERE tw.WAFER_ID LIKE :prefix
              AND tw.OPERATION_ID = :op
              {time_filter}
        )
        WHERE rn = 1
        """,
        params,
    )
    return rows_as_dicts(cursor)


def query_bsl_map(cursor, product_id: str) -> dict[int, float]:
    if not product_id:
        return {}
    cursor.execute(
        """
        SELECT d.CODE, d.BSL
        FROM DEFECT_CODE d
        JOIN PRODUCT_INFO p ON p.ID = d.PRODUCT_ID
        WHERE p.PRODUCT_ID = :pid
          AND d.BSL IS NOT NULL
        """,
        {'pid': product_id},
    )
    out = {}
    for row in rows_as_dicts(cursor):
        code = _to_int(row.get('CODE'))
        bsl = row.get('BSL')
        if code is None or bsl is None:
            continue
        try:
            out[code] = float(bsl)
        except (TypeError, ValueError):
            continue
    return out


def query_product_gross(cursor, product_id: str) -> Optional[int]:
    if not product_id:
        return None
    cursor.execute(
        """
        SELECT GROSS_DIE FROM PRODUCT_INFO
        WHERE PRODUCT_ID = :pid AND ROWNUM = 1
        """,
        {'pid': product_id},
    )
    row = cursor.fetchone()
    if not row or row[0] is None:
        return None
    return int(row[0])


def query_latest_testlog_path(
    cursor,
    wafer_id: str,
    step: str,
    hold_dttm: Optional[datetime],
) -> Optional[str]:
    params: dict[str, Any] = {'wid': wafer_id, 'step': step}
    time_filter = ''
    if hold_dttm is not None:
        time_filter = 'AND TEST_DATE <= :hold_dttm'
        params['hold_dttm'] = hold_dttm
    cursor.execute(
        f"""
        SELECT FTP_PATH FROM (
            SELECT FTP_PATH
            FROM FT_WLT_TESTLOG
            WHERE WAFER_ID = :wid AND STEP = :step
              {time_filter}
            ORDER BY TEST_DATE DESC, ID DESC
        )
        WHERE ROWNUM = 1
        """,
        params,
    )
    row = cursor.fetchone()
    if not row or not row[0]:
        return None
    return str(row[0])


def _first_eng_rate_sql(extra_where: str) -> str:
    return f"""
        SELECT
            COUNT(*) AS N,
            SUM(CASE WHEN DISPOSE = 1 THEN 1 ELSE 0 END) AS N_REL
        FROM (
            SELECT
                c.DISPOSE,
                ROW_NUMBER() OVER (
                    PARTITION BY r.ID
                    ORDER BY c.DISPOSE_DTTM, c.ID
                ) AS rn
            FROM FT_HOLD_RECORD r
            JOIN CIRCULATION_HISTORY c ON c.HOLD_RECORD_ID = r.ID
            WHERE r.RECORD_TYPE = :rt
              AND c.DISPOSE IN (1, 2, 3, 5)
              AND r.HOLD_DTTM < :hold_dttm
              AND r.HOLD_DTTM >= :start_dttm
              AND r.ID <> :rid
              {extra_where}
        )
        WHERE rn = 1
    """


def query_release_rate(
    cursor,
    hold_dttm: datetime,
    record_id: int,
    days: int,
    extra_where: str = '',
    extra_params: Optional[dict] = None,
) -> Optional[float]:
    params = {
        'rt': RECORD_TYPE_FT,
        'hold_dttm': hold_dttm,
        'start_dttm': hold_dttm - timedelta(days=days),
        'rid': int(record_id),
    }
    if extra_params:
        params.update(extra_params)
    cursor.execute(_first_eng_rate_sql(extra_where), params)
    row = fetch_one_dict(cursor)
    if not row:
        return None
    n = _to_int(row.get('N'), 0) or 0
    if n <= 0:
        return None
    n_rel = _to_int(row.get('N_REL'), 0) or 0
    return round(n_rel / n, 6)


def query_wafer_prior_hold_cnt(
    cursor,
    record_id: int,
    wafer_ids: list[str],
    hold_dttm: Optional[datetime],
) -> int:
    ids = [w for w in wafer_ids if w]
    if not ids:
        return 0
    binds = {f'w{i}': w for i, w in enumerate(ids)}
    in_clause = ', '.join(f':w{i}' for i in range(len(ids)))
    params: dict[str, Any] = dict(binds)
    params['rid'] = int(record_id)
    params['rt'] = RECORD_TYPE_FT
    time_filter = ''
    if hold_dttm is not None:
        time_filter = 'AND HOLD_DTTM < :hold_dttm'
        params['hold_dttm'] = hold_dttm
    cursor.execute(
        f"""
        SELECT COUNT(*) AS N
        FROM FT_HOLD_RECORD
        WHERE RECORD_TYPE = :rt
          AND ID <> :rid
          AND WAFER_ID IN ({in_clause})
          {time_filter}
        """,
        params,
    )
    row = fetch_one_dict(cursor)
    return _to_int(row.get('N') if row else None, 0) or 0


def query_product_hold_cnt(
    cursor,
    record_id: int,
    product_id: str,
    hold_dttm: datetime,
    days: int = 7,
) -> int:
    cursor.execute(
        """
        SELECT COUNT(*) AS N
        FROM FT_HOLD_RECORD
        WHERE RECORD_TYPE = :rt
          AND ID <> :rid
          AND PRODUCT_ID = :pid
          AND HOLD_DTTM < :hold_dttm
          AND HOLD_DTTM >= :start_dttm
        """,
        {
            'rt': RECORD_TYPE_FT,
            'rid': int(record_id),
            'pid': product_id,
            'hold_dttm': hold_dttm,
            'start_dttm': hold_dttm - timedelta(days=days),
        },
    )
    row = fetch_one_dict(cursor)
    return _to_int(row.get('N') if row else None, 0) or 0


def insert_predict_row(cursor, connection, row: dict) -> int:
    payload = json.dumps(row.get('FEATURES_JSON') or {}, ensure_ascii=False, default=str)
    ins = connection.cursor()
    try:
        ins.setinputsizes(features_json=oracledb.DB_TYPE_CLOB)
        ins.execute(
            """
            INSERT INTO FT_HOLD_PREDICT (
                HOLD_RECORD_ID, MODEL_VERSION, FEATURE_VERSION, P_RELEASE,
                BYSITE_INDEX, ROUTE_IS_ENG, MISSING_BYSITE, MISSING_TEST_WAFER,
                FEATURES_JSON, PREDICTED_AT, LABEL_DISPOSE, LABEL_Y, LABELED_AT
            ) VALUES (
                :hold_record_id, :model_version, :feature_version, :p_release,
                :bysite_index, :route_is_eng, :missing_bysite, :missing_test_wafer,
                :features_json, SYSDATE, :label_dispose, :label_y, :labeled_at
            )
            """,
            {
                'hold_record_id': row['HOLD_RECORD_ID'],
                'model_version': row['MODEL_VERSION'],
                'feature_version': row['FEATURE_VERSION'],
                'p_release': row.get('P_RELEASE'),
                'bysite_index': row.get('BYSITE_INDEX'),
                'route_is_eng': row.get('ROUTE_IS_ENG'),
                'missing_bysite': int(row.get('MISSING_BYSITE') or 0),
                'missing_test_wafer': int(row.get('MISSING_TEST_WAFER') or 0),
                'features_json': payload,
                'label_dispose': row.get('LABEL_DISPOSE'),
                'label_y': row.get('LABEL_Y'),
                'labeled_at': row.get('LABELED_AT'),
            },
        )
        connection.commit()
        return ins.rowcount or 0
    finally:
        ins.close()


def backfill_labels(cursor, connection, batch_size: int = 200) -> int:
    cursor.execute(
        """
        UPDATE FT_HOLD_PREDICT p
        SET (
            LABEL_DISPOSE,
            LABEL_Y,
            LABELED_AT
        ) = (
            SELECT
                f.DISPOSE,
                CASE WHEN f.DISPOSE = 1 THEN 1 ELSE 0 END,
                SYSDATE
            FROM (
                SELECT
                    c.HOLD_RECORD_ID,
                    c.DISPOSE,
                    ROW_NUMBER() OVER (
                        PARTITION BY c.HOLD_RECORD_ID
                        ORDER BY c.DISPOSE_DTTM, c.ID
                    ) AS rn
                FROM CIRCULATION_HISTORY c
                WHERE c.DISPOSE IN (1, 2, 3, 5)
            ) f
            WHERE f.HOLD_RECORD_ID = p.HOLD_RECORD_ID
              AND f.rn = 1
        )
        WHERE p.LABEL_Y IS NULL
          AND EXISTS (
                SELECT 1
                FROM CIRCULATION_HISTORY c
                WHERE c.HOLD_RECORD_ID = p.HOLD_RECORD_ID
                  AND c.DISPOSE IN (1, 2, 3, 5)
          )
          AND ROWNUM <= :n
        """,
        {'n': int(batch_size)},
    )
    n = cursor.rowcount or 0
    connection.commit()
    return n


def query_labeled_predict_rows(
    cursor,
    model_version: Optional[str] = None,
) -> list[dict]:
    sql = """
        SELECT
            ID, HOLD_RECORD_ID, MODEL_VERSION, FEATURE_VERSION, P_RELEASE,
            BYSITE_INDEX, ROUTE_IS_ENG, MISSING_BYSITE, MISSING_TEST_WAFER,
            FEATURES_JSON, PREDICTED_AT, LABEL_DISPOSE, LABEL_Y, LABELED_AT
        FROM FT_HOLD_PREDICT
        WHERE LABEL_Y IS NOT NULL
    """
    params: dict[str, Any] = {}
    if model_version:
        sql += " AND MODEL_VERSION = :mv"
        params['mv'] = model_version
    sql += " ORDER BY PREDICTED_AT ASC, ID ASC"
    cursor.execute(sql, params)
    rows = rows_as_dicts(cursor)
    for row in rows:
        raw = row.get('FEATURES_JSON')
        if raw is None:
            row['FEATURES'] = {}
            continue
        if hasattr(raw, 'read'):
            raw = raw.read()
        if isinstance(raw, bytes):
            raw = raw.decode('utf-8', errors='replace')
        try:
            row['FEATURES'] = json.loads(raw) if raw else {}
        except (TypeError, json.JSONDecodeError):
            row['FEATURES'] = {}
    return rows
