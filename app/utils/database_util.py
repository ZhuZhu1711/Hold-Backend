import oracledb
from datetime import date
import logging
import os
import re
from logging.handlers import RotatingFileHandler

USER = "FT_OWEN"
PWD = "Mee0MvpgXU!Lcp"
DSN = "172.18.202.5:1521/jsqy"

# 1. 获取 logger 实例
logger = logging.getLogger(__name__)
# 设置日志级别，否则默认只有 WARNING 及以上才会输出
logger.setLevel(logging.INFO)

# 2. 确保日志目录存在
if not os.path.exists('logs'):
    os.makedirs('./logs')
    
handler = RotatingFileHandler(
    'logs/test_log.log', 
    maxBytes=50 * 1024 * 1024,  # 50MB
    backupCount=3, 
    encoding='utf-8'
)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)

_ALLOWED_HOLD_INFO_TABLES = {'FT_HOLD_INFO', 'FT_HOLD_INFO_TEST'}
_ALLOWED_HOLD_RECORD_TABLES = {'FT_HOLD_RECORD', 'FT_HOLD_RECORD_TEST'}
_ALLOWED_CIRCULATION_TABLES = {'CIRCULATION_HISTORY', 'CIRCULATION_HISTORY_TEST'}
_ALLOWED_HOLD_PREDICT_TABLES = {'FT_HOLD_PREDICT', 'FT_HOLD_PREDICT_TEST'}
_ALLOWED_SEQS = {
    'FT_HOLD_RECORD_SEQ',
    'FT_HOLD_RECORD_TEST_SEQ',
    'SEQ_CIRCULATION',
    'SEQ_CIRCULATION_TEST',
}
_RECORD_CIRC_MAP = {
    'FT_HOLD_RECORD': 'CIRCULATION_HISTORY',
    'FT_HOLD_RECORD_TEST': 'CIRCULATION_HISTORY_TEST',
}
_RECORD_SEQ_MAP = {
    'FT_HOLD_RECORD': 'FT_HOLD_RECORD_SEQ',
    'FT_HOLD_RECORD_TEST': 'FT_HOLD_RECORD_TEST_SEQ',
}
_CIRC_SEQ_MAP = {
    'CIRCULATION_HISTORY': 'SEQ_CIRCULATION',
    'CIRCULATION_HISTORY_TEST': 'SEQ_CIRCULATION_TEST',
}
# 源表 HOLD_RECORD_ID：0/NULL=待处理；>0=已关联；-1=转换失败脏数据（需人工）
HOLD_RECORD_ID_PENDING = 0
HOLD_RECORD_ID_DIRTY = -1


def resolve_hold_record_table(name=None) -> str:
    from app.config import Config
    tbl = (name or getattr(Config, 'HOLD_RECORD_TABLE', None) or 'FT_HOLD_RECORD').upper()
    if tbl not in _ALLOWED_HOLD_RECORD_TABLES:
        raise ValueError(f'非法 HOLD_RECORD 表名: {tbl}')
    return tbl


def resolve_circulation_table(name=None, record_table=None) -> str:
    from app.config import Config
    if name:
        tbl = name.upper()
    elif record_table:
        tbl = _RECORD_CIRC_MAP.get(record_table.upper(), '')
    else:
        tbl = (getattr(Config, 'CIRCULATION_HISTORY_TABLE', None) or 'CIRCULATION_HISTORY').upper()
    if tbl not in _ALLOWED_CIRCULATION_TABLES:
        raise ValueError(f'非法 CIRCULATION_HISTORY 表名: {tbl}')
    return tbl


def resolve_hold_predict_table(name=None) -> str:
    from app.config import Config
    tbl = (name or getattr(Config, 'HOLD_PREDICT_TABLE', None) or 'FT_HOLD_PREDICT').upper()
    if tbl not in _ALLOWED_HOLD_PREDICT_TABLES:
        raise ValueError(f'非法 HOLD_PREDICT 表名: {tbl}')
    return tbl


def seq_for_hold_record(record_table: str) -> str:
    seq = _RECORD_SEQ_MAP.get((record_table or '').upper())
    if not seq:
        raise ValueError(f'非法 HOLD_RECORD 表名: {record_table}')
    return seq


def seq_for_circulation(circ_table: str) -> str:
    seq = _CIRC_SEQ_MAP.get((circ_table or '').upper())
    if not seq:
        raise ValueError(f'非法 CIRCULATION_HISTORY 表名: {circ_table}')
    return seq


def _next_positive_seq(cursor, seq_name: str) -> int:
    """取序列下一个 >0 的值（部分序列 MIN_VALUE=0，需跳过哨兵 0）。"""
    if seq_name not in _ALLOWED_SEQS:
        raise ValueError(f"非法序列名: {seq_name}")
    for _ in range(5):
        cursor.execute(f"SELECT {seq_name}.NEXTVAL FROM DUAL")
        val = cursor.fetchone()[0]
        if val is not None and int(val) > 0:
            return int(val)
    raise RuntimeError(f"序列 {seq_name} 连续返回非法 ID(<=0)")


def insert_FT_WLT_TESTLOG(wafer_id: str, equip_id: str, product_id:str, ftp_path: str, step: str, test_date: date)->int:
    connection = oracledb.connect(
        user=USER,
        password=PWD,
        dsn=DSN 
    )
    sql = """
        INSERT INTO FT_WLT_TESTLOG (WAFER_ID, EQUIP_ID, PRODUCT_ID, FTP_PATH, STEP, TEST_DATE)
        VALUES (:1, :2, :3, :4, :5, :6)
    """
    
    try:
        with connection.cursor() as cursor:
            params = [wafer_id, equip_id, product_id, ftp_path, step, test_date]
            cursor.execute(sql, params)
            
            affect_row_count = cursor.rowcount
            if affect_row_count > 0:
                connection.commit()
                return affect_row_count
    except Exception as e:
        logger.info(e)
        connection.rollback()
        return -1
    finally:
        connection.close()
        
def query_testlog_history(test_date: date):
    connection = oracledb.connect(
        user=USER,
        password=PWD,
        dsn=DSN 
    )
    sql = """
        SELECT 
            FTP_PATH
        FROM 
            FT_WLT_TESTLOG
        WHERE 
            TEST_DATE = :1
        ORDER BY CREATE_TIME DESC
    """
    
    try:
        with connection.cursor() as cursor:
            params = [test_date]
            cursor.execute(sql, params)
            return cursor.fetchall()
            
    except Exception as e:
        logger.info(e)
        return None
    finally:
        connection.close()


def query_online_hold_info(table_name: str = 'FT_HOLD_INFO_TEST'):
    """
    查询指定表中在线且尚未关联 hold_record 的 hold_info
    （HOLDING = 0 且 HOLD_RECORD_ID 为 NULL/0）。
    HOLD_RECORD_ID = -1 视为转换失败/无需转换的脏数据，轮询一律跳过，需人工处置。

    仅捞取满足 dispose_api.md「处置单划分」的候选行：
      FT  : PRODUCT_ID LIKE '%-3.5', HOLD_CODE∈(023,024,025,027,028,AQL_HOLD), STATION∉(FAOIFINISH,FFVI)
      FVI : HOLD_CODE=023, STATION∈(FAOIFINISH,FFVI)
      WLT : PRODUCT_ID LIKE '%-2.6', HOLD_CODE∈(004,022), STATION=WOQC
    精确 RECORD_TYPE 仍由调用方按同样规则判定后写入 FT_HOLD_RECORD。

    为保证 MES 多条同 wafer 记录插入完整：固定排除 HOLD_DTTM 最新的那个
    WAFER_ID 的全部记录（HOLD_DTTM 为 VARCHAR2，格式 YYYY-MM-DD HH24:MI:SS，
    可直接按字符串排序），留给下次轮询再处理。

    返回 list[dict]，字段名与表列名一致；失败返回 None。
    """
    # 表名不能走 bind，做白名单校验防止注入
    if table_name.upper() not in _ALLOWED_HOLD_INFO_TABLES:
        logger.error(f"非法 hold_info 表名: {table_name}")
        return None

    connection = oracledb.connect(
        user=USER,
        password=PWD,
        dsn=DSN
    )
    # 仅待处理(NULL/0)；排除已关联(>0)与脏数据(-1)；
    # 处置单划分三选一（与 resolve_record_type 保持一致）
    base_filter = """
            HOLDING = 0
            AND NVL(HOLD_RECORD_ID, 0) = 0
            AND (
                (
                    PRODUCT_ID LIKE '%-3.5'
                    AND HOLD_CODE IN ('023', '024', '025', '027', '028', 'AQL_HOLD')
                    AND STATION NOT IN ('FAOIFINISH', 'FFVI')
                )
                OR (
                    HOLD_CODE = '023'
                    AND STATION IN ('FAOIFINISH', 'FFVI')
                )
                OR (
                    PRODUCT_ID LIKE '%-2.6'
                    AND HOLD_CODE IN ('004', '022')
                    AND STATION = 'WOQC'
                )
            )
    """
    tbl = table_name.upper()

    # HOLD_DTTM 为 VARCHAR2(YYYY-MM-DD HH24:MI:SS)，字典序即时间序；
    # 取 HOLD_DTTM 最大的一条所在 WAFER_ID，排除该 wafer 全部 N 条记录
    sql = f"""
        SELECT
            ID,
            HOLD_DTTM,
            STATION,
            EQUIP_ID,
            PRODUCT_ID,
            LOT_ID,
            WAFER_ID,
            HOLD_CODE,
            HOLD_REASON,
            SOURCE,
            SECOND_CODE,
            ROUTE_ID,
            GRADE_NUM,
            HOLD_RECORD_ID,
            HOLDING,
            REMARK
        FROM
            {tbl}
        WHERE
            {base_filter}
            AND WAFER_ID <> (
                SELECT WAFER_ID FROM (
                    SELECT WAFER_ID
                    FROM {tbl}
                    WHERE
                        {base_filter}
                    ORDER BY HOLD_DTTM DESC, ID DESC
                ) WHERE ROWNUM = 1
            )
        ORDER BY
            WAFER_ID, HOLD_DTTM, ID
    """

    try:
        with connection.cursor() as cursor:
            cursor.execute(sql)
            columns = [col[0] for col in cursor.description]
            rows = cursor.fetchall()
            return [dict(zip(columns, row)) for row in rows]
    except Exception as e:
        logger.error(f"查询在线 hold_info 失败: {e}", exc_info=True)
        return None
    finally:
        connection.close()


DISPOSE_CLOSE = 99
_AUTO_CLOSE_DETAIL = 'MES已解hold，系统自动关闭'


def query_released_unclosed_hold_records(
    info_table: str = 'FT_HOLD_INFO_TEST',
    record_table: str = 'FT_HOLD_RECORD',
    limit: int = 200,
):
    """
    查询「关联 hold_info 已全部解 hold（无 HOLDING=0），但 record 尚未关闭」的 hold_record ID。
    从 STATUS<>99 的 record 侧驱动，避免扫全表历史 HOLDING=1。
    成功返回 list[int]；失败返回 None。
    """
    info_tbl = (info_table or '').upper()
    record_tbl = (record_table or '').upper()
    if info_tbl not in _ALLOWED_HOLD_INFO_TABLES:
        logger.error(f"非法 hold_info 表名: {info_table}")
        return None
    if record_tbl not in _ALLOWED_HOLD_RECORD_TABLES:
        logger.error(f"非法 hold_record 表名: {record_table}")
        return None

    try:
        limit_n = max(1, int(limit or 200))
    except (TypeError, ValueError):
        limit_n = 200

    sql = f"""
        SELECT r.ID
        FROM {record_tbl} r
        WHERE NVL(r.STATUS, 0) <> :closed
          AND EXISTS (
              SELECT 1 FROM {info_tbl} i
              WHERE i.HOLD_RECORD_ID = r.ID
                AND i.HOLDING = 1
          )
          AND NOT EXISTS (
              SELECT 1 FROM {info_tbl} i
              WHERE i.HOLD_RECORD_ID = r.ID
                AND NVL(i.HOLDING, 1) = 0
          )
          AND ROWNUM <= :lim
    """

    connection = oracledb.connect(user=USER, password=PWD, dsn=DSN)
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, {'closed': DISPOSE_CLOSE, 'lim': limit_n})
            rows = cursor.fetchall()
            return [int(row[0]) for row in rows if row and row[0] is not None]
    except Exception as e:
        logger.error(f"查询待自动关闭 hold_record 失败: {e}", exc_info=True)
        return None
    finally:
        connection.close()


def auto_close_hold_records(
    record_ids,
    record_table: str = 'FT_HOLD_RECORD',
    actor_user_id: int = 1,
    dispose_detail: str | None = None,
):
    """
    系统/root 自动关闭 hold_record：插入 CIRCULATION_HISTORY(DISPOSE=99)，
    回写 STATUS=99 / LAST_CIRCULATION_ID。
    同一 connection 内逐条处理；单条失败记日志并继续。
    dispose_detail 可覆盖默认备注；无待处理返回 (0, 0)。
    返回 (ok_count, fail_count)。
    """
    record_tbl = (record_table or '').upper()
    if record_tbl not in _ALLOWED_HOLD_RECORD_TABLES:
        logger.error(f"非法 hold_record 表名: {record_table}")
        return -1, -1
    circ_tbl = resolve_circulation_table(record_table=record_tbl)
    circ_seq = seq_for_circulation(circ_tbl)

    ids = []
    seen = set()
    for raw in record_ids or []:
        try:
            rid = int(raw)
        except (TypeError, ValueError):
            continue
        if rid <= 0 or rid in seen:
            continue
        seen.add(rid)
        ids.append(rid)
    if not ids:
        return 0, 0

    try:
        actor_id = int(actor_user_id)
    except (TypeError, ValueError):
        actor_id = 1

    detail = (dispose_detail or '').strip() or _AUTO_CLOSE_DETAIL

    insert_circ_sql = f"""
        INSERT INTO {circ_tbl} (
            ID,
            HOLD_RECORD_ID,
            DISPOSED_OWNER_ID,
            DISPOSE,
            NEXT_OWNER_ID,
            DISPOSE_SOURCE,
            DISPOSE_DTTM,
            DISPOSE_TYPE,
            DISPOSE_DETAIL
        ) VALUES (
            :circ_id,
            :hold_record_id,
            :disposed_owner_id,
            :dispose,
            NULL,
            :dispose_source,
            SYSDATE,
            :dispose_type,
            :dispose_detail
        )
    """
    update_record_sql = f"""
        UPDATE {record_tbl}
        SET LAST_CIRCULATION_ID = :circ_id,
            STATUS = :status
        WHERE ID = :rid
          AND NVL(STATUS, 0) <> :closed
    """

    ok, fail = 0, 0
    connection = oracledb.connect(user=USER, password=PWD, dsn=DSN)
    try:
        with connection.cursor() as cursor:
            for rid in ids:
                try:
                    circ_id = _next_positive_seq(cursor, circ_seq)
                    cursor.execute(
                        insert_circ_sql,
                        {
                            'circ_id': circ_id,
                            'hold_record_id': rid,
                            'disposed_owner_id': actor_id,
                            'dispose': DISPOSE_CLOSE,
                            'dispose_source': 'SYS',
                            'dispose_type': DISPOSE_CLOSE,
                            'dispose_detail': detail,
                        },
                    )
                    cursor.execute(
                        update_record_sql,
                        {
                            'circ_id': circ_id,
                            'status': DISPOSE_CLOSE,
                            'rid': rid,
                            'closed': DISPOSE_CLOSE,
                        },
                    )
                    if (cursor.rowcount or 0) <= 0:
                        connection.rollback()
                        logger.info(
                            f"自动关闭跳过 hold_record id={rid}（已关闭或不存在）"
                        )
                        continue
                    connection.commit()
                    ok += 1
                    logger.info(
                        f"自动关闭成功 hold_record id={rid}, "
                        f"circulation_id={circ_id}, disposed_owner_id={actor_id}"
                    )
                except Exception as e:
                    connection.rollback()
                    fail += 1
                    logger.error(
                        f"自动关闭失败 hold_record id={rid}: {e}",
                        exc_info=True,
                    )
        return ok, fail
    except Exception as e:
        connection.rollback()
        logger.error(f"自动关闭 hold_record 连接级失败: {e}", exc_info=True)
        return ok, fail + (len(ids) - ok - fail)
    finally:
        connection.close()


def mark_hold_infos_dirty(
    source_info_ids,
    info_table: str = 'FT_HOLD_INFO_TEST',
    reason: str = '',
):
    """
    将源 hold_info 标记为脏数据：HOLD_RECORD_ID = -1，并写入 REMARK（失败原因）。
    仅更新当前仍为待处理(NULL/0)的行；成功返回更新行数，失败返回 -1。
    """
    info_tbl = (info_table or '').upper()
    if info_tbl not in _ALLOWED_HOLD_INFO_TABLES:
        logger.error(f"非法 hold_info 表名: {info_table}")
        return -1

    ids = [int(i) for i in (source_info_ids or []) if i is not None]
    if not ids:
        return 0

    remark = (str(reason).strip() if reason else '') or 'merge failed'
    if len(remark) > 512:
        remark = remark[:512]

    id_binds = {f'id{i}': v for i, v in enumerate(ids)}
    id_ph = ', '.join(f':id{i}' for i in range(len(ids)))
    sql = f"""
        UPDATE {info_tbl}
        SET HOLD_RECORD_ID = :dirty_id,
            REMARK = :remark
        WHERE ID IN ({id_ph})
          AND NVL(HOLD_RECORD_ID, 0) = 0
    """

    connection = oracledb.connect(user=USER, password=PWD, dsn=DSN)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                sql,
                {
                    'dirty_id': HOLD_RECORD_ID_DIRTY,
                    'remark': remark,
                    **id_binds,
                },
            )
            n = cursor.rowcount or 0
            connection.commit()
            logger.warning(
                f"标记脏数据 {info_tbl} HOLD_RECORD_ID={HOLD_RECORD_ID_DIRTY} "
                f"更新 {n}/{len(ids)} 行，原因: {remark}"
            )
            return n
    except Exception as e:
        connection.rollback()
        logger.error(f"标记脏数据失败: {e}", exc_info=True)
        return -1
    finally:
        connection.close()


def insert_hold_record_and_link(
    record: dict,
    source_info_ids,
    info_table: str = 'FT_HOLD_INFO_TEST',
    record_table: str = 'FT_HOLD_RECORD',
):
    """
    同一事务内完成：
      1) 插入 FT_HOLD_RECORD（含 HOLD_DTTM）
      2) 按 PRODUCT_ID 查 PRODUCT_INFO.PRO_ENG_ID 作为 NEXT_OWNER_ID（无则 1）
      3) 插入 CIRCULATION_HISTORY（DISPOSE_TYPE=0），并回写 LAST_CIRCULATION_ID
      4) 回写源表 HOLD_RECORD_ID
    成功返回新 hold_record ID；失败整单回滚，并将源行 HOLD_RECORD_ID 置为 -1，返回 None。
    """
    info_tbl = (info_table or '').upper()
    record_tbl = (record_table or '').upper()
    if info_tbl not in _ALLOWED_HOLD_INFO_TABLES:
        logger.error(f"非法 hold_info 表名: {info_table}")
        return None
    if record_tbl not in _ALLOWED_HOLD_RECORD_TABLES:
        logger.error(f"非法 hold_record 表名: {record_table}")
        return None
    circ_tbl = resolve_circulation_table(record_table=record_tbl)
    record_seq = seq_for_hold_record(record_tbl)
    circ_seq = seq_for_circulation(circ_tbl)

    ids = [int(i) for i in (source_info_ids or []) if i is not None]
    if not ids:
        logger.error("insert_hold_record_and_link: 无源 hold_info ID，跳过")
        return None

    def _fail(reason: str):
        mark_hold_infos_dirty(ids, info_table=info_tbl, reason=reason)
        return None

    required = (
        'PRODUCT_ID', 'STATION', 'EQUIP_ID', 'LOT_ID', 'WAFER_ID',
        'SOURCE', 'RECORD_TYPE', 'STATUS',
    )
    missing = [k for k in required if record.get(k) is None]
    if missing:
        logger.error(f"insert_hold_record_and_link: 缺少必填字段 {missing}")
        return _fail(f"缺少必填字段 {missing}")

    # SOURCE: 0=MES → SYS；1=JDY → JDY
    dispose_source = 'JDY' if int(record['SOURCE']) == 1 else 'SYS'

    insert_record_sql = f"""
        INSERT INTO {record_tbl} (
            ID,
            PRODUCT_ID, STATION, EQUIP_ID, LOT_ID, WAFER_ID,
            HOLD_CODE, HOLD_REASON, SOURCE, SECOND_CODE, ROUTE_ID,
            GRADE_NUM, RECORD_TYPE, STATUS, HOLD_DTTM, HOLD_WAFER_ATTR
        ) VALUES (
            :new_id,
            :product_id, :station, :equip_id, :lot_id, :wafer_id,
            :hold_code, :hold_reason, :source, :second_code, :route_id,
            :grade_num, :record_type, :status, :hold_dttm, :hold_wafer_attr
        )
    """

    lookup_owner_sql = """
        SELECT PRO_ENG_ID
        FROM PRODUCT_INFO
        WHERE PRODUCT_ID = :product_id
          AND ROWNUM = 1
    """

    insert_circ_sql = f"""
        INSERT INTO {circ_tbl} (
            ID,
            HOLD_RECORD_ID,
            DISPOSED_OWNER_ID,
            DISPOSE,
            NEXT_OWNER_ID,
            DISPOSE_SOURCE,
            DISPOSE_DTTM,
            DISPOSE_TYPE,
            DISPOSE_DETAIL
        ) VALUES (
            :circ_id,
            :hold_record_id,
            1,
            0,
            :next_owner_id,
            :dispose_source,
            SYSDATE,
            0,
            :dispose_detail
        )
    """

    update_last_circ_sql = f"""
        UPDATE {record_tbl}
        SET LAST_CIRCULATION_ID = :circ_id
        WHERE ID = :record_id
    """

    id_binds = {f'id{i}': v for i, v in enumerate(ids)}
    id_ph = ', '.join(f':id{i}' for i in range(len(ids)))
    update_info_sql = f"""
        UPDATE {info_tbl}
        SET HOLD_RECORD_ID = :record_id
        WHERE ID IN ({id_ph})
          AND NVL(HOLD_RECORD_ID, 0) = 0
    """

    connection = oracledb.connect(user=USER, password=PWD, dsn=DSN)
    try:
        with connection.cursor() as cursor:
            # 1) hold_record
            new_id = _next_positive_seq(cursor, record_seq)
            cursor.execute(
                insert_record_sql,
                {
                    'new_id': new_id,
                    'product_id': record['PRODUCT_ID'],
                    'station': record['STATION'],
                    'equip_id': record['EQUIP_ID'],
                    'lot_id': record['LOT_ID'],
                    'wafer_id': record['WAFER_ID'],
                    'hold_code': record.get('HOLD_CODE'),
                    'hold_reason': record.get('HOLD_REASON'),
                    'source': record['SOURCE'],
                    'second_code': record.get('SECOND_CODE'),
                    'route_id': record.get('ROUTE_ID'),
                    'grade_num': record.get('GRADE_NUM'),
                    'record_type': record['RECORD_TYPE'],
                    'status': record['STATUS'],
                    'hold_dttm': record.get('HOLD_DTTM'),
                    'hold_wafer_attr': int(record.get('HOLD_WAFER_ATTR') or 0),
                },
            )

            # 2) NEXT_OWNER_ID ← PRODUCT_INFO.PRO_ENG_ID，缺省 1
            cursor.execute(lookup_owner_sql, {'product_id': record['PRODUCT_ID']})
            owner_row = cursor.fetchone()
            next_owner_id = 1
            if owner_row and owner_row[0] is not None:
                next_owner_id = int(owner_row[0])

            # 3) circulation_history
            circ_id = _next_positive_seq(cursor, circ_seq)
            cursor.execute(
                insert_circ_sql,
                {
                    'circ_id': circ_id,
                    'hold_record_id': new_id,
                    'next_owner_id': next_owner_id,
                    'dispose_source': dispose_source,
                    'dispose_detail': None,
                },
            )

            cursor.execute(
                update_last_circ_sql,
                {'circ_id': circ_id, 'record_id': new_id},
            )

            # 4) 回写源表 HOLD_RECORD_ID
            cursor.execute(
                update_info_sql,
                {'record_id': new_id, **id_binds},
            )
            linked = cursor.rowcount or 0
            if linked <= 0:
                connection.rollback()
                logger.error(
                    f"插入 hold_record id={new_id} 成功，但回写源表 "
                    f"{info_tbl} HOLD_RECORD_ID 影响 0 行，已回滚"
                )
                return _fail("回写源表 HOLD_RECORD_ID 影响 0 行")

            connection.commit()
            logger.info(
                f"写入 {record_tbl} id={new_id}, wafer={record['WAFER_ID']}, "
                f"HOLD_DTTM={record.get('HOLD_DTTM')}, "
                f"circulation_id={circ_id}, next_owner_id={next_owner_id}, "
                f"回写 {info_tbl} {linked}/{len(ids)} 行 HOLD_RECORD_ID"
            )
            return new_id
    except Exception as e:
        connection.rollback()
        logger.error(
            f"插入 hold_record / circulation / 回写 HOLD_RECORD_ID 失败: {e}",
            exc_info=True,
        )
        return _fail(str(e))
    finally:
        connection.close()


def insert_manual_hold_record(
    record: dict,
    record_table: str = 'FT_HOLD_RECORD',
):
    """
    手提 Hold：直接插入 FT_HOLD_RECORD + 创建流转（DISPOSE=0），不写 FT_HOLD_INFO。
    SOURCE 应为 1。成功返回新 ID；失败返回 None。
    """
    record_tbl = (record_table or '').upper()
    if record_tbl not in _ALLOWED_HOLD_RECORD_TABLES:
        logger.error(f"非法 hold_record 表名: {record_table}")
        return None
    circ_tbl = resolve_circulation_table(record_table=record_tbl)
    record_seq = seq_for_hold_record(record_tbl)
    circ_seq = seq_for_circulation(circ_tbl)

    required = (
        'PRODUCT_ID', 'STATION', 'EQUIP_ID', 'LOT_ID', 'WAFER_ID',
        'SOURCE', 'RECORD_TYPE', 'STATUS',
    )
    missing = [k for k in required if record.get(k) is None]
    if missing:
        logger.error(f"insert_manual_hold_record: 缺少必填字段 {missing}")
        return None

    dispose_source = 'JDY' if int(record['SOURCE']) == 1 else 'SYS'

    insert_record_sql = f"""
        INSERT INTO {record_tbl} (
            ID,
            PRODUCT_ID, STATION, EQUIP_ID, LOT_ID, WAFER_ID,
            HOLD_CODE, HOLD_REASON, SOURCE, SECOND_CODE, ROUTE_ID,
            GRADE_NUM, RECORD_TYPE, STATUS, HOLD_DTTM, ANNEX_FTP_PATH,
            HOLD_WAFER_ATTR
        ) VALUES (
            :new_id,
            :product_id, :station, :equip_id, :lot_id, :wafer_id,
            :hold_code, :hold_reason, :source, :second_code, :route_id,
            :grade_num, :record_type, :status, :hold_dttm, :annex_ftp_path,
            :hold_wafer_attr
        )
    """
    lookup_owner_sql = """
        SELECT PRO_ENG_ID
        FROM PRODUCT_INFO
        WHERE PRODUCT_ID = :product_id
          AND ROWNUM = 1
    """
    insert_circ_sql = f"""
        INSERT INTO {circ_tbl} (
            ID,
            HOLD_RECORD_ID,
            DISPOSED_OWNER_ID,
            DISPOSE,
            NEXT_OWNER_ID,
            DISPOSE_SOURCE,
            DISPOSE_DTTM,
            DISPOSE_TYPE,
            DISPOSE_DETAIL
        ) VALUES (
            :circ_id,
            :hold_record_id,
            1,
            0,
            :next_owner_id,
            :dispose_source,
            SYSDATE,
            0,
            :dispose_detail
        )
    """
    update_last_circ_sql = f"""
        UPDATE {record_tbl}
        SET LAST_CIRCULATION_ID = :circ_id
        WHERE ID = :record_id
    """

    connection = oracledb.connect(user=USER, password=PWD, dsn=DSN)
    try:
        with connection.cursor() as cursor:
            new_id = _next_positive_seq(cursor, record_seq)
            cursor.execute(
                insert_record_sql,
                {
                    'new_id': new_id,
                    'product_id': record['PRODUCT_ID'],
                    'station': record['STATION'],
                    'equip_id': record['EQUIP_ID'],
                    'lot_id': record['LOT_ID'],
                    'wafer_id': record['WAFER_ID'],
                    'hold_code': record.get('HOLD_CODE'),
                    'hold_reason': record.get('HOLD_REASON'),
                    'source': record['SOURCE'],
                    'second_code': record.get('SECOND_CODE'),
                    'route_id': record.get('ROUTE_ID'),
                    'grade_num': record.get('GRADE_NUM'),
                    'record_type': record['RECORD_TYPE'],
                    'status': record['STATUS'],
                    'hold_dttm': record.get('HOLD_DTTM'),
                    'annex_ftp_path': record.get('ANNEX_FTP_PATH'),
                    'hold_wafer_attr': int(record.get('HOLD_WAFER_ATTR') or 0),
                },
            )

            cursor.execute(lookup_owner_sql, {'product_id': record['PRODUCT_ID']})
            owner_row = cursor.fetchone()
            next_owner_id = 1
            if owner_row and owner_row[0] is not None:
                next_owner_id = int(owner_row[0])

            circ_id = _next_positive_seq(cursor, circ_seq)
            cursor.execute(
                insert_circ_sql,
                {
                    'circ_id': circ_id,
                    'hold_record_id': new_id,
                    'next_owner_id': next_owner_id,
                    'dispose_source': dispose_source,
                    'dispose_detail': None,
                },
            )
            cursor.execute(
                update_last_circ_sql,
                {'circ_id': circ_id, 'record_id': new_id},
            )
            connection.commit()
            logger.info(
                f"手提写入 {record_tbl} id={new_id}, wafer={record['WAFER_ID']}, "
                f"hold_code={record.get('HOLD_CODE')}, "
                f"circulation_id={circ_id}, next_owner_id={next_owner_id}"
            )
            return new_id
    except Exception as e:
        connection.rollback()
        logger.error(f"插入手提 hold_record 失败: {e}", exc_info=True)
        return None
    finally:
        connection.close()


def update_manual_hold_annex_path(record_id, annex_ftp_path, record_table=None) -> bool:
    """回写 ANNEX_FTP_PATH。成功 True。"""
    record_tbl = resolve_hold_record_table(record_table)
    if record_tbl not in _ALLOWED_HOLD_RECORD_TABLES:
        logger.error(f"非法 hold_record 表名: {record_table}")
        return False
    try:
        rid = int(record_id)
    except (TypeError, ValueError):
        return False
    connection = oracledb.connect(user=USER, password=PWD, dsn=DSN)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                    UPDATE {record_tbl}
                    SET ANNEX_FTP_PATH = :path
                    WHERE ID = :rid
                """,
                {'path': annex_ftp_path, 'rid': rid},
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return False
            connection.commit()
            return True
    except Exception as e:
        connection.rollback()
        logger.error(f"回写 ANNEX_FTP_PATH 失败 id={record_id}: {e}", exc_info=True)
        return False
    finally:
        connection.close()


def query_dirty_hold_infos(
    info_table: str = 'FT_HOLD_INFO_TEST',
    product_id: str = '',
    lot_id: str = '',
    wafer_id: str = '',
    station: str = '',
    hold_code: str = '',
    keyword: str = '',
    page: int = 1,
    page_size: int = 20,
):
    """
    查询 HOLD_RECORD_ID=-1 的 hold_info（分页）。
    成功返回 (items, total)；失败返回 (None, -1)。
    """
    info_tbl = (info_table or '').upper()
    if info_tbl not in _ALLOWED_HOLD_INFO_TABLES:
        logger.error(f"非法 hold_info 表名: {info_table}")
        return None, -1

    try:
        page = max(1, int(page or 1))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = max(1, min(int(page_size or 20), 200))
    except (TypeError, ValueError):
        page_size = 20
    offset = (page - 1) * page_size

    where = ["HOLD_RECORD_ID = :dirty_id"]
    params = {
        'dirty_id': HOLD_RECORD_ID_DIRTY,
        'offset': offset,
        'page_size': page_size,
    }

    def _like(field: str, value: str, bind: str):
        text = (value or '').strip()
        if not text:
            return
        where.append(f"UPPER({field}) LIKE UPPER(:{bind})")
        params[bind] = f'%{text}%'

    _like('PRODUCT_ID', product_id, 'product_id')
    _like('LOT_ID', lot_id, 'lot_id')
    _like('WAFER_ID', wafer_id, 'wafer_id')
    _like('STATION', station, 'station')
    _like('HOLD_CODE', hold_code, 'hold_code')

    kw = (keyword or '').strip()
    if kw:
        where.append(
            "("
            "UPPER(PRODUCT_ID) LIKE UPPER(:keyword) OR "
            "UPPER(LOT_ID) LIKE UPPER(:keyword) OR "
            "UPPER(WAFER_ID) LIKE UPPER(:keyword) OR "
            "UPPER(HOLD_REASON) LIKE UPPER(:keyword) OR "
            "UPPER(NVL(REMARK, '')) LIKE UPPER(:keyword)"
            ")"
        )
        params['keyword'] = f'%{kw}%'

    where_sql = ' AND '.join(where)
    count_sql = f"SELECT COUNT(*) AS CNT FROM {info_tbl} WHERE {where_sql}"
    data_sql = f"""
        SELECT
            ID, HOLD_DTTM, STATION, EQUIP_ID, PRODUCT_ID, LOT_ID, WAFER_ID,
            HOLD_CODE, HOLD_REASON, SOURCE, SECOND_CODE, ROUTE_ID, GRADE_NUM,
            HOLD_RECORD_ID, HOLDING, REMARK
        FROM {info_tbl}
        WHERE {where_sql}
        ORDER BY HOLD_DTTM DESC, ID DESC
        OFFSET :offset ROWS FETCH NEXT :page_size ROWS ONLY
    """

    connection = oracledb.connect(user=USER, password=PWD, dsn=DSN)
    try:
        with connection.cursor() as cursor:
            cursor.execute(count_sql, {
                k: v for k, v in params.items() if k not in ('offset', 'page_size')
            })
            total = int(cursor.fetchone()[0] or 0)

            cursor.execute(data_sql, params)
            cols = [d[0].upper() for d in cursor.description]
            items = [dict(zip(cols, row)) for row in cursor.fetchall()]
            return items, total
    except Exception as e:
        logger.error(f"查询脏 hold_info 失败: {e}", exc_info=True)
        return None, -1
    finally:
        connection.close()


def query_hold_infos_by_ids(
    source_info_ids,
    info_table: str = 'FT_HOLD_INFO_TEST',
    require_dirty: bool = False,
):
    """
    按 ID 列表查询 hold_info。
    require_dirty=True 时仅返回 HOLD_RECORD_ID=-1 的行。
    成功返回 list[dict]；失败返回 None。
    """
    info_tbl = (info_table or '').upper()
    if info_tbl not in _ALLOWED_HOLD_INFO_TABLES:
        logger.error(f"非法 hold_info 表名: {info_table}")
        return None

    ids = sorted({int(i) for i in (source_info_ids or []) if i is not None})
    if not ids:
        return []

    id_binds = {f'id{i}': v for i, v in enumerate(ids)}
    id_ph = ', '.join(f':id{i}' for i in range(len(ids)))
    dirty_filter = (
        f" AND HOLD_RECORD_ID = {HOLD_RECORD_ID_DIRTY}" if require_dirty else ''
    )
    sql = f"""
        SELECT
            ID, HOLD_DTTM, STATION, EQUIP_ID, PRODUCT_ID, LOT_ID, WAFER_ID,
            HOLD_CODE, HOLD_REASON, SOURCE, SECOND_CODE, ROUTE_ID, GRADE_NUM,
            HOLD_RECORD_ID, HOLDING, REMARK
        FROM {info_tbl}
        WHERE ID IN ({id_ph})
        {dirty_filter}
        ORDER BY HOLD_DTTM, ID
    """

    connection = oracledb.connect(user=USER, password=PWD, dsn=DSN)
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, id_binds)
            cols = [d[0].upper() for d in cursor.description]
            return [dict(zip(cols, row)) for row in cursor.fetchall()]
    except Exception as e:
        logger.error(f"按 ID 查询 hold_info 失败: {e}", exc_info=True)
        return None
    finally:
        connection.close()


def reset_dirty_hold_infos(
    source_info_ids,
    info_table: str = 'FT_HOLD_INFO_TEST',
    operator: str = '',
):
    """
    将脏 hold_info 重置为待处理：HOLD_RECORD_ID=0，REMARK=NULL。
    仅更新当前仍为 -1 的行；成功返回更新行数，失败返回 -1。
    """
    info_tbl = (info_table or '').upper()
    if info_tbl not in _ALLOWED_HOLD_INFO_TABLES:
        logger.error(f"非法 hold_info 表名: {info_table}")
        return -1

    ids = [int(i) for i in (source_info_ids or []) if i is not None]
    if not ids:
        return 0

    id_binds = {f'id{i}': v for i, v in enumerate(ids)}
    id_ph = ', '.join(f':id{i}' for i in range(len(ids)))
    sql = f"""
        UPDATE {info_tbl}
        SET HOLD_RECORD_ID = :pending_id,
            REMARK = NULL
        WHERE ID IN ({id_ph})
          AND HOLD_RECORD_ID = :dirty_id
    """

    connection = oracledb.connect(user=USER, password=PWD, dsn=DSN)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                sql,
                {
                    'pending_id': HOLD_RECORD_ID_PENDING,
                    'dirty_id': HOLD_RECORD_ID_DIRTY,
                    **id_binds,
                },
            )
            n = cursor.rowcount or 0
            connection.commit()
            op = f"，操作者={operator}" if operator else ''
            logger.info(
                f"重置脏 hold_info {info_tbl} → HOLD_RECORD_ID=0 "
                f"更新 {n}/{len(ids)} 行{op}，ids={ids}"
            )
            return n
    except Exception as e:
        connection.rollback()
        logger.error(f"重置脏 hold_info 失败: {e}", exc_info=True)
        return -1
    finally:
        connection.close()


def insert_hold_record_and_link_from_dirty(
    record: dict,
    source_info_ids,
    info_table: str = 'FT_HOLD_INFO_TEST',
    record_table: str = 'FT_HOLD_RECORD',
    operator: str = '',
):
    """
    从已标记脏数据（HOLD_RECORD_ID=-1）的 hold_info 手动创建 hold_record。
    逻辑同 insert_hold_record_and_link，但回写条件为 HOLD_RECORD_ID=-1，
    成功后清 REMARK；失败不改状态（仍为 -1），仅更新 REMARK 为新失败原因。
    成功返回新 hold_record ID；失败返回 None。
    """
    info_tbl = (info_table or '').upper()
    record_tbl = (record_table or '').upper()
    if info_tbl not in _ALLOWED_HOLD_INFO_TABLES:
        logger.error(f"非法 hold_info 表名: {info_table}")
        return None
    if record_tbl not in _ALLOWED_HOLD_RECORD_TABLES:
        logger.error(f"非法 hold_record 表名: {record_table}")
        return None
    circ_tbl = resolve_circulation_table(record_table=record_tbl)
    record_seq = seq_for_hold_record(record_tbl)
    circ_seq = seq_for_circulation(circ_tbl)

    ids = [int(i) for i in (source_info_ids or []) if i is not None]
    if not ids:
        logger.error("insert_hold_record_and_link_from_dirty: 无源 hold_info ID")
        return None

    def _note_fail(reason: str):
        remark = (str(reason).strip() if reason else '') or 'manual create failed'
        if len(remark) > 512:
            remark = remark[:512]
        id_binds_f = {f'id{i}': v for i, v in enumerate(ids)}
        id_ph_f = ', '.join(f':id{i}' for i in range(len(ids)))
        sql_f = f"""
            UPDATE {info_tbl}
            SET REMARK = :remark
            WHERE ID IN ({id_ph_f})
              AND HOLD_RECORD_ID = :dirty_id
        """
        conn_f = oracledb.connect(user=USER, password=PWD, dsn=DSN)
        try:
            with conn_f.cursor() as cur:
                cur.execute(
                    sql_f,
                    {
                        'remark': remark,
                        'dirty_id': HOLD_RECORD_ID_DIRTY,
                        **id_binds_f,
                    },
                )
                conn_f.commit()
            logger.warning(
                f"手动提 record 失败，已回写 REMARK，原因: {remark}，ids={ids}"
            )
        except Exception as e:
            conn_f.rollback()
            logger.error(f"回写手动创建失败原因失败: {e}", exc_info=True)
        finally:
            conn_f.close()
        return None

    required = (
        'PRODUCT_ID', 'STATION', 'EQUIP_ID', 'LOT_ID', 'WAFER_ID',
        'SOURCE', 'RECORD_TYPE', 'STATUS',
    )
    missing = [k for k in required if record.get(k) is None]
    if missing:
        logger.error(
            f"insert_hold_record_and_link_from_dirty: 缺少必填字段 {missing}"
        )
        return _note_fail(f"缺少必填字段 {missing}")

    dispose_source = 'JDY' if int(record['SOURCE']) == 1 else 'SYS'

    insert_record_sql = f"""
        INSERT INTO {record_tbl} (
            ID,
            PRODUCT_ID, STATION, EQUIP_ID, LOT_ID, WAFER_ID,
            HOLD_CODE, HOLD_REASON, SOURCE, SECOND_CODE, ROUTE_ID,
            GRADE_NUM, RECORD_TYPE, STATUS, HOLD_DTTM, HOLD_WAFER_ATTR
        ) VALUES (
            :new_id,
            :product_id, :station, :equip_id, :lot_id, :wafer_id,
            :hold_code, :hold_reason, :source, :second_code, :route_id,
            :grade_num, :record_type, :status, :hold_dttm, :hold_wafer_attr
        )
    """
    lookup_owner_sql = """
        SELECT PRO_ENG_ID
        FROM PRODUCT_INFO
        WHERE PRODUCT_ID = :product_id
          AND ROWNUM = 1
    """
    insert_circ_sql = f"""
        INSERT INTO {circ_tbl} (
            ID,
            HOLD_RECORD_ID,
            DISPOSED_OWNER_ID,
            DISPOSE,
            NEXT_OWNER_ID,
            DISPOSE_SOURCE,
            DISPOSE_DTTM,
            DISPOSE_TYPE,
            DISPOSE_DETAIL
        ) VALUES (
            :circ_id,
            :hold_record_id,
            1,
            0,
            :next_owner_id,
            :dispose_source,
            SYSDATE,
            0,
            :dispose_detail
        )
    """
    update_last_circ_sql = f"""
        UPDATE {record_tbl}
        SET LAST_CIRCULATION_ID = :circ_id
        WHERE ID = :record_id
    """

    id_binds = {f'id{i}': v for i, v in enumerate(ids)}
    id_ph = ', '.join(f':id{i}' for i in range(len(ids)))
    update_info_sql = f"""
        UPDATE {info_tbl}
        SET HOLD_RECORD_ID = :record_id,
            REMARK = NULL
        WHERE ID IN ({id_ph})
          AND HOLD_RECORD_ID = :dirty_id
    """

    connection = oracledb.connect(user=USER, password=PWD, dsn=DSN)
    try:
        with connection.cursor() as cursor:
            new_id = _next_positive_seq(cursor, record_seq)
            cursor.execute(
                insert_record_sql,
                {
                    'new_id': new_id,
                    'product_id': record['PRODUCT_ID'],
                    'station': record['STATION'],
                    'equip_id': record['EQUIP_ID'],
                    'lot_id': record['LOT_ID'],
                    'wafer_id': record['WAFER_ID'],
                    'hold_code': record.get('HOLD_CODE'),
                    'hold_reason': record.get('HOLD_REASON'),
                    'source': record['SOURCE'],
                    'second_code': record.get('SECOND_CODE'),
                    'route_id': record.get('ROUTE_ID'),
                    'grade_num': record.get('GRADE_NUM'),
                    'record_type': record['RECORD_TYPE'],
                    'status': record['STATUS'],
                    'hold_dttm': record.get('HOLD_DTTM'),
                    'hold_wafer_attr': int(record.get('HOLD_WAFER_ATTR') or 0),
                },
            )

            cursor.execute(lookup_owner_sql, {'product_id': record['PRODUCT_ID']})
            owner_row = cursor.fetchone()
            next_owner_id = 1
            if owner_row and owner_row[0] is not None:
                next_owner_id = int(owner_row[0])

            circ_id = _next_positive_seq(cursor, circ_seq)
            cursor.execute(
                insert_circ_sql,
                {
                    'circ_id': circ_id,
                    'hold_record_id': new_id,
                    'next_owner_id': next_owner_id,
                    'dispose_source': dispose_source,
                    'dispose_detail': None,
                },
            )
            cursor.execute(
                update_last_circ_sql,
                {'circ_id': circ_id, 'record_id': new_id},
            )

            cursor.execute(
                update_info_sql,
                {
                    'record_id': new_id,
                    'dirty_id': HOLD_RECORD_ID_DIRTY,
                    **id_binds,
                },
            )
            linked = cursor.rowcount or 0
            if linked <= 0:
                connection.rollback()
                logger.error(
                    f"手动提 record id={new_id} 插入成功但回写脏 hold_info "
                    f"影响 0 行，已回滚；ids={ids}"
                )
                return _note_fail("回写脏 hold_info HOLD_RECORD_ID 影响 0 行")
            if linked < len(ids):
                connection.rollback()
                logger.error(
                    f"手动提 record：期望回写 {len(ids)} 行，实际 {linked}，已回滚"
                )
                return _note_fail(
                    f"部分源行已非脏数据，回写 {linked}/{len(ids)}，已回滚"
                )

            connection.commit()
            op = f", operator={operator}" if operator else ''
            logger.info(
                f"手动提 {record_tbl} id={new_id} 成功{op}, "
                f"wafer={record['WAFER_ID']}, circulation_id={circ_id}, "
                f"回写脏 {info_tbl} {linked}/{len(ids)} 行, ids={ids}"
            )
            return new_id
    except Exception as e:
        connection.rollback()
        logger.error(f"手动提 hold_record 失败: {e}", exc_info=True)
        return _note_fail(str(e))
    finally:
        connection.close()


def _short_defect_code(raw) -> str:
    """取 DEFECT_CODE 最后一个 '-' 之后的文本。"""
    text = str(raw or '').strip()
    if not text:
        return ''
    if '-' in text:
        return text.rsplit('-', 1)[-1].strip()
    return text


def normalize_lot_id(lot_id) -> str:
    """
    MES / 手提 LOT ID → 用于拼真实片号的 lot 前缀。
    含 '-'：截取第一个 '-' 之前。
    含 '.NN'（1~2 位数字，WLT 手提 LOT.NO）：去掉 .NO。
    """
    text = str(lot_id).strip() if lot_id is not None else ''
    if not text:
        return ''
    if '-' in text:
        return text.split('-', 1)[0].strip()
    m = re.match(r'^(.*)\.(\d{1,2})$', text)
    if m:
        return m.group(1).strip()
    return text


def lot_id_digit_suffix_len(lot_id) -> int:
    """
    取 lot_id 最后一个 '-' 后的后缀：若为纯数字则返回位数，否则 0。
    用于同 lot 分析判定合批（位数 > 2）。
    """
    text = str(lot_id).strip() if lot_id is not None else ''
    if not text or '-' not in text:
        return 0
    suffix = text.rsplit('-', 1)[-1].strip()
    if suffix and suffix.isdigit():
        return len(suffix)
    return 0


# FT_HOLD_RECORD.HOLD_WAFER_ATTR 比特位（十进制存储）
HOLD_WAFER_ATTR_VACUUM = 1       # bit0 真空包
HOLD_WAFER_ATTR_ZIYI = 2         # bit1 梓一合批
HOLD_WAFER_ATTR_IQC_ATE = 4      # bit2 IQC_ATE 合批
HOLD_WAFER_ATTR_ATE = 8          # bit3 ATE 合批
HOLD_WAFER_ATTR_FVI = 16         # bit4 FVI 合批


def _parse_equip_num(equip_id):
    """EQUIP_ID 去空白后纯数字才解析，否则 None。"""
    text = str(equip_id).strip() if equip_id is not None else ''
    if not text or not text.isdigit():
        return None
    return int(text)


def _station_is_wlt(station) -> bool:
    """WLT 站点：以 WLT 开头或 WOQC（合批 WLT 站）。"""
    text = str(station).strip().upper() if station is not None else ''
    if not text:
        return False
    return text.startswith('WLT') or text == 'WOQC'


def compute_hold_wafer_attr(lot_id, equip_id, station) -> int:
    """
    按 LOT_ID / EQUIP_ID / STATION 计算 HOLD_WAFER_ATTR（可多 bit OR）。
    须用源 lot（WLT 合批截断前），缺省返回 0。
    """
    lot = str(lot_id).strip() if lot_id is not None else ''
    if not lot:
        return 0

    attr = 0
    has_dash = '-' in lot
    lot_u = lot.upper()

    if not has_dash:
        if (lot_u.startswith('VSH') or lot_u.startswith('TSH')) and not _station_is_wlt(station):
            attr |= HOLD_WAFER_ATTR_VACUUM
        if lot_u.startswith('A'):
            attr |= HOLD_WAFER_ATTR_ATE
        if lot_u.startswith('I'):
            attr |= HOLD_WAFER_ATTR_FVI
    elif lot_id_digit_suffix_len(lot) > 2:
        equip_num = _parse_equip_num(equip_id)
        if equip_num is not None:
            if equip_num >= 200:
                attr |= HOLD_WAFER_ATTR_ZIYI
            else:
                attr |= HOLD_WAFER_ATTR_IQC_ATE

    return attr


def wafer_suffix(wafer_id) -> str:
    """取最后一个 '-' 之后的文本；无 '-' 则原样返回。"""
    text = str(wafer_id).strip() if wafer_id is not None else ''
    if not text:
        return ''
    if '-' in text:
        return text.rsplit('-', 1)[-1].strip()
    return text


def is_fragmented_merged_lot(lot_id, wafer_id) -> bool:
    """
    实物已合批、info 仍分片插入：
      LOT_ID != WAFER_ID，且 LOT_ID 含 '-'，且 '-' 后为数字且位数 > 2。
    例：LOT_ID=C123456-033, WAFER_ID=C123456-03。
    """
    lot = str(lot_id).strip() if lot_id is not None else ''
    wafer = str(wafer_id).strip() if wafer_id is not None else ''
    if not lot or not wafer or lot == wafer:
        return False
    if '-' not in lot:
        return False
    suffix = lot.rsplit('-', 1)[-1].strip()
    return bool(suffix) and suffix.isdigit() and len(suffix) > 2


def format_wafer_id_display(wafer_id) -> str:
    """
    Hold Record 展示用 Wafer：
      - 已是 #03 / #01#02 / #03 #04 展示串 → 原样
      - 含 '-' → '#' + 后缀（C123456-03 → #03）
      - 否则原样
    """
    text = str(wafer_id).strip() if wafer_id is not None else ''
    if not text:
        return ''
    if text.startswith('#'):
        return text
    if '-' in text:
        suffix = text.rsplit('-', 1)[-1].strip()
        return f'#{suffix}' if suffix else text
    return text


def build_merged_wafer_display(wafer_ids, max_len: int = 100) -> str:
    """
    多片 WAFER_ID → 展示串，如 #01#02#03。
    后缀去重；能转成数字的按数值排序，否则按文本。
    """
    seen = set()
    suffixes = []
    for raw in wafer_ids or []:
        suffix = wafer_suffix(raw)
        if not suffix or suffix in seen:
            continue
        seen.add(suffix)
        suffixes.append(suffix)

    def _sort_key(s: str):
        if s.isdigit():
            return (0, int(s))
        return (1, s)

    suffixes.sort(key=_sort_key)
    joined = ''.join(f'#{s}' for s in suffixes)
    if max_len is not None and len(joined) > max_len:
        return joined[:max_len]
    return joined


def expand_display_wafer_ids(wafer_id, lot_id) -> list:
    """
    将展示串 / 完整片号还原为可查 MES 的真实 wafer id 列表。
      - #01#02#03 + lot=C123456 → [C123456-01, C123456-02, C123456-03]
      - #03 #04 #05 + lot=C123456 → 同上（兼容旧空格分隔）
      - #03 + lot → [C123456-03]
      - 普通 C123456-03 → [C123456-03]
    """
    wafer = str(wafer_id).strip() if wafer_id is not None else ''
    lot = normalize_lot_id(lot_id)
    if not wafer:
        return []

    if wafer.startswith('#'):
        if not lot:
            return []
        parts = []
        # 兼容 #01#02 与 #01 #02
        for suffix in re.findall(r'#([^#\s]+)', wafer):
            if suffix:
                parts.append(f'{lot}-{suffix}')
        return parts

    return [wafer]


def query_fvi_defect_details(lot_id: str, line_type: str = 'FT'):
    """
    查询 FVI 缺陷明细（MES DB link）。
    SELECT DEFECT_CODE, DEFECT_DESC, QTY, FROM_BIN_NAME
      FROM MESPROD.DEFECT_BIN_RELATION_H@MES16019 d
     WHERE d.LOT_RRN = (
           SELECT l.LOT_RRN FROM MESPROD.LOT@MES16019 l
            WHERE l.LOT_ID = :lot_id AND l.LINE_TYPE = :line_type
     )

    返回 list[dict]:
      defect_code      截取后的短码
      defect_code_raw  原始 DEFECT_CODE
      defect_desc
      qty
    失败返回 None。
    """
    lot_id = (lot_id or '').strip()
    if not lot_id:
        logger.warning("query_fvi_defect_details: lot_id 为空")
        return None

    line_type = (line_type or 'FT').strip() or 'FT'
    sql = """
        SELECT DEFECT_CODE, DEFECT_DESC, QTY, FROM_BIN_NAME
        FROM MESPROD.DEFECT_BIN_RELATION_H@MES16019 d
        WHERE d.LOT_RRN = (
            SELECT l.LOT_RRN
            FROM MESPROD.LOT@MES16019 l
            WHERE l.LOT_ID = :lot_id
              AND l.LINE_TYPE = :line_type
        )
        ORDER BY QTY DESC NULLS LAST, DEFECT_CODE
    """

    connection = oracledb.connect(user=USER, password=PWD, dsn=DSN)
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, {'lot_id': lot_id, 'line_type': line_type})
            rows = cursor.fetchall()
            result = []
            for code_raw, desc, qty, grade in rows:
                result.append({
                    'defect_code': _short_defect_code(code_raw),
                    'defect_code_raw': str(code_raw).strip() if code_raw is not None else '',
                    'defect_desc': str(desc).strip() if desc is not None else '',
                    'qty': int(qty) if qty is not None else 0,
                    'grade': str(grade).strip() if grade is not None else ''
                })
            return result
    except Exception as e:
        logger.error(
            f"查询 FVI 缺陷明细失败 lot_id={lot_id}: {e}",
            exc_info=True,
        )
        return None
    finally:
        connection.close()


def query_mes_defect_bin_qty(lot_id: str, line_type: str = 'FT', bin_name: str = 'F'):
    """
    MES 缺陷 BIN 数量（DB link）。
    SELECT DEFECT_CODE, QTY
      FROM MESPROD.DEFECT_BIN_RELATION_H@MES16019 d
     WHERE d.LOT_RRN = (
           SELECT l.LOT_RRN FROM MESPROD.LOT@MES16019 l
            WHERE l.LOT_ID = :lot_id AND l.LINE_TYPE = :line_type
     )
       AND d.BIN_NAME = :bin_name

    返回 list[{defect_code, qty}]（未去重）；失败返回 None。
    """
    lot_id = (lot_id or '').strip()
    if not lot_id:
        logger.warning("query_mes_defect_bin_qty: lot_id 为空")
        return None

    line_type = (line_type or 'FT').strip() or 'FT'
    bin_name = (bin_name or 'F').strip() or 'F'
    sql = """
        SELECT DEFECT_CODE, QTY
        FROM MESPROD.DEFECT_BIN_RELATION_H@MES16019 d
        WHERE d.LOT_RRN = (
            SELECT l.LOT_RRN
            FROM MESPROD.LOT@MES16019 l
            WHERE l.LOT_ID = :lot_id
              AND l.LINE_TYPE = :line_type
        )
          AND d.BIN_NAME = :bin_name
        ORDER BY QTY DESC NULLS LAST, DEFECT_CODE
    """
    params = {
        'lot_id': lot_id,
        'line_type': line_type,
        'bin_name': bin_name,
    }

    connection = oracledb.connect(user=USER, password=PWD, dsn=DSN)
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall()
            result = []
            for code_raw, qty in rows:
                if code_raw is None:
                    continue
                code = str(code_raw).strip()
                if not code:
                    continue
                result.append({
                    'defect_code': code,
                    'qty': int(qty) if qty is not None else 0,
                })
            return result
    except Exception as e:
        logger.error(
            f"查询 MES 缺陷 BIN 失败 lot_id={lot_id} line_type={line_type} "
            f"bin_name={bin_name}: {e}",
            exc_info=True,
        )
        return None
    finally:
        connection.close()


def is_merged_wafer_id(wafer_id) -> bool:
    """
    合批后的 wafer id：必含 '-'，且 '-' 后数字位数 > 2
    （普通片号多为两位，如 LOT-01；合批目标 id 后缀更长）。
    """
    text = str(wafer_id).strip() if wafer_id is not None else ''
    if '-' not in text:
        return False
    suffix = text.rsplit('-', 1)[-1].strip()
    return bool(suffix) and suffix.isdigit() and len(suffix) > 2


def query_split_merge_history(wafer_id: str, sql_trace=None):
    """
    查询 wafer 合批记录（MES DB link）。
    SELECT source_lot_id
      FROM mesprod.SPLIT_MERGE_HISTORY@MES16019 s
     WHERE s.TARGET_LOT_ID = :wafer_id
     ORDER BY source_lot_id ASC

    返回 list[str]（source_lot_id）；失败返回 None。
    sql_trace: 可选 list，追加本次 SQL（供 analysis 记日志）。
    """
    wafer_id = (wafer_id or '').strip()
    if not wafer_id:
        logger.warning("query_split_merge_history: wafer_id 为空")
        return None

    sql = """
        SELECT source_lot_id
        FROM mesprod.SPLIT_MERGE_HISTORY@MES16019 s
        WHERE s.TARGET_LOT_ID = :wafer_id
        ORDER BY source_lot_id ASC
    """
    params = {'wafer_id': wafer_id}
    if sql_trace is not None:
        sql_trace.append({
            'tag': 'query_split_merge_history',
            'sql': ' '.join(sql.split()),
            'params': dict(params),
        })

    connection = oracledb.connect(user=USER, password=PWD, dsn=DSN)
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall()
            result = []
            for (source_lot_id,) in rows:
                if source_lot_id is None:
                    continue
                text = str(source_lot_id).strip()
                if text:
                    result.append(text)
            return result
    except Exception as e:
        logger.error(
            f"查询合批记录失败 wafer_id={wafer_id}: {e}",
            exc_info=True,
        )
        return None
    finally:
        connection.close()


def query_mes_engineering_notes(product_model: str):
    """
    按型号查询 MES 工程备注（DB link）。
    SELECT e.ENGINEERING_NOTES
      FROM MESPROD.ENGINEERING_NOTES_CONFIG@MES16019 e
     WHERE e.PRODUCT_MODEL = :product_model

    返回 list[str]（去空、strip）；失败返回 None。
    """
    product_model = (product_model or '').strip()
    if not product_model:
        logger.warning("query_mes_engineering_notes: product_model 为空")
        return None

    sql = """
        SELECT e.ENGINEERING_NOTES
        FROM MESPROD.ENGINEERING_NOTES_CONFIG@MES16019 e
        WHERE e.PRODUCT_MODEL = :product_model
    """

    connection = oracledb.connect(user=USER, password=PWD, dsn=DSN)
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, {'product_model': product_model})
            rows = cursor.fetchall()
            result = []
            seen = set()
            for (note,) in rows:
                if note is None:
                    continue
                text = str(note).strip()
                if not text or text in seen:
                    continue
                seen.add(text)
                result.append(text)
            return result
    except Exception as e:
        logger.error(
            f"查询 MES 工程备注失败 product_model={product_model}: {e}",
            exc_info=True,
        )
        return None
    finally:
        connection.close()
