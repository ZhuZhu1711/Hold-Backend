import sys
import os
import json
import re
import threading
import time
import schedule
import logging
from logging.handlers import RotatingFileHandler

# 冻结后日志和水位文件写到 exe 旁边，不写到解包临时目录。
if getattr(sys, 'frozen', False):
    os.chdir(os.path.dirname(os.path.abspath(sys.executable)))
    project_root_path = getattr(sys, '_MEIPASS', os.getcwd())
else:
    current_file_path = os.path.abspath(__file__)
    project_root_path = os.path.dirname(os.path.dirname(os.path.dirname(current_file_path)))
if project_root_path not in sys.path:
    sys.path.insert(0, project_root_path)

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

import oracledb

from app.config import Config, is_test_env, reject_legacy_mode_arg
from app.utils.database_util import USER, PWD, DSN, build_merged_wafer_display
from app.utils.mail_alert import notify_severe_error

# CP 只按 hold_code 捞 AREA=1、HOLDING=0。不按产品后缀、站点或 record_type 划分。
CP_HOLD_CODES = (
    'SYSTEM',
    '027',
    '01',
    '004',
    '015',
    '016',
    'CP_FUTURE_HOLD',
)
CP_PRODUCTION_ROLE = 2
CP_PRODUCTION_EMPLOYEE_NO = '88888'
CP_RECORD_TYPE = 0
CP_INFO_AREA = 1

_HOLD_CODE_RE = re.compile(r'^[A-Za-z0-9_]+$')
_INFO_TABLES = frozenset({'FT_HOLD_INFO', 'FT_HOLD_INFO_TEST'})
_RECORD_TABLES = frozenset({'CP_HOLD_RECORD', 'CP_HOLD_RECORD_TEST'})
_CIRC_TABLES = frozenset({
    'CP_CIRCULATION_HISTORY',
    'CP_CIRCULATION_HISTORY_TEST',
})
_RECORD_SEQ = {
    'CP_HOLD_RECORD': 'CP_HOLD_RECORD_SEQ',
    'CP_HOLD_RECORD_TEST': 'CP_HOLD_RECORD_TEST_SEQ',
}
_CIRC_SEQ = {
    'CP_CIRCULATION_HISTORY': 'CP_CIRC_SEQ',
    'CP_CIRCULATION_HISTORY_TEST': 'CP_CIRC_TEST_SEQ',
}
_ALLOWED_SEQS = frozenset(_RECORD_SEQ.values()) | frozenset(_CIRC_SEQ.values())

_HOLD_DTTM_FORMATS = (
    '%Y-%m-%d %H:%M:%S',
    '%Y/%m/%d %H:%M:%S',
    '%Y-%m-%d %H:%M:%S.%f',
    '%Y/%m/%d %H:%M:%S.%f',
    '%Y%m%d%H%M%S',
    '%Y-%m-%dT%H:%M:%S',
    '%Y-%m-%dT%H:%M:%S.%f',
)

_LOT_QUIET_MINUTES_DEFAULT = 30
_LOT_QUIET_MAX_MINUTES_DEFAULT = 120

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not os.path.exists('logs'):
    os.makedirs('./logs')
file_handler = RotatingFileHandler(
    'logs/cp_hold_merge.log',
    maxBytes=50 * 1024 * 1024,
    backupCount=3,
    encoding='utf-8',
)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)


def hold_code_in_sql() -> str:
    """白名单拼进 SQL。只接受字母、数字和下划线。"""
    parts = []
    for code in CP_HOLD_CODES:
        if not _HOLD_CODE_RE.fullmatch(code):
            raise ValueError(f'非法 hold_code: {code}')
        parts.append("'" + code + "'")
    return 'HOLD_CODE IN (' + ', '.join(parts) + ')'


def parse_hold_dttm(raw) -> Optional[datetime]:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw
    text = str(raw).strip()
    if not text:
        return None
    for fmt in _HOLD_DTTM_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    logger.warning(f"无法解析 HOLD_DTTM: {raw!r}")
    return None


def _join_unique(values, sep: str = '@', max_len: Optional[int] = None) -> Optional[str]:
    seen = set()
    parts = []
    for raw in values:
        if raw is None:
            continue
        text = str(raw).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        parts.append(text)
    if not parts:
        return None
    joined = sep.join(parts)
    if max_len is not None and len(joined) > max_len:
        return joined[:max_len]
    return joined


def _norm_grade_num(value) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _merge_grade_num(items: List['CpHoldInfo']) -> Optional[str]:
    """全相同取该值；只有一个非空取该值；多个非空取 HOLD_DTTM 最新一行。"""
    if not items:
        return None
    norms = [_norm_grade_num(i.grade_num) for i in items]
    unique_all = set(norms)
    if len(unique_all) <= 1:
        return norms[0] if norms else None
    non_null = {v for v in unique_all if v is not None}
    if len(non_null) == 1:
        return next(iter(non_null))
    if not non_null:
        return None
    latest = max(items, key=lambda x: (x.hold_dttm or datetime.min, x.id or 0))
    return _norm_grade_num(latest.grade_num)


@dataclass
class CpHoldInfo:
    id: int
    hold_dttm: Optional[datetime]
    hold_dttm_raw: str
    station: str
    equip_id: str
    product_id: str
    lot_id: str
    wafer_id: str
    hold_code: str
    hold_reason: str
    source: int
    second_code: Optional[str] = None
    route_id: Optional[str] = None
    grade_num: Optional[str] = None

    @classmethod
    def from_row(cls, row: dict) -> 'CpHoldInfo':
        raw = row.get('HOLD_DTTM')
        lot = row.get('LOT_ID') or ''
        return cls(
            id=row.get('ID'),
            hold_dttm=parse_hold_dttm(raw),
            hold_dttm_raw=str(raw) if raw is not None else '',
            station=row.get('STATION') or '',
            equip_id=row.get('EQUIP_ID') or '',
            product_id=row.get('PRODUCT_ID') or '',
            lot_id=str(lot).strip(),
            wafer_id=row.get('WAFER_ID') or '',
            hold_code=(row.get('HOLD_CODE') or '').strip(),
            hold_reason=row.get('HOLD_REASON') or '',
            source=row.get('SOURCE') if row.get('SOURCE') is not None else 0,
            second_code=row.get('SECOND_CODE'),
            route_id=row.get('ROUTE_ID'),
            grade_num=row.get('GRADE_NUM'),
        )


@dataclass
class CpRoughRecord:
    lot_id: str
    items: List[CpHoldInfo] = field(default_factory=list)
    all_source_ids: List[int] = field(default_factory=list)

    @property
    def source_ids(self) -> List[int]:
        return list(self.all_source_ids) or [
            item.id for item in self.items if item.id is not None
        ]

    def summary(self) -> str:
        codes = sorted({i.hold_code for i in self.items})
        return (
            f"lot={self.lot_id}, items={len(self.items)}, "
            f"source_ids={len(self.source_ids)}, codes={codes}"
        )

    def to_record_dict(self, status: int = 0) -> Optional[dict]:
        """多原因合成一条。RECORD_TYPE 固定为缺省 0。不写片属性。"""
        if not self.items:
            return None
        ordered = sorted(
            self.items,
            key=lambda x: (x.hold_dttm or datetime.min, x.id or 0),
        )
        first = ordered[0]
        wafers = []
        seen = set()
        for item in ordered:
            wafer = (item.wafer_id or '').strip()
            if not wafer or wafer in seen:
                continue
            seen.add(wafer)
            wafers.append(wafer)
        if not wafers:
            return None
        if len(wafers) > 1:
            wafer_out = build_merged_wafer_display(wafers, max_len=200) or wafers[0]
        else:
            wafer_out = wafers[0]
        if not (first.product_id and first.station and first.equip_id and self.lot_id):
            return None
        return {
            'PRODUCT_ID': first.product_id,
            'STATION': first.station,
            'EQUIP_ID': first.equip_id,
            'LOT_ID': self.lot_id,
            'WAFER_ID': wafer_out,
            'HOLD_CODE': _join_unique((i.hold_code for i in ordered), sep='@', max_len=100),
            'HOLD_REASON': _join_unique(
                (i.hold_reason for i in ordered), sep='@', max_len=512
            ),
            'SOURCE': first.source if first.source is not None else 0,
            'SECOND_CODE': next((i.second_code for i in ordered if i.second_code), None),
            'ROUTE_ID': next((i.route_id for i in ordered if i.route_id), None),
            'GRADE_NUM': _merge_grade_num(ordered),
            'RECORD_TYPE': CP_RECORD_TYPE,
            'STATUS': status,
            'HOLD_DTTM': first.hold_dttm,
        }


def dedupe_cp_infos(
    infos: List[CpHoldInfo],
    window: timedelta = timedelta(hours=1),
) -> List[CpHoldInfo]:
    """(WAFER_ID, STATION, HOLD_CODE) 相同且时间相差在 window 内，只留最早一条。"""
    by_key = defaultdict(list)
    no_time = []
    for info in infos:
        if info.hold_dttm is None:
            no_time.append(info)
            continue
        by_key[(info.wafer_id, info.station, info.hold_code)].append(info)
    kept: List[CpHoldInfo] = list(no_time)
    for group in by_key.values():
        group.sort(key=lambda x: (x.hold_dttm, x.id or 0))
        last_kept = None
        for info in group:
            if last_kept is None or (info.hold_dttm - last_kept.hold_dttm) > window:
                kept.append(info)
                last_kept = info
    kept.sort(key=lambda x: (x.hold_dttm or datetime.min, x.id or 0))
    return kept


def build_cp_rough_records(
    rows: List[dict],
    window: timedelta = timedelta(hours=1),
) -> Tuple[List[CpRoughRecord], List[int]]:
    """
    同一 LOT_ID 合成一条，不按 hold_code 拆开。
    LOT_ID 为空的行返回其 ID，调用方标 -1。不在白名单的行不标脏。
    """
    allowed = set(CP_HOLD_CODES)
    by_lot = defaultdict(list)
    dirty_ids: List[int] = []
    for row in rows or []:
        info = CpHoldInfo.from_row(row)
        if info.hold_code not in allowed:
            continue
        if not info.lot_id:
            if info.id is not None:
                dirty_ids.append(int(info.id))
            continue
        by_lot[info.lot_id].append(info)

    records = []
    for lot_id in sorted(by_lot):
        items = by_lot[lot_id]
        all_ids = [int(i.id) for i in items if i.id is not None]
        records.append(CpRoughRecord(
            lot_id=lot_id,
            items=dedupe_cp_infos(items, window=window),
            all_source_ids=all_ids,
        ))
    return records, dirty_ids


def _connect():
    return oracledb.connect(user=USER, password=PWD, dsn=DSN)


def _next_positive_seq(cursor, seq_name: str) -> int:
    if seq_name not in _ALLOWED_SEQS:
        raise ValueError(f'非法序列名: {seq_name}')
    for _ in range(5):
        cursor.execute(f'SELECT {seq_name}.NEXTVAL FROM DUAL')
        val = cursor.fetchone()[0]
        if val is not None and int(val) > 0:
            return int(val)
    raise RuntimeError(f'序列 {seq_name} 连续返回非法 ID(<=0)')


def query_cp_online_hold_info(table_name: str):
    """AREA=1、HOLDING=0、未关联、hold_code 在白名单。失败返回 None。"""
    tbl = (table_name or '').upper()
    if tbl not in _INFO_TABLES:
        logger.error(f'非法 hold_info 表名: {table_name}')
        return None
    code_sql = hold_code_in_sql()
    sql = f"""
        SELECT
            ID, HOLD_DTTM, STATION, EQUIP_ID, PRODUCT_ID, LOT_ID, WAFER_ID,
            HOLD_CODE, HOLD_REASON, SOURCE, SECOND_CODE, ROUTE_ID, GRADE_NUM,
            HOLD_RECORD_ID, HOLDING, REMARK
        FROM {tbl}
        WHERE AREA = 1
          AND NVL(HOLD_RECORD_ID, 0) = 0
          AND HOLDING = 0
          AND {code_sql}
        ORDER BY LOT_ID, HOLD_DTTM, ID
    """
    connection = _connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql)
            columns = [col[0] for col in cursor.description]
            rows = cursor.fetchall()
            return [dict(zip(columns, row)) for row in rows]
    except Exception as exc:
        logger.error(f'查询 CP hold_info 失败: {exc}', exc_info=True)
        return None
    finally:
        connection.close()


def find_cp_production_user_id() -> Optional[int]:
    """ROLE=2 且工号 88888。不是恰好一行时返回 None。"""
    sql = """
        SELECT ID
        FROM USERS
        WHERE ROLE = :role
          AND EMPLOYEE_NO = :employee_no
    """
    connection = _connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, {
                'role': CP_PRODUCTION_ROLE,
                'employee_no': CP_PRODUCTION_EMPLOYEE_NO,
            })
            rows = cursor.fetchall()
    except Exception as exc:
        logger.error(f'查询 CP 生产用户失败: {exc}', exc_info=True)
        return None
    finally:
        connection.close()
    if len(rows) != 1 or rows[0][0] is None:
        return None
    return int(rows[0][0])


def mark_cp_infos_dirty(source_info_ids, info_table: str, reason: str = '') -> int:
    """只更新 AREA=1 且仍待处理的行。失败返回 -1。"""
    info_tbl = (info_table or '').upper()
    if info_tbl not in _INFO_TABLES:
        logger.error(f'非法 hold_info 表名: {info_table}')
        return -1
    ids = [int(i) for i in (source_info_ids or []) if i is not None]
    if not ids:
        return 0
    remark = (str(reason).strip() if reason else '') or 'cp merge failed'
    if len(remark) > 512:
        remark = remark[:512]
    id_binds = {f'id{i}': v for i, v in enumerate(ids)}
    id_ph = ', '.join(f':id{i}' for i in range(len(ids)))
    sql = f"""
        UPDATE {info_tbl}
        SET HOLD_RECORD_ID = -1,
            REMARK = :remark
        WHERE ID IN ({id_ph})
          AND NVL(HOLD_RECORD_ID, 0) = 0
          AND AREA = 1
    """
    connection = _connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, {'remark': remark, **id_binds})
            n = cursor.rowcount or 0
            connection.commit()
            logger.warning(f'CP 标记脏数据 {info_tbl} 更新 {n}/{len(ids)} 行，原因: {remark}')
            return n
    except Exception as exc:
        connection.rollback()
        logger.error(f'CP 标记脏数据失败: {exc}', exc_info=True)
        return -1
    finally:
        connection.close()


def insert_cp_hold_record_and_link(
    record: dict,
    source_info_ids,
    next_owner_id: int,
    info_table: str,
    record_table: str,
    circ_table: str,
) -> Optional[int]:
    """
    同一事务：插 CP_HOLD_RECORD、插 CP 流转、回写 AREA=1 的 info。
    失败回滚并把源行标 -1。不写 FT_HOLD_RECORD / CIRCULATION_HISTORY。
    """
    info_tbl = (info_table or '').upper()
    record_tbl = (record_table or '').upper()
    circ_tbl = (circ_table or '').upper()
    if info_tbl not in _INFO_TABLES:
        logger.error(f'非法 hold_info 表名: {info_table}')
        return None
    if record_tbl not in _RECORD_TABLES or circ_tbl not in _CIRC_TABLES:
        logger.error(f'非法 CP 表名: record={record_table} circ={circ_table}')
        return None
    ids = [int(i) for i in (source_info_ids or []) if i is not None]
    if not ids:
        logger.error('insert_cp_hold_record_and_link: 无源 hold_info ID，跳过')
        return None

    def _fail(reason: str):
        mark_cp_infos_dirty(ids, info_table=info_tbl, reason=reason)
        return None

    required = ('PRODUCT_ID', 'STATION', 'EQUIP_ID', 'LOT_ID', 'WAFER_ID', 'SOURCE', 'RECORD_TYPE', 'STATUS')
    missing = [k for k in required if record.get(k) is None]
    if missing:
        logger.error(f'insert_cp_hold_record_and_link: 缺少必填字段 {missing}')
        return _fail(f'缺少必填字段 {missing}')
    try:
        owner_id = int(next_owner_id)
    except (TypeError, ValueError):
        return _fail('CP 生产用户 ID 无效')

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
    insert_circ_sql = f"""
        INSERT INTO {circ_tbl} (
            ID, HOLD_RECORD_ID, DISPOSED_OWNER_ID, DISPOSE, NEXT_OWNER_ID,
            DISPOSE_SOURCE, DISPOSE_DTTM, DISPOSE_TYPE, DISPOSE_DETAIL
        ) VALUES (
            :circ_id, :hold_record_id, 1, 0, :next_owner_id,
            :dispose_source, SYSDATE, 0, NULL
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
          AND AREA = 1
    """
    connection = _connect()
    try:
        with connection.cursor() as cursor:
            new_id = _next_positive_seq(cursor, _RECORD_SEQ[record_tbl])
            cursor.execute(insert_record_sql, {
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
            })
            circ_id = _next_positive_seq(cursor, _CIRC_SEQ[circ_tbl])
            cursor.execute(insert_circ_sql, {
                'circ_id': circ_id,
                'hold_record_id': new_id,
                'next_owner_id': owner_id,
                'dispose_source': dispose_source,
            })
            cursor.execute(update_last_circ_sql, {'circ_id': circ_id, 'record_id': new_id})
            cursor.execute(update_info_sql, {'record_id': new_id, **id_binds})
            linked = cursor.rowcount or 0
            if linked <= 0:
                connection.rollback()
                logger.error(f'插入 CP record id={new_id} 后回写影响 0 行，已回滚')
                return _fail('回写源表 HOLD_RECORD_ID 影响 0 行')
            connection.commit()
            logger.info(
                f'写入 {record_tbl} id={new_id}, lot={record["LOT_ID"]}, '
                f'next_owner_id={owner_id}, 回写 {info_tbl} {linked}/{len(ids)} 行'
            )
            return new_id
    except Exception as exc:
        connection.rollback()
        logger.error(f'插入 CP record / 流转 / 回写失败: {exc}', exc_info=True)
        return _fail(str(exc))
    finally:
        connection.close()


def group_max_source_id(rec: CpRoughRecord) -> Optional[int]:
    ids = []
    for raw in rec.source_ids:
        try:
            ids.append(int(raw))
        except (TypeError, ValueError):
            continue
    if not ids:
        return None
    return max(ids)


def _format_quiet_time(value: datetime) -> str:
    return value.strftime('%Y-%m-%dT%H:%M:%S')


def _parse_quiet_time(raw) -> Optional[datetime]:
    if isinstance(raw, datetime):
        return raw
    text = str(raw or '').strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _coerce_quiet_mark(mark) -> Optional[dict]:
    if not isinstance(mark, dict):
        return None
    try:
        max_id = int(mark.get('max_id'))
    except (TypeError, ValueError):
        return None
    last_growth = _parse_quiet_time(mark.get('last_growth_at'))
    first_seen = _parse_quiet_time(mark.get('first_seen_at'))
    if last_growth is None or first_seen is None:
        return None
    return {
        'max_id': max_id,
        'last_growth_at': last_growth,
        'first_seen_at': first_seen,
    }


def evaluate_cp_lot_quiet(
    mark,
    max_id: int,
    now: datetime,
    quiet: timedelta,
    max_wait: timedelta,
) -> Tuple[str, Optional[dict]]:
    """
    返回 (status, mark)：
      defer  首次出现，或 max(ID) 变大且未超过最长等待
      ready  水位没涨，且距上次变大已满安静窗口
      cap    自首次看到已超过最长等待，用已有行建单
    """
    prev = _coerce_quiet_mark(mark)
    try:
        current = int(max_id)
    except (TypeError, ValueError):
        return 'ready', None
    if prev is None or current > prev['max_id']:
        first_seen = prev['first_seen_at'] if prev is not None else now
        if prev is not None and (now - first_seen) >= max_wait:
            return 'cap', None
        return 'defer', {
            'max_id': current,
            'last_growth_at': _format_quiet_time(now),
            'first_seen_at': _format_quiet_time(first_seen),
        }
    if (now - prev['last_growth_at']) >= quiet:
        return 'ready', None
    if (now - prev['first_seen_at']) >= max_wait:
        return 'cap', None
    return 'defer', {
        'max_id': prev['max_id'],
        'last_growth_at': _format_quiet_time(prev['last_growth_at']),
        'first_seen_at': _format_quiet_time(prev['first_seen_at']),
    }


def quiet_state_key(lot_id: str) -> str:
    return f'lot|{lot_id}'


def prune_cp_lot_quiet_state(state: dict, keep: set, now: datetime, max_wait: timedelta) -> None:
    for key in list(state):
        if key in keep:
            continue
        coerced = _coerce_quiet_mark(state.get(key))
        if coerced is None or (now - coerced['last_growth_at']) >= max_wait:
            state.pop(key, None)


def decide_cp_lot_quiet(
    records: List[CpRoughRecord],
    state: Optional[dict],
    now: datetime,
    quiet: timedelta,
    max_wait: timedelta,
) -> Tuple[dict, List[CpRoughRecord], dict]:
    """返回 (action_by_identity, capped, new_state)。不修改传入的 state。"""
    new_state = dict(state or {})
    action_by_id = {}
    capped: List[CpRoughRecord] = []
    keep = set()
    for rec in records:
        key = quiet_state_key(rec.lot_id)
        max_id = group_max_source_id(rec)
        if max_id is None:
            new_state.pop(key, None)
            action_by_id[id(rec)] = 'ready'
            continue
        status, mark = evaluate_cp_lot_quiet(
            new_state.get(key), max_id, now, quiet, max_wait,
        )
        action_by_id[id(rec)] = status
        if status == 'defer' and mark is not None:
            new_state[key] = mark
            keep.add(key)
            continue
        new_state.pop(key, None)
        if status == 'cap':
            capped.append(rec)
    prune_cp_lot_quiet_state(new_state, keep, now, max_wait)
    return action_by_id, capped, new_state


def load_cp_lot_quiet_state(path: str) -> dict:
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding='utf-8') as handle:
            data = json.load(handle)
    except Exception as exc:
        logger.warning(f'读取 CP 同 lot 安静水位失败，本轮按空水位处理: {exc}')
        return {}
    if not isinstance(data, dict):
        logger.warning('CP 同 lot 安静水位不是对象，本轮按空水位处理')
        return {}
    return data


def save_cp_lot_quiet_state(path: str, state: dict) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    temporary = path + '.tmp'
    with open(temporary, 'w', encoding='utf-8') as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    os.replace(temporary, path)


class CpHoldMergeScheduler(threading.Thread):
    """
    定时把 FT_HOLD_INFO 中 AREA=1、HOLDING=0、白名单 hold_code 的行
    按 LOT_ID 等齐后写入 CP_HOLD_RECORD。不写 FT record，也不进 FT 流转。
    """

    def __init__(self):
        super().__init__(daemon=True)
        self._stop_event = threading.Event()
        self.logger = logger
        self.config = Config()
        self.interval_minutes = getattr(self.config, 'HOLD_MERGE_INTERVAL_MINUTES', 30)
        self.hold_info_table = getattr(self.config, 'HOLD_INFO_TABLE', 'FT_HOLD_INFO_TEST')
        self.hold_record_table = getattr(self.config, 'CP_HOLD_RECORD_TABLE', 'CP_HOLD_RECORD_TEST')
        self.circ_table = getattr(
            self.config, 'CP_CIRCULATION_TABLE', 'CP_CIRCULATION_HISTORY_TEST'
        )
        self.record_status = getattr(self.config, 'HOLD_RECORD_STATUS', 0)
        self.dedup_window = timedelta(
            hours=getattr(self.config, 'HOLD_DEDUP_WINDOW_HOURS', 1)
        )
        self.lot_quiet_minutes = getattr(
            self.config, 'HOLD_LOT_QUIET_MINUTES', _LOT_QUIET_MINUTES_DEFAULT
        )
        self.lot_quiet_max_minutes = getattr(
            self.config, 'HOLD_LOT_QUIET_MAX_MINUTES', _LOT_QUIET_MAX_MINUTES_DEFAULT
        )
        self.lot_quiet = timedelta(minutes=self.lot_quiet_minutes)
        self.lot_quiet_max = timedelta(minutes=self.lot_quiet_max_minutes)

    def _quiet_state_path(self) -> str:
        table = (self.hold_info_table or 'FT_HOLD_INFO').upper()
        return os.path.join('logs', f'cp_hold_merge_lot_quiet_{table}.json')

    def stop(self):
        self.logger.info('正在停止 CP Hold 合并调度器...')
        self._stop_event.set()

    def _persist(self, rec: CpRoughRecord, next_owner_id: int) -> Optional[int]:
        row = rec.to_record_dict(status=self.record_status)
        if not row:
            mark_cp_infos_dirty(
                rec.source_ids,
                info_table=self.hold_info_table,
                reason=f'lot={rec.lot_id} 无法生成 CP record',
            )
            self.logger.warning(f'lot={rec.lot_id} 无法生成 CP record，已标记 HOLD_RECORD_ID=-1')
            return None
        new_id = insert_cp_hold_record_and_link(
            row,
            rec.source_ids,
            next_owner_id,
            info_table=self.hold_info_table,
            record_table=self.hold_record_table,
            circ_table=self.circ_table,
        )
        if new_id is None:
            self.logger.error(
                f'写入失败 lot={rec.lot_id} codes={row.get("HOLD_CODE")}，已标记 HOLD_RECORD_ID=-1'
            )
        return new_id

    def _run_job(self):
        try:
            self.logger.info('>>> CP Hold 合并定时任务开始执行...')
            hold_infos = query_cp_online_hold_info(self.hold_info_table)
            if hold_infos is None:
                self.logger.error('查询 CP hold_info 失败')
                return

            self.logger.info(
                f'从表 {self.hold_info_table} 查询到 {len(hold_infos)} 条待合批 hold_info '
                f'(AREA=1, HOLDING=0, HOLD_RECORD_ID∈{{NULL,0}}，'
                f'hold_code 白名单，写入 {self.hold_record_table})'
            )
            rough_records, dirty_ids = build_cp_rough_records(
                hold_infos, window=self.dedup_window
            )
            if dirty_ids:
                mark_cp_infos_dirty(
                    dirty_ids,
                    info_table=self.hold_info_table,
                    reason='LOT_ID 为空，无法按 lot 建 CP record',
                )
                self.logger.info(f'跳过 {len(dirty_ids)} 条空 LOT_ID，已标记 HOLD_RECORD_ID=-1')

            if not rough_records:
                self.logger.info('<<< CP Hold 合并定时任务执行完毕：没有可建的 lot')
                return

            owner_id = find_cp_production_user_id()
            if owner_id is None:
                self.logger.error(
                    '未找到唯一的 CP 生产用户 '
                    f'ROLE={CP_PRODUCTION_ROLE} EMPLOYEE_NO={CP_PRODUCTION_EMPLOYEE_NO}，'
                    '本轮不建单'
                )
                return

            now = datetime.now()
            state_path = self._quiet_state_path()
            state = load_cp_lot_quiet_state(state_path)
            actions, capped, state = decide_cp_lot_quiet(
                rough_records, state, now, self.lot_quiet, self.lot_quiet_max,
            )
            capped_ids = {id(rec) for rec in capped}
            ok, fail, deferred = 0, 0, 0
            for rec in rough_records:
                self.logger.info(f'  - {rec.summary()}')
                action = actions.get(id(rec), 'ready')
                if action == 'defer':
                    self.logger.info(
                        f'同 lot 仍有新 info，本轮不建单，'
                        f'等 {self.lot_quiet_minutes} 分钟无新行: {rec.summary()}'
                    )
                    deferred += 1
                    continue
                if id(rec) in capped_ids:
                    self.logger.info(
                        f'同 lot 自首次看到已超过 {self.lot_quiet_max_minutes} 分钟，'
                        f'用已有行建单: {rec.summary()}'
                    )
                if self._persist(rec, owner_id) is None:
                    fail += 1
                else:
                    ok += 1
            try:
                save_cp_lot_quiet_state(state_path, state)
            except Exception as save_exc:
                self.logger.error(f'写入 CP 同 lot 安静水位失败: {save_exc}', exc_info=True)
            self.logger.info(
                f'<<< CP Hold 合并定时任务执行完毕：成功 {ok}，失败 {fail}，'
                f'空 LOT {len(dirty_ids)}，同 lot 等待下次 {deferred}'
            )
        except Exception as exc:
            self.logger.error(f'CP Hold 合并定时任务执行出错: {exc}', exc_info=True)
            notify_severe_error('CP Hold 合并定时任务整轮失败', str(exc), exc=exc)

    def run(self):
        self.logger.info(
            f'CP Hold 合并调度器已启动，间隔 {self.interval_minutes} 分钟，'
            f'源表={self.hold_info_table}，目标={self.hold_record_table}'
        )
        self._run_job()
        if self._stop_event.is_set():
            self.logger.info('CP Hold 合并调度器线程已退出')
            return
        sch = schedule.Scheduler()
        sch.every(self.interval_minutes).minutes.do(self._run_job)
        while not self._stop_event.is_set():
            sch.run_pending()
            time.sleep(1)
        self.logger.info('CP Hold 合并调度器线程已退出')


if __name__ == '__main__':
    reject_legacy_mode_arg()
    print(
        f"HOLD_ENV={'test' if is_test_env() else 'release'} "
        f"源表={Config.HOLD_INFO_TABLE} 目标={Config.CP_HOLD_RECORD_TABLE}"
    )
    scheduler = CpHoldMergeScheduler()
    scheduler.start()
    try:
        while scheduler.is_alive():
            time.sleep(1)
    except KeyboardInterrupt:
        scheduler.stop()
        scheduler.join(timeout=5)
