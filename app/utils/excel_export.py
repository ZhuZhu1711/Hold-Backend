"""
报表 xlsx 导出公共工具。

列表类报表按当前筛选条件导出，最多 EXPORT_MAX_ROWS 行。
"""
from datetime import datetime
from io import BytesIO

from flask import Response, jsonify
from openpyxl import Workbook

EXPORT_MAX_ROWS = 5000
XLSX_MIMETYPE = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


def cell(value):
    if value is None:
        return ''
    if isinstance(value, bool):
        return '是' if value else '否'
    return value


def build_xlsx(sheet_title, headers, rows):
    wb = Workbook()
    ws = wb.active
    ws.title = (sheet_title or 'Sheet')[:31]
    ws.append(list(headers))
    for row in rows:
        ws.append([cell(v) for v in row])
    bio = BytesIO()
    wb.save(bio)
    bio.seek(0)
    return bio.getvalue()


def stamp_filename(prefix):
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    return f'{prefix}_{stamp}.xlsx'


def xlsx_attachment(content, filename):
    return Response(
        content,
        mimetype=XLSX_MIMETYPE,
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )


def xlsx_or_error(success, msg, content, filename, bad_keys=()):
    if success:
        return xlsx_attachment(content, filename)
    status = 400 if any(k in (msg or '') for k in bad_keys) else 500
    return jsonify({'code': status, 'msg': msg, 'data': None}), status


def from_page_payload(success, msg, payload, headers, row_mapper, sheet_title):
    """
    将分页查询结果写成 xlsx。
    成功返回 (True, note, bytes)；失败返回 (False, msg, None)。
    """
    if not success:
        return False, msg, None
    items = (payload or {}).get('items') or []
    total = int((payload or {}).get('total') or 0)
    content = build_xlsx(sheet_title, headers, [row_mapper(it) for it in items])
    note = msg or '导出成功'
    if total > len(items):
        note = f'{note}（共 {total} 条，已导出前 {len(items)} 条）'
    return True, note, content
