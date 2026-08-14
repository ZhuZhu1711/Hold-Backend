"""
产品工程师业务逻辑：仅可操作 / 查看自己负责（PRODUCT_INFO.PRO_ENG_ID）的型号。
"""
from sqlalchemy import text

from app import db
from app.models.product import ProductInfo
from app.models.defect_code import DefectCode
from app.models.eng_notes import EngNote
from app.controllers import hold_report_ctrl, dispose_ctrl
from app.utils.database_util import query_mes_engineering_notes

NOTE_TYPE_MES = 'MES'
NOTE_TYPE_MANUL = 'MANUL'
NOTE_MAX_LEN = 500


def get_owned_products(eng_user_id, search=''):
    """获取工程师所属型号列表。"""
    try:
        eng_user_id = int(eng_user_id)
    except (TypeError, ValueError):
        return False, '工程师 ID 无效', []

    try:
        query = ProductInfo.query.filter(ProductInfo.PRO_ENG_ID == eng_user_id)
        if search and str(search).strip():
            query = query.filter(
                ProductInfo.PRODUCT_ID.like(f"%{str(search).strip()}%")
            )
        products = query.order_by(ProductInfo.PRODUCT_ID.asc()).all()
        data = [
            {
                'id': p.ID,
                'product_id': p.PRODUCT_ID,
                'gross_die': p.GROSS_DIE,
                'line_type': p.LINE_TYPE,
            }
            for p in products
        ]
        return True, '获取成功', data
    except Exception as e:
        db.session.rollback()
        return False, f'查询失败: {e}', []


def _owned_product_by_code(eng_user_id, product_code):
    """按型号字符串取所属产品；不存在或不属于该工程师返回 None。"""
    if not product_code or not str(product_code).strip():
        return None
    return (
        ProductInfo.query
        .filter(
            ProductInfo.PRODUCT_ID == str(product_code).strip(),
            ProductInfo.PRO_ENG_ID == int(eng_user_id),
        )
        .first()
    )


def _owned_product_by_pk(eng_user_id, product_pk):
    """按 PRODUCT_INFO.ID 取所属产品。"""
    try:
        pk = int(product_pk)
    except (TypeError, ValueError):
        return None
    return (
        ProductInfo.query
        .filter(
            ProductInfo.ID == pk,
            ProductInfo.PRO_ENG_ID == int(eng_user_id),
        )
        .first()
    )


def get_owned_defects(eng_user_id, product_code):
    """查询所属型号的缺陷代码列表。"""
    product = _owned_product_by_code(eng_user_id, product_code)
    if not product:
        return False, '型号不存在或不属于当前工程师', []

    try:
        defects = (
            DefectCode.query
            .filter(DefectCode.PRODUCT_ID == product.ID)
            .order_by(DefectCode.CODE.asc(), DefectCode.ID.asc())
            .all()
        )
        data = []
        for d in defects:
            item = d.to_dict()
            item['product_code'] = product.PRODUCT_ID
            data.append(item)
        return True, '获取成功', data
    except Exception as e:
        db.session.rollback()
        return False, f'查询失败: {e}', []


def create_owned_defect(eng_user_id, data):
    """
    为所属型号新增缺陷代码。
    data: product_code 或 product_id(PK), grade, code, name, bsl
    """
    data = data or {}
    product = None
    if data.get('product_code'):
        product = _owned_product_by_code(eng_user_id, data.get('product_code'))
    elif data.get('product_id') is not None:
        product = _owned_product_by_pk(eng_user_id, data.get('product_id'))

    if not product:
        return False, '型号不存在或不属于当前工程师', None

    try:
        code = data.get('code')
        if code is None or str(code).strip() == '':
            return False, 'code 必填', None
        name = (data.get('name') or '').strip()
        if not name:
            return False, 'name 必填', None

        bsl_raw = data.get('bsl')
        bsl = None if bsl_raw is None or str(bsl_raw).strip() == '' else float(bsl_raw)

        new_defect = DefectCode(
            PRODUCT_ID=product.ID,
            GRADE=(data.get('grade') or '').strip() or None,
            CODE=int(code),
            NAME=name,
            BSL=bsl,
        )
        db.session.add(new_defect)
        db.session.commit()
        item = new_defect.to_dict()
        item['product_code'] = product.PRODUCT_ID
        return True, '新增成功', item
    except (TypeError, ValueError):
        db.session.rollback()
        return False, 'code / bsl 格式无效', None
    except Exception as e:
        db.session.rollback()
        return False, f'新增失败: {e}', None


def update_owned_defect(eng_user_id, defect_id, data):
    """
    更新所属型号的缺陷（grade / code / name / bsl）。
    """
    data = data or {}
    try:
        defect_id = int(defect_id)
    except (TypeError, ValueError):
        return False, 'defect_id 无效', None

    try:
        defect = (
            DefectCode.query
            .join(ProductInfo, DefectCode.PRODUCT_ID == ProductInfo.ID)
            .filter(
                DefectCode.ID == defect_id,
                ProductInfo.PRO_ENG_ID == int(eng_user_id),
            )
            .first()
        )
        if not defect:
            return False, '缺陷不存在或不属于当前工程师', None

        if 'grade' in data:
            grade = data.get('grade')
            defect.GRADE = (str(grade).strip() if grade is not None else '') or None
        if 'code' in data and data.get('code') is not None and str(data.get('code')).strip() != '':
            defect.CODE = int(data.get('code'))
        if 'name' in data:
            name = (data.get('name') or '').strip()
            if not name:
                return False, 'name 不能为空', None
            defect.NAME = name
        if 'bsl' in data:
            bsl_raw = data.get('bsl')
            defect.BSL = (
                None if bsl_raw is None or str(bsl_raw).strip() == '' else float(bsl_raw)
            )

        db.session.commit()
        item = defect.to_dict()
        if defect.product_info:
            item['product_code'] = defect.product_info.PRODUCT_ID
        return True, '更新成功', item
    except (TypeError, ValueError):
        db.session.rollback()
        return False, 'code / bsl 格式无效', None
    except Exception as e:
        db.session.rollback()
        return False, f'更新失败: {e}', None


def delete_owned_defect(eng_user_id, defect_id):
    """删除所属型号的缺陷代码。"""
    try:
        defect_id = int(defect_id)
    except (TypeError, ValueError):
        return False, 'defect_id 无效'

    try:
        defect = (
            DefectCode.query
            .join(ProductInfo, DefectCode.PRODUCT_ID == ProductInfo.ID)
            .filter(
                DefectCode.ID == defect_id,
                ProductInfo.PRO_ENG_ID == int(eng_user_id),
            )
            .first()
        )
        if not defect:
            return False, '缺陷不存在或不属于当前工程师'
        db.session.delete(defect)
        db.session.commit()
        return True, '删除成功'
    except Exception as e:
        db.session.rollback()
        return False, f'删除失败: {e}'


def get_owned_holding_records(
    eng_user_id,
    product_id='',
    station='',
    keyword='',
    record_type=None,
    page=1,
    page_size=20,
    pending_only=False,
    max_page_size=200,
):
    """
    查看所属型号的在线 Hold Record（分页）。
    pending_only=True 时仅返回当前负责人为该工程师的待办。
    每条附带 CAN_DISPOSE：当前负责人是本人且未关闭。
    成功返回 (True, msg, page_payload)。
    """
    success, msg, payload = hold_report_ctrl.get_holding_records(
        product_id=product_id,
        station=station,
        keyword=keyword,
        record_type=record_type,
        page=page,
        page_size=page_size,
        owner_eng_id=eng_user_id,
        current_owner_id=eng_user_id if pending_only else None,
        max_page_size=max_page_size,
    )
    if not success:
        return success, msg, payload

    try:
        eng_id = int(eng_user_id)
    except (TypeError, ValueError):
        eng_id = None

    items = payload.get('items') or []
    for item in items:
        try:
            owner = int(item['CURRENT_OWNER_ID']) if item.get('CURRENT_OWNER_ID') is not None else None
        except (TypeError, ValueError):
            owner = None
        item['CAN_DISPOSE'] = bool(
            eng_id is not None
            and owner == eng_id
            and not item.get('IS_CLOSED')
        )
    dispose_ctrl.attach_reliability_followup_many(items)
    payload['items'] = items
    return True, msg, payload


ENGINEER_HOLDING_EXPORT_HEADERS = list(hold_report_ctrl.HOLDING_EXPORT_HEADERS) + ['是否可处置']


def engineer_holding_export_row(item):
    return hold_report_ctrl.holding_export_row(item) + [
        '是' if item.get('CAN_DISPOSE') else '否',
    ]


def export_owned_holding_records_xlsx(
    eng_user_id,
    product_id='',
    station='',
    keyword='',
    record_type=None,
    pending_only=False,
):
    """导出所属型号在线 Hold 为 xlsx（筛选条件与列表一致，最多 5000 行）。"""
    from app.utils.excel_export import EXPORT_MAX_ROWS, from_page_payload

    success, msg, payload = get_owned_holding_records(
        eng_user_id=eng_user_id,
        product_id=product_id,
        station=station,
        keyword=keyword,
        record_type=record_type,
        page=1,
        page_size=EXPORT_MAX_ROWS,
        pending_only=pending_only,
        max_page_size=EXPORT_MAX_ROWS,
    )
    return from_page_payload(
        success, msg, payload,
        ENGINEER_HOLDING_EXPORT_HEADERS, engineer_holding_export_row, '工程师Hold',
    )


def get_owned_dispose_record(eng_user_id, hold_record_id):
    """
    加载工程师处置页所需的 hold_record。
    须为所属型号；附带 CAN_DISPOSE / GRADE 解析结果 / WAFERS（WLT 按片用）。
    """
    from app.controllers.hold_report_ctrl import RECORD_TYPE_LABELS

    try:
        eng_id = int(eng_user_id)
        rid = int(hold_record_id)
    except (TypeError, ValueError):
        return False, '参数无效', None

    record = dispose_ctrl._load_record(rid)
    if not record:
        return False, 'hold_record 不存在', None

    product_id = record.get('PRODUCT_ID')
    owned = ProductInfo.query.filter_by(
        PRODUCT_ID=product_id, PRO_ENG_ID=eng_id,
    ).first()
    if not owned:
        return False, '该记录不属于您负责的型号', None

    try:
        rt = int(record.get('RECORD_TYPE')) if record.get('RECORD_TYPE') is not None else None
    except (TypeError, ValueError):
        rt = None
    record['RECORD_TYPE_NAME'] = RECORD_TYPE_LABELS.get(rt, '-')

    last_circ = dispose_ctrl._load_circulation(record.get('LAST_CIRCULATION_ID'))
    current_owner_id = last_circ.get('NEXT_OWNER_ID') if last_circ else None
    record['CURRENT_OWNER_ID'] = current_owner_id
    try:
        status_val = int(record.get('STATUS')) if record.get('STATUS') is not None else 0
    except (TypeError, ValueError):
        status_val = 0
    record['IS_CLOSED'] = status_val == dispose_ctrl.DISPOSE_CLOSE
    record['CAN_DISPOSE'] = bool(
        current_owner_id is not None
        and int(current_owner_id) == eng_id
        and not record['IS_CLOSED']
    )
    dispose_ctrl.attach_reliability_followup(record)
    return True, '获取成功', record


def get_owned_fvi_defect_details(eng_user_id, lot_id, line_type='FT'):
    """
    查询所属型号 FVI 缺陷明细。
    仅当该 LOT_ID 出现在工程师所属型号的 hold_record 中时允许查询。
    """
    if lot_id is None or not str(lot_id).strip():
        return False, '请指定 lot_id', None

    lot_id = str(lot_id).strip()
    try:
        eng_user_id = int(eng_user_id)
    except (TypeError, ValueError):
        return False, '工程师 ID 无效', None

    try:
        owned = (
            db.session.execute(
                text("""
                    SELECT COUNT(*) AS CNT
                    FROM FT_HOLD_RECORD r
                    WHERE (
                        r.LOT_ID = :lot_id
                        OR r.LOT_ID LIKE :lot_id_prefix
                    )
                      AND r.RECORD_TYPE = 1
                      AND r.PRODUCT_ID IN (
                          SELECT p.PRODUCT_ID
                          FROM PRODUCT_INFO p
                          WHERE p.PRO_ENG_ID = :eng_id
                      )
                """),
                {
                    'lot_id': lot_id,
                    'lot_id_prefix': f'{lot_id}-%',
                    'eng_id': eng_user_id,
                },
            ).fetchone()
        )
        cnt = int(owned[0] or 0) if owned else 0
        if cnt <= 0:
            return False, '该 Lot 不存在于所属型号的 FVI Hold Record', None
    except Exception as e:
        db.session.rollback()
        return False, f'权限校验失败: {e}', None

    return hold_report_ctrl.get_fvi_defect_details(lot_id, line_type=line_type)


def _normalize_note_text(note):
    """strip + 截断至 NOTE_MAX_LEN；空串返回 ''。"""
    if note is None:
        return ''
    text_val = str(note).strip()
    if not text_val:
        return ''
    if len(text_val) > NOTE_MAX_LEN:
        return text_val[:NOTE_MAX_LEN]
    return text_val


def _owned_note_by_id(eng_user_id, note_id):
    """取所属型号下的工程备注；不存在或不属于当前工程师返回 None。"""
    try:
        note_id = int(note_id)
        eng_user_id = int(eng_user_id)
    except (TypeError, ValueError):
        return None
    return (
        EngNote.query
        .join(ProductInfo, EngNote.PRODUCT_ID == ProductInfo.ID)
        .filter(
            EngNote.ID == note_id,
            ProductInfo.PRO_ENG_ID == eng_user_id,
        )
        .first()
    )


def get_owned_eng_notes(eng_user_id, product_code):
    """查询所属型号可用工程备注列表。"""
    product = _owned_product_by_code(eng_user_id, product_code)
    if not product:
        return False, '型号不存在或不属于当前工程师', []

    try:
        notes = (
            EngNote.query
            .filter(
                EngNote.PRODUCT_ID == product.ID,
                EngNote.IS_AVAILABLE == 1,
            )
            .order_by(EngNote.TYPE.asc(), EngNote.ID.asc())
            .all()
        )
        data = []
        for n in notes:
            item = n.to_dict()
            item['product_code'] = product.PRODUCT_ID
            data.append(item)
        return True, '获取成功', data
    except Exception as e:
        db.session.rollback()
        return False, f'查询失败: {e}', []


def create_owned_eng_note(eng_user_id, data):
    """
    手动新增工程备注。
    data: product_code 或 product_id(PK), note
    TYPE 固定 MANUL。
    """
    data = data or {}
    product = None
    if data.get('product_code'):
        product = _owned_product_by_code(eng_user_id, data.get('product_code'))
    elif data.get('product_id') is not None:
        # 兼容前端传型号字符串或 PK
        raw = data.get('product_id')
        product = _owned_product_by_code(eng_user_id, raw)
        if not product:
            product = _owned_product_by_pk(eng_user_id, raw)

    if not product:
        return False, '型号不存在或不属于当前工程师', None

    note_text = _normalize_note_text(data.get('note'))
    if not note_text:
        return False, 'note 必填', None

    try:
        new_note = EngNote(
            PRODUCT_ID=product.ID,
            NOTE=note_text,
            IS_AVAILABLE=1,
            TYPE=NOTE_TYPE_MANUL,
        )
        db.session.add(new_note)
        db.session.commit()
        item = new_note.to_dict()
        item['product_code'] = product.PRODUCT_ID
        return True, '新增成功', item
    except Exception as e:
        db.session.rollback()
        return False, f'新增失败: {e}', None


def update_owned_eng_note(eng_user_id, note_id, data):
    """仅允许修改 TYPE=MANUL 的备注文案。"""
    data = data or {}
    note = _owned_note_by_id(eng_user_id, note_id)
    if not note:
        return False, '备注不存在或不属于当前工程师', None

    if (note.TYPE or '') != NOTE_TYPE_MANUL:
        return False, '仅可修改手动添加的备注', None

    if int(note.IS_AVAILABLE or 0) != 1:
        return False, '备注已不可用', None

    note_text = _normalize_note_text(data.get('note'))
    if not note_text:
        return False, 'note 不能为空', None

    try:
        note.NOTE = note_text
        db.session.commit()
        item = note.to_dict()
        if note.product_info:
            item['product_code'] = note.product_info.PRODUCT_ID
        return True, '更新成功', item
    except Exception as e:
        db.session.rollback()
        return False, f'更新失败: {e}', None


def delete_owned_eng_note(eng_user_id, note_id):
    """软删除：IS_AVAILABLE=0（MES / MANUL 均可）。"""
    note = _owned_note_by_id(eng_user_id, note_id)
    if not note:
        return False, '备注不存在或不属于当前工程师'

    if int(note.IS_AVAILABLE or 0) != 1:
        return True, '备注已不可用'

    try:
        note.IS_AVAILABLE = 0
        db.session.commit()
        return True, '删除成功'
    except Exception as e:
        db.session.rollback()
        return False, f'删除失败: {e}'


def sync_owned_eng_notes(eng_user_id, product_code):
    """
    按型号从 MES 同步工程备注到 FT_ENG_NOTES。
    - MES 有且本地无：INSERT TYPE=MES
    - MES 有且本地已软删：恢复 IS_AVAILABLE=1
    - 本地可用 MES 不在结果中：IS_AVAILABLE=0
    不触碰 MANUL 行。
    """
    product = _owned_product_by_code(eng_user_id, product_code)
    if not product:
        return False, '型号不存在或不属于当前工程师', None

    mes_notes = query_mes_engineering_notes(product.PRODUCT_ID)
    if mes_notes is None:
        return False, 'MES 工程备注查询失败', None

    mes_set = set()
    for raw in mes_notes:
        text_val = _normalize_note_text(raw)
        if text_val:
            mes_set.add(text_val)

    try:
        local_mes = (
            EngNote.query
            .filter(
                EngNote.PRODUCT_ID == product.ID,
                EngNote.TYPE == NOTE_TYPE_MES,
            )
            .all()
        )
        by_note = {}
        for row in local_mes:
            key = (row.NOTE or '').strip()
            if key and key not in by_note:
                by_note[key] = row

        added = 0
        reactivated = 0
        expired = 0

        for note_text in mes_set:
            existing = by_note.get(note_text)
            if existing is None:
                db.session.add(EngNote(
                    PRODUCT_ID=product.ID,
                    NOTE=note_text,
                    IS_AVAILABLE=1,
                    TYPE=NOTE_TYPE_MES,
                ))
                added += 1
            elif int(existing.IS_AVAILABLE or 0) != 1:
                existing.IS_AVAILABLE = 1
                reactivated += 1

        for key, row in by_note.items():
            if key not in mes_set and int(row.IS_AVAILABLE or 0) == 1:
                row.IS_AVAILABLE = 0
                expired += 1

        db.session.commit()
        summary = {
            'product_code': product.PRODUCT_ID,
            'mes_count': len(mes_set),
            'added': added,
            'reactivated': reactivated,
            'expired': expired,
        }
        return True, (
            f'同步完成：新增 {added}，恢复 {reactivated}，过期 {expired}'
        ), summary
    except Exception as e:
        db.session.rollback()
        return False, f'同步失败: {e}', None
