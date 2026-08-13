"""可放行预测特征契约（FEATURE_VERSION=v1）。"""
from __future__ import annotations

FEATURE_VERSION = 'v1'

# 进模型的列顺序。缺测数值为 None，对应 missing_* 为 1，禁止用 0 填「没测到」。
FEATURE_COLUMNS = [
    'hold_code_023',
    'hold_code_024',
    'hold_code_025',
    'hold_code_027',
    'hold_code_n',
    'source',
    'station_is_vbox',
    'route_is_eng',
    'route_missing',
    'route_mismatch',
    'hold_hour',
    'hold_weekday',
    'qty_F',
    'qty_HA',
    'qty_TA',
    'qty_other',
    'total_qty',
    'ratio_F',
    'ratio_passA',
    'grade_n',
    'missing_grade_num',
    'tw_yield',
    'tw_ng_ratio',
    'tw_gross',
    'missing_test_wafer',
    'bin_top1_ratio',
    'bin_top2_ratio',
    'bin_top3_ratio',
    'bin_top4_ratio',
    'bin_top5_ratio',
    'bin_over_bsl_cnt',
    'lot_yield_mean',
    'lot_yield_std',
    'wafer_yield_rank',
    'bysite_index',
    'fail_cv',
    'fail_rate_cv',
    'fail_max_share',
    'fail_max_z',
    'bin_cv_max',
    'bin_cv_over_thr_cnt',
    'site_n',
    'missing_bysite',
    'bysite_degenerate',
    'product_release_rate_30d',
    'holdcode_release_rate_30d',
    'route_eng_release_rate_30d',
    'wafer_prior_hold_cnt',
    'product_hold_cnt_7d',
]

FLAG_COLUMNS = {
    'hold_code_023',
    'hold_code_024',
    'hold_code_025',
    'hold_code_027',
    'station_is_vbox',
    'route_is_eng',
    'route_missing',
    'route_mismatch',
    'missing_grade_num',
    'missing_test_wafer',
    'missing_bysite',
    'bysite_degenerate',
}


def empty_features() -> dict:
    feats = {k: None for k in FEATURE_COLUMNS}
    for flag in FLAG_COLUMNS:
        feats[flag] = 0
    feats['missing_grade_num'] = 1
    feats['missing_test_wafer'] = 1
    feats['missing_bysite'] = 1
    return feats


def vector_from_features(features: dict) -> list:
    return [features.get(k) for k in FEATURE_COLUMNS]
