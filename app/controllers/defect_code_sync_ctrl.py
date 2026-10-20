import os
import subprocess
import sys
import threading

from sqlalchemy import func

from app import db
from app.config import argv_is_debug_mode
from app.models.defect_code_sync_log import DefectCodeSyncLog

_STATUS = {'OK', 'SKIPPED', 'FAILED'}
_LINES = {'FT', 'WLT'}
_PAGE_SIZE = 200
_sync_lock = threading.Lock()
_sync_proc = None


def _project_root():
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def spawn_defect_sync_process():
    """子进程 thick 模式连 RPTDB，跑完一轮退出。不要在 Web 进程里 init_oracle_client。"""
    if getattr(sys, 'frozen', False):
        cmd = [sys.executable, '--defect-sync', '--mode', 'release']
        cwd = os.getcwd()
    else:
        root = _project_root()
        script = os.path.join(root, 'app', 'backend_schedule', 'FT_DEFECT_CODE_SYNC_sche.py')
        cmd = [sys.executable, script, '--mode', 'release']
        cwd = root
    return subprocess.Popen(cmd, cwd=cwd)


def trigger_defect_sync(debug_mode=None):
    """
    root 手动触发一轮。
    debug 不写正式表 DEFECT_CODE。
    返回 (success, msg, data)。
    """
    if debug_mode is None:
        debug_mode = argv_is_debug_mode()
    if debug_mode:
        return False, '当前为 debug，不写正式表 DEFECT_CODE', None

    global _sync_proc
    with _sync_lock:
        if _sync_proc is not None and _sync_proc.poll() is None:
            return False, '同步进行中', None
        try:
            _sync_proc = spawn_defect_sync_process()
        except Exception as exc:
            _sync_proc = None
            return False, f'启动同步失败: {exc}', None
        pid = _sync_proc.pid
    return True, '已开始同步', {'pid': pid}


def query_sync_logs(product_code='', prod_line='', status=''):
    """最近一批同步记录。返回 (success, msg, data)。"""
    product_code = (product_code or '').strip()
    prod_line = (prod_line or '').strip().upper()
    status = (status or '').strip().upper()
    if prod_line and prod_line not in _LINES:
        return False, '产线只能是 FT 或 WLT', None
    if status and status not in _STATUS:
        return False, '状态无效', None

    try:
        query = DefectCodeSyncLog.query
        if product_code:
            escaped = (
                product_code.replace('\\', '\\\\')
                .replace('%', '\\%')
                .replace('_', '\\_')
            )
            query = query.filter(
                func.upper(DefectCodeSyncLog.PRODUCT_CODE).like(
                    f'%{escaped.upper()}%',
                    escape='\\',
                )
            )
        if prod_line:
            query = query.filter(DefectCodeSyncLog.PROD_LINE == prod_line)
        if status:
            query = query.filter(DefectCodeSyncLog.STATUS == status)
        rows = (
            query.order_by(
                DefectCodeSyncLog.RUN_AT.desc(),
                DefectCodeSyncLog.ID.desc(),
            )
            .limit(_PAGE_SIZE)
            .all()
        )
        return True, '获取成功', {
            'rows': [row.to_dict() for row in rows],
            'total': len(rows),
        }
    except Exception as exc:
        db.session.rollback()
        return False, f'查询失败: {exc}', None
