"""前身 HOLD_INFO + HISTORY_DISPOSITION → 训练用伪 record（不落库）。"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from app.utils.database_util import normalize_lot_id

# 前身表训练代码保留，默认关闭。勿接入调度；仅手动 --enable-legacy-source 才可跑。
LEGACY_TRAIN_ENABLED = False
LEGACY_RELEASE_ENG_DISPOSE = 0
HOLD_CODE_WARN_RATE = 0.5

_HOLD_CODE_RE = re.compile(r'(?<!\d)(023|024|025|027)(?!\d)')

_HOLD_DT_FORMATS = (
    '%Y-%m-%d %H:%M:%S',
    '%Y/%m/%d %H:%M:%S',
    '%Y-%m-%d %H:%M:%S.%f',
    '%Y/%m/%d %H:%M:%S.%f',
    '%Y-%m-%dT%H:%M:%S',
    '%Y-%m-%dT%H:%M:%S.%f',
    '%Y%m%d%H%M%S',
    '%Y-%m-%d',
    '%Y/%m/%d',
)


def parse_hold_datetime(raw) -> Optional[datetime]:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw
    text = str(raw).strip()
    if not text:
        return None
    for fmt in _HOLD_DT_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    if 'T' in text:
        return parse_hold_datetime(text.replace('T', ' ', 1))
    if len(text) > 19:
        return parse_hold_datetime(text[:19])
    return None


def extract_hold_codes_from_reason(reason) -> list[str]:
    if reason is None:
        return []
    seen = []
    for match in _HOLD_CODE_RE.finditer(str(reason)):
        code = match.group(1)
        if code not in seen:
            seen.append(code)
    return seen


def legacy_label_y(eng_dispose) -> int:
    try:
        return 1 if int(eng_dispose) == LEGACY_RELEASE_ENG_DISPOSE else 0
    except (TypeError, ValueError):
        return 0


def to_pseudo_record(row: dict) -> dict:
    """HOLD_INFO 行 + 首次处置 → 形如 FT_HOLD_RECORD 的内存 dict。"""
    wafer = str(row.get('WAFER_ID') or '').strip()
    codes = extract_hold_codes_from_reason(row.get('HOLD_REASON'))
    hold_dttm = row.get('HOLD_DTTM')
    if not isinstance(hold_dttm, datetime):
        hold_dttm = parse_hold_datetime(hold_dttm) or parse_hold_datetime(row.get('HOLD_DATETIME'))
    dispose = row.get('LABEL_DISPOSE')
    if dispose is None:
        dispose = row.get('ENG_DISPOSE')
    return {
        'ID': int(row['ID']),
        'PRODUCT_ID': row.get('PRODUCT_ID'),
        'STATION': None,
        'EQUIP_ID': row.get('EQUIP_ID'),
        'LOT_ID': normalize_lot_id(wafer) or None,
        'WAFER_ID': wafer,
        'HOLD_CODE': '@'.join(codes) if codes else None,
        'HOLD_REASON': row.get('HOLD_REASON'),
        'SOURCE': None,
        'SECOND_CODE': row.get('SECOND_CODE'),
        'ROUTE_ID': row.get('ROUTE_ID'),
        'GRADE_NUM': row.get('GRADE_NUM'),
        'HOLD_DTTM': hold_dttm,
        'LABEL_DISPOSE': dispose,
        'LABEL_DTTM': row.get('LABEL_DTTM') or row.get('DISPOSE_TIME'),
        'LABEL_Y': legacy_label_y(dispose),
        '_prior_source': 'legacy',
    }


def summarize_legacy_records(records: list[dict]) -> dict:
    n = len(records)
    with_code = sum(1 for rec in records if rec.get('HOLD_CODE'))
    with_dttm = sum(1 for rec in records if isinstance(rec.get('HOLD_DTTM'), datetime))
    pos = sum(1 for rec in records if rec.get('LABEL_Y') == 1)
    rate = (with_code / n) if n else 0.0
    return {
        'n': n,
        'release_n': pos,
        'hold_code_n': with_code,
        'hold_code_rate': rate,
        'hold_dttm_n': with_dttm,
    }
