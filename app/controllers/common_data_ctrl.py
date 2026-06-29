from app.models.product import ProductInfo
from sqlalchemy.exc import SQLAlchemyError


def get_gross_die_value(product_id: str):
    """
    获取 Gross Die 信息
    优化逻辑：允许模糊匹配到多条记录，只要提取出的 gross_die 值唯一即可
    """
    if not product_id or not str(product_id).strip():
        return False, "Product ID cannot be empty"

    product_id = str(product_id).strip()
    try:
        # 1. 查出所有匹配的记录
        products = ProductInfo.query.filter(
            ProductInfo.PRODUCT_ID.contains(product_id)
        ).all()

        # 2. 没查到任何数据
        if not products:
            return False, "No product ID matched"

        # 3. 提取所有记录的 GROSS_DIE 并去重
        # 使用 set 去除重复项，比如 [10, 10, 10] -> {10}
        unique_gross_dies = {p.GROSS_DIE for p in products if p.GROSS_DIE is not None}

        # 4. 核心判断：如果去重后只有 1 个值，说明业务上是等价的，直接返回
        if len(unique_gross_dies) == 1:
            return True, unique_gross_dies.pop()
        
        # 5. 如果去重后有多个不同的值，说明数据存在真正的业务冲突
        return False, f"Found multiple different gross_die values: {list(unique_gross_dies)}. Please be more specific."

    except SQLAlchemyError as e:
        return False, f"Database error: {str(e)}"
    except Exception as e:
        return False, f"Unknown Error: {e}"