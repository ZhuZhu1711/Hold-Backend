from app.models.product import ProductInfo


def get_gross_die(product_id: str):
    if not product_id or not str(product_id).strip():
        return None

    product_id = str(product_id).strip()
    try:
        product = ProductInfo.query.filter_by(PRODUCT_ID=product_id).one_or_none()
        if product is None:
            return None
        return product.GROSS_DIE
    except Exception:
        return None