import sys
import os
import json
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
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from app.backend_schedule.singleton_lock import LOCK_FT_HOLD_MERGE, hold_singleton
from app.config import Config, is_test_env, reject_legacy_mode_arg
from app.utils.mail_alert import notify_severe_error
from app.utils.database_util import (
    HOLD_WAFER_ATTR_ZIYI,
    append_hold_infos_to_record,
    build_merged_wafer_display,
    compute_hold_wafer_attr,
    find_existing_ziyi_hold_record,
    insert_hold_record_and_link,
    is_fragmented_merged_lot,
    mark_hold_infos_dirty,
    normalize_lot_id,
    query_hold_infos_by_record_id,
    query_online_hold_info,
)

# 处置单划分（见 dispose_api.md）：
#   FT异常反馈单  RECORD_TYPE=0  PRODUCT_ID *-3.5, HOLD_CODE∈{023,024,025,027,028},
#                 STATION∉{FAOIFINISH,FFVI}
#   FVI异常反馈单 RECORD_TYPE=1  HOLD_CODE=023 + STATION∈{FAOIFINISH,FFVI}
#   WLT异常反馈单 RECORD_TYPE=2  PRODUCT_ID *-2.6, HOLD_CODE∈{004,022}, STATION=WOQC
# AQL_HOLD 不再合批；手提创建仍可直接写 record。
# 不满足以上规则的 hold_info 不转成 record。
# 028（重码风险）仍为 RECORD_TYPE=0，但与同片/同批良率、缺陷率 hold 分列，不拼进同一条 record。
# 非 ENG 的 FPQC + HOLD_CODE=025 且 HOLD_REASON 含 FUTURE HOLD：仍不建单。
# ENG 路线 + PQC/FPQC + 原因含 FUTURE：RECORD_TYPE=3，建单后自动上报 NCMR。
from app.backend_schedule.hold_merge_rules import (
    RECORD_TYPE_ENG_FUTURE,
    RECORD_TYPE_FT,
    RECORD_TYPE_WLT,
    HoldInfo,
    RoughHoldRecord,
    _FT_DUPCODE_HOLD_CODE,
    is_eng_pqc_future_hold,
    resolve_record_type,
)

_FPQC_FUTURE_HOLD_CODE = '025'
_FPQC_FUTURE_HOLD_STATION = 'FPQC'
_FPQC_FUTURE_HOLD_REASON = 'FUTURE HOLD'

# 梓一 / WLT 同 lot：水位不再变大后，再安静这么久才建单。调度器优先读配置。
_LOT_QUIET_MINUTES_DEFAULT = 30
# 自首次看到该组起超过该时长仍在进新行，用已有行建单。
_LOT_QUIET_MAX_MINUTES_DEFAULT = 120



def _is_fpqc_future_hold(info: 'HoldInfo') -> bool:
    """非 ENG 的 FPQC 025 且原因含 FUTURE HOLD：仍不建单。

    ENG + PQC/FPQC + FUTURE 改走 RECORD_TYPE_ENG_FUTURE，不在这里排除。
    """
    if is_eng_pqc_future_hold(info.route_id, info.station, info.hold_reason):
        return False
    code = (info.hold_code or '').strip()
    station = (info.station or '').strip().upper()
    reason = info.hold_reason or ''
    return (
        code == _FPQC_FUTURE_HOLD_CODE
        and station == _FPQC_FUTURE_HOLD_STATION
        and _FPQC_FUTURE_HOLD_REASON in reason.upper()
    )


def _merge_code_bucket(record_type: int, hold_code: str) -> str:
    """
    合批分组附加键：FT 重码(028)单独成桶，避免与 023/024/025/027 等拼成一条 record。
    """
    if record_type == RECORD_TYPE_FT and (hold_code or '').strip() == _FT_DUPCODE_HOLD_CODE:
        return _FT_DUPCODE_HOLD_CODE
    return ''


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not os.path.exists('logs'):
    os.makedirs('./logs')
file_handler = RotatingFileHandler(
    'logs/hold_merge.log',
    maxBytes=50 * 1024 * 1024,
    backupCount=3,
    encoding='utf-8'
)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)
# 控制台输出，方便 debug 单次测试查看结果
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)


def dedupe_hold_infos(
    infos: List[HoldInfo],
    window: timedelta = timedelta(hours=1),
) -> List[HoldInfo]:
    """
    去重：WAFER_ID + STATION + HOLD_CODE 相同，且 HOLD_DTTM 相差在 window 内 → 视为重复。
    保留时间最早的一条；无法解析时间的记录单独保留（不做时间窗合并）。
    """
    by_key = defaultdict(list)
    no_time = []
    for info in infos:
        if info.hold_dttm is None:
            no_time.append(info)
            continue
        key = (info.wafer_id, info.station, info.hold_code)
        by_key[key].append(info)

    kept: List[HoldInfo] = list(no_time)
    for key, group in by_key.items():
        group.sort(key=lambda x: (x.hold_dttm, x.id or 0))
        last_kept: Optional[HoldInfo] = None
        for info in group:
            if last_kept is None or (info.hold_dttm - last_kept.hold_dttm) > window:
                kept.append(info)
                last_kept = info
            else:
                logger.debug(
                    f"去重丢弃 id={info.id} key={key} "
                    f"dttm={info.hold_dttm_raw}（相对 id={last_kept.id} "
                    f"dttm={last_kept.hold_dttm_raw} 在 {window} 内）"
                )
    kept.sort(key=lambda x: (x.hold_dttm or datetime.min, x.id or 0))
    return kept


def build_rough_hold_records(
    rows: List[dict],
    window: timedelta = timedelta(hours=1),
) -> Tuple[List[RoughHoldRecord], List[int]]:
    """
    查询结果 → 按处置单划分判定 RECORD_TYPE → 分组 → 去重 → 粗糙 hold record。

    分组键：
      - WLT：('wlt_lot', lot_prefix, record_type, '')，lot_prefix 为 wafer/lot 去掉 '-' / `.NN` 后的文本
      - 分片合批（LOT!=WAFER 且 LOT 后缀数字>2位）：('lot', exact_lot_id, record_type, bucket)
      - 其它：('wafer', wafer_id, record_type, bucket)
      bucket：FT 的 028 为 '028'，其余为空。同 lot/wafer 上良率、缺陷率与重码拆成两条 record。

    返回 (records, skipped_ids)：
      records     可写入的 RoughHoldRecord
      skipped_ids 不满足处置单划分、或 FPQC 025 FUTURE HOLD 排除、无需转成 record 的源 hold_info ID
    """
    by_key = defaultdict(list)
    skipped_ids: List[int] = []

    for row in rows:
        info = HoldInfo.from_row(row)
        if not info.wafer_id:
            logger.warning(f"跳过无 WAFER_ID 的 hold_info id={info.id}")
            if info.id is not None:
                skipped_ids.append(info.id)
            continue

        if _is_fpqc_future_hold(info):
            logger.info(
                f"hold_info id={info.id} 为 FPQC 025 FUTURE HOLD，不参与合批 "
                f"(wafer={info.wafer_id}, reason={info.hold_reason!r})"
            )
            if info.id is not None:
                skipped_ids.append(info.id)
            continue

        if is_eng_pqc_future_hold(info.route_id, info.station, info.hold_reason):
            rtype = RECORD_TYPE_ENG_FUTURE
        else:
            rtype = resolve_record_type(info.product_id, info.hold_code, info.station)
        if rtype is None:
            logger.info(
                f"hold_info id={info.id} 不满足处置单划分，无需转成 record "
                f"(product={info.product_id}, code={info.hold_code}, "
                f"station={info.station})"
            )
            if info.id is not None:
                skipped_ids.append(info.id)
            continue

        bucket = _merge_code_bucket(rtype, info.hold_code)
        if rtype == RECORD_TYPE_WLT:
            # 同 lot（'-' 前相同）的 wafer 合并为一条；优先取 wafer 前缀
            lot_prefix = (
                normalize_lot_id(info.wafer_id)
                or normalize_lot_id(info.lot_id)
            )
            if not lot_prefix:
                logger.warning(
                    f"跳过无法解析 lot 前缀的 WLT hold_info id={info.id} "
                    f"(wafer={info.wafer_id}, lot={info.lot_id})"
                )
                if info.id is not None:
                    skipped_ids.append(info.id)
                continue
            group_key = ('wlt_lot', lot_prefix, rtype, bucket)
        elif is_fragmented_merged_lot(info.lot_id, info.wafer_id):
            group_key = ('lot', info.lot_id, rtype, bucket)
        else:
            group_key = ('wafer', info.wafer_id, rtype, bucket)
        by_key[group_key].append(info)

    records: List[RoughHoldRecord] = []
    for group_key in sorted(by_key.keys(), key=lambda k: (k[0], k[1], k[2], k[3])):
        mode, key_id, rtype, bucket = group_key
        raw_items = by_key[group_key]
        all_source_ids = [i.id for i in raw_items if i.id is not None]
        deduped = dedupe_hold_infos(raw_items, window=window)
        multi_wafer = mode in ('lot', 'wlt_lot')
        if len(deduped) < len(raw_items):
            bucket_part = f" bucket={bucket}" if bucket else ''
            logger.info(
                f"mode={mode} key={key_id} record_type={rtype}{bucket_part}: "
                f"{len(raw_items)} → {len(deduped)} "
                f"（去掉 {len(raw_items) - len(deduped)} 条重复）"
            )
        if multi_wafer:
            # 占位：真正写入值由 to_record_dict 用多片后缀生成（#01#02）
            wafer_label = build_merged_wafer_display(
                (i.wafer_id for i in deduped), max_len=100
            ) or key_id
        else:
            wafer_label = key_id
        records.append(
            RoughHoldRecord(
                wafer_id=wafer_label,
                record_type=rtype,
                items=deduped,
                all_source_ids=all_source_ids,
                fragmented_merged=multi_wafer,
                lot_id_override=key_id if mode == 'wlt_lot' else None,
                merge_code_bucket=bucket,
            )
        )
    return records, skipped_ids


def is_ziyi_append_candidate(rec: RoughHoldRecord) -> bool:
    """
    梓一合批才走跨周期追加：分片合批、非 WLT、ATTR 含梓一 bit。
    IQC_ATE / 普通单片 / ENG 预 Hold 不合此条件。
    """
    if rec.record_type in (RECORD_TYPE_WLT, RECORD_TYPE_ENG_FUTURE):
        return False
    if rec.lot_id_override is not None:
        return False
    if not rec.fragmented_merged or not rec.items:
        return False
    first = rec.items[0]
    attr = compute_hold_wafer_attr(first.lot_id, first.equip_id, first.station)
    return bool(attr & HOLD_WAFER_ATTR_ZIYI)


def format_quiet_state_key(mode: str, key_id: str, record_type: int, bucket: str) -> str:
    """水位文件里的分组键。028 分桶与合批分组键一致。"""
    return f'{mode}|{key_id}|{int(record_type)}|{bucket or ""}'


def quiet_state_key_for_row(row: dict) -> Optional[str]:
    """
    梓一分片合批或 WLT 同 lot 返回水位键；单片 / IQC_ATE / FVI / 不参与合批的行返回 None。
    判定顺序与 build_rough_hold_records 的分组一致。
    """
    if not isinstance(row, dict):
        return None
    info = HoldInfo.from_row(row)
    if not str(info.wafer_id or '').strip():
        return None
    if _is_fpqc_future_hold(info):
        return None
    if is_eng_pqc_future_hold(info.route_id, info.station, info.hold_reason):
        return None
    rtype = resolve_record_type(info.product_id, info.hold_code, info.station)
    if rtype is None:
        return None
    bucket = _merge_code_bucket(rtype, info.hold_code)
    if rtype == RECORD_TYPE_WLT:
        lot_prefix = (
            normalize_lot_id(info.wafer_id) or normalize_lot_id(info.lot_id)
        )
        if not lot_prefix:
            return None
        return format_quiet_state_key('wlt_lot', lot_prefix, rtype, bucket)
    if is_fragmented_merged_lot(info.lot_id, info.wafer_id):
        attr = compute_hold_wafer_attr(info.lot_id, info.equip_id, info.station)
        if attr & HOLD_WAFER_ATTR_ZIYI:
            return format_quiet_state_key('lot', info.lot_id, rtype, bucket)
    return None


def quiet_state_key_for_record(rec: RoughHoldRecord) -> Optional[str]:
    """与 quiet_state_key_for_row 对齐的 record 水位键。非梓一 / WLT 返回 None。"""
    if rec.record_type == RECORD_TYPE_WLT and rec.lot_id_override:
        return format_quiet_state_key(
            'wlt_lot', rec.lot_id_override, rec.record_type, rec.merge_code_bucket,
        )
    if is_ziyi_append_candidate(rec):
        return format_quiet_state_key(
            'lot', rec.items[0].lot_id, rec.record_type, rec.merge_code_bucket,
        )
    return None


def pick_latest_wafer_id(rows: List[dict]) -> Optional[str]:
    """
    全局 HOLD_DTTM 最新的 WAFER_ID。HOLD_DTTM 为 VARCHAR2，字典序即时间序；
    相同则 ID 更大者优先。与原先 SQL `ORDER BY HOLD_DTTM DESC, ID DESC` 一致。
    """
    best_key = None
    best_wafer = None
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        wafer = str(row.get('WAFER_ID') or '').strip()
        if not wafer:
            continue
        dttm = '' if row.get('HOLD_DTTM') is None else str(row.get('HOLD_DTTM'))
        try:
            row_id = int(row.get('ID') or 0)
        except (TypeError, ValueError):
            row_id = 0
        key = (dttm, row_id)
        if best_key is None or key > best_key:
            best_key = key
            best_wafer = wafer
    return best_wafer


def rows_for_merge(rows: List[dict]) -> List[dict]:
    """
    梓一 / WLT 行全部保留（含全局最新一片）。
    其它行扣掉全局最新 WAFER_ID 的全部记录，等下一轮再合，避免同片多行未插完。
    """
    latest = pick_latest_wafer_id(rows)
    kept: List[dict] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if quiet_state_key_for_row(row):
            kept.append(row)
            continue
        wafer = str(row.get('WAFER_ID') or '').strip()
        if latest and wafer == latest:
            continue
        kept.append(row)
    return kept


def group_max_source_id(rec: RoughHoldRecord) -> Optional[int]:
    """组内源 info 的最大 ID。后插入的行 ID 更大，用来判断 MES 是否还在写。"""
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


def evaluate_lot_quiet(
    mark,
    max_id: int,
    now: datetime,
    quiet: timedelta,
    max_wait: timedelta,
) -> Tuple[str, Optional[dict]]:
    """
    判断一组梓一 / WLT 本轮是继续等还是建单。

    返回 (status, mark)：
      defer  首次出现，或 max(ID) 变大且未超过最长等待。mark 为要写回的水位。
      ready  水位没涨，且距上次变大已满安静窗口。
      cap    自首次看到已超过最长等待，用已有行建单。
    建单时 mark 为 None，调用方删掉该键。
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


def prune_lot_quiet_state(
    state: dict,
    keep: set,
    now: datetime,
    max_wait: timedelta,
) -> None:
    """丢掉本轮没再出现、且上次变大已超过最长等待的水位。"""
    for key in list(state):
        if key in keep:
            continue
        coerced = _coerce_quiet_mark(state.get(key))
        if coerced is None:
            state.pop(key, None)
            continue
        if (now - coerced['last_growth_at']) >= max_wait:
            state.pop(key, None)


def decide_lot_quiet(
    records: List[RoughHoldRecord],
    state: Optional[dict],
    now: datetime,
    quiet: timedelta,
    max_wait: timedelta,
) -> Tuple[dict, List[RoughHoldRecord], List[RoughHoldRecord], dict]:
    """
    给梓一 / WLT record 标上 defer / ready / cap。其它 record 不在返回的 action 里。
    不修改传入的 state。返回 (action_by_identity, deferred, capped, new_state)。
    """
    new_state = dict(state or {})
    action_by_id = {}
    deferred: List[RoughHoldRecord] = []
    capped: List[RoughHoldRecord] = []
    keep = set()
    for rec in records:
        key = quiet_state_key_for_record(rec)
        if not key:
            continue
        max_id = group_max_source_id(rec)
        if max_id is None:
            new_state.pop(key, None)
            action_by_id[id(rec)] = 'ready'
            continue
        status, mark = evaluate_lot_quiet(
            new_state.get(key), max_id, now, quiet, max_wait,
        )
        action_by_id[id(rec)] = status
        if status == 'defer' and mark is not None:
            new_state[key] = mark
            keep.add(key)
            deferred.append(rec)
            continue
        new_state.pop(key, None)
        if status == 'cap':
            capped.append(rec)
    prune_lot_quiet_state(new_state, keep, now, max_wait)
    return action_by_id, deferred, capped, new_state


def load_lot_quiet_state(path: str) -> dict:
    """读取水位文件。缺失或损坏时按空水位，本组会先等一个安静周期。"""
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding='utf-8') as handle:
            data = json.load(handle)
    except Exception as exc:
        logger.warning(f"读取同 lot 安静水位失败，本轮按空水位处理: {exc}")
        return {}
    if not isinstance(data, dict):
        logger.warning("同 lot 安静水位不是对象，本轮按空水位处理")
        return {}
    return data


def save_lot_quiet_state(path: str, state: dict) -> None:
    """原子替换水位文件，避免写到一半被下次调度读到。"""
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    temporary = path + '.tmp'
    with open(temporary, 'w', encoding='utf-8') as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    os.replace(temporary, path)


def build_ziyi_append_updates(
    existing_rows: List[dict],
    new_record: RoughHoldRecord,
    window: timedelta,
    status: int = 0,
) -> Optional[dict]:
    """已挂 info + 本轮新 info 重算 WAFER_ID / HOLD_CODE / HOLD_REASON / GRADE_NUM / HOLD_DTTM。"""
    existing_infos = [HoldInfo.from_row(r) for r in existing_rows or []]
    combined_items = dedupe_hold_infos(
        existing_infos + list(new_record.items),
        window=window,
    )
    combined = RoughHoldRecord(
        wafer_id=new_record.wafer_id,
        record_type=new_record.record_type,
        items=combined_items,
        all_source_ids=list(new_record.source_ids),
        fragmented_merged=True,
        lot_id_override=None,
        merge_code_bucket=new_record.merge_code_bucket,
    )
    row = combined.to_record_dict(status=status)
    if not row:
        return None
    return {
        'WAFER_ID': row['WAFER_ID'],
        'HOLD_CODE': row['HOLD_CODE'],
        'HOLD_REASON': row['HOLD_REASON'],
        'GRADE_NUM': row.get('GRADE_NUM'),
        'HOLD_DTTM': row.get('HOLD_DTTM'),
    }


class HoldMergeScheduler(threading.Thread):
    """
    定时将 FT_HOLD_INFO 中 AREA=0、尚未关联 record、且满足处置单划分的 hold 合并写入 FT_HOLD_RECORD。
    HOLDING=0 不限日期。HOLDING=1 只合 HOLD_DTTM 在 2026-10-15 及以后的 info。
      - 普通：按 (WAFER_ID, RECORD_TYPE)；FT 028 与良率/缺陷率分列
      - 分片合批：按 (LOT_ID, RECORD_TYPE)；FT 028 同样分列
      - 梓一 / WLT：组内 max(info ID) 还在变大，或距上次变大未满安静窗口，则本轮整组不建单
      - 梓一合批：安静期结束后，同 LOT 已有 MES record 且 HOLD_DTTM 在合批间隔回看窗口内则追加，不按 STATUS
      - WLT：按 (lot_prefix, RECORD_TYPE) 分组，LOT_ID 保留 INFO 原值（LOT.NO），WAFER_ID 为 #01#02
      - 单片 / IQC_ATE / FVI：仍扣掉全局 HOLD_DTTM 最新的那一个 WAFER_ID
    """

    def __init__(self):
        super().__init__(daemon=True)
        self._stop_event = threading.Event()
        self.logger = logger
        self.config = Config()
        self.interval_minutes = getattr(self.config, 'HOLD_MERGE_INTERVAL_MINUTES', 30)
        self.hold_info_table = getattr(self.config, 'HOLD_INFO_TABLE', 'FT_HOLD_INFO')
        self.hold_record_table = getattr(self.config, 'HOLD_RECORD_TABLE', 'FT_HOLD_RECORD')
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
        return os.path.join('logs', f'hold_merge_lot_quiet_{table}.json')

    def stop(self):
        self.logger.info("正在停止 Hold 合并调度器...")
        self._stop_event.set()

    def _persist_rough_record(self, rec: RoughHoldRecord) -> Optional[int]:
        """
        写入或追加一条 rough record。
        梓一且同 LOT 已有单的 HOLD_DTTM 在合批间隔回看窗口内 → append；否则 insert。
        成功返回 hold_record ID；失败返回 None（源 info 已标 -1）。
        """
        row = rec.to_record_dict(status=self.record_status)
        if not row:
            mark_hold_infos_dirty(
                rec.source_ids,
                info_table=self.hold_info_table,
                reason=f"wafer={rec.wafer_id} 无有效 items",
            )
            self.logger.warning(
                f"wafer={rec.wafer_id} 无有效 items，已标记 HOLD_RECORD_ID=-1"
            )
            return None

        if is_ziyi_append_candidate(rec):
            existing = find_existing_ziyi_hold_record(
                lot_id=row['LOT_ID'],
                record_type=rec.record_type,
                is_028_bucket=(rec.merge_code_bucket == _FT_DUPCODE_HOLD_CODE),
                record_table=self.hold_record_table,
                ref_dttm=row.get('HOLD_DTTM'),
                window_minutes=self.interval_minutes,
            )
            if existing is not None:
                existing_id = existing.get('ID')
                existing_rows = query_hold_infos_by_record_id(
                    existing_id,
                    info_table=self.hold_info_table,
                )
                if existing_rows is None:
                    mark_hold_infos_dirty(
                        rec.source_ids,
                        info_table=self.hold_info_table,
                        reason=f"梓一追加：查询已有 info 失败 record={existing_id}",
                    )
                    self.logger.error(
                        f"梓一追加失败 lot={row['LOT_ID']} 已有 record={existing_id}："
                        f"查询已挂 info 失败，已标记 HOLD_RECORD_ID=-1"
                    )
                    return None
                updates = build_ziyi_append_updates(
                    existing_rows,
                    rec,
                    window=self.dedup_window,
                    status=self.record_status,
                )
                if not updates:
                    mark_hold_infos_dirty(
                        rec.source_ids,
                        info_table=self.hold_info_table,
                        reason=f"梓一追加：无法重算字段 record={existing_id}",
                    )
                    self.logger.error(
                        f"梓一追加失败 lot={row['LOT_ID']} record={existing_id}："
                        f"无法重算展示字段，已标记 HOLD_RECORD_ID=-1"
                    )
                    return None
                appended_id = append_hold_infos_to_record(
                    existing_id,
                    updates,
                    rec.source_ids,
                    info_table=self.hold_info_table,
                    record_table=self.hold_record_table,
                )
                if appended_id is None:
                    self.logger.error(
                        f"梓一追加写入失败 lot={row['LOT_ID']} "
                        f"record={existing_id} wafer={updates.get('WAFER_ID')}，"
                        f"已标记 HOLD_RECORD_ID=-1"
                    )
                    return None
                self.logger.info(
                    f"梓一追加成功 lot={row['LOT_ID']} → "
                    f"{self.hold_record_table}.ID={appended_id}, "
                    f"WAFER_ID={updates.get('WAFER_ID')}, "
                    f"HOLD_CODE={updates.get('HOLD_CODE')}"
                )
                return appended_id

        new_id = insert_hold_record_and_link(
            row,
            rec.source_ids,
            info_table=self.hold_info_table,
            record_table=self.hold_record_table,
        )
        if new_id is None:
            self.logger.error(
                f"写入失败 wafer={rec.wafer_id} "
                f"record_type={row.get('RECORD_TYPE')} "
                f"codes={row.get('HOLD_CODE')}，已标记 HOLD_RECORD_ID=-1"
            )
            return None
        self.logger.info(
            f"写入成功 wafer={rec.wafer_id} → "
            f"{self.hold_record_table}.ID={new_id}, "
            f"RECORD_TYPE={row.get('RECORD_TYPE')}, "
            f"HOLD_CODE={row.get('HOLD_CODE')}"
        )
        if int(row.get('RECORD_TYPE') or -1) == RECORD_TYPE_ENG_FUTURE:
            self._report_eng_future(new_id, row)
        return new_id

    def _report_eng_future(self, record_id, record) -> None:
        """上报失败只记日志。STATUS 仍是创建，下一轮合批会补报。"""
        try:
            from app.controllers.hold_info_export_ctrl import report_eng_future_to_ncmr
            ok, msg = report_eng_future_to_ncmr(
                record_id, record, record_table=self.hold_record_table,
            )
        except Exception as exc:
            self.logger.error(
                f"ENG预Hold 上报异常 id={record_id}: {exc}", exc_info=True,
            )
            return
        if ok:
            self.logger.info(f"ENG预Hold 已上报 NCMR id={record_id}: {msg}")
            return
        self.logger.error(f"ENG预Hold 上报未完成 id={record_id}: {msg}")

    def _retry_unreported_eng_future(self) -> None:
        from app.utils.database_util import query_unreported_eng_future_records
        rows = query_unreported_eng_future_records(self.hold_record_table)
        if rows is None:
            self.logger.error("查询未上报 ENG预Hold 失败，本轮不补报")
            return
        if not rows:
            return
        self.logger.info(f"补报未上报 ENG预Hold {len(rows)} 条")
        for record in rows:
            self._report_eng_future(record.get('ID'), record)

    def _run_job(self):
        try:
            self.logger.info(">>> Hold 合并定时任务开始执行...")
            hold_infos = query_online_hold_info(self.hold_info_table)
            if hold_infos is None:
                self.logger.error("查询在线 hold_info 失败")
                return

            self.logger.info(
                f"从表 {self.hold_info_table} 查询到 {len(hold_infos)} 条待合批 hold_info "
                f"(AREA=0, HOLD_RECORD_ID∈{{NULL,0}}，HOLDING=0 或 "
                f"2026-10-15 及以后的 HOLDING=1，已排除 -1 脏数据，"
                f"已按处置单划分预过滤)"
            )

            merge_rows = rows_for_merge(hold_infos)
            rough_records, skipped_ids = build_rough_hold_records(
                merge_rows, window=self.dedup_window
            )
            if skipped_ids:
                # 不满足划分规则 / FUTURE HOLD 排除：标记 -1，避免轮询反复捞取
                mark_hold_infos_dirty(
                    skipped_ids,
                    info_table=self.hold_info_table,
                    reason='无需转成 record（不满足划分或 FPQC 025 FUTURE HOLD）',
                )
                self.logger.info(
                    f"跳过 {len(skipped_ids)} 条无需合批的 hold_info，"
                    f"已标记 HOLD_RECORD_ID=-1"
                )

            self.logger.info(
                f"归纳得到 {len(rough_records)} 条粗糙 hold_record "
                f"（去重窗口={self.dedup_window}，"
                f"同 lot 安静={self.lot_quiet_minutes} 分钟）"
            )

            ok, fail, deferred = 0, 0, 0
            now = datetime.now()
            state_path = self._quiet_state_path()
            state = load_lot_quiet_state(state_path)
            actions, _deferred_recs, capped, state = decide_lot_quiet(
                rough_records,
                state,
                now,
                self.lot_quiet,
                self.lot_quiet_max,
            )
            capped_ids = {id(rec) for rec in capped}
            for rec in rough_records:
                self.logger.info(f"  - {rec.summary()}")
                action = actions.get(id(rec), 'ready')
                if action == 'defer':
                    self.logger.info(
                        f"同 lot 仍有新 info，本轮不建单，"
                        f"等 {self.lot_quiet_minutes} 分钟无新行: {rec.summary()}"
                    )
                    deferred += 1
                    continue
                if id(rec) in capped_ids:
                    self.logger.info(
                        f"同 lot 自首次看到已超过 {self.lot_quiet_max_minutes} 分钟，"
                        f"用已有行建单: {rec.summary()}"
                    )
                if self._persist_rough_record(rec) is None:
                    fail += 1
                else:
                    ok += 1

            try:
                save_lot_quiet_state(state_path, state)
            except Exception as save_exc:
                self.logger.error(
                    f"写入同 lot 安静水位失败: {save_exc}", exc_info=True
                )

            self._retry_unreported_eng_future()
            self.logger.info(
                f"<<< Hold 合并定时任务执行完毕：成功 {ok}，失败 {fail}，"
                f"跳过 {len(skipped_ids)}，同 lot 等待下次 {deferred}"
            )
        except Exception as e:
            self.logger.error(f"Hold 合并定时任务执行出错: {e}", exc_info=True)
            notify_severe_error('Hold 合并定时任务整轮失败', str(e), exc=e)

    def run(self):
        self.logger.info(
            f"Hold 合并调度器已启动，间隔 {self.interval_minutes} 分钟，"
            f"源表={self.hold_info_table}，"
            f"RECORD_TYPE 按 dispose_api.md 处置单划分判定"
        )
        # 启动时立即跑一轮，再按间隔调度
        self._run_job()
        if self._stop_event.is_set():
            self.logger.info("Hold 合并调度器线程已退出")
            return

        # 使用独立 Scheduler，避免与其他定时任务共用全局 schedule 冲突
        sch = schedule.Scheduler()
        sch.every(self.interval_minutes).minutes.do(self._run_job)

        while not self._stop_event.is_set():
            sch.run_pending()
            time.sleep(1)

        self.logger.info("Hold 合并调度器线程已退出")


if __name__ == '__main__':
    reject_legacy_mode_arg()
    print(f"HOLD_ENV={'test' if is_test_env() else 'release'} 源表={Config.HOLD_INFO_TABLE}")
    # 独立进程常驻。同一套库上只留一个进程，锁在库会话里。
    hold_singleton(LOCK_FT_HOLD_MERGE, logger)
    scheduler = HoldMergeScheduler()
    scheduler.start()
    try:
        while scheduler.is_alive():
            time.sleep(1)
    except KeyboardInterrupt:
        scheduler.stop()
        scheduler.join(timeout=5)
