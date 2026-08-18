from app.routes.user_routes import user_bp
from app.routes.auth_routes import auth_bp
from app.routes.product_routes import product_bp
from app.routes.defect_code_routes import defect_bp
from app.routes.test_data_routes import test_data_bp
from app.routes.common_data_routes import common_data_bp
from app.routes.rawdata_routes import rawdata_bp
from app.routes.hold_report_routes import hold_report_bp
from app.routes.dispose_routes import dispose_bp
from app.routes.engineer_routes import engineer_bp
from app.routes.production_routes import production_bp
from app.routes.quality_routes import quality_bp
from app.routes.client_error_routes import client_error_bp

__all__ = [
    'user_bp', 'auth_bp', 'product_bp', 'defect_bp',
    'test_data_bp', 'common_data_bp', 'rawdata_bp', 'hold_report_bp',
    'dispose_bp', 'engineer_bp', 'production_bp', 'quality_bp',
    'client_error_bp',
]
