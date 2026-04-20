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
