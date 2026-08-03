"""
产品工程师业务逻辑：仅可操作 / 查看自己负责（PRODUCT_INFO.PRO_ENG_ID）的型号。
"""
from sqlalchemy import text

from app import db
from app.models.product import ProductInfo
from app.models.defect_code import DefectCode
from app.controllers import hold_report_ctrl


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
    payload['items'] = items
    return True, msg, payload


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
                    WHERE r.LOT_ID = :lot_id
                      AND r.RECORD_TYPE = 1
                      AND r.PRODUCT_ID IN (
                          SELECT p.PRODUCT_ID
                          FROM PRODUCT_INFO p
                          WHERE p.PRO_ENG_ID = :eng_id
                      )
                """),
                {'lot_id': lot_id, 'eng_id': eng_user_id},
            ).fetchone()
        )
        cnt = int(owned[0] or 0) if owned else 0
        if cnt <= 0:
            return False, '该 Lot 不存在于所属型号的 FVI Hold Record', None
    except Exception as e:
        db.session.rollback()
        return False, f'权限校验失败: {e}', None

    return hold_report_ctrl.get_fvi_defect_details(lot_id, line_type=line_type)
