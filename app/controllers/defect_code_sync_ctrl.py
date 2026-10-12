from sqlalchemy import func

from app import db
from app.models.defect_code_sync_log import DefectCodeSyncLog

_STATUS = {'OK', 'SKIPPED', 'FAILED'}
_LINES = {'FT', 'WLT'}
_PAGE_SIZE = 200


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
