"""从 FT hold 单抽取可放行预测特征（打分当时快照）。不含 HOLD_REASON。"""
from __future__ import annotations

import json
import logging
import math
import re
from datetime import datetime
from typing import Optional

from app.hold_predict.bysite_index import compute_bysite_index, merge_site_bin_matrices
from app.hold_predict.db import (
    query_bsl_map,
    query_latest_test_wafer,
    query_latest_testlog_path,
    query_product_gross,
    query_product_hold_cnt,
    query_release_rate,
    query_same_lot_yields,
    query_test_bincodes,
    query_wafer_prior_hold_cnt,
)
from app.hold_predict.schema import FEATURE_VERSION, empty_features
from app.utils.database_util import expand_display_wafer_ids, normalize_lot_id

logger = logging.getLogger(__name__)

_GRADE_SPLIT = re.compile(r'[,，;；]+')


def split_hold_codes(raw) -> list[str]:
    if raw is None:
        return []
    parts = []
    for token in str(raw).replace(',', '@').split('@'):
        token = token.strip()
        if token:
            parts.append(token)
    return parts


def route_is_eng(route_id) -> tuple[int, int]:
    """返回 (route_is_eng, route_missing)。"""
    if route_id is None or not str(route_id).strip():
        return 0, 1
    return (1 if 'ENG' in str(route_id).upper() else 0), 0


def ft_analysis_station(lot_id) -> str:
    text = str(lot_id or '').strip()
    if '-' in text:
        return 'FATE-FA'
    return 'VBOX-FA'


def parse_grade_num(raw) -> list[dict]:
    if raw is None:
        return []
    text_val = str(raw).strip()
    if not text_val:
        return []
    items = []
    for part in _GRADE_SPLIT.split(text_val):
        part = part.strip()
        if not part:
            continue
        if ':' in part:
            grade, qty = part.split(':', 1)
        elif '：' in part:
            grade, qty = part.split('：', 1)
        else:
            grade, qty = part, ''
        grade = grade.strip()
        qty = qty.strip()
        if not grade:
            continue
        items.append({'grade': grade, 'qty': qty})
    return items


def _qty_float(raw) -> Optional[float]:
    if raw is None or raw == '':
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _grades_pass_qty(grades_qty_raw) -> Optional[float]:
    if not grades_qty_raw:
        return None
    try:
        grades = json.loads(grades_qty_raw) if isinstance(grades_qty_raw, str) else grades_qty_raw
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(grades, dict):
        return None
    total = 0.0
    for grade, qty in grades.items():
        if 'A' in str(grade).upper():
            try:
                total += float(qty or 0)
            except (TypeError, ValueError):
                continue
    return total


def _wafer_yield(row: dict) -> Optional[float]:
    gross = _qty_float(row.get('GROSS_DIE'))
    if not gross or gross <= 0:
        return None
    pass_die = _grades_pass_qty(row.get('GRADES_QTY'))
    if pass_die is None:
        pass_die = _qty_float(row.get('PASS_DIE'))
    if pass_die is None:
        return None
    return pass_die / gross


def extract_grade_features(grade_num) -> dict:
    items = parse_grade_num(grade_num)
    out = {
        'qty_F': None,
        'qty_HA': None,
        'qty_TA': None,
        'qty_other': None,
        'total_qty': None,
        'ratio_F': None,
        'ratio_passA': None,
        'grade_n': None,
        'missing_grade_num': 1,
    }
    if not items:
        return out
    qty_f = 0.0
    qty_ha = 0.0
    qty_ta = 0.0
    qty_other = 0.0
    qty_pass_a = 0.0
    parsed = 0
    for it in items:
        qty = _qty_float(it.get('qty'))
        if qty is None:
            continue
        parsed += 1
        g = str(it.get('grade') or '').upper()
        if g == 'F' or (g.startswith('F') and 'A' not in g):
            qty_f += qty
        elif g == 'HA':
            qty_ha += qty
        elif g == 'TA':
            qty_ta += qty
        else:
            qty_other += qty
        if 'A' in g:
            qty_pass_a += qty
    if parsed <= 0:
        return out
    total = qty_f + qty_ha + qty_ta + qty_other
    out.update({
        'qty_F': qty_f,
        'qty_HA': qty_ha,
        'qty_TA': qty_ta,
        'qty_other': qty_other,
        'total_qty': total,
        'ratio_F': (qty_f / total) if total else None,
        'ratio_passA': (qty_pass_a / total) if total else None,
        'grade_n': parsed,
        'missing_grade_num': 0,
    })
    return out


def fetch_bysite_matrix(cursor, wafer_ids: list[str], hold_dttm, skip_ftp: bool = False):
    if skip_ftp:
        return None, 'skip_ftp'
    from app.controllers.testlog_ctrl import _download_and_parse_testlog

    last_err = 'no_wafer'
    payloads = []
    for wid in wafer_ids:
        path = query_latest_testlog_path(cursor, wid, 'FA', hold_dttm)
        if not path:
            last_err = 'no_testlog'
            continue
        try:
            parsed = _download_and_parse_testlog(path)
        except Exception as exc:  # noqa: BLE001
            last_err = f'ftp:{exc}'
            logger.warning('bysite download failed wafer=%s: %s', wid, exc)
            continue
        if parsed is None:
            last_err = 'parse_empty'
            continue
        payloads.append(parsed)
        break
    if not payloads:
        return None, last_err
    return merge_site_bin_matrices(payloads), 'ok'


def extract_features(
    cursor,
    record: dict,
    *,
    skip_bysite: bool = False,
) -> dict:
    feats = empty_features()
    hold_dttm = record.get('HOLD_DTTM')
    if isinstance(hold_dttm, str):
        for fmt in ('%Y-%m-%d %H:%M:%S', '%Y/%m/%d %H:%M:%S'):
            try:
                hold_dttm = datetime.strptime(hold_dttm.strip()[:19], fmt)
                break
            except ValueError:
                hold_dttm = record.get('HOLD_DTTM')

    codes = split_hold_codes(record.get('HOLD_CODE'))
    primary = codes[0] if codes else ''
    feats['hold_code_n'] = len(codes)
    feats['hold_code_023'] = 1 if '023' in codes else 0
    feats['hold_code_024'] = 1 if '024' in codes else 0
    feats['hold_code_025'] = 1 if '025' in codes else 0
    feats['hold_code_027'] = 1 if '027' in codes else 0

    source = record.get('SOURCE')
    try:
        feats['source'] = int(source) if source is not None else None
    except (TypeError, ValueError):
        feats['source'] = None

    station = str(record.get('STATION') or '')
    lot_id = record.get('LOT_ID')
    analysis_station = ft_analysis_station(lot_id)
    feats['station_is_vbox'] = 1 if 'VBOX' in analysis_station.upper() or 'VBOX' in station.upper() else 0

    is_eng, route_missing = route_is_eng(record.get('ROUTE_ID'))
    feats['route_is_eng'] = is_eng
    feats['route_missing'] = route_missing

    if isinstance(hold_dttm, datetime):
        feats['hold_hour'] = hold_dttm.hour
        feats['hold_weekday'] = hold_dttm.weekday()

    feats.update(extract_grade_features(record.get('GRADE_NUM')))

    wafer_ids = expand_display_wafer_ids(record.get('WAFER_ID'), lot_id)
    if not wafer_ids:
        raw_w = str(record.get('WAFER_ID') or '').strip()
        if raw_w and not raw_w.startswith('#'):
            wafer_ids = [raw_w]

    operation_id = analysis_station
    tw = query_latest_test_wafer(cursor, wafer_ids, operation_id, hold_dttm if isinstance(hold_dttm, datetime) else None)
    if tw:
        feats['missing_test_wafer'] = 0
        gross = _qty_float(tw.get('GROSS_DIE'))
        ng = _qty_float(tw.get('NG_NUM'))
        feats['tw_gross'] = gross
        feats['tw_yield'] = _wafer_yield(tw)
        if gross and gross > 0 and ng is not None:
            feats['tw_ng_ratio'] = ng / gross
        tw_eng, _ = route_is_eng(tw.get('ROUTE'))
        rec_eng, rec_miss = route_is_eng(record.get('ROUTE_ID'))
        if rec_miss == 0:
            feats['route_mismatch'] = 1 if tw_eng != rec_eng else 0

        bins = query_test_bincodes(cursor, int(tw['ID']))
        qty_pairs = []
        for b in bins:
            code = b.get('BIN_CODE')
            qty = _qty_float(b.get('BIN_CODE_QTY'))
            if code is None or qty is None:
                continue
            try:
                qty_pairs.append((int(code), qty))
            except (TypeError, ValueError):
                continue
        total_bin = sum(q for _, q in qty_pairs)
        qty_pairs.sort(key=lambda x: x[1], reverse=True)
        for i in range(5):
            key = f'bin_top{i + 1}_ratio'
            if total_bin and i < len(qty_pairs):
                feats[key] = qty_pairs[i][1] / total_bin
            else:
                feats[key] = None
        bsl_map = query_bsl_map(cursor, str(record.get('PRODUCT_ID') or tw.get('PRODUCT_ID') or ''))
        over = 0
        if bsl_map:
            for code, qty in qty_pairs:
                thr = bsl_map.get(code)
                if thr is not None and qty > thr:
                    over += 1
        feats['bin_over_bsl_cnt'] = over

        prefix = normalize_lot_id(lot_id) or normalize_lot_id(tw.get('LOT_ID')) or normalize_lot_id(tw.get('WAFER_ID'))
        lot_rows = query_same_lot_yields(
            cursor,
            prefix,
            operation_id,
            hold_dttm if isinstance(hold_dttm, datetime) else None,
        )
        yields = []
        current_y = feats['tw_yield']
        for row in lot_rows:
            y = _wafer_yield(row)
            if y is not None:
                yields.append(y)
        if yields:
            feats['lot_yield_mean'] = sum(yields) / len(yields)
            if len(yields) > 1:
                mean = feats['lot_yield_mean']
                var = sum((y - mean) ** 2 for y in yields) / (len(yields) - 1)
                feats['lot_yield_std'] = math.sqrt(var)
            else:
                feats['lot_yield_std'] = 0.0
            if current_y is not None:
                ranked = sorted(yields)
                # 1 = 同 lot 最低良率
                feats['wafer_yield_rank'] = sum(1 for y in ranked if y <= current_y) / len(ranked)

        product_gross = query_product_gross(cursor, str(record.get('PRODUCT_ID') or ''))
        if product_gross and product_gross > 0 and gross:
            feats['tw_gross'] = gross / product_gross

    matrix, bysite_status = fetch_bysite_matrix(
        cursor,
        wafer_ids,
        hold_dttm if isinstance(hold_dttm, datetime) else None,
        skip_ftp=skip_bysite,
    )
    bysite = compute_bysite_index(matrix)
    for key in (
        'site_n', 'fail_cv', 'fail_rate_cv', 'fail_max_share', 'fail_max_z',
        'bin_cv_max', 'bin_cv_over_thr_cnt', 'bysite_index',
        'missing_bysite', 'bysite_degenerate',
    ):
        feats[key] = bysite.get(key)
    feats['_bysite_status'] = bysite_status
    feats['_suspect_site'] = bysite.get('suspect_site')
    feats['suspect_site'] = bysite.get('suspect_site')

    record_id = int(record['ID'])
    product_id = str(record.get('PRODUCT_ID') or '')
    if isinstance(hold_dttm, datetime):
        feats['product_release_rate_30d'] = query_release_rate(
            cursor, hold_dttm, record_id, 30,
            extra_where='AND r.PRODUCT_ID = :pid',
            extra_params={'pid': product_id},
        )
        feats['holdcode_release_rate_30d'] = query_release_rate(
            cursor, hold_dttm, record_id, 30,
            extra_where="AND REGEXP_SUBSTR(r.HOLD_CODE, '[^@]+', 1, 1) = :code",
            extra_params={'code': primary},
        ) if primary else None
        if route_missing:
            feats['route_eng_release_rate_30d'] = None
        else:
            if is_eng:
                extra = "AND UPPER(NVL(r.ROUTE_ID, '')) LIKE '%ENG%'"
            else:
                extra = "AND UPPER(NVL(r.ROUTE_ID, '')) NOT LIKE '%ENG%'"
            feats['route_eng_release_rate_30d'] = query_release_rate(
                cursor, hold_dttm, record_id, 30, extra_where=extra,
            )
        feats['wafer_prior_hold_cnt'] = query_wafer_prior_hold_cnt(
            cursor, record_id, wafer_ids, hold_dttm,
        )
        feats['product_hold_cnt_7d'] = query_product_hold_cnt(
            cursor, record_id, product_id, hold_dttm, days=7,
        )
    else:
        feats['wafer_prior_hold_cnt'] = query_wafer_prior_hold_cnt(
            cursor, record_id, wafer_ids, None,
        )

    feats['_feature_version'] = FEATURE_VERSION
    feats['_hold_code_primary'] = primary
    feats['_resolved_wafer_ids'] = wafer_ids
    feats['_operation_id'] = operation_id
    return feats


def snapshot_ready(features: dict, wait_expired: bool) -> bool:
    """有测试+bysite，或已超过等待窗口，才落库。"""
    if wait_expired:
        return True
    has_tw = int(features.get('missing_test_wafer') or 0) == 0
    has_bs = int(features.get('missing_bysite') or 0) == 0
    return has_tw and has_bs
