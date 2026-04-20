import sys
import os
from cryptography import x509
from cryptography.hazmat.primitives.kdf import pbkdf2
# ==========================================
# 路径配置
# ==========================================
current_file_path = os.path.abspath(__file__)
app_folder_path = os.path.dirname(current_file_path)
project_root_path = os.path.dirname(app_folder_path)
if project_root_path not in sys.path:
    sys.path.insert(0, project_root_path)

# ==========================================
# 应用初始化
# ==========================================
from app import create_app, db
# 直接从 routes 包中导入所有蓝图
from app.routes import user_bp, auth_bp, product_bp, defect_bp, test_data_bp
from app.backend_schedule.FT_WLT_TESTLOG_sche import FlaskTaskScheduler

app = create_app()
task_scheduler = FlaskTaskScheduler()

# ==========================================
# 注册蓝图
# ==========================================
app.register_blueprint(user_bp)   # 注册用户管理模块 (/admin/users)
app.register_blueprint(auth_bp)   # 注册认证模块 (/login, /api/login)
app.register_blueprint(product_bp)
app.register_blueprint(defect_bp)
app.register_blueprint(test_data_bp)

# ==========================================
# 程序入口
# ==========================================
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    
    # 打印路由表方便调试
    print("\n=== 🚀 已注册的路由 ===")
    for rule in app.url_map.iter_rules():
        methods = ','.join(sorted([m for m in rule.methods if m not in ('HEAD', 'OPTIONS')]))
        print(f"{methods:7} {rule.rule} -> {rule.endpoint}")
    print("======================\n")
    
    task_scheduler = FlaskTaskScheduler()    # 启动后台线程
    task_scheduler.start()

    app.run(host='0.0.0.0', debug=False, port=50001)
    