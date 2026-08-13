"""评估静默预测：AUC / PR / Brier / ECE，以及分层与基线。"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Iterable, Optional


def _safe_float(value) -> Optional[float]:
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(v):
        return None
    return v


def brier_score(y_true: list[int], p: list[float]) -> Optional[float]:
    if not y_true:
        return None
    s = 0.0
    n = 0
    for yt, pr in zip(y_true, p):
        if pr is None:
            continue
        s += (pr - yt) ** 2
        n += 1
    if n <= 0:
        return None
    return s / n


def log_loss(y_true: list[int], p: list[float], eps: float = 1e-15) -> Optional[float]:
    if not y_true:
        return None
    s = 0.0
    n = 0
    for yt, pr in zip(y_true, p):
        if pr is None:
            continue
        pr = min(1 - eps, max(eps, pr))
        s += -(yt * math.log(pr) + (1 - yt) * math.log(1 - pr))
        n += 1
    if n <= 0:
        return None
    return s / n


def ece_score(y_true: list[int], p: list[float], n_bins: int = 10) -> Optional[float]:
    pairs = [(yt, pr) for yt, pr in zip(y_true, p) if pr is not None]
    if not pairs:
        return None
    bins = [[] for _ in range(n_bins)]
    for yt, pr in pairs:
        idx = min(n_bins - 1, max(0, int(pr * n_bins)))
        bins[idx].append((yt, pr))
    ece = 0.0
    n = len(pairs)
    for bucket in bins:
        if not bucket:
            continue
        acc = sum(yt for yt, _ in bucket) / len(bucket)
        conf = sum(pr for _, pr in bucket) / len(bucket)
        ece += (len(bucket) / n) * abs(acc - conf)
    return ece


def auc_roc(y_true: list[int], p: list[float]) -> Optional[float]:
    pairs = [(pr, yt) for yt, pr in zip(y_true, p) if pr is not None]
    if not pairs:
        return None
    pos = sum(1 for _, yt in pairs if yt == 1)
    neg = len(pairs) - pos
    if pos <= 0 or neg <= 0:
        return None
    pairs.sort(key=lambda x: x[0])
    rank_sum = 0.0
    for i, (_, yt) in enumerate(pairs, start=1):
        if yt == 1:
            rank_sum += i
    return (rank_sum - pos * (pos + 1) / 2.0) / (pos * neg)


def auc_pr(y_true: list[int], p: list[float]) -> Optional[float]:
    """Average precision。"""
    pairs = [(pr, yt) for yt, pr in zip(y_true, p) if pr is not None]
    if not pairs:
        return None
    pos = sum(1 for _, yt in pairs if yt == 1)
    if pos <= 0:
        return None
    pairs.sort(key=lambda x: x[0], reverse=True)
    hit = 0
    ap = 0.0
    for i, (_, yt) in enumerate(pairs, start=1):
        if yt != 1:
            continue
        hit += 1
        ap += hit / i
    return ap / pos


def sklearn_metrics(y_true: list[int], p: list[float]) -> dict:
    out = {
        'n': len(y_true),
        'positive_n': sum(1 for y in y_true if y == 1),
        'release_rate': (sum(y_true) / len(y_true)) if y_true else None,
        'brier': brier_score(y_true, p),
        'log_loss': log_loss(y_true, p),
        'ece': ece_score(y_true, p),
        'auc_roc': auc_roc(y_true, p),
        'auc_pr': auc_pr(y_true, p),
    }
    try:
        from sklearn.metrics import (
            average_precision_score,
            brier_score_loss,
            roc_auc_score,
        )
        import numpy as np

        yt = np.array(y_true)
        pr = np.array(p, dtype=float)
        mask = ~np.isnan(pr)
        if mask.sum() > 0 and len(set(yt[mask].tolist())) > 1:
            out['auc_roc'] = float(roc_auc_score(yt[mask], pr[mask]))
            out['auc_pr'] = float(average_precision_score(yt[mask], pr[mask]))
            out['brier'] = float(brier_score_loss(yt[mask], pr[mask]))
    except Exception:  # noqa: BLE001
        pass
    return out


def baseline_from_group_rate(y_true: list[int], group_keys: list) -> list[float]:
    """用组内放行率当基线概率（含当前样本，仅作对照）。"""
    buckets = defaultdict(list)
    for key, yt in zip(group_keys, y_true):
        buckets[key].append(yt)
    rates = {k: (sum(v) / len(v) if v else 0.0) for k, v in buckets.items()}
    return [rates.get(k, 0.0) for k in group_keys]


def stratified_report(rows: Iterable[dict], p_key: str = 'P_RELEASE') -> dict:
    y_true = []
    p_vals = []
    hold_codes = []
    route_flags = []
    missing_bs = []
    bysite_high = []
    for row in rows:
        yt = row.get('LABEL_Y')
        if yt is None:
            continue
        try:
            yt = int(yt)
        except (TypeError, ValueError):
            continue
        feats = row.get('FEATURES') or {}
        y_true.append(yt)
        p_vals.append(_safe_float(row.get(p_key)))
        hold_codes.append(str(feats.get('_hold_code_primary') or ''))
        route_flags.append(int(feats.get('route_is_eng') or row.get('ROUTE_IS_ENG') or 0))
        missing_bs.append(int(feats.get('missing_bysite') or row.get('MISSING_BYSITE') or 0))
        idx = _safe_float(feats.get('bysite_index') or row.get('BYSITE_INDEX'))
        bysite_high.append(1 if idx is not None and idx >= 0.5 else 0)

    report = {'overall': sklearn_metrics(y_true, p_vals)}
    if y_true:
        global_rate = [sum(y_true) / len(y_true)] * len(y_true)
        report['baseline_global'] = sklearn_metrics(y_true, global_rate)
        report['baseline_hold_code'] = sklearn_metrics(
            y_true, baseline_from_group_rate(y_true, hold_codes)
        )
        report['baseline_route_eng'] = sklearn_metrics(
            y_true, baseline_from_group_rate(y_true, route_flags)
        )

    def _subset(mask: list[bool], name: str):
        ys = [y for y, m in zip(y_true, mask) if m]
        ps = [p for p, m in zip(p_vals, mask) if m]
        report[name] = sklearn_metrics(ys, ps)

    _subset([c == '023' for c in hold_codes], 'hold_code_023')
    _subset([c == '024' for c in hold_codes], 'hold_code_024')
    _subset([c == '025' for c in hold_codes], 'hold_code_025')
    _subset([c == '027' for c in hold_codes], 'hold_code_027')
    _subset([f == 1 for f in route_flags], 'route_eng')
    _subset([f == 0 for f in route_flags], 'route_non_eng')
    _subset([m == 1 for m in missing_bs], 'missing_bysite')
    _subset([m == 0 for m in missing_bs], 'has_bysite')
    _subset([h == 1 for h in bysite_high], 'bysite_index_ge_0.5')
    _subset([h == 0 for h in bysite_high], 'bysite_index_lt_0.5')
    return report


def format_report(report: dict) -> str:
    lines = []
    for name, metrics in report.items():
        if not isinstance(metrics, dict):
            continue
        lines.append(
            f"{name:24} n={metrics.get('n')} pos={metrics.get('positive_n')} "
            f"auc={_fmt(metrics.get('auc_roc'))} ap={_fmt(metrics.get('auc_pr'))} "
            f"brier={_fmt(metrics.get('brier'))} ece={_fmt(metrics.get('ece'))}"
        )
    return '\n'.join(lines)


def _fmt(value) -> str:
    if value is None:
        return '-'
    try:
        return f'{float(value):.4f}'
    except (TypeError, ValueError):
        return str(value)


if __name__ == '__main__':
    import argparse
    import json
    import os
    import sys

    _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if _root not in sys.path:
        sys.path.insert(0, _root)

    from app.hold_predict.db import connect, query_labeled_predict_rows

    parser = argparse.ArgumentParser(description='评估 FT 可放行静默预测')
    parser.add_argument('--model-version', default=None)
    args = parser.parse_args()

    conn = connect()
    try:
        with conn.cursor() as cursor:
            rows = query_labeled_predict_rows(cursor, model_version=args.model_version)
    finally:
        conn.close()

    report = stratified_report(rows)
    print(format_report(report))
    print(json.dumps(report.get('overall') or {}, ensure_ascii=False, default=str))
