"""合批与手动提草稿共用的处置单划分、HoldInfo、RoughHoldRecord。

不放进 FT_HOLD_MERGE_sche.py，避免 Web 进程和 HoldBackend.exe 带上合批调度脚本。
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from app.utils.database_util import (
    build_merged_wafer_display,
    compute_hold_wafer_attr,
    normalize_lot_id,
)

logger = logging.getLogger('app.backend_schedule.FT_HOLD_MERGE_sche')

_FT_HOLD_CODES = frozenset({'023', '024', '025', '027', '028'})
_FT_DUPCODE_HOLD_CODE = '028'
_FVI_HOLD_CODES = frozenset({'023'})
_WLT_HOLD_CODES = frozenset({'004', '022'})
_FVI_STATIONS = frozenset({'FAOIFINISH', 'FFVI'})
_WLT_STATIONS = frozenset({'WOQC'})

RECORD_TYPE_FT = 0
RECORD_TYPE_FVI = 1
RECORD_TYPE_WLT = 2
# ENG 路线、PQC 站、原因含 FUTURE：建单留档，不进入处置。
RECORD_TYPE_ENG_FUTURE = 3

_PQC_STATIONS = frozenset({'FPQC', 'PQC'})


def is_eng_pqc_future_hold(route_id, station, hold_reason) -> bool:
    """ENG 路线 + PQC/FPQC + 原因含 FUTURE。不看 Hold 码。"""
    route = (route_id or '').upper()
    sta = (station or '').strip().upper()
    reason = (hold_reason or '').upper()
    return 'ENG' in route and sta in _PQC_STATIONS and 'FUTURE' in reason

_HOLD_DTTM_FORMATS = (
    '%Y-%m-%d %H:%M:%S',
    '%Y/%m/%d %H:%M:%S',
    '%Y-%m-%d %H:%M:%S.%f',
    '%Y/%m/%d %H:%M:%S.%f',
    '%Y%m%d%H%M%S',
    '%Y-%m-%dT%H:%M:%S',
    '%Y-%m-%dT%H:%M:%S.%f',
)


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


def _pick_wlt_lot_id(
    items: List['HoldInfo'],
    fallback: Optional[str] = None,
) -> Optional[str]:
    """
    WLT 写入 RECORD 的 LOT_ID：保留 INFO 原值（LOT.NO），不用分组前缀。
    同组混有前缀与 LOT.NO 时优先带 `.NN` 的值。
    """
    lots: List[str] = []
    seen = set()
    for item in items or []:
        text = str(item.lot_id or '').strip()
        if not text or text in seen:
            continue
        seen.add(text)
        lots.append(text)
    if not lots:
        return fallback
    dotted = [x for x in lots if '.' in x and normalize_lot_id(x) != x]
    if dotted:
        return dotted[0]
    suffixed = [x for x in lots if normalize_lot_id(x) != x]
    if suffixed:
        return suffixed[0]
    return lots[0]


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
    普通：按 (WAFER_ID, RECORD_TYPE, 重码桶) 分组；
    分片合批（LOT_ID!=WAFER_ID 且 LOT 后缀数字>2位）：按 (LOT_ID, RECORD_TYPE, 重码桶) 分组；
    WLT：同 lot（wafer/lot 中 '-' / `.NN` 前文本相同）合并，按 (lot_prefix, RECORD_TYPE) 分组。
    FT 重码(028) 与同片/同批良率、缺陷率 hold 分列。
    并对 (WAFER_ID, STATION, HOLD_CODE) 做时间窗去重后的粗糙 hold record。
    items 为去重后用于拼装 FT_HOLD_RECORD 的条目；
    all_source_ids 含去重前全部源 ID，写入成功后一并回写 HOLD_RECORD_ID。
    """
    wafer_id: str
    record_type: int
    items: List[HoldInfo] = field(default_factory=list)
    all_source_ids: List[int] = field(default_factory=list)
    fragmented_merged: bool = False
    lot_id_override: Optional[str] = None
    merge_code_bucket: str = ''

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
        WLT：LOT_ID 保留源 INFO 原值（如 S83209.13）；lot_id_override 只作分组前缀。
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
        if self.lot_id_override is not None:
            lot_out = _pick_wlt_lot_id(ordered, fallback=self.lot_id_override)
        else:
            lot_out = first.lot_id
        hold_wafer_attr = compute_hold_wafer_attr(
            first.lot_id, first.equip_id, first.station
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
            'HOLD_DTTM': first.hold_dttm,
            'HOLD_WAFER_ATTR': hold_wafer_attr,
        }
