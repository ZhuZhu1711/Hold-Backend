"""
Root：merge 失败（HOLD_RECORD_ID=-1）的 hold_info 查询 / 重置 / 手动提 record。
"""
import logging
from datetime import datetime

from app.config import Config
from app.backend_schedule.FT_HOLD_MERGE_sche import (
    HoldInfo,
    RECORD_TYPE_FVI,
    RECORD_TYPE_FT,
    RECORD_TYPE_WLT,
    RoughHoldRecord,
    resolve_record_type,
)
from app.utils.database_util import (
    build_merged_wafer_display,
    insert_hold_record_and_link_from_dirty,
    normalize_lot_id,
    query_dirty_hold_infos,
    query_hold_infos_by_ids,
    reset_dirty_hold_infos,
)

logger = logging.getLogger(__name__)

RECORD_TYPE_LABELS = {
    RECORD_TYPE_FT: 'FT异常反馈单',
    RECORD_TYPE_FVI: 'FVI异常反馈单',
    RECORD_TYPE_WLT: 'WLT异常反馈单',
}

_EMPTY_REASON = '历史数据无失败原因'


def _info_table():
    return (getattr(Config, 'HOLD_INFO_TABLE', None) or 'FT_HOLD_INFO_TEST').upper()


def _record_table():
    return (getattr(Config, 'HOLD_RECORD_TABLE', None) or 'FT_HOLD_RECORD').upper()


def _default_status():
    return int(getattr(Config, 'HOLD_RECORD_STATUS', 0) or 0)


def _parse_page(page=1, page_size=20):
    try:
        page = int(page if page is not None else 1)
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = int(page_size if page_size is not None else 20)
    except (TypeError, ValueError):
        page_size = 20
    page = max(1, page)
    page_size = max(1, min(page_size, 200))
    return page, page_size


def _page_payload(items, total, page, page_size):
    total = max(0, int(total or 0))
    pages = max(1, (total + page_size - 1) // page_size) if total > 0 else 1
    page = min(page, pages)
    return {
        'items': items,
        'total': total,
        'page': page,
        'page_size': page_size,
        'pages': pages,
    }


def _serialize_info_row(row: dict) -> dict:
    data = {}
    for key, value in (row or {}).items():
        out_key = str(key).upper()
        if isinstance(value, datetime):
            data[out_key] = value.strftime('%Y-%m-%d %H:%M:%S')
        else:
            data[out_key] = value
    remark = data.get('REMARK')
    if remark is None or str(remark).strip() == '':
        data['FAIL_REASON'] = _EMPTY_REASON
        data['REMARK'] = None
    else:
        data['FAIL_REASON'] = str(remark).strip()
    return data


def _parse_ids(raw_ids):
    ids = []
    seen = set()
    for raw in raw_ids or []:
        try:
            val = int(raw)
        except (TypeError, ValueError):
            continue
        if val in seen:
            continue
        seen.add(val)
        ids.append(val)
    return ids


def list_dirty_hold_infos(
    product_id='',
    lot_id='',
    wafer_id='',
    station='',
    hold_code='',
    keyword='',
    page=1,
    page_size=20,
):
    page, page_size = _parse_page(page, page_size)
    items, total = query_dirty_hold_infos(
        info_table=_info_table(),
        product_id=product_id,
        lot_id=lot_id,
        wafer_id=wafer_id,
        station=station,
        hold_code=hold_code,
        keyword=keyword,
        page=page,
        page_size=page_size,
    )
    if items is None:
        return False, '查询脏 hold_info 失败', _page_payload([], 0, page, page_size)

    data = [_serialize_info_row(r) for r in items]
    return True, '获取成功', _page_payload(data, total, page, page_size)


def reset_dirty_infos(ids, operator=''):
    id_list = _parse_ids(ids)
    if not id_list:
        return False, '请选择要重置的 hold_info', None

    n = reset_dirty_hold_infos(
        id_list,
        info_table=_info_table(),
        operator=operator or '',
    )
    if n < 0:
        return False, '重置失败', None
    if n == 0:
        return False, '未更新任何行（可能已非脏数据）', {'updated': 0, 'ids': id_list}
    logger.info(
        f"root 重置脏 hold_info updated={n} ids={id_list} operator={operator}"
    )
    return True, f'已重置 {n} 条，等待下次 merge', {'updated': n, 'ids': id_list}


def _guess_record_type(infos):
    """优先用规则判定；多类型时取最早一条判定结果。"""
    ordered = sorted(
        infos,
        key=lambda x: (x.hold_dttm or datetime.min, x.id or 0),
    )
    for info in ordered:
        rtype = resolve_record_type(info.product_id, info.hold_code, info.station)
        if rtype is not None:
            return rtype
    return None


def _build_draft_from_infos(infos: list) -> dict:
    """
    用与 merge 相近的字段归纳生成草稿。
    WLT：LOT 截取 '-' 前，WAFER 用 #01#02；其它多片则拼展示串。
    """
    if not infos:
        return {}

    rtype = _guess_record_type(infos)
    if rtype is None:
        rtype = RECORD_TYPE_FT

    is_wlt = rtype == RECORD_TYPE_WLT
    lot_override = None
    if is_wlt:
        first_wafer = next((i.wafer_id for i in infos if i.wafer_id), '')
        first_lot = next((i.lot_id for i in infos if i.lot_id), '')
        lot_override = normalize_lot_id(first_wafer) or normalize_lot_id(first_lot)

    multi = len({(i.wafer_id or '').strip() for i in infos if i.wafer_id}) > 1
    rough = RoughHoldRecord(
        wafer_id=infos[0].wafer_id,
        record_type=rtype,
        items=list(infos),
        all_source_ids=[i.id for i in infos if i.id is not None],
        fragmented_merged=is_wlt or multi,
        lot_id_override=lot_override,
    )
    draft = rough.to_record_dict(status=_default_status()) or {}

    # 非 WLT 单片时仍可能要补展示；to_record_dict 已处理 fragmented
    if is_wlt and not draft.get('WAFER_ID'):
        draft['WAFER_ID'] = build_merged_wafer_display(
            (i.wafer_id for i in infos), max_len=100
        )

    # HOLD_DTTM 可能是 datetime，序列化为字符串便于前端
    hold_dttm = draft.get('HOLD_DTTM')
    if isinstance(hold_dttm, datetime):
        draft['HOLD_DTTM'] = hold_dttm.strftime('%Y-%m-%d %H:%M:%S')

    draft['RECORD_TYPE_NAME'] = RECORD_TYPE_LABELS.get(rtype, str(rtype))
    draft['SOURCE_IDS'] = [i.id for i in infos if i.id is not None]
    return draft


def build_manual_draft(ids):
    id_list = _parse_ids(ids)
    if not id_list:
        return False, '请选择 hold_info', None

    rows = query_hold_infos_by_ids(
        id_list,
        info_table=_info_table(),
        require_dirty=True,
    )
    if rows is None:
        return False, '查询选中 hold_info 失败', None
    if len(rows) != len(id_list):
        found = {int(r['ID']) for r in rows}
        missing = [i for i in id_list if i not in found]
        return False, f'部分记录不存在或已非脏数据: {missing}', None

    infos = [HoldInfo.from_row(r) for r in rows]
    draft = _build_draft_from_infos(infos)
    if not draft:
        return False, '无法生成草稿', None

    return True, '草稿已生成', {
        'draft': draft,
        'infos': [_serialize_info_row(r) for r in rows],
    }


def _normalize_create_record(raw: dict) -> tuple:
    """
    规范化前端提交的 record 字段。
    成功返回 (True, '', record_dict)；失败返回 (False, msg, None)。
    """
    if not isinstance(raw, dict):
        return False, 'record 须为对象', None

    def _s(key, default=None):
        val = raw.get(key, default)
        if val is None:
            return None
        text = str(val).strip()
        return text if text != '' else None

    try:
        source = int(raw.get('SOURCE', 0))
    except (TypeError, ValueError):
        return False, 'SOURCE 须为整数', None
    try:
        record_type = int(raw.get('RECORD_TYPE'))
    except (TypeError, ValueError):
        return False, 'RECORD_TYPE 须为 0/1/2', None
    if record_type not in RECORD_TYPE_LABELS:
        return False, 'RECORD_TYPE 须为 0/1/2', None

    try:
        status = int(raw.get('STATUS', _default_status()))
    except (TypeError, ValueError):
        status = _default_status()

    hold_dttm = raw.get('HOLD_DTTM')
    if isinstance(hold_dttm, str) and hold_dttm.strip():
        text = hold_dttm.strip()
        parsed = None
        for fmt in (
            '%Y-%m-%d %H:%M:%S',
            '%Y/%m/%d %H:%M:%S',
            '%Y-%m-%dT%H:%M:%S',
            '%Y-%m-%d',
        ):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        hold_dttm = parsed
    elif not isinstance(hold_dttm, datetime):
        hold_dttm = None

    record = {
        'PRODUCT_ID': _s('PRODUCT_ID'),
        'STATION': _s('STATION'),
        'EQUIP_ID': _s('EQUIP_ID'),
        'LOT_ID': _s('LOT_ID'),
        'WAFER_ID': _s('WAFER_ID'),
        'HOLD_CODE': _s('HOLD_CODE'),
        'HOLD_REASON': _s('HOLD_REASON'),
        'SOURCE': source,
        'SECOND_CODE': _s('SECOND_CODE'),
        'ROUTE_ID': _s('ROUTE_ID'),
        'GRADE_NUM': _s('GRADE_NUM'),
        'RECORD_TYPE': record_type,
        'STATUS': status,
        'HOLD_DTTM': hold_dttm,
    }
    required = (
        'PRODUCT_ID', 'STATION', 'EQUIP_ID', 'LOT_ID', 'WAFER_ID',
    )
    missing = [k for k in required if not record.get(k)]
    if missing:
        return False, f'缺少必填字段: {", ".join(missing)}', None
    return True, '', record


def create_record_from_dirty(ids, record_raw, operator=''):
    id_list = _parse_ids(ids)
    if not id_list:
        return False, '请选择 hold_info', None

    ok, msg, record = _normalize_create_record(record_raw or {})
    if not ok:
        return False, msg, None

    rows = query_hold_infos_by_ids(
        id_list,
        info_table=_info_table(),
        require_dirty=True,
    )
    if rows is None:
        return False, '校验选中 hold_info 失败', None
    if len(rows) != len(id_list):
        found = {int(r['ID']) for r in rows}
        missing = [i for i in id_list if i not in found]
        return False, f'部分记录不存在或已非脏数据: {missing}', None

    new_id = insert_hold_record_and_link_from_dirty(
        record,
        id_list,
        info_table=_info_table(),
        record_table=_record_table(),
        operator=operator or '',
    )
    if new_id is None:
        return False, '手动提 record 失败（详见 REMARK / 日志）', None

    logger.info(
        f"root 手动提 record id={new_id} from dirty ids={id_list} "
        f"operator={operator}"
    )
    return True, f'已创建 hold_record ID={new_id}', {
        'record_id': new_id,
        'ids': id_list,
    }
