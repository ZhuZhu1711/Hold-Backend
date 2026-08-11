import sys
import os
import argparse
import threading
import time
import schedule
import logging
from logging.handlers import RotatingFileHandler

# 直接运行本文件时补齐项目根路径（与 main.py 一致）
current_file_path = os.path.abspath(__file__)
project_root_path = os.path.dirname(os.path.dirname(os.path.dirname(current_file_path)))
if project_root_path not in sys.path:
    sys.path.insert(0, project_root_path)

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from app.config import Config
from app.utils.database_util import (
    build_merged_wafer_display,
    insert_hold_record_and_link,
    is_fragmented_merged_lot,
    mark_hold_infos_dirty,
    normalize_lot_id,
    query_online_hold_info,
)

# 处置单划分（见 dispose_api.md）：
#   FT异常反馈单  RECORD_TYPE=0  PRODUCT_ID *-3.5, HOLD_CODE∈{023,024,025,027}, STATION∉{FAOIFINISH,FFVI}
#   FVI异常反馈单 RECORD_TYPE=1  PRODUCT_ID *,     HOLD_CODE=023,               STATION∈{FAOIFINISH,FFVI}
#   WLT异常反馈单 RECORD_TYPE=2  PRODUCT_ID *-2.6, HOLD_CODE∈{004,022},         STATION=WOQC
# 不满足以上规则的 hold_info 不转成 record。
_FT_HOLD_CODES = frozenset({'023', '024', '025', '027'})
_FVI_HOLD_CODES = frozenset({'023'})
_WLT_HOLD_CODES = frozenset({'004', '022'})
_FVI_STATIONS = frozenset({'FAOIFINISH', 'FFVI'})
_WLT_STATIONS = frozenset({'WOQC'})

RECORD_TYPE_FT = 0
RECORD_TYPE_FVI = 1
RECORD_TYPE_WLT = 2


def resolve_record_type(
    product_id: str,
    hold_code: str,
    station: str,
) -> Optional[int]:
    """
    按 dispose_api.md「处置单划分」判定 RECORD_TYPE。
    不匹配任何规则时返回 None（无需转成 record）。
    """
    pid = (product_id or '').strip()
    code = (hold_code or '').strip()
    sta = (station or '').strip().upper()

    # FVI：先判站点限定规则，避免与 FT 的「站点排除」交叉误伤
    if code in _FVI_HOLD_CODES and sta in _FVI_STATIONS:
        return RECORD_TYPE_FVI

    if (
        pid.endswith('-3.5')
        and code in _FT_HOLD_CODES
        and sta not in _FVI_STATIONS
    ):
        return RECORD_TYPE_FT

    if (
        pid.endswith('-2.6')
        and code in _WLT_HOLD_CODES
        and sta in _WLT_STATIONS
    ):
        return RECORD_TYPE_WLT

    return None

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

# HOLD_DTTM 常见格式（VARCHAR2）
_HOLD_DTTM_FORMATS = (
    '%Y-%m-%d %H:%M:%S',
    '%Y/%m/%d %H:%M:%S',
    '%Y-%m-%d %H:%M:%S.%f',
    '%Y/%m/%d %H:%M:%S.%f',
    '%Y%m%d%H%M%S',
    '%Y-%m-%dT%H:%M:%S',
    '%Y-%m-%dT%H:%M:%S.%f',
)


def parse_hold_dttm(raw) -> Optional[datetime]:
    """解析 HOLD_DTTM；无法解析时返回 None。"""
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
    """按出现顺序去重拼接；空结果返回 None。"""
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


def _merge_grade_num(items: List['HoldInfo']) -> Optional[str]:
    """
    合并多条 hold_info 的 GRADE_NUM：
      1) 全部相同（含全 NULL）→ 任取其一
      2) 除 NULL 外仅一个非空值 → 用该值
      3) 除 NULL 外有多个非空值 → 取 HOLD_DTTM 最新一行的值
    """
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

    latest = max(
        items,
        key=lambda x: (x.hold_dttm or datetime.min, x.id or 0),
    )
    return _norm_grade_num(latest.grade_num)


@dataclass
class HoldInfo:
    """单条 FT_HOLD_INFO 的内存表示。"""
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
    hold_record_id: int = 0
    holding: int = 0
    remark: Optional[str] = None

    @classmethod
    def from_row(cls, row: dict) -> 'HoldInfo':
        raw = row.get('HOLD_DTTM')
        return cls(
            id=row.get('ID'),
            hold_dttm=parse_hold_dttm(raw),
            hold_dttm_raw=str(raw) if raw is not None else '',
            station=row.get('STATION') or '',
            equip_id=row.get('EQUIP_ID') or '',
            product_id=row.get('PRODUCT_ID') or '',
            lot_id=row.get('LOT_ID') or '',
            wafer_id=row.get('WAFER_ID') or '',
            hold_code=row.get('HOLD_CODE') or '',
            hold_reason=row.get('HOLD_REASON') or '',
            source=row.get('SOURCE') if row.get('SOURCE') is not None else 0,
            second_code=row.get('SECOND_CODE'),
            route_id=row.get('ROUTE_ID'),
            grade_num=row.get('GRADE_NUM'),
            hold_record_id=(
                row.get('HOLD_RECORD_ID')
                if row.get('HOLD_RECORD_ID') is not None
                else 0
            ),
            holding=row.get('HOLDING') if row.get('HOLDING') is not None else 0,
            remark=row.get('REMARK'),
        )


@dataclass
class RoughHoldRecord:
    """
    普通：按 (WAFER_ID, RECORD_TYPE) 分组；
    分片合批（LOT_ID!=WAFER_ID 且 LOT 后缀数字>2位）：按 (LOT_ID, RECORD_TYPE) 分组；
    WLT：同 lot（wafer/lot 中 '-' 前文本相同）合并，按 (lot_prefix, RECORD_TYPE) 分组。
    并对 (WAFER_ID, STATION, HOLD_CODE) 做时间窗去重后的粗糙 hold record。
    items 为去重后用于拼装 FT_HOLD_RECORD 的条目；
    all_source_ids 含去重前全部源 ID，写入成功后一并回写 HOLD_RECORD_ID。
    """
    wafer_id: str
    record_type: int
    items: List[HoldInfo] = field(default_factory=list)
    all_source_ids: List[int] = field(default_factory=list)
    # True：分片合批 / WLT 同 lot 合并，WAFER_ID 写入 #01#02 展示串
    fragmented_merged: bool = False
    # WLT：写入 record 时 LOT_ID 用 '-' 前截取结果；其它模式为 None
    lot_id_override: Optional[str] = None

    @property
    def source_ids(self) -> List[int]:
        return list(self.all_source_ids) or [
            item.id for item in self.items if item.id is not None
        ]

    def summary(self) -> str:
        codes = sorted({i.hold_code for i in self.items})
        stations = sorted({i.station for i in self.items})
        if self.lot_id_override is not None:
            mode = 'wlt_lot'
        elif self.fragmented_merged:
            mode = 'fragmented_lot'
        else:
            mode = 'wafer'
        lot_part = (
            f", lot={self.lot_id_override}" if self.lot_id_override else ''
        )
        return (
            f"mode={mode}, wafer={self.wafer_id}{lot_part}, "
            f"record_type={self.record_type}, "
            f"items={len(self.items)}, source_ids={len(self.source_ids)}, "
            f"codes={codes}, stations={stations}"
        )

    def to_record_dict(
        self,
        status: int = 0,
    ) -> Optional[dict]:
        """
        将去重后的 items 归纳为一条 FT_HOLD_RECORD 行数据。
        基础字段取时间最早一条；HOLD_CODE / HOLD_REASON 按时间序去重后用 @ 拼接。
        RECORD_TYPE 取本 rough record 按处置单划分判定的值。
        分片合批 / WLT 同 lot：WAFER_ID 存多片展示串（如 #01#02）。
        WLT：LOT_ID 取 lot_id_override（'-' 前文本）。
        """
        if not self.items:
            return None
        ordered = sorted(
            self.items,
            key=lambda x: (x.hold_dttm or datetime.min, x.id or 0),
        )
        first = ordered[0]
        hold_code = _join_unique(
            (i.hold_code for i in ordered), sep='@', max_len=100
        )
        hold_reason = _join_unique(
            (i.hold_reason for i in ordered), sep='@', max_len=512
        )
        second_code = next(
            (i.second_code for i in ordered if i.second_code), None
        )
        route_id = next((i.route_id for i in ordered if i.route_id), None)
        grade_num = _merge_grade_num(ordered)
        if self.fragmented_merged:
            wafer_out = build_merged_wafer_display(
                (i.wafer_id for i in ordered), max_len=100
            ) or self.wafer_id
        else:
            wafer_out = self.wafer_id
        lot_out = (
            self.lot_id_override
            if self.lot_id_override is not None
            else first.lot_id
        )
        return {
            'PRODUCT_ID': first.product_id,
            'STATION': first.station,
            'EQUIP_ID': first.equip_id,
            'LOT_ID': lot_out,
            'WAFER_ID': wafer_out,
            'HOLD_CODE': hold_code,
            'HOLD_REASON': hold_reason,
            'SOURCE': first.source if first.source is not None else 0,
            'SECOND_CODE': second_code,
            'ROUTE_ID': route_id,
            'GRADE_NUM': grade_num,
            'RECORD_TYPE': self.record_type,
            'STATUS': status,
            # FT_HOLD_RECORD.HOLD_DTTM 为 DATE，取最早一条的时间
            'HOLD_DTTM': first.hold_dttm,
        }


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
      - WLT：('wlt_lot', lot_prefix, record_type)，lot_prefix 为 wafer/lot 中 '-' 前文本
      - 分片合批（LOT!=WAFER 且 LOT 后缀数字>2位）：('lot', exact_lot_id, record_type)
      - 其它：('wafer', wafer_id, record_type)

    返回 (records, skipped_ids)：
      records     可写入的 RoughHoldRecord
      skipped_ids 不满足处置单划分、无需转成 record 的源 hold_info ID
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
            group_key = ('wlt_lot', lot_prefix, rtype)
        elif is_fragmented_merged_lot(info.lot_id, info.wafer_id):
            group_key = ('lot', info.lot_id, rtype)
        else:
            group_key = ('wafer', info.wafer_id, rtype)
        by_key[group_key].append(info)

    records: List[RoughHoldRecord] = []
    for group_key in sorted(by_key.keys(), key=lambda k: (k[0], k[1], k[2])):
        mode, key_id, rtype = group_key
        raw_items = by_key[group_key]
        all_source_ids = [i.id for i in raw_items if i.id is not None]
        deduped = dedupe_hold_infos(raw_items, window=window)
        multi_wafer = mode in ('lot', 'wlt_lot')
        if len(deduped) < len(raw_items):
            logger.info(
                f"mode={mode} key={key_id} record_type={rtype}: "
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
            )
        )
    return records, skipped_ids


class HoldMergeScheduler(threading.Thread):
    """
    定时将 FT_HOLD_INFO 中满足处置单划分的在线 hold 合并写入 FT_HOLD_RECORD：
      - 普通：按 (WAFER_ID, RECORD_TYPE)
      - 分片合批：按 (LOT_ID, RECORD_TYPE)
      - WLT：按 (lot_prefix, RECORD_TYPE)，LOT_ID 截取 '-' 前，WAFER_ID 为 #01#02
    """

    def __init__(self):
        super().__init__(daemon=True)
        self._stop_event = threading.Event()
        self.logger = logger
        self.config = Config()
        self.interval_minutes = getattr(self.config, 'HOLD_MERGE_INTERVAL_MINUTES', 30)
        self.hold_info_table = getattr(self.config, 'HOLD_INFO_TABLE', 'FT_HOLD_INFO_TEST')
        self.hold_record_table = getattr(self.config, 'HOLD_RECORD_TABLE', 'FT_HOLD_RECORD')
        self.record_status = getattr(self.config, 'HOLD_RECORD_STATUS', 0)
        self.dedup_window = timedelta(
            hours=getattr(self.config, 'HOLD_DEDUP_WINDOW_HOURS', 1)
        )

    def stop(self):
        self.logger.info("正在停止 Hold 合并调度器...")
        self._stop_event.set()

    def _run_job(self):
        try:
            self.logger.info(">>> Hold 合并定时任务开始执行...")
            hold_infos = query_online_hold_info(self.hold_info_table)
            if hold_infos is None:
                self.logger.error("查询在线 hold_info 失败")
                return

            self.logger.info(
                f"从表 {self.hold_info_table} 查询到 {len(hold_infos)} 条在线 hold_info "
                f"(HOLDING=0, HOLD_RECORD_ID∈{{NULL,0}}，已排除 -1 脏数据，"
                f"已按处置单划分预过滤)"
            )

            rough_records, skipped_ids = build_rough_hold_records(
                hold_infos, window=self.dedup_window
            )
            if skipped_ids:
                # 不满足划分规则：标记 -1，避免轮询反复捞取
                mark_hold_infos_dirty(
                    skipped_ids,
                    info_table=self.hold_info_table,
                    reason='不满足处置单划分，无需转成 record',
                )
                self.logger.info(
                    f"跳过 {len(skipped_ids)} 条不满足处置单划分的 hold_info，"
                    f"已标记 HOLD_RECORD_ID=-1"
                )

            self.logger.info(
                f"归纳得到 {len(rough_records)} 条粗糙 hold_record "
                f"（去重窗口={self.dedup_window}）"
            )

            ok, fail = 0, 0
            for rec in rough_records:
                self.logger.info(f"  - {rec.summary()}")
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
                    fail += 1
                    continue
                new_id = insert_hold_record_and_link(
                    row,
                    rec.source_ids,
                    info_table=self.hold_info_table,
                    record_table=self.hold_record_table,
                )
                if new_id is None:
                    # insert_hold_record_and_link 失败时已置 HOLD_RECORD_ID=-1
                    fail += 1
                    self.logger.error(
                        f"写入失败 wafer={rec.wafer_id} "
                        f"record_type={row.get('RECORD_TYPE')} "
                        f"codes={row.get('HOLD_CODE')}，已标记 HOLD_RECORD_ID=-1"
                    )
                else:
                    ok += 1
                    self.logger.info(
                        f"写入成功 wafer={rec.wafer_id} → "
                        f"{self.hold_record_table}.ID={new_id}, "
                        f"RECORD_TYPE={row.get('RECORD_TYPE')}, "
                        f"HOLD_CODE={row.get('HOLD_CODE')}"
                    )

            self.logger.info(
                f"<<< Hold 合并定时任务执行完毕：成功 {ok}，失败 {fail}，"
                f"跳过 {len(skipped_ids)}"
            )
        except Exception as e:
            self.logger.error(f"Hold 合并定时任务执行出错: {e}", exc_info=True)

    def run(self):
        self.logger.info(
            f"Hold 合并调度器已启动，间隔 {self.interval_minutes} 分钟，"
            f"源表={self.hold_info_table}，"
            f"RECORD_TYPE 按 dispose_api.md 处置单划分判定"
        )
        # 使用独立 Scheduler，避免与其他定时任务共用全局 schedule 冲突
        sch = schedule.Scheduler()
        sch.every(self.interval_minutes).minutes.do(self._run_job)

        while not self._stop_event.is_set():
            sch.run_pending()
            time.sleep(1)

        self.logger.info("Hold 合并调度器线程已退出")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Hold 合并定时任务，可选 debug/release 模式。默认 debug。')
    parser.add_argument(
        '--mode',
        choices=['debug', 'release'],
        default='debug',
        help='debug：立即执行一次后退出；release：按配置间隔循环调度。'
    )
    args = parser.parse_args()

    print(f"运行模式: {args.mode}")
    scheduler = HoldMergeScheduler()

    if args.mode == 'debug':
        # 开发单次测试：立刻跑一遍任务逻辑，不进入定时循环
        scheduler._run_job()
    else:
        # 作为独立进程常驻调度（主程序 release 模式通常走 HoldMergeScheduler.start()）
        scheduler.start()
        try:
            while scheduler.is_alive():
                time.sleep(1)
        except KeyboardInterrupt:
            scheduler.stop()
            scheduler.join(timeout=5)
