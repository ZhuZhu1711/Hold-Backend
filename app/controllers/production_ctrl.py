"""
生产角色：查看/处置当前节点在生产 OP 的在线 Hold，以及导出。
"""
from io import BytesIO

from openpyxl import Workbook

from app.config import Config
from app.controllers import hold_report_ctrl, dispose_ctrl

EXPORT_MAX_ROWS = 5000


def _production_op_id():
    return int(getattr(Config, 'PRODUCTION_OP_ID', 181) or 181)


def get_production_holding_records(
    product_id='',
    station='',
    keyword='',
    record_type=None,
    page=1,
    page_size=20,
):
    """
    当前流转节点在生产 OP、仍在线 hold 的 record 列表。
    record_type：0=FT / 1=FVI / 2=WLT。
    每条附带 CAN_DISPOSE（未关闭即可，列表已限定生产节点）。
    """
    success, msg, payload = hold_report_ctrl.get_holding_records(
        product_id=product_id,
        station=station,
        keyword=keyword,
        record_type=record_type,
        page=page,
        page_size=page_size,
        current_owner_id=_production_op_id(),
    )
    if not success:
        return success, msg, payload

    items = payload.get('items') or []
    for item in items:
        item['CAN_DISPOSE'] = not bool(item.get('IS_CLOSED'))
    payload['items'] = items
    return True, msg, payload


def get_production_dispose_record(hold_record_id):
    """
    加载生产处置页所需的 hold_record。
    须当前节点为生产 OP；附带 CAN_DISPOSE。
    """
    from app.controllers.hold_report_ctrl import RECORD_TYPE_LABELS

    try:
        rid = int(hold_record_id)
    except (TypeError, ValueError):
        return False, '参数无效', None

    record = dispose_ctrl._load_record(rid)
    if not record:
        return False, 'hold_record 不存在', None

    try:
        rt = int(record.get('RECORD_TYPE')) if record.get('RECORD_TYPE') is not None else None
    except (TypeError, ValueError):
        rt = None
    record['RECORD_TYPE_NAME'] = RECORD_TYPE_LABELS.get(rt, '-')

    last_circ = dispose_ctrl._load_circulation(record.get('LAST_CIRCULATION_ID'))
    current_owner_id = last_circ.get('NEXT_OWNER_ID') if last_circ else None
    record['CURRENT_OWNER_ID'] = current_owner_id
    if last_circ:
        record['LAST_DISPOSE'] = last_circ.get('DISPOSE')
        record['LAST_DISPOSE_DETAIL'] = last_circ.get('DISPOSE_DETAIL')
        record['LAST_DISPOSE_NOTE'] = last_circ.get('DISPOSE_NOTE')
        record['LAST_DISPOSE_LABEL'] = dispose_ctrl.DISPOSE_LABELS.get(
            last_circ.get('DISPOSE'),
            str(last_circ.get('DISPOSE') if last_circ.get('DISPOSE') is not None else '-'),
        )

    try:
        status_val = int(record.get('STATUS')) if record.get('STATUS') is not None else 0
    except (TypeError, ValueError):
        status_val = 0
    record['IS_CLOSED'] = status_val == dispose_ctrl.DISPOSE_CLOSE

    prod_op = _production_op_id()
    at_production = (
        current_owner_id is not None and int(current_owner_id) == prod_op
    )
    record['CAN_DISPOSE'] = bool(at_production and not record['IS_CLOSED'])
    record['CAN_ANALYZE_RETURN'] = bool(
        record['CAN_DISPOSE']
        and dispose_ctrl._last_dispose_was_analyze(last_circ)
    )
    if not at_production and not record['IS_CLOSED']:
        return False, '该记录当前节点不在生产', None
    return True, '获取成功', record


def export_production_holding_records_xlsx(
    product_id='',
    station='',
    keyword='',
    record_type=None,
):
    """
    导出与列表相同筛选条件的生产节点 Hold 为 xlsx。
    成功返回 (True, msg, bytes)；失败返回 (False, msg, None)。
    """
    success, msg, payload = hold_report_ctrl.get_holding_records(
        product_id=product_id,
        station=station,
        keyword=keyword,
        record_type=record_type,
        page=1,
        page_size=EXPORT_MAX_ROWS,
        current_owner_id=_production_op_id(),
    )
    if not success:
        return False, msg, None

    items = (payload or {}).get('items') or []
    total = int((payload or {}).get('total') or 0)
    truncated = total > len(items)

    wb = Workbook()
    ws = wb.active
    ws.title = '生产节点Hold'
    headers = [
        'Record ID',
        '处置单类型',
        '型号',
        '站点',
        '设备',
        'Lot',
        'Wafer',
        'Hold Code',
        'Hold 原因',
        '工程师处置',
        '处置详情',
        '工程备注',
        '处置人',
        '处置时间',
        'Hold 时间',
        '等级/数量',
    ]
    ws.append(headers)

    for item in items:
        ws.append([
            item.get('ID'),
            item.get('RECORD_TYPE_NAME') or '',
            item.get('PRODUCT_ID') or '',
            item.get('STATION') or '',
            item.get('EQUIP_ID') or '',
            item.get('LOT_ID') or '',
            item.get('WAFER_ID') or '',
            item.get('HOLD_CODE') or '',
            item.get('HOLD_REASON') or '',
            item.get('LAST_DISPOSE_LABEL') or '',
            item.get('LAST_DISPOSE_DETAIL') or '',
            item.get('LAST_DISPOSE_NOTE') or '',
            item.get('LAST_DISPOSED_OWNER_NAME') or item.get('LAST_DISPOSED_OWNER_ID') or '',
            item.get('LAST_DISPOSE_DTTM') or '',
            item.get('HOLD_DTTM') or '',
            item.get('GRADE_NUM_DISPLAY') or '',
        ])

    bio = BytesIO()
    wb.save(bio)
    bio.seek(0)
    note = msg
    if truncated:
        note = f'{msg}（共 {total} 条，已导出前 {len(items)} 条）'
    return True, note, bio.getvalue()
