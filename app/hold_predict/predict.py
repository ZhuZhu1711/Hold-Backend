"""加载离线模型并对特征向量打分。无模型时 P_RELEASE 为空。"""
from __future__ import annotations

import logging
import math
import os
from typing import Optional

from app.hold_predict.schema import FEATURE_COLUMNS, FEATURE_VERSION

logger = logging.getLogger(__name__)

UNTRAINED_VERSION = 'untrained'


def _is_missing(value) -> bool:
    if value is None:
        return True
    try:
        return math.isnan(float(value))
    except (TypeError, ValueError):
        return False


def fill_vector(features: dict, impute_values: dict) -> list[float]:
    vec = []
    for col in FEATURE_COLUMNS:
        value = features.get(col)
        if _is_missing(value):
            value = impute_values.get(col, 0.0)
        vec.append(float(value))
    return vec


class ReleaseModel:
    def __init__(self, bundle: dict):
        self.bundle = bundle
        self.model_version = str(bundle.get('model_version') or UNTRAINED_VERSION)
        self.feature_version = str(bundle.get('feature_version') or FEATURE_VERSION)
        self.impute_values = bundle.get('impute_values') or {}
        self.estimator = bundle.get('estimator')

    def predict_proba(self, features: dict) -> Optional[float]:
        if self.estimator is None:
            return None
        import numpy as np

        vec = fill_vector(features, self.impute_values)
        proba = self.estimator.predict_proba(np.array([vec], dtype=float))[0]
        # 正类在 classes_ 中的位置
        classes = list(getattr(self.estimator, 'classes_', [0, 1]))
        if 1 in classes:
            idx = classes.index(1)
        else:
            idx = 1 if len(proba) > 1 else 0
        p = float(proba[idx])
        if p < 0:
            p = 0.0
        if p > 1:
            p = 1.0
        return round(p, 6)


def load_model(path: str) -> ReleaseModel:
    if not path or not os.path.isfile(path):
        logger.info('预测模型文件不存在，仅抽取特征: %s', path)
        return ReleaseModel({
            'model_version': UNTRAINED_VERSION,
            'feature_version': FEATURE_VERSION,
            'impute_values': {},
            'estimator': None,
        })
    import joblib

    bundle = joblib.load(path)
    if not isinstance(bundle, dict):
        raise ValueError(f'模型文件格式错误: {path}')
    logger.info(
        '已加载模型 version=%s feature_version=%s path=%s',
        bundle.get('model_version'),
        bundle.get('feature_version'),
        path,
    )
    return ReleaseModel(bundle)
