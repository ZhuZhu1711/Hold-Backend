from app.models.defect_code import DefectCode
from app.models.product import ProductInfo
from app import db

def get_defects_by_product(product_id):
    """
    1. 根据产品型号查询缺陷代码
    """
    query = (
            DefectCode.query
            .join(ProductInfo) 
            .filter(ProductInfo.PRODUCT_ID == product_id)
        )
    defects = query.all()
    return defects


def query_bincode_defect(product_code):
    """
    按产品型号查询 bincode ↔ defect 映射（只读）。
    product_code: PRODUCT_INFO.PRODUCT_ID 字符串。
    返回 (success, msg, data)，data 为 [{id, code, name, grade, bsl}, ...]
    """
    product_code = (product_code or '').strip()
    if not product_code:
        return False, '请指定 product_id', []

    try:
        defects = (
            DefectCode.query
            .join(ProductInfo)
            .filter(ProductInfo.PRODUCT_ID == product_code)
            .order_by(DefectCode.CODE.asc(), DefectCode.ID.asc())
            .all()
        )
        data = []
        for d in defects:
            data.append({
                'id': int(d.ID) if d.ID is not None else None,
                'code': int(d.CODE) if d.CODE is not None else None,
                'name': str(d.NAME).strip() if d.NAME is not None else '',
                'grade': str(d.GRADE).strip() if d.GRADE is not None else '',
                'bsl': float(d.BSL) if d.BSL is not None else None,
            })
        return True, '获取成功', data
    except Exception as e:
        db.session.rollback()
        return False, f'查询失败: {e}', []

def create_defect(data):
    """
    2. 增加缺陷代码
    """
    new_defect = DefectCode(
        PRODUCT_ID=data['product_id'],
        GRADE=data['grade'],
        CODE=data['code'],
        NAME=data['name'],
        BSL=data['bsl']
    )
    db.session.add(new_defect)
    db.session.commit()
    return new_defect

def delete_defect(defect_id):
    """
    2. 删除缺陷代码
    """
    defect = DefectCode.query.get(defect_id)
    if defect:
        db.session.delete(defect)
        db.session.commit()
    return defect

def update_grade(defect_id, new_grade):
    """
    3. 仅修改 GRADE 字段
    """
    defect = DefectCode.query.get(defect_id)
    if defect:
        defect.GRADE = new_grade
        db.session.commit()
    return defect
