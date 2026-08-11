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
_ALLOWED_HOLD_RECORD_TABLES = {'FT_HOLD_RECORD'}
# 源表 HOLD_RECORD_ID：0/NULL=待处理；>0=已关联；-1=转换失败脏数据（需人工）
HOLD_RECORD_ID_PENDING = 0
HOLD_RECORD_ID_DIRTY = -1


def _next_positive_seq(cursor, seq_name: str) -> int:
    """取序列下一个 >0 的值（部分序列 MIN_VALUE=0，需跳过哨兵 0）。"""
    allowed = {'FT_HOLD_RECORD_SEQ', 'SEQ_CIRCULATION'}
    if seq_name not in allowed:
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
      FT  : PRODUCT_ID LIKE '%-3.5', HOLD_CODE∈(023,024,025,027), STATION∉(FAOIFINISH,FFVI)
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
                    AND HOLD_CODE IN ('023', '024', '025', '027')
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


def mark_hold_infos_dirty(
    source_info_ids,
    info_table: str = 'FT_HOLD_INFO_TEST',
    reason: str = '',
):
    """
    将源 hold_info 标记为脏数据：HOLD_RECORD_ID = -1。
    仅更新当前仍为待处理(NULL/0)的行；成功返回更新行数，失败返回 -1。
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
        SET HOLD_RECORD_ID = :dirty_id
        WHERE ID IN ({id_ph})
          AND NVL(HOLD_RECORD_ID, 0) = 0
    """

    connection = oracledb.connect(user=USER, password=PWD, dsn=DSN)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                sql,
                {'dirty_id': HOLD_RECORD_ID_DIRTY, **id_binds},
            )
            n = cursor.rowcount or 0
            connection.commit()
            logger.warning(
                f"标记脏数据 {info_tbl} HOLD_RECORD_ID={HOLD_RECORD_ID_DIRTY} "
                f"更新 {n}/{len(ids)} 行"
                + (f"，原因: {reason}" if reason else "")
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
            GRADE_NUM, RECORD_TYPE, STATUS, HOLD_DTTM
        ) VALUES (
            :new_id,
            :product_id, :station, :equip_id, :lot_id, :wafer_id,
            :hold_code, :hold_reason, :source, :second_code, :route_id,
            :grade_num, :record_type, :status, :hold_dttm
        )
    """

    lookup_owner_sql = """
        SELECT PRO_ENG_ID
        FROM PRODUCT_INFO
        WHERE PRODUCT_ID = :product_id
          AND ROWNUM = 1
    """

    insert_circ_sql = """
        INSERT INTO CIRCULATION_HISTORY (
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
            new_id = _next_positive_seq(cursor, 'FT_HOLD_RECORD_SEQ')
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
                },
            )

            # 2) NEXT_OWNER_ID ← PRODUCT_INFO.PRO_ENG_ID，缺省 1
            cursor.execute(lookup_owner_sql, {'product_id': record['PRODUCT_ID']})
            owner_row = cursor.fetchone()
            next_owner_id = 1
            if owner_row and owner_row[0] is not None:
                next_owner_id = int(owner_row[0])

            # 3) circulation_history
            circ_id = _next_positive_seq(cursor, 'SEQ_CIRCULATION')
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
    MES LOT ID → 用户侧 LOT ID。
    若含 '-'，截取第一个 '-' 之前的文本（不含 '-'）；否则原样返回。
    """
    text = str(lot_id).strip() if lot_id is not None else ''
    if not text:
        return ''
    if '-' in text:
        return text.split('-', 1)[0].strip()
    return text


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


def query_split_merge_history(wafer_id: str):
    """
    查询 wafer 合批记录（MES DB link）。
    SELECT source_lot_id
      FROM mesprod.SPLIT_MERGE_HISTORY@MES16019 s
     WHERE s.TARGET_LOT_ID = :wafer_id
     ORDER BY source_lot_id ASC

    返回 list[str]（source_lot_id）；失败返回 None。
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

    connection = oracledb.connect(user=USER, password=PWD, dsn=DSN)
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, {'wafer_id': wafer_id})
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
