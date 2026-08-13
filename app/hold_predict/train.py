"""离线训练 FT 可放行概率模型。

  python app/hold_predict/train.py --limit 800 --skip-bysite
  python app/hold_predict/train.py --source table --model-version untrained
  python app/hold_predict/train.py --algo lgb --skip-bysite

前身 HOLD_INFO 训练（--source legacy/mixed）代码保留但默认关闭，
需同时传 --enable-legacy-source 才会执行。
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime

current_file_path = os.path.abspath(__file__)
project_root_path = os.path.dirname(os.path.dirname(os.path.dirname(current_file_path)))
if project_root_path not in sys.path:
    sys.path.insert(0, project_root_path)

from app.hold_predict.db import connect, query_labeled_ft_records, query_labeled_predict_rows, query_legacy_labeled_holds
from app.hold_predict.eval import format_report, sklearn_metrics, stratified_report
from app.hold_predict.features import extract_features
from app.hold_predict.legacy import (
    HOLD_CODE_WARN_RATE,
    LEGACY_TRAIN_ENABLED,
    summarize_legacy_records,
    to_pseudo_record,
)
from app.hold_predict.predict import fill_vector
from app.hold_predict.schema import FEATURE_COLUMNS, FEATURE_VERSION

logger = logging.getLogger('hold_predict.train')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
)


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _impute_from_rows(feature_rows: list[dict]) -> dict:
    impute = {}
    for col in FEATURE_COLUMNS:
        nums = []
        for feats in feature_rows:
            v = feats.get(col)
            if v is None:
                continue
            try:
                nums.append(float(v))
            except (TypeError, ValueError):
                continue
        impute[col] = _median(nums)
    return impute


def _time_split(samples: list[dict], train_ratio=0.7, valid_ratio=0.15):
    n = len(samples)
    if n < 10:
        return samples, [], []
    n_train = max(1, int(n * train_ratio))
    n_valid = max(1, int(n * valid_ratio))
    if n_train + n_valid >= n:
        n_valid = max(0, n - n_train - 1)
    train = samples[:n_train]
    valid = samples[n_train:n_train + n_valid]
    test = samples[n_train + n_valid:]
    return train, valid, test


def _xy(samples: list[dict], impute: dict):
    import numpy as np

    x = [fill_vector(s['features'], impute) for s in samples]
    y = [int(s['y']) for s in samples]
    return np.array(x, dtype=float), np.array(y, dtype=int)


def _fit_logistic(x_train, y_train, x_valid, y_valid):
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    base = Pipeline([
        ('scaler', StandardScaler()),
        ('clf', LogisticRegression(
            max_iter=1000,
            class_weight='balanced',
            solver='lbfgs',
        )),
    ])
    base.fit(x_train, y_train)
    if len(set(y_valid.tolist())) > 1 and len(y_valid) >= 20:
        try:
            model = CalibratedClassifierCV(base, method='sigmoid', cv='prefit')
            model.fit(x_valid, y_valid)
            return model, 'lr-calibrated-v1'
        except Exception:  # noqa: BLE001
            return base, 'lr-v1'
    return base, 'lr-v1'


def _fit_lgb(x_train, y_train, x_valid, y_valid):
    from lightgbm import LGBMClassifier
    from sklearn.calibration import CalibratedClassifierCV

    clf = LGBMClassifier(
        n_estimators=200,
        learning_rate=0.05,
        num_leaves=31,
        subsample=0.8,
        colsample_bytree=0.8,
        class_weight='balanced',
        random_state=42,
    )
    if len(y_valid) >= 10:
        clf.fit(
            x_train, y_train,
            eval_set=[(x_valid, y_valid)],
        )
    else:
        clf.fit(x_train, y_train)
    if len(set(y_valid.tolist())) > 1 and len(y_valid) >= 20:
        model = CalibratedClassifierCV(clf, method='isotonic', cv='prefit')
        model.fit(x_valid, y_valid)
        return model, 'lgb-calibrated-v1'
    return clf, 'lgb-v1'


def _predict_p(estimator, x):
    import numpy as np

    proba = estimator.predict_proba(x)
    classes = list(getattr(estimator, 'classes_', [0, 1]))
    idx = classes.index(1) if 1 in classes else min(1, proba.shape[1] - 1)
    return [float(v) for v in proba[:, idx]]


def _extract_record_samples(cursor, records, skip_bysite, prior_source) -> list[dict]:
    samples = []
    for i, rec in enumerate(records, start=1):
        try:
            feats = extract_features(
                cursor, rec, skip_bysite=skip_bysite, prior_source=prior_source,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning('特征失败 id=%s: %s', rec.get('ID'), exc)
            continue
        if prior_source == 'legacy':
            y = int(rec.get('LABEL_Y') or 0)
            dispose = rec.get('LABEL_DISPOSE')
            try:
                dispose = int(dispose) if dispose is not None else 0
            except (TypeError, ValueError):
                dispose = 0
        else:
            dispose = rec.get('LABEL_DISPOSE')
            y = 1 if int(dispose) == 1 else 0
            dispose = int(dispose)
        samples.append({
            'id': rec.get('ID'),
            'hold_dttm': rec.get('HOLD_DTTM'),
            'y': y,
            'dispose': dispose,
            'features': feats,
            'prior_source': prior_source,
        })
        if i % 20 == 0:
            logger.info('已抽取 %s / %s (%s)', i, len(records), prior_source)
    return samples


def load_samples_from_db(limit, skip_bysite) -> list[dict]:
    conn = connect()
    try:
        with conn.cursor() as cursor:
            records = query_labeled_ft_records(cursor, limit=limit)
            logger.info('载入已标注 FT 单 %s 条', len(records))
            return _extract_record_samples(cursor, records, skip_bysite, 'record')
    finally:
        conn.close()


def load_samples_from_legacy(limit, skip_bysite, max_lag_days=None) -> list[dict]:
    conn = connect()
    try:
        with conn.cursor() as cursor:
            rows = query_legacy_labeled_holds(cursor, limit=limit, max_lag_days=max_lag_days)
            records = [to_pseudo_record(row) for row in rows]
            stats = summarize_legacy_records(records)
            logger.info(
                '载入前身匹配样本 %s 条 放行 %s hold码覆盖 %.1f%% (%s/%s)',
                stats['n'],
                stats['release_n'],
                stats['hold_code_rate'] * 100,
                stats['hold_code_n'],
                stats['n'],
            )
            if stats['n'] and stats['hold_code_rate'] < HOLD_CODE_WARN_RATE:
                logger.warning(
                    'Hold 码覆盖率 %.1f%% < %.0f%%，模型将弱化 hold 码特征',
                    stats['hold_code_rate'] * 100,
                    HOLD_CODE_WARN_RATE * 100,
                )
            tw_hit = 0
            samples = _extract_record_samples(cursor, records, skip_bysite, 'legacy')
            for sample in samples:
                if int(sample['features'].get('missing_test_wafer') or 1) == 0:
                    tw_hit += 1
            if samples:
                logger.info(
                    '前身特征抽取完成 %s 条 TEST_WAFER 命中 %.1f%%',
                    len(samples),
                    100.0 * tw_hit / len(samples),
                )
            return samples
    finally:
        conn.close()


def load_samples_from_mixed(limit, skip_bysite, max_lag_days=None) -> list[dict]:
    legacy = load_samples_from_legacy(limit, skip_bysite, max_lag_days=max_lag_days)
    current = load_samples_from_db(limit, skip_bysite)
    logger.info('混合样本 前身 %s + 现网 %s', len(legacy), len(current))
    return legacy + current


def load_samples_from_table(model_version) -> list[dict]:
    conn = connect()
    try:
        with conn.cursor() as cursor:
            rows = query_labeled_predict_rows(cursor, model_version=model_version)
        samples = []
        for row in rows:
            feats = row.get('FEATURES') or {}
            samples.append({
                'id': row.get('HOLD_RECORD_ID'),
                'hold_dttm': row.get('PREDICTED_AT'),
                'y': int(row.get('LABEL_Y')),
                'dispose': int(row.get('LABEL_DISPOSE') or 0),
                'features': feats,
                'p_release': row.get('P_RELEASE'),
            })
        logger.info('从 FT_HOLD_PREDICT 载入 %s 条已标注快照', len(samples))
        return samples
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description='训练 FT 可放行概率模型')
    parser.add_argument('--source', choices=['db', 'table', 'legacy', 'mixed'], default='db')
    parser.add_argument('--limit', type=int, default=None)
    parser.add_argument('--skip-bysite', action='store_true')
    parser.add_argument('--algo', choices=['lr', 'lgb'], default='lr')
    parser.add_argument('--model-version', default=None, help='--source table 时筛选 MODEL_VERSION')
    parser.add_argument(
        '--max-dispose-lag-days',
        type=int,
        default=None,
        help='legacy/mixed：处置须在 hold 后 N 天内，默认不限制',
    )
    parser.add_argument(
        '--enable-legacy-source',
        action='store_true',
        help='解锁 --source legacy/mixed（默认关闭，前身表训练不启用）',
    )
    parser.add_argument(
        '--out',
        default=os.path.join(project_root_path, 'app', 'hold_predict', 'artifacts', 'model_v1.joblib'),
    )
    args = parser.parse_args()

    if args.source in ('legacy', 'mixed') and not (
        LEGACY_TRAIN_ENABLED or args.enable_legacy_source
    ):
        logger.error(
            '前身表训练已关闭（LEGACY_TRAIN_ENABLED=False）。'
            '代码保留；若要跑需同时传 --enable-legacy-source'
        )
        sys.exit(3)

    try:
        import sklearn  # noqa: F401
        import joblib  # noqa: F401
    except ImportError:
        logger.error('请先安装: pip install -r app/hold_predict/requirements-ml.txt')
        sys.exit(1)

    if args.source == 'table':
        samples = load_samples_from_table(args.model_version)
    elif args.source == 'legacy':
        samples = load_samples_from_legacy(
            args.limit, args.skip_bysite, max_lag_days=args.max_dispose_lag_days,
        )
    elif args.source == 'mixed':
        samples = load_samples_from_mixed(
            args.limit, args.skip_bysite, max_lag_days=args.max_dispose_lag_days,
        )
    else:
        samples = load_samples_from_db(args.limit, args.skip_bysite)

    samples = [s for s in samples if s.get('features')]
    samples.sort(key=lambda s: (s.get('hold_dttm') or datetime.min, s.get('id') or 0))
    pos = sum(1 for s in samples if s['y'] == 1)
    logger.info('可用样本 %s 正例 %s 负例 %s', len(samples), pos, len(samples) - pos)
    if len(samples) < 30 or pos < 5 or (len(samples) - pos) < 5:
        logger.error('样本量不足，停止训练（建议正负例各 200+）')
        sys.exit(2)

    train, valid, test = _time_split(samples)
    logger.info('时间切分 train=%s valid=%s test=%s', len(train), len(valid), len(test))
    impute = _impute_from_rows([s['features'] for s in train])
    x_train, y_train = _xy(train, impute)
    x_valid, y_valid = _xy(valid, impute) if valid else (x_train[:1], y_train[:1])
    x_test, y_test = _xy(test, impute) if test else (x_valid, y_valid)

    if args.algo == 'lgb':
        try:
            estimator, version = _fit_lgb(x_train, y_train, x_valid, y_valid)
        except ImportError:
            logger.warning('lightgbm 未安装，回退逻辑回归')
            estimator, version = _fit_logistic(x_train, y_train, x_valid, y_valid)
    else:
        estimator, version = _fit_logistic(x_train, y_train, x_valid, y_valid)

    for name, xs, ys in (('train', x_train, y_train), ('valid', x_valid, y_valid), ('test', x_test, y_test)):
        p = _predict_p(estimator, xs)
        metrics = sklearn_metrics(ys.tolist(), p)
        logger.info('%s %s', name, json.dumps(metrics, ensure_ascii=False, default=str))

    eval_rows = []
    for sample, p in zip(test or valid, _predict_p(estimator, x_test if len(test) else x_valid)):
        eval_rows.append({
            'LABEL_Y': sample['y'],
            'P_RELEASE': p,
            'FEATURES': sample['features'],
            'ROUTE_IS_ENG': sample['features'].get('route_is_eng'),
            'MISSING_BYSITE': sample['features'].get('missing_bysite'),
            'BYSITE_INDEX': sample['features'].get('bysite_index'),
        })
    report = stratified_report(eval_rows)
    print(format_report(report))

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    import joblib

    bundle = {
        'model_version': version,
        'feature_version': FEATURE_VERSION,
        'feature_columns': FEATURE_COLUMNS,
        'impute_values': impute,
        'estimator': estimator,
        'algo': args.algo,
        'trained_at': datetime.now().isoformat(timespec='seconds'),
        'n_train': len(train),
        'n_valid': len(valid),
        'n_test': len(test),
    }
    joblib.dump(bundle, args.out)
    logger.info('已保存 %s version=%s', args.out, version)


if __name__ == '__main__':
    main()
