from app.models.product import ProductInfo
from app.models.user import User
from app import db
from datetime import datetime

def get_all_products(search=""):
    """
    获取产品列表，支持按产品ID搜索，并按ID倒序排列
    """
    try:
        query = ProductInfo.query
        
        if search:
            query = query.filter(ProductInfo.PRODUCT_ID.like(f"%{search}%"))
            
        # 默认按 ID 倒序，显示最新的在前面
        products = query.order_by(ProductInfo.ID.desc()).all()
        return True, "获取成功", products
    except Exception as e:
        return False, str(e), []

def add_product(data):
    """
    新增产品
    """
    try:
        # 检查产品ID是否重复
        if ProductInfo.query.filter_by(PRODUCT_ID=data['product_id']).first():
            return False, "产品ID已存在"

        new_product = ProductInfo()
        new_product.PRODUCT_ID = data['product_id']
        new_product.GROSS_DIE = data.get('gross_die', 0)
        new_product.LINE_TYPE = data.get('line_type', 0)
        new_product.PRO_ENG_ID = data.get('engineer_id') # 可以为空
        new_product.UPDATE_DTTM = datetime.now().date() # 记录创建时间
        
        db.session.add(new_product)
        db.session.commit()
        return True, "产品添加成功"
    except Exception as e:
        db.session.rollback()
        return False, str(e)

def update_product(product_id, data):
    """
    更新产品：仅允许修改 GROSS_DIE 和 PRO_ENG_ID
    """
    try:
        product = ProductInfo.query.get(product_id)
        if not product:
            return False, "产品不存在"

        # 1. 更新 GROSS_DIE
        if 'gross_die' in data:
            product.GROSS_DIE = data['gross_die']
            
        # 2. 更新 工程师绑定 (PRO_ENG_ID)
        if 'engineer_id' in data:
            # 如果传了ID，检查该用户是否存在
            eng_id = data['engineer_id']
            if eng_id:
                user = User.query.get(eng_id)
                if not user:
                    return False, "指定的工程师用户不存在"
            product.PRO_ENG_ID = eng_id

        # 3. 更新时间
        product.UPDATE_DTTM = datetime.now().date()

        db.session.commit()
        return True, "更新成功"
    except Exception as e:
        db.session.rollback()
        return False, str(e)