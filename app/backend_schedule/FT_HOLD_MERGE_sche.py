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
from typing import List, Optional

from app.config import Config
from app.utils.database_util import (
    insert_hold_record_and_link,
    mark_hold_infos_dirty,
    query_online_hold_info,
)

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
    按 WAFER_ID 分组、并对 (STATION, HOLD_CODE) 做时间窗去重后的粗糙 hold record。
    items 为去重后用于拼装 FT_HOLD_RECORD 的条目；
    all_source_ids 含去重前全部源 ID，写入成功后一并回写 HOLD_RECORD_ID。
    """
    wafer_id: str
    items: List[HoldInfo] = field(default_factory=list)
    all_source_ids: List[int] = field(default_factory=list)

    @property
    def source_ids(self) -> List[int]:
        return list(self.all_source_ids) or [
            item.id for item in self.items if item.id is not None
        ]

    def summary(self) -> str:
        codes = sorted({i.hold_code for i in self.items})
        stations = sorted({i.station for i in self.items})
        return (
            f"wafer={self.wafer_id}, items={len(self.items)}, "
            f"source_ids={len(self.source_ids)}, codes={codes}, stations={stations}"
        )

    def to_record_dict(
        self,
        record_type: int = 0,
        status: int = 0,
    ) -> Optional[dict]:
        """
        将去重后的 items 归纳为一条 FT_HOLD_RECORD 行数据。
        基础字段取时间最早一条；HOLD_CODE / HOLD_REASON 按时间序去重后用 @ 拼接。
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
        return {
            'PRODUCT_ID': first.product_id,
            'STATION': first.station,
            'EQUIP_ID': first.equip_id,
            'LOT_ID': first.lot_id,
            'WAFER_ID': self.wafer_id,
            'HOLD_CODE': hold_code,
            'HOLD_REASON': hold_reason,
            'SOURCE': first.source if first.source is not None else 0,
            'SECOND_CODE': second_code,
            'ROUTE_ID': route_id,
            'RECORD_TYPE': record_type,
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
) -> List[RoughHoldRecord]:
    """查询结果 → 按 wafer 分组 → 去重 → 粗糙 hold record 列表。"""
    by_wafer = defaultdict(list)
    for row in rows:
        info = HoldInfo.from_row(row)
        if not info.wafer_id:
            logger.warning(f"跳过无 WAFER_ID 的 hold_info id={info.id}")
            continue
        by_wafer[info.wafer_id].append(info)

    records: List[RoughHoldRecord] = []
    for wafer_id in sorted(by_wafer.keys()):
        raw_items = by_wafer[wafer_id]
        all_source_ids = [i.id for i in raw_items if i.id is not None]
        deduped = dedupe_hold_infos(raw_items, window=window)
        if len(deduped) < len(raw_items):
            logger.info(
                f"wafer={wafer_id}: {len(raw_items)} → {len(deduped)} "
                f"（去掉 {len(raw_items) - len(deduped)} 条重复）"
            )
        records.append(
            RoughHoldRecord(
                wafer_id=wafer_id,
                items=deduped,
                all_source_ids=all_source_ids,
            )
        )
    return records


class HoldMergeScheduler(threading.Thread):
    """
    定时将 FT_HOLD_INFO 中同一 wafer 的多条在线 hold 合并为一条 FT_HOLD_RECORD。
    """

    def __init__(self):
        super().__init__(daemon=True)
        self._stop_event = threading.Event()
        self.logger = logger
        self.config = Config()
        self.interval_minutes = getattr(self.config, 'HOLD_MERGE_INTERVAL_MINUTES', 30)
        self.hold_info_table = getattr(self.config, 'HOLD_INFO_TABLE', 'FT_HOLD_INFO_TEST')
        self.hold_record_table = getattr(self.config, 'HOLD_RECORD_TABLE', 'FT_HOLD_RECORD')
        self.hold_codes = getattr(self.config, 'HOLD_MERGE_HOLD_CODES', [])
        self.stations = getattr(self.config, 'HOLD_MERGE_STATIONS', [])
        self.record_type = getattr(self.config, 'HOLD_RECORD_TYPE', 0)
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
            hold_infos = query_online_hold_info(
                self.hold_info_table,
                hold_codes=self.hold_codes,
                stations=self.stations,
            )
            if hold_infos is None:
                self.logger.error("查询在线 hold_info 失败")
                return

            self.logger.info(
                f"从表 {self.hold_info_table} 查询到 {len(hold_infos)} 条在线 hold_info "
                f"(HOLDING=0, HOLD_RECORD_ID∈{{NULL,0}}，已排除 -1 脏数据, "
                f"HOLD_CODE∈{self.hold_codes}, STATION∈{self.stations})"
            )

            rough_records = build_rough_hold_records(
                hold_infos, window=self.dedup_window
            )
            self.logger.info(
                f"归纳得到 {len(rough_records)} 条粗糙 hold_record "
                f"（去重窗口={self.dedup_window}）"
            )

            ok, fail = 0, 0
            for rec in rough_records:
                self.logger.info(f"  - {rec.summary()}")
                row = rec.to_record_dict(
                    record_type=self.record_type,
                    status=self.record_status,
                )
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
                        f"写入失败 wafer={rec.wafer_id} codes={row.get('HOLD_CODE')}，"
                        f"已标记 HOLD_RECORD_ID=-1"
                    )
                else:
                    ok += 1
                    self.logger.info(
                        f"写入成功 wafer={rec.wafer_id} → "
                        f"{self.hold_record_table}.ID={new_id}, "
                        f"HOLD_CODE={row.get('HOLD_CODE')}"
                    )

            self.logger.info(
                f"<<< Hold 合并定时任务执行完毕：成功 {ok}，失败 {fail}"
            )
        except Exception as e:
            self.logger.error(f"Hold 合并定时任务执行出错: {e}", exc_info=True)

    def run(self):
        self.logger.info(
            f"Hold 合并调度器已启动，间隔 {self.interval_minutes} 分钟，"
            f"源表={self.hold_info_table}，"
            f"HOLD_CODE={self.hold_codes}，STATION={self.stations}"
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
