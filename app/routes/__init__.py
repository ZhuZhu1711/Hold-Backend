from app.routes.user_routes import user_bp
from app.routes.auth_routes import auth_bp
from app.routes.product_routes import product_bp
from app.routes.defect_code_routes import defect_bp
from app.routes.test_data_routes import test_data_bp
from app.routes.common_data_routes import common_data_bp

__all__ = ['user_bp', 'auth_bp', 'product_bp', 'defect_bp', 'test_data_bp', 'common_data_bp']