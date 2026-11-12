"""ENG 预 Hold PQC 每天 07:00 和 19:00 上报 NCMR。只由 Web 进程启动。"""
from __future__ import annotations

import logging
import os
import threading
from logging.handlers import RotatingFileHandler

import schedule

logger = logging.getLogger('eng_pqc_ncmr')
logger.setLevel(logging.INFO)
if not logger.handlers:
    os.makedirs('logs', exist_ok=True)
    _handler = RotatingFileHandler(
        'logs/eng_pqc_ncmr.log',
        maxBytes=10 * 1024 * 1024,
        backupCount=3,
        encoding='utf-8',
    )
    _handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    logger.addHandler(_handler)

_state_lock = threading.Lock()


def _state_path():
    """测试和正式各记一份，避免在同一目录里互相关掉。文件不存在视为开启。"""
    from app.config import is_test_env
    name = 'eng_pqc_auto_report.test' if is_test_env() else 'eng_pqc_auto_report.release'
    return os.path.join('logs', name)


def auto_report_enabled():
    path = _state_path()
    with _state_lock:
        try:
            with open(path, encoding='utf-8') as handle:
                text = handle.read().strip()
        except FileNotFoundError:
            return True
        except OSError as exc:
            logger.warning('读取自动上报开关失败，按开启处理: %s', exc)
            return True
    if text == '0':
        return False
    return True


def set_auto_report_enabled(enabled):
    flag = bool(enabled)
    path = _state_path()
    folder = os.path.dirname(path) or 'logs'
    os.makedirs(folder, exist_ok=True)
    temporary = path + '.tmp'
    with _state_lock:
        with open(temporary, 'w', encoding='utf-8') as handle:
            handle.write('1' if flag else '0')
        os.replace(temporary, path)
    logger.info('ENG预Hold PQC 自动上报已%s', '开启' if flag else '关闭')
    return flag


class EngPqcNcmrScheduler(threading.Thread):
    morning = '07:00'
    evening = '19:00'

    def __init__(self):
        super().__init__(daemon=True, name='eng-pqc-ncmr')
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def _install(self, sch):
        sch.every().day.at(self.morning).do(self._run_job)
        sch.every().day.at(self.evening).do(self._run_job)

    def _run_job(self):
        if not auto_report_enabled():
            logger.info('ENG预Hold PQC 自动上报已关闭，跳过本次')
            return
        try:
            from app.controllers.hold_info_export_ctrl import report_unreported_eng_pqc
            ok, msg, _data = report_unreported_eng_pqc()
            if ok:
                logger.info('ENG预Hold PQC 定时上报完成: %s', msg)
            else:
                logger.error('ENG预Hold PQC 定时上报未完成: %s', msg)
        except Exception as exc:
            logger.error('ENG预Hold PQC 定时上报失败: %s', exc, exc_info=True)

    def run(self):
        sch = schedule.Scheduler()
        self._install(sch)
        logger.info(
            'ENG预Hold PQC 上报调度已启动，每天 %s 和 %s，当前%s',
            self.morning, self.evening,
            '开启' if auto_report_enabled() else '关闭',
        )
        while not self._stop.is_set():
            sch.run_pending()
            self._stop.wait(1)
        logger.info('ENG预Hold PQC 上报调度已退出')
