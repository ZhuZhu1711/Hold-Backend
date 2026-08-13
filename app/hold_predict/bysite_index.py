"""Bysite 指数：刻画 ATE 机台少数 site 系统性误测的程度。

输入 site → {bin_code: count}。指数越高，失败越集中在少数 site，
越像机台误测（通常越倾向放行）。

CV 口径与客户端 hold_client/ui/bysite_widget.py 的
_anomaly_coefficient / STD_CV=0.6 对齐：stdev / mean。
"""
from __future__ import annotations

import statistics
from typing import Any, Iterable, Mapping, Optional


CV_THR = 0.6
DEFAULT_PASS_BINS = (1,)

_EMPTY = {
    'site_n': 0,
    'fail_cv': None,
    'fail_rate_cv': None,
    'fail_max_share': None,
    'fail_max_z': None,
    'bin_cv_max': None,
    'bin_cv_over_thr_cnt': None,
    'suspect_site': None,
    'bysite_index': None,
    'missing_bysite': 1,
    'bysite_degenerate': 0,
}


def _cv(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = statistics.mean(values)
    if mean == 0:
        return 0.0
    return statistics.stdev(values) / mean


def _clip01(value: float) -> float:
    if value < 0:
        return 0.0
    if value > 1:
        return 1.0
    return float(value)


def merge_site_bin_matrices(payload: Any) -> dict[str, dict[str, int]]:
    """把 testlog 解析结果（单对象或列表）合成 site→bin→count。"""
    matrix: dict[str, dict[str, int]] = {}
    if payload is None:
        return matrix
    items = payload if isinstance(payload, list) else [payload]
    for item in items:
        if not isinstance(item, dict):
            continue
        inner = item.get('bysite') if isinstance(item.get('bysite'), dict) else item
        if not isinstance(inner, dict):
            continue
        for site_raw, bins in inner.items():
            if not isinstance(bins, dict):
                continue
            slot = matrix.setdefault(str(site_raw), {})
            for code_raw, qty in bins.items():
                try:
                    code = str(int(code_raw))
                    count = int(qty or 0)
                except (TypeError, ValueError):
                    continue
                slot[code] = slot.get(code, 0) + count
    return matrix


def compute_bysite_index(
    site_bin_matrix: Optional[Mapping[Any, Mapping[Any, Any]]],
    pass_bins: Iterable[int] = DEFAULT_PASS_BINS,
    cv_thr: float = CV_THR,
) -> dict:
    """
    site_bin_matrix: {site: {bin_code: count}}
    返回指数越高 → 越像「少数 site 误测」→ 通常越倾向放行。
    """
    if not site_bin_matrix:
        return dict(_EMPTY)

    pass_set = {int(x) for x in pass_bins}
    sites: dict[int, dict] = {}
    for site_raw, bins in site_bin_matrix.items():
        try:
            site = int(site_raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(bins, dict):
            continue
        n_s = 0
        fail_s = 0
        bin_counts: dict[int, int] = {}
        for bin_raw, qty in bins.items():
            try:
                code = int(bin_raw)
                count = int(qty or 0)
            except (TypeError, ValueError):
                continue
            if count < 0:
                continue
            n_s += count
            bin_counts[code] = bin_counts.get(code, 0) + count
            if code not in pass_set:
                fail_s += count
        if n_s > 0:
            sites[site] = {'n': n_s, 'fail': fail_s, 'bins': bin_counts}

    if len(sites) < 2:
        out = dict(_EMPTY)
        out['site_n'] = len(sites)
        out['missing_bysite'] = 0
        out['bysite_degenerate'] = 1
        return out

    fail_counts = [float(v['fail']) for v in sites.values()]
    fail_rates = [v['fail'] / v['n'] for v in sites.values()]
    total_fail = sum(fail_counts)
    if total_fail <= 0:
        out = dict(_EMPTY)
        out['site_n'] = len(sites)
        out['missing_bysite'] = 0
        out['bysite_degenerate'] = 1
        return out

    fail_cv = _cv(fail_counts)
    fail_rate_cv = _cv(fail_rates)
    fail_max_share = max(fail_counts) / total_fail
    mean_rate = statistics.mean(fail_rates)
    std_rate = statistics.stdev(fail_rates) if len(fail_rates) > 1 else 0.0
    max_rate = max(fail_rates)
    fail_max_z = (max_rate - mean_rate) / std_rate if std_rate > 0 else 0.0

    all_fail_bins = set()
    for v in sites.values():
        for code in v['bins']:
            if code not in pass_set:
                all_fail_bins.add(code)

    site_ids = sorted(sites)
    bin_cvs = []
    over_thr = 0
    for code in all_fail_bins:
        vals = [float(sites[s]['bins'].get(code, 0)) for s in site_ids]
        c = _cv(vals)
        bin_cvs.append(c)
        if c > cv_thr:
            over_thr += 1
    bin_cv_max = max(bin_cvs) if bin_cvs else 0.0

    suspect = max(sites.items(), key=lambda kv: kv[1]['fail'] / kv[1]['n'])[0]

    bysite_index = _clip01(
        0.4 * fail_max_share
        + 0.3 * min(fail_rate_cv / 1.2, 1.0)
        + 0.3 * min(bin_cv_max / 1.2, 1.0)
    )

    return {
        'site_n': len(sites),
        'fail_cv': round(fail_cv, 6),
        'fail_rate_cv': round(fail_rate_cv, 6),
        'fail_max_share': round(fail_max_share, 6),
        'fail_max_z': round(fail_max_z, 6),
        'bin_cv_max': round(bin_cv_max, 6),
        'bin_cv_over_thr_cnt': over_thr,
        'suspect_site': suspect,
        'bysite_index': round(bysite_index, 6),
        'missing_bysite': 0,
        'bysite_degenerate': 0,
    }
