"""
生产角色：查看/处置当前节点在生产 OP 的在线 Hold，以及待留样记录。
"""
from app.config import Config
from app.controllers import hold_report_ctrl, dispose_ctrl
from app.utils.excel_export import EXPORT_MAX_ROWS, from_page_payload


def _production_op_id():
    return int(getattr(Config, 'PRODUCTION_OP_ID', 181) or 181)


def get_production_holding_records(
    product_id='',
    station='',
    keyword='',
    record_type=None,
    page=1,
    page_size=20,
    max_page_size=200,
):
    """
    生产待办：当前节点在生产 OP，或待留样（可靠性分析后尚未留样完成）。
    record_type：0=FT / 1=FVI / 2=WLT。
    每条附带 CAN_DISPOSE（节点在生产且未关闭）、CAN_SAMPLE_DONE / PENDING_SAMPLE_RETAIN。
    """
    prod_op = _production_op_id()
    success, msg, payload = hold_report_ctrl.get_holding_records(
        product_id=product_id,
        station=station,
        keyword=keyword,
        record_type=record_type,
        page=page,
        page_size=page_size,
        current_owner_id=prod_op,
        include_pending_sample=True,
        max_page_size=max_page_size,
    )
    if not success:
        return success, msg, payload

    items = payload.get('items') or []
    dispose_ctrl.attach_reliability_followup_many(items)
    for item in items:
        try:
            owner = int(item['CURRENT_OWNER_ID']) if item.get('CURRENT_OWNER_ID') is not None else None
        except (TypeError, ValueError):
            owner = None
        at_production = owner == prod_op
        closed = bool(item.get('IS_CLOSED'))
        item['CAN_DISPOSE'] = bool(at_production and not closed)
        item['CAN_SAMPLE_DONE'] = bool(item.get('PENDING_SAMPLE_RETAIN') and not closed)
    payload['items'] = items
    return True, msg, payload


def get_production_dispose_record(hold_record_id):
    """
    加载生产处置页所需的 hold_record。
    允许：当前节点为生产 OP，或待留样。
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
        record['LAST_DISPOSE_MANUAL_NOTE'] = last_circ.get('DISPOSE_MANUAL_NOTE')
        record['LAST_DISPOSE_LABEL'] = dispose_ctrl.DISPOSE_LABELS.get(
            last_circ.get('DISPOSE'),
            str(last_circ.get('DISPOSE') if last_circ.get('DISPOSE') is not None else '-'),
        )

    try:
        status_val = int(record.get('STATUS')) if record.get('STATUS') is not None else 0
    except (TypeError, ValueError):
        status_val = 0
    record['IS_CLOSED'] = status_val == dispose_ctrl.DISPOSE_CLOSE

    dispose_ctrl.attach_reliability_followup(record)

    prod_op = _production_op_id()
    at_production = (
        current_owner_id is not None and int(current_owner_id) == prod_op
    )
    record['CAN_DISPOSE'] = bool(at_production and not record['IS_CLOSED'])
    record['CAN_SAMPLE_DONE'] = bool(
        record.get('PENDING_SAMPLE_RETAIN') and not record['IS_CLOSED']
    )
    record['CAN_ANALYZE_RETURN'] = False
    if not at_production and not record['CAN_SAMPLE_DONE'] and not record['IS_CLOSED']:
        return False, '该记录当前不在生产待办且无需留样', None
    return True, '获取成功', record


PRODUCTION_HOLDING_EXPORT_HEADERS = [
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
    '手输备注',
    '处置人',
    '处置时间',
    'Hold 时间',
    '等级/数量',
    '待留样',
]


def production_holding_export_row(item):
    return [
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
        item.get('LAST_DISPOSE_MANUAL_NOTE') or '',
        item.get('LAST_DISPOSED_OWNER_NAME') or item.get('LAST_DISPOSED_OWNER_ID') or '',
        item.get('LAST_DISPOSE_DTTM') or '',
        item.get('HOLD_DTTM') or '',
        item.get('GRADE_NUM_DISPLAY') or '',
        '是' if item.get('PENDING_SAMPLE_RETAIN') else '否',
    ]


def export_production_holding_records_xlsx(
    product_id='',
    station='',
    keyword='',
    record_type=None,
):
    """
    导出与列表相同筛选条件的生产待办 Hold 为 xlsx。
    成功返回 (True, msg, bytes)；失败返回 (False, msg, None)。
    """
    success, msg, payload = get_production_holding_records(
        product_id=product_id,
        station=station,
        keyword=keyword,
        record_type=record_type,
        page=1,
        page_size=EXPORT_MAX_ROWS,
        max_page_size=EXPORT_MAX_ROWS,
    )
    return from_page_payload(
        success, msg, payload,
        PRODUCTION_HOLDING_EXPORT_HEADERS, production_holding_export_row, '生产节点Hold',
    )
