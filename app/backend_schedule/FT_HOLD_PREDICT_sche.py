"""
独立进程：FT 可放行概率静默打分 + 标签回填。

不改处置接口 / UI。启动方式：
  python app/backend_schedule/FT_HOLD_PREDICT_sche.py --mode debug    # 跑一轮后退出
  python app/backend_schedule/FT_HOLD_PREDICT_sche.py --mode release  # 常驻
也可由 main.py release 模式挂后台线程。
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from typing import Optional

import schedule

current_file_path = os.path.abspath(__file__)
project_root_path = os.path.dirname(os.path.dirname(os.path.dirname(current_file_path)))
if project_root_path not in sys.path:
    sys.path.insert(0, project_root_path)

from app.config import Config
from app.utils.mail_alert import install_severe_error_hooks, notify_severe_error
from app.hold_predict.db import (
    backfill_labels,
    connect,
    insert_predict_row,
    query_pending_ft_records,
)
from app.hold_predict.features import extract_features, snapshot_ready
from app.hold_predict.predict import UNTRAINED_VERSION, load_model
from app.hold_predict.schema import FEATURE_VERSION

logger = logging.getLogger('hold_predict.sche')
logger.setLevel(logging.INFO)
if not os.path.exists('logs'):
    os.makedirs('./logs')
_fmt = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
_fh = RotatingFileHandler(
    'logs/hold_predict.log',
    maxBytes=50 * 1024 * 1024,
    backupCount=3,
    encoding='utf-8',
)
_fh.setFormatter(_fmt)
logger.addHandler(_fh)
_ch = logging.StreamHandler()
_ch.setFormatter(_fmt)
logger.addHandler(_ch)


def _wait_expired(hold_dttm, wait_hours: float) -> bool:
    if hold_dttm is None:
        return True
    if not isinstance(hold_dttm, datetime):
        return True
    return datetime.now() - hold_dttm >= timedelta(hours=float(wait_hours))


def run_once(config: Optional[Config] = None, force: bool = False) -> dict:
    cfg = config or Config()
    model_path = getattr(cfg, 'HOLD_PREDICT_MODEL_PATH', '')
    wait_hours = float(getattr(cfg, 'HOLD_PREDICT_WAIT_HOURS', 24))
    batch_size = int(getattr(cfg, 'HOLD_PREDICT_BATCH_SIZE', 40))
    label_batch = int(getattr(cfg, 'HOLD_PREDICT_LABEL_BATCH_SIZE', 200))

    model = load_model(model_path)
    model_version = model.model_version or UNTRAINED_VERSION
    stats = {
        'scored': 0,
        'skipped_wait': 0,
        'failed': 0,
        'labeled': 0,
        'model_version': model_version,
    }

    conn = connect()
    try:
        with conn.cursor() as cursor:
            stats['labeled'] = backfill_labels(cursor, conn, batch_size=label_batch)
            pending = query_pending_ft_records(cursor, model_version, batch_size)
            logger.info(
                '待打分 FT 单 %s 条 model=%s feature=%s',
                len(pending), model_version, FEATURE_VERSION,
            )
            for rec in pending:
                rid = rec.get('ID')
                try:
                    feats = extract_features(cursor, rec, skip_bysite=False)
                    expired = force or _wait_expired(rec.get('HOLD_DTTM'), wait_hours)
                    if not snapshot_ready(feats, expired):
                        stats['skipped_wait'] += 1
                        logger.info(
                            '等待测试/bysite record=%s missing_tw=%s missing_bysite=%s',
                            rid,
                            feats.get('missing_test_wafer'),
                            feats.get('missing_bysite'),
                        )
                        continue
                    p_release = model.predict_proba(feats)
                    insert_predict_row(cursor, conn, {
                        'HOLD_RECORD_ID': int(rid),
                        'MODEL_VERSION': model_version,
                        'FEATURE_VERSION': FEATURE_VERSION,
                        'P_RELEASE': p_release,
                        'BYSITE_INDEX': feats.get('bysite_index'),
                        'ROUTE_IS_ENG': feats.get('route_is_eng'),
                        'MISSING_BYSITE': feats.get('missing_bysite'),
                        'MISSING_TEST_WAFER': feats.get('missing_test_wafer'),
                        'FEATURES_JSON': feats,
                        'LABEL_DISPOSE': None,
                        'LABEL_Y': None,
                        'LABELED_AT': None,
                    })
                    stats['scored'] += 1
                    logger.info(
                        '已打分 record=%s p=%s bysite_index=%s route_is_eng=%s',
                        rid, p_release, feats.get('bysite_index'), feats.get('route_is_eng'),
                    )
                except Exception as exc:  # noqa: BLE001
                    stats['failed'] += 1
                    logger.exception('打分失败 record=%s: %s', rid, exc)
            if stats['labeled']:
                logger.info('回填标签 %s 条', stats['labeled'])
    finally:
        conn.close()
    return stats


class HoldPredictScheduler(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self._stop_event = threading.Event()
        self.config = Config()
        self.interval_minutes = int(getattr(self.config, 'HOLD_PREDICT_INTERVAL_MINUTES', 15))

    def stop(self):
        logger.info('正在停止 Hold 预测调度器...')
        self._stop_event.set()

    def _run_job(self):
        if not getattr(self.config, 'HOLD_PREDICT_ENABLED', False):
            logger.info('HOLD_PREDICT_ENABLED=False，跳过')
            return
        try:
            logger.info('>>> Hold 可放行预测任务开始')
            stats = run_once(self.config)
            logger.info('>>> Hold 可放行预测任务结束 %s', stats)
        except Exception as exc:
            logger.error('Hold 可放行预测任务执行出错: %s', exc, exc_info=True)
            notify_severe_error('Hold 可放行预测任务整轮失败', str(exc), exc=exc)

    def run(self):
        schedule.every(self.interval_minutes).minutes.do(self._run_job)
        logger.info('Hold 预测调度器已启动，间隔 %s 分钟', self.interval_minutes)
        self._run_job()
        while not self._stop_event.is_set():
            schedule.run_pending()
            time.sleep(1)


def main():
    parser = argparse.ArgumentParser(description='FT 可放行概率静默打分')
    parser.add_argument('--mode', choices=['debug', 'release'], default='debug')
    parser.add_argument('--force', action='store_true', help='不等待 TEST_WAFER/bysite')
    args = parser.parse_args()
    install_severe_error_hooks()
    cfg = Config()
    if not getattr(cfg, 'HOLD_PREDICT_ENABLED', False):
        logger.info('HOLD_PREDICT_ENABLED=False，退出（改 config.py 后重启即可启用）')
        return
    if args.mode == 'debug':
        stats = run_once(cfg, force=args.force)
        logger.info('debug 单轮结束 %s', stats)
        return
    scheduler = HoldPredictScheduler()
    scheduler.start()
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        scheduler.stop()


if __name__ == '__main__':
    main()
